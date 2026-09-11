"""Bridge automatic statement discovery into the structured store."""

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

MIN_TABLE_QUALITY = 0.5

_UNIT_HINT_RE = re.compile(
    r"(?:₹|rs\.?|inr|usd|\$)\s*(?:in\s+)?"
    r"(crores?|cr|lakh?s?|lacs?|millions?|mn|thousands?|billions?|bn)",
    re.IGNORECASE,
)

_EMPTY_SUMMARY = {
    "line_items_stored": 0, "pages_used": 0, "skipped_low_quality_tables": 0,
    "skipped_ambiguous_multi_period_rows": 0, "skipped_unparsed_rows": 0,
    "cleanup_errors": [], "raw_table_rows": 0, "cleaned_rows": 0,
}


def _detect_page_unit(pdf_path: str, page: int) -> str | None:
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = pdf.pages[page - 1].extract_text() or ""
    except Exception:
        return None
    match = _UNIT_HINT_RE.search(text)
    return match.group(0).strip() if match else None


def _table_debug_preview(rows: Any, limit: int = 3) -> list[Any]:
    if not isinstance(rows, list):
        return [str(type(rows).__name__)]
    preview: list[Any] = []
    for row in rows[:limit]:
        if isinstance(row, (list, tuple)):
            preview.append([str(cell) for cell in row[:8]])
        else:
            preview.append(str(row))
    return preview


def ingest_pages_into_store(conn, document_id: int, entity: str, period: str, statement: str,
                            pages: list[dict], *, model: str = "mistral-small3.2:24b",
                            consolidated: bool | None = None,
                            source_type: str = "MANUAL_UPLOAD",
                            unit_resolver: Callable[[int], str | None] | None = None) -> dict[str, Any]:
    requested_period = canonicalize_period(period)
    summary = dict(_EMPTY_SUMMARY)
    summary["cleanup_errors"] = []

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
        rows = table.get("rows") or []
        summary["raw_table_rows"] += len(rows) if isinstance(rows, list) else 0
        try:
            cleaned = cleanup_table(rows, model=model)
        except Exception as exc:
            summary["cleanup_errors"].append({"page": page, "error": str(exc)})
            continue
        summary["cleaned_rows"] += len(cleaned)
        if not cleaned:
            summary["cleanup_errors"].append({
                "page": page, "error": "table cleanup returned 0 rows",
                "raw_row_count": len(rows) if isinstance(rows, list) else None,
                "table_method": table.get("method"),
                "table_quality": table.get("quality_score"),
                "table_preview": _table_debug_preview(rows),
            })
            continue

        for row in cleaned:
            if row.get("ambiguous_multi_period"):
                summary["skipped_ambiguous_multi_period_rows"] += 1
                continue
            metric = canonicalize_metric(row.get("metric_raw", ""))
            if not metric or row.get("value") is None:
                summary["skipped_unparsed_rows"] += 1
                continue
            row_period = canonicalize_period(row["period_raw"]) if row.get("period_raw") else requested_period
            add_line_item(conn, document_id, LineItem(
                entity=entity, period=row_period, statement=statement,
                metric=metric, metric_raw=row["metric_raw"], value=row["value"],
                unit=page_unit or "unspecified", consolidated=consolidated,
                source_page=page, source_table=table.get("table_caption"),
                extraction_method=table.get("method", "unknown"),
                extraction_confidence=table.get("quality_score", 0.0),
                source_type=source_type,
            ))
            summary["line_items_stored"] += 1
    return summary


def auto_ingest(pdf_path: str, entity: str, doc_type: str, fiscal_year: str,
                period: str, db_path: str, *, top_k: int = 3,
                model: str = "mistral-small3.2:24b", consolidated: bool | None = None,
                source_type: str = "MANUAL_UPLOAD") -> dict[str, Any]:
    discovered = extract_financial_statements(pdf_path, top_k=top_k)
    conn = init_db(db_path)
    document_id = add_document(conn, entity, doc_type, fiscal_year, pdf_path, source_type=source_type)
    requested_period = canonicalize_period(period)
    totals = dict(_EMPTY_SUMMARY)
    totals["cleanup_errors"] = []
    per_statement: dict[str, int] = {}

    for statement, pages in discovered["statements"].items():
        summary = ingest_pages_into_store(
            conn, document_id, entity, requested_period, statement, pages,
            model=model, consolidated=consolidated, source_type=source_type,
            unit_resolver=lambda page: _detect_page_unit(pdf_path, page),
        )
        for key in ("line_items_stored", "pages_used", "skipped_low_quality_tables",
                    "skipped_ambiguous_multi_period_rows", "skipped_unparsed_rows",
                    "raw_table_rows", "cleaned_rows"):
            totals[key] += summary[key]
        totals["cleanup_errors"].extend(summary["cleanup_errors"])
        per_statement[statement] = summary["line_items_stored"]

    conn.close()
    return {
        "pdf": pdf_path, "entity": entity, "period": requested_period, "document_id": document_id,
        "source_type": source_type, **totals, "by_statement": per_statement,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Discover, extract and ingest a filing in one step")
    p.add_argument("pdf_path")
    p.add_argument("--entity", required=True)
    p.add_argument("--doc-type", required=True,
                   choices=["10K", "annual_report", "sebi_quarterly", "sebi_annual", "investor_deck"])
    p.add_argument("--fiscal-year", required=True)
    p.add_argument("--period", required=True)
    p.add_argument("--db", default="data/financials.db")
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--model", default="mistral-small3.2:24b")
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
    if summary["cleanup_errors"]:
        print(f"Cleanup errors: {summary['cleanup_errors']}")
    print(summary["by_statement"])
