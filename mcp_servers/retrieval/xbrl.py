"""Small, deterministic XBRL/iXBRL instance reader for core statement facts.

This is intentionally conservative: only well-known concept local names are
normalized. Unknown taxonomy concepts are ignored rather than guessed.
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


def _period_from_context(context: ET.Element) -> str | None:
    period = context.find("{*}period")
    if period is None:
        return None
    instant = period.findtext("{*}instant")
    if instant:
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
        quarter_map = {(4, 1, 6): "Q1", (7, 1, 9): "Q2", (10, 1, 12): "Q3", (1, 1, 3): "Q4"}
        q = quarter_map.get((start_date.month, start_date.day, end_date.month))
        if q:
            fy = end_date.year + 1 if end_date.month in {6, 9, 12} else end_date.year
            return f"{q}FY{fy}"
    return None


def _unit(root: ET.Element, unit_ref: str | None) -> str:
    """Resolve common XBRL units, including inline filings that use an
    abbreviated or directly referenced unit id without a separate unit node.
    """
    if not unit_ref:
        return "unspecified"
    direct = unit_ref.strip()
    if direct.upper() in {"INR", "INDIANRUPEE"}:
        return "INR absolute"
    if direct.upper() in {"USD", "EUR", "GBP"}:
        return direct.upper()
    if direct.lower() in {"shares", "share"}:
        return "shares"
    if direct.lower() in {"pure", "ratio"}:
        return "x"

    node = root.find(f".//{{*}}unit[@id='{unit_ref}']")
    if node is None:
        return "unspecified"
    measure = node.findtext("{*}measure") or ""
    local = _local_name(measure)
    if local in {"INR", "IndianRupee"}:
        return "INR absolute"
    if local in {"USD", "EUR", "GBP"}:
        return local
    if local in {"shares", "Shares"}:
        return "shares"
    if local.lower() in {"pure", "ratio"}:
        return "x"
    return local or "unspecified"


def _concept_map() -> dict[str, str]:
    return {alias: metric for metric, aliases in CONCEPTS.items() for alias in aliases}


def parse_xbrl(path: str | Path) -> list[dict[str, Any]]:
    root = ET.parse(str(path)).getroot()
    contexts = {node.attrib.get("id"): node for node in root.findall(".//{*}context") if node.attrib.get("id")}
    concept_map = _concept_map()
    facts: list[dict[str, Any]] = []
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        concept = _local_name(node.tag)
        metric = concept_map.get(concept)
        if metric is None and concept in {"nonFraction", "nonNumeric"}:
            name = node.attrib.get("name")
            concept = name.rsplit(":", 1)[-1] if name else concept
            metric = concept_map.get(concept)
        if metric is None:
            continue
        context_ref = node.attrib.get("contextRef")
        context = contexts.get(context_ref)
        if context is None:
            continue
        period = _period_from_context(context)
        if not period:
            continue
        raw = (node.text or "").strip()
        if not raw:
            continue
        try:
            value = Decimal(raw)
            if node.attrib.get("scale"):
                value *= Decimal(10) ** int(node.attrib["scale"])
            if node.attrib.get("sign") == "-":
                value = -abs(value)
        except Exception:
            continue
        facts.append({
            "metric": canonicalize_metric(metric),
            "period": canonicalize_period(period),
            "statement": STATEMENT_BY_METRIC[metric],
            "value": float(value),
            "unit": _unit(root, node.attrib.get("unitRef")),
            "metric_raw": concept,
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
            add_line_item(conn, document_id, LineItem(
                entity=entity, period=fact["period"], statement=fact["statement"],
                metric=fact["metric"], metric_raw=fact["metric_raw"], value=fact["value"],
                unit=fact["unit"], consolidated=consolidated, source_page=None,
                source_table="XBRL", extraction_method="xbrl_instance",
                extraction_confidence=0.99, source_type=source_type,
            ))
            stored += 1
    finally:
        conn.close()
    return {"document_id": document_id, "facts_found": len(facts),
            "line_items_stored": stored, "source_type": source_type}
