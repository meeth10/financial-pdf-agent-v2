"""Conservative XBRL/iXBRL fact reader for core Indian statement metrics.

NSE exposes Regulation 33 financial-results XBRL/iXBRL filings, including
Integrated Filing - Financial - Ind AS. The parser accepts both ordinary XBRL
instances and iXBRL/XHTML documents. Unknown taxonomy concepts are ignored
rather than guessed. citeturn538405search10turn538405search3
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import xml.etree.ElementTree as ET
from typing import Any

from src.agent.derivation import canonicalize_metric
from src.agent.periods import canonicalize_period
from src.store.db import LineItem, add_document, add_line_item
from src.store.schema import init_db

CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": ("Revenue", "RevenueFromOperations", "RevenueFromContractsWithCustomers", "Sales"),
    "other_income": ("OtherIncome", "OtherIncomeExpense"),
    "pbt": ("ProfitBeforeTax", "ProfitLossBeforeTax", "ProfitBeforeIncomeTax"),
    "net_income": ("ProfitLoss", "ProfitForThePeriod", "ProfitAfterTax", "ProfitLossAttributableToOwnersOfParent"),
    "tax_expense": ("IncomeTaxExpense", "IncomeTaxExpenseBenefit", "TaxExpense"),
    "ebitda": ("EBITDA", "EarningsBeforeInterestTaxDepreciationAndAmortisation"),
    "ebit": ("EBIT", "OperatingProfit", "OperatingIncome"),
    "depreciation": ("DepreciationExpense", "DepreciationDepletionAndAmortisation"),
    "amortisation": ("AmortisationExpense", "AmortizationExpense"),
    "total_assets": ("Assets", "TotalAssets"),
    "total_liabilities": ("Liabilities", "TotalLiabilities"),
    "shareholders_equity": ("Equity", "EquityAttributableToOwnersOfParent", "ShareholdersEquity"),
    "cash_and_equivalents": ("CashAndCashEquivalents", "CashCashEquivalents"),
    "total_debt": ("Borrowings", "BorrowingsCurrentAndNoncurrent", "Debt"),
    "current_assets": ("CurrentAssets",),
    "current_liabilities": ("CurrentLiabilities",),
    "operating_cash_flow": ("CashFlowsFromUsedInOperatingActivities", "NetCashFlowsFromOperatingActivities"),
    "investing_cash_flow": ("CashFlowsFromUsedInInvestingActivities", "NetCashFlowsFromInvestingActivities"),
    "financing_cash_flow": ("CashFlowsFromUsedInFinancingActivities", "NetCashFlowsFromFinancingActivities"),
    "capital_expenditure": ("PurchaseOfPropertyPlantAndEquipment", "AdditionsToPropertyPlantAndEquipment", "PaymentsToAcquirePropertyPlantAndEquipment"),
}

STATEMENT_BY_METRIC = {
    "revenue": "income_statement", "other_income": "income_statement", "pbt": "income_statement",
    "net_income": "income_statement", "tax_expense": "income_statement", "ebitda": "income_statement",
    "ebit": "income_statement", "depreciation": "income_statement", "amortisation": "income_statement",
    "total_assets": "balance_sheet", "total_liabilities": "balance_sheet", "shareholders_equity": "balance_sheet",
    "cash_and_equivalents": "balance_sheet", "total_debt": "balance_sheet",
    "current_assets": "balance_sheet", "current_liabilities": "balance_sheet",
    "operating_cash_flow": "cash_flow", "investing_cash_flow": "cash_flow",
    "financing_cash_flow": "cash_flow", "capital_expenditure": "cash_flow",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _concept_name(node: ET.Element) -> str | None:
    local = _local_name(node.tag)
    if local in {"nonFraction", "nonNumeric"}:
        name = node.attrib.get("name")
        return name.rsplit(":", 1)[-1] if name else None
    return local


def _period_from_context(context: ET.Element) -> str | None:
    period = context.find("{*}period")
    if period is None:
        return None
    start = period.findtext("{*}startDate")
    end = period.findtext("{*}endDate")
    if not end:
        return None
    try:
        end_date = date.fromisoformat(end)
    except ValueError:
        return None
    if start:
        try:
            start_date = date.fromisoformat(start)
        except ValueError:
            return None
        if start_date.month == 4 and start_date.day == 1 and end_date.month == 3:
            return f"FY{end_date.year}"
        quarter_by_range = {
            (4, 6): "Q1", (7, 9): "Q2", (10, 12): "Q3", (1, 3): "Q4",
        }
        quarter = quarter_by_range.get((start_date.month, end_date.month))
        if quarter:
            fy = end_date.year + 1 if end_date.month in {6, 9, 12} else end_date.year
            return f"{quarter}FY{fy}"
    return None


def _context_scope(context: ET.Element) -> bool | None:
    """Infer consolidation from explicit XBRL entity/segment labels when present."""
    values = " ".join((node.text or "") for node in context.iter() if isinstance(node.tag, str)).lower()
    has_consolidated = "consolidated" in values
    has_standalone = "standalone" in values or "separate" in values
    if has_consolidated and not has_standalone:
        return True
    if has_standalone and not has_consolidated:
        return False
    return None


def _unit(root: ET.Element, unit_ref: str | None) -> str:
    if not unit_ref:
        return "unspecified"
    node = root.find(f".//{{*}}unit[@id='{unit_ref}']")
    if node is None:
        return "unspecified"
    measure = node.findtext("{*}measure") or ""
    local = _local_name(measure)
    if local in {"INR", "IndianRupee"}:
        return "INR absolute"
    if local in {"USD", "EUR", "GBP"}:
        return local
    if local.lower() in {"pure", "ratio"}:
        return "x"
    if local.lower() in {"shares", "share"}:
        return "shares"
    return local or "unspecified"


def _numeric_value(node: ET.Element) -> float | None:
    raw = (node.text or "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except Exception:
        return None
    scale = node.attrib.get("scale")
    if scale:
        try:
            value *= Decimal(10) ** int(scale)
        except Exception:
            return None
    if node.attrib.get("sign") == "-":
        value = -abs(value)
    return float(value)


def parse_xbrl(path: str | Path) -> list[dict[str, Any]]:
    root = ET.parse(str(path)).getroot()
    contexts = {node.attrib.get("id"): node for node in root.iter() if _local_name(node.tag) == "context" and node.attrib.get("id")}
    concept_map = {alias: metric for metric, aliases in CONCEPTS.items() for alias in aliases}
    facts: list[dict[str, Any]] = []

    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        concept = _concept_name(node)
        metric = concept_map.get(concept or "")
        if metric is None:
            continue
        context = contexts.get(node.attrib.get("contextRef"))
        if context is None:
            continue
        period = _period_from_context(context)
        value = _numeric_value(node)
        if not period or value is None:
            continue
        facts.append({
            "metric": canonicalize_metric(metric),
            "period": canonicalize_period(period),
            "statement": STATEMENT_BY_METRIC[metric],
            "value": value,
            "unit": _unit(root, node.attrib.get("unitRef")),
            "metric_raw": concept,
            "consolidated": _context_scope(context),
        })
    return facts


def ingest_xbrl(path: str | Path, *, entity: str, fiscal_year: str,
                 consolidated: bool, db_path: str, source_type: str) -> dict[str, Any]:
    facts = parse_xbrl(path)
    conn = init_db(db_path)
    document_id = add_document(conn, entity, "sebi_xbrl", fiscal_year, str(path), source_type=source_type)
    stored = 0
    try:
        for fact in facts:
            fact_scope = fact.get("consolidated")
            if fact_scope is not None and fact_scope is not consolidated:
                continue
            add_line_item(conn, document_id, LineItem(
                entity=entity,
                period=fact["period"],
                statement=fact["statement"],
                metric=fact["metric"],
                metric_raw=fact["metric_raw"],
                value=fact["value"],
                unit=fact["unit"],
                consolidated=consolidated,
                source_page=None,
                source_table="XBRL/iXBRL",
                extraction_method="xbrl_instance",
                extraction_confidence=0.99,
                source_type=source_type,
            ))
            stored += 1
    finally:
        conn.close()
    return {
        "document_id": document_id,
        "facts_found": len(facts),
        "line_items_stored": stored,
        "source_type": source_type,
    }
