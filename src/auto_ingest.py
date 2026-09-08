"""Bridge automatic statement discovery straight into the structured store.

`auto_extract.extract_financial_statements` already finds and ranks the
pages likely to hold each statement — that's the part of the pipeline
that's "doing a great job." What was missing was the next step: turning
those ranked pages/tables into canonical `line_items` rows, using the
SAME dictionary the query-time agent (derivation.calculate_metric,
agent.run.ask) already resolves against, so ingestion and retrieval can
never silently drift out of sync (Rule 1).

This replaces hand-specifying --pages/--statement/--period per call
(still available in ingest.py for one-off manual correction) with a
single call per filing. It deliberately refuses rather than guesses in
two places extraction is most likely to lie: low-quality tables
(MIN_TABLE_QUALITY) and rows with more than one plausible numeric value
and no year header to resolve them against (ambiguous_multi_period,
see extraction/llm_cleanup.py) — both are skipped and counted, not
silently stored.

`ingest_pages_into_store` is the reusable core: it takes already-shaped
page/table JSON (the same structure extract_financial_statements
returns per statement) and needs no PDF on disk, which is why
analyst_webapp.py's /analyze also calls it directly — that UI's
uploaded file is gone (temp dir cleanup) by the time a question comes
in, but the extracted table JSON already round-tripped through the
browser is enough to ingest from.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Callable

import pdfplumber

from src.auto_extract import extract_financial_statements
from src.extraction.llm_cleanup import cleanup_table
from src.agent.derivation import canonicalize_metric
from src.agent.periods import canonicalize_period
from src.store.schema import init_db
from src.store.db import add_document, add_line_item, LineItem

MIN_TABLE_QUALITY = 0.5  # below this, a table is more likely to mislead than help — Rule 2

_UNIT_HINT_RE = re.compile(
    r"(?:₹|rs\.?|inr|usd|\$)\s*(?:in\s+)?"
    r"(crores?|cr|lakh?s?|lacs?|millions?|mn|thousands?|billions?|bn)",
    re.IGNORECASE,
)

_EMPTY_SUMMARY = {
    "line_items_stored": 0, "pages_used": 0, "skipped_low_quality_tables": 0,
    "skipped_ambiguous_multi_period_rows": 0, "skipped_unparsed_rows": 0,
}


def _detect_page_unit(pdf_path: str, page: int) -> str | None:
    """Read the free-text scale/currency declaration filings put near a
    statement header (e.g. '(₹ in Crores)') — Rule 55. Best-effort only:
    an undetected unit is stored as 'unspecified', never guessed."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = pdf.pages[page - 1].extract_text() or ""
    except Exception:
        return None
    match = _UNIT_HINT_RE.search(text)
    return match.group(0).strip() if match else None


def ingest_pages_into_store(conn, document_id: int, entity: str, period: str, statement: str,
                            pages: list[dict], *, model: str = "hermes3:8b",
                            consolidated: bool | None = None,
                            unit_resolver: Callable[[int], str | None] | None = None) -> dict[str, Any]:
    """Core ingest loop. `pages` is a list of page results shaped like
    `extract_financial_statements(...)["statements"][statement]`:
    `[{"page": int, "needs_ocr": bool, "tables": [...]}]`. `unit_resolver`,
    when given, is called with a page number to detect that page's declared
    scale (Rule 55) — omit it when there's no PDF on disk to re-read; every
    stored row's unit falls back to "unspecified" (never guessed) rather
    than erroring.
    """
    requested_period = canonicalize_period(period)
    summary = dict(_EMPTY_SUMMARY)

    for page_result in pages:
        if page_result.get("needs_ocr"):
            continue
        tables = sorted(page_result.get("tables") or [], key=lambda t: t.get("quality_score", 0), reverse=True)
        if not tables:
            continue
        table = tables[0]
        if table.get("quality_score", 0) < MIN_TABLE_QUALITY:
            summary["skipped_low_quality_tables"] += 1
            continue

        page = page_result.get("page")
        page_unit = unit_resolver(page) if unit_resolver else None
        summary["pages_used"] += 1

        try:
            cleaned = cleanup_table(table["rows"], model=model)
        except Exception:
            continue

        for row in cleaned:
            if row.get("ambiguous_multi_period"):
                summary["skipped_ambiguous_multi_period_rows"] += 1
                continue
            metric = canonicalize_metric(row.get("metric_raw", ""))
            if not metric or row.get("value") is None:
                summary["skipped_unparsed_rows"] += 1
                continue
            # A detected column period (see extraction/llm_cleanup.py) is more
            # precise than the single `period` argument when a table carries
            # 2-3 statement years — prefer it when present.
            row_period = canonicalize_period(row["period_raw"]) if row.get("period_raw") else requested_period
            add_line_item(conn, document_id, LineItem(
                entity=entity, period=row_period, statement=statement,
                metric=metric, metric_raw=row["metric_raw"], value=row["value"],
                unit=page_unit or "unspecified", consolidated=consolidated,
                source_page=page, source_table=table.get("table_caption"),
                extraction_method=table.get("method", "unknown"),
                extraction_confidence=table.get("quality_score", 0.0),
            ))
            summary["line_items_stored"] += 1

    return summary


def auto_ingest(pdf_path: str, entity: str, doc_type: str, fiscal_year: str,
                period: str, db_path: str, *, top_k: int = 3,
                model: str = "hermes3:8b", consolidated: bool | None = None) -> dict[str, Any]:
    discovered = extract_financial_statements(pdf_path, top_k=top_k)
    conn = init_db(db_path)
    document_id = add_document(conn, entity, doc_type, fiscal_year, pdf_path)
    requested_period = canonicalize_period(period)

    totals = dict(_EMPTY_SUMMARY)
    per_statement: dict[str, int] = {}

    for statement, pages in discovered["statements"].items():
        summary = ingest_pages_into_store(
            conn, document_id, entity, requested_period, statement, pages,
            model=model, consolidated=consolidated,
            unit_resolver=lambda page: _detect_page_unit(pdf_path, page),
        )
        for key in totals:
            totals[key] += summary[key]
        per_statement[statement] = summary["line_items_stored"]

    return {
        "pdf": pdf_path, "entity": entity, "period": requested_period, "document_id": document_id,
        **totals, "by_statement": per_statement,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Discover, extract and ingest a filing in one step "
                                             "(no manual --pages needed)")
    p.add_argument("pdf_path")
    p.add_argument("--entity", required=True)
    p.add_argument("--doc-type", required=True,
                   choices=["10K", "annual_report", "sebi_quarterly", "sebi_annual", "investor_deck"])
    p.add_argument("--fiscal-year", required=True)
    p.add_argument("--period", required=True)
    p.add_argument("--db", default="data/financials.db")
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--model", default="hermes3:8b")
    p.add_argument("--consolidated", choices=["true", "false"], default=None)
    args = p.parse_args()

    consolidated = None if args.consolidated is None else args.consolidated == "true"
    summary = auto_ingest(args.pdf_path, args.entity, args.doc_type, args.fiscal_year,
                          args.period, args.db, top_k=args.top_k, model=args.model,
                          consolidated=consolidated)
    print(f"Stored {summary['line_items_stored']} line items across {summary['pages_used']} pages.")
    print(f"Skipped: {summary['skipped_low_quality_tables']} low-quality tables, "
          f"{summary['skipped_ambiguous_multi_period_rows']} ambiguous multi-period rows, "
          f"{summary['skipped_unparsed_rows']} unparsed rows.")
    print(summary["by_statement"])
