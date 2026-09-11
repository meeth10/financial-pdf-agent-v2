"""Canonical company financial profile for downstream analytics and UI.

The profile is a read model over the existing evidence store. It does not
create a second financial-data source and it preserves document/page
provenance on every reported fact.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from src.agent.derivation import canonicalize_metric
from src.agent.periods import canonicalize_period


def build_company_profile(conn: sqlite3.Connection, entity: str,
                          periods: Iterable[str] | None = None) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    requested = {canonicalize_period(p) for p in periods} if periods else None
    rows = conn.execute(
        """
        SELECT li.*, d.doc_type, d.fiscal_year, d.filepath, d.source_type AS document_source_type
        FROM line_items li
        JOIN documents d ON d.id = li.document_id
        WHERE li.entity = ?
        ORDER BY li.period, li.statement, li.metric, li.id
        """,
        (entity,),
    ).fetchall()

    by_period: dict[str, dict[str, Any]] = {}
    period_order: list[str] = []
    for row in rows:
        period = canonicalize_period(row["period"])
        if requested is not None and period not in requested:
            continue
        if period not in by_period:
            by_period[period] = {
                "period": period,
                "statements": {"balance_sheet": {}, "income_statement": {}, "cash_flow": {}},
                "evidence_count": 0,
            }
            period_order.append(period)
        record = {
            "value": row["value"],
            "unit": row["unit"],
            "metric_raw": row["metric_raw"],
            "consolidated": row["consolidated"],
            "source_page": row["source_page"],
            "source_table": row["source_table"],
            "source_type": row["source_type"],
            "document_source_type": row["document_source_type"],
            "document_id": row["document_id"],
            "filepath": row["filepath"],
            "extraction_method": row["extraction_method"],
            "extraction_confidence": row["extraction_confidence"],
            "fiscal_year": row["fiscal_year"],
            "doc_type": row["doc_type"],
        }
        statement = row["statement"]
        by_period[period]["statements"].setdefault(statement, {})[canonicalize_metric(row["metric"])] = record
        by_period[period]["evidence_count"] += 1

    return {
        "entity": entity,
        "periods": period_order,
        "profile": [by_period[p] for p in period_order],
        "period_count": len(period_order),
        "evidence_count": sum(item["evidence_count"] for item in by_period.values()),
    }
