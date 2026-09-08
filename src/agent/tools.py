"""Deterministic tools exposed to the financial reasoning agent."""

import sqlite3
from src.store import db
from src.export.excel_export import export_entity
from .derivation import (calculate_metric, calculate_growth, calculate_return_ratio,
                        calculate_cagr, canonicalize_metric)
from .periods import canonicalize_period
from . import validation


def get_line_item(conn: sqlite3.Connection, entity: str, metric: str, period: str,
                  statement: str | None = None, consolidated: bool | None = None) -> dict:
    canonical = canonicalize_metric(metric)
    canonical_period = canonicalize_period(period)
    rows = db.get_line_item(conn, entity, canonical, canonical_period, statement, consolidated)
    if not rows:
        return {"status": "UNAVAILABLE", "entity": entity, "metric": canonical, "period": canonical_period}
    if len(rows) > 1:
        return {"status": "CONFLICTED", "entity": entity, "metric": canonical, "period": canonical_period,
                "candidates": [dict(r) for r in rows]}
    row = dict(rows[0])
    confidence = row["extraction_confidence"] or 0.0
    return {
        "status": "REPORTED",
        "value": row["value"], "unit": row["unit"], "period": row["period"],
        "statement": row["statement"],
        "consolidated": bool(row["consolidated"]) if row["consolidated"] is not None else None,
        "source_page": row["source_page"], "source_table": row["source_table"],
        "metric_raw": row["metric_raw"],
        "confidence": "HIGH" if confidence >= 0.9 else "MEDIUM" if confidence >= 0.6 else "LOW",
        "extraction_confidence": confidence,
    }


def list_available_periods(conn: sqlite3.Connection, entity: str) -> dict:
    return {"entity": entity, "periods": db.list_periods(conn, entity)}


def list_available_metrics(conn: sqlite3.Connection, entity: str, statement: str | None = None) -> dict:
    return {"entity": entity, "statement": statement, "metrics": db.list_metrics(conn, entity, statement)}


def calculate_metric_tool(conn: sqlite3.Connection, entity: str, metric: str, period: str,
                          statement: str | None = None,
                          consolidated: bool | None = None) -> dict:
    return calculate_metric(conn, entity, metric, period, statement, consolidated)


def calculate_growth_tool(conn: sqlite3.Connection, entity: str, metric: str,
                          current_period: str, prior_period: str,
                          statement: str | None = None,
                          consolidated: bool | None = None) -> dict:
    return calculate_growth(conn, entity, metric, current_period, prior_period, statement, consolidated)


def calculate_yoy(conn: sqlite3.Connection, entity: str, metric: str,
                  period_current: str, period_prior: str, statement: str | None = None) -> dict:
    return calculate_growth_tool(conn, entity, metric, period_current, period_prior, statement)


def calculate_return_ratio_tool(conn: sqlite3.Connection, entity: str, ratio: str, period: str,
                                prior_period: str | None = None, statement: str | None = None,
                                consolidated: bool | None = None) -> dict:
    return calculate_return_ratio(conn, entity, ratio, period, prior_period, statement, consolidated)


def calculate_cagr_tool(conn: sqlite3.Connection, entity: str, metric: str,
                        start_period: str, end_period: str, n_years: float,
                        statement: str | None = None, consolidated: bool | None = None) -> dict:
    return calculate_cagr(conn, entity, metric, start_period, end_period, n_years, statement, consolidated)


def run_validation_checks(conn: sqlite3.Connection, entity: str, period: str,
                          consolidated: bool | None = None) -> dict:
    return {"entity": entity, "period": canonicalize_period(period),
            "checks": validation.run_all(conn, entity, canonicalize_period(period), consolidated)}


def export_to_excel(conn: sqlite3.Connection, entity: str, output_path: str) -> dict:
    return {"status": "ok", "path": export_entity(conn, entity, output_path)}


TOOL_SCHEMAS = [
    {"type":"function","function":{"name":"get_line_item","description":"Retrieve one directly reported financial line item. Always prefer this before deriving.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"metric":{"type":"string"},"period":{"type":"string"},"statement":{"type":"string","enum":["balance_sheet","income_statement","cash_flow"]},"consolidated":{"type":"boolean"}},"required":["entity","metric","period"]}}},
    {"type":"function","function":{"name":"list_available_periods","description":"List periods available in the financial store.","parameters":{"type":"object","properties":{"entity":{"type":"string"}},"required":["entity"]}}},
    {"type":"function","function":{"name":"list_available_metrics","description":"List normalized financial metrics available in the store.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"statement":{"type":"string","enum":["balance_sheet","income_statement","cash_flow"]}},"required":["entity"]}}},
    {"type":"function","function":{"name":"calculate_metric","description":"Calculate a rule-book metric. Directly reported values take precedence over derived values. Returns formula, inputs, provenance and confidence.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"metric":{"type":"string"},"period":{"type":"string"},"statement":{"type":"string","enum":["balance_sheet","income_statement","cash_flow"]},"consolidated":{"type":"boolean"}},"required":["entity","metric","period"]}}},
    {"type":"function","function":{"name":"calculate_growth","description":"Calculate rule-book growth for revenue, EBITDA or net income using current and prior periods.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"metric":{"type":"string"},"current_period":{"type":"string"},"prior_period":{"type":"string"},"statement":{"type":"string","enum":["balance_sheet","income_statement","cash_flow"]},"consolidated":{"type":"boolean"}},"required":["entity","metric","current_period","prior_period"]}}},
    {"type":"function","function":{"name":"calculate_return_ratio","description":"Calculate ROA or ROE (rule-book \u00a741-42). Prefers the average of opening+closing balance-sheet base when prior_period is given; falls back to closing-only marked PROXY otherwise.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"ratio":{"type":"string","enum":["roa","roe"]},"period":{"type":"string"},"prior_period":{"type":"string"},"statement":{"type":"string","enum":["balance_sheet","income_statement","cash_flow"]},"consolidated":{"type":"boolean"}},"required":["entity","ratio","period"]}}},
    {"type":"function","function":{"name":"calculate_cagr","description":"Calculate CAGR between two periods (rule-book \u00a749). Refuses on a zero or negative starting value.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"metric":{"type":"string"},"start_period":{"type":"string"},"end_period":{"type":"string"},"n_years":{"type":"number"},"statement":{"type":"string","enum":["balance_sheet","income_statement","cash_flow"]},"consolidated":{"type":"boolean"}},"required":["entity","metric","start_period","end_period","n_years"]}}},
    {"type":"function","function":{"name":"run_validation_checks","description":"Run rule-book \u00a752 accounting reconciliation checks (balance sheet, EBITDA bridge, cash flow composition, etc.) for one entity/period. Use before trusting derived ratios for a newly ingested filing.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"period":{"type":"string"},"consolidated":{"type":"boolean"}},"required":["entity","period"]}}},
    {"type":"function","function":{"name":"export_to_excel","description":"Export stored financial data to Excel.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"output_path":{"type":"string"}},"required":["entity","output_path"]}}},
]

DISPATCH = {
    "get_line_item": get_line_item,
    "list_available_periods": list_available_periods,
    "list_available_metrics": list_available_metrics,
    "calculate_metric": calculate_metric_tool,
    "calculate_growth": calculate_growth_tool,
    "calculate_yoy": calculate_yoy,
    "calculate_return_ratio": calculate_return_ratio_tool,
    "calculate_cagr": calculate_cagr_tool,
    "run_validation_checks": run_validation_checks,
    "export_to_excel": export_to_excel,
}
