"""MCP-facing deterministic evidence tools."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from shared.provenance import attach_provenance
from src.agent.derivation import canonicalize_metric
from src.agent.periods import canonicalize_period
from .conflicts import classify_candidates


def _db_path() -> str:
    return os.getenv("FINANCIAL_DB_PATH", "data/financials.db")


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _fetch(conn: sqlite3.Connection, entity: str, metric: str, period: str,
           statement: str | None, consolidated: bool | None) -> list[dict[str, Any]]:
    query = """
        SELECT li.*, d.doc_type, d.fiscal_year, d.filepath, d.ingested_at
        FROM line_items li
        JOIN documents d ON d.id = li.document_id
        WHERE li.entity = ? AND li.metric = ? AND li.period = ?
    """
    params: list[Any] = [entity, canonicalize_metric(metric), canonicalize_period(period)]
    if statement:
        query += " AND li.statement = ?"; params.append(statement)
    if consolidated is not None:
        query += " AND li.consolidated = ?"; params.append(int(consolidated))
    query += " ORDER BY li.document_id, li.id"
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def get_evidence(entity: str, metric: str, period: str, statement: str | None = None,
                 consolidated: bool | None = None) -> dict[str, Any]:
    with open_db() as conn:
        candidates = _fetch(conn, entity, metric, period, statement, consolidated)
    if not candidates:
        return {"status": "UNAVAILABLE", "entity": entity,
                "metric": canonicalize_metric(metric), "period": canonicalize_period(period),
                "reason": "no matching evidence"}
    status = "REPORTED" if len(candidates) == 1 else "CONFLICTED"
    result: dict[str, Any] = {
        "status": status,
        "entity": entity,
        "metric": canonicalize_metric(metric),
        "period": canonicalize_period(period),
        "candidates": candidates,
    }
    if len(candidates) == 1:
        result.update({k: candidates[0].get(k) for k in (
            "value", "unit", "statement", "consolidated", "source_page",
            "source_table", "extraction_method", "extraction_confidence", "filepath",
        )})
        result["provenance"] = {"document_id": candidates[0]["document_id"],
                                 "source_page": candidates[0].get("source_page"),
                                 "source": candidates[0].get("filepath"),
                                 "extraction_method": candidates[0].get("extraction_method"),
                                 "extraction_confidence": candidates[0].get("extraction_confidence")}
    else:
        result["comparison"] = classify_candidates(candidates)
    return attach_provenance(result, entity=entity)


def list_available_periods(entity: str) -> dict[str, Any]:
    with open_db() as conn:
        rows = conn.execute("SELECT DISTINCT period FROM line_items WHERE entity = ? ORDER BY period", (entity,)).fetchall()
    return {"status": "REPORTED" if rows else "UNAVAILABLE", "entity": entity,
            "periods": [r[0] for r in rows]}


def list_available_metrics(entity: str, statement: str | None = None) -> dict[str, Any]:
    query = "SELECT DISTINCT metric FROM line_items WHERE entity = ?"
    params: list[Any] = [entity]
    if statement:
        query += " AND statement = ?"; params.append(statement)
    with open_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return {"status": "REPORTED" if rows else "UNAVAILABLE", "entity": entity,
            "statement": statement, "metrics": [r[0] for r in rows]}


def compare_evidence(entity: str, metric: str, period: str,
                     candidates: list[dict[str, Any]] | None = None,
                     statement: str | None = None,
                     consolidated: bool | None = None) -> dict[str, Any]:
    if candidates is None:
        with open_db() as conn:
            candidates = _fetch(conn, entity, metric, period, statement, consolidated)
    if not candidates:
        return {"status": "UNAVAILABLE", "entity": entity, "metric": canonicalize_metric(metric),
                "period": canonicalize_period(period), "reason": "no evidence candidates"}
    comparison = classify_candidates(candidates)
    return {"status": comparison["overall"] if comparison["overall"] in {
        "TRUE_CONFLICT", "SCOPE_DIFFERENCE", "RESTATED", "UNIT_DIFFERENCE", "ROUNDING_DIFFERENCE", "AGREES"
    } else "TRUE_CONFLICT", "entity": entity, "metric": canonicalize_metric(metric),
            "period": canonicalize_period(period), **comparison}
