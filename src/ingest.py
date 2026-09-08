"""Ingest one PDF into the structured financial store."""

import argparse
import sys

from src.extraction.pdf_router import extract_page_tables, page_needs_ocr
from src.extraction.llm_cleanup import cleanup_table
from src.store.schema import init_db
from src.store.db import add_document, add_line_item, LineItem
from src.agent.derivation import canonicalize_metric
from src.agent.periods import canonicalize_period


def normalize_metric(raw_label: str) -> str | None:
    """Delegates to the SAME canonical dictionary the query-time agent
    resolves against (src/agent/rules.yaml canonical_terms, via
    derivation.canonicalize_metric). This used to be a separate,
    ~15-term dictionary here — two sources of truth for the same
    mapping will drift, and a drift here means a row silently never
    matches what the agent looks up (Rule 1 depends on the two staying
    identical). Falls back to the same slugify behavior canonicalize_metric
    already applies for unrecognized labels."""
    cleaned = raw_label.strip()
    if not cleaned:
        return None
    return canonicalize_metric(cleaned)


def ingest(pdf_path: str, entity: str, doc_type: str, fiscal_year: str,
           period: str, statement: str, pages: list[int], db_path: str,
           model: str = "hermes3:8b", skip_llm_cleanup: bool = False) -> None:
    conn = init_db(db_path)
    document_id = add_document(conn, entity, doc_type, fiscal_year, pdf_path)
    stored = 0
    skipped = 0
    requested_period = canonicalize_period(period)

    for page in pages:
        if page_needs_ocr(pdf_path, page):
            print(f"[page {page}] looks scanned — skipping, wire up OCR fallback first", file=sys.stderr)
            continue

        tables = extract_page_tables(pdf_path, page)
        if not tables:
            print(f"[page {page}] no table found by any extractor", file=sys.stderr)
            continue

        for table in tables:
            try:
                if skip_llm_cleanup:
                    cleaned = cleanup_table(table.rows, model=model)
                else:
                    cleaned = cleanup_table(table.rows, model=model)
            except Exception as e:
                print(f"[page {page}] cleanup failed: {e}", file=sys.stderr)
                continue

            for row in cleaned:
                if row.get("ambiguous_multi_period"):
                    print(f"[page {page}] skipping '{row.get('metric_raw')}': more than one "
                          f"numeric value and no year header to resolve them against", file=sys.stderr)
                    skipped += 1
                    continue
                metric = normalize_metric(row.get("metric_raw", ""))
                if metric is None or row.get("value") is None:
                    skipped += 1
                    continue

                # A detected year header (see extraction/llm_cleanup.py) gives a
                # more precise, per-column period than the single --period flag
                # can — use it when present, so one page with 2-3 statement
                # years ingests all of them correctly in one pass instead of
                # requiring one ingest() call per column.
                if row.get("period_raw"):
                    row_period = canonicalize_period(row["period_raw"])
                    if row_period != requested_period:
                        print(f"[page {page}] '{row.get('metric_raw')}': column period "
                              f"{row_period!r} (detected) differs from --period "
                              f"{requested_period!r} (requested) — storing under {row_period!r}",
                              file=sys.stderr)
                else:
                    row_period = requested_period

                add_line_item(conn, document_id, LineItem(
                    entity=entity, period=row_period, statement=statement,
                    metric=metric, metric_raw=row["metric_raw"], value=row["value"],
                    unit=row.get("unit") or "unspecified", consolidated=None,
                    source_page=page, source_table=table.table_caption,
                    extraction_method=table.method,
                    extraction_confidence=table.confidence,
                ))
                stored += 1

    print(f"Ingested {pdf_path} for {entity} / {requested_period} into {db_path}")
    print(f"Stored line items: {stored}; skipped/unparsed: {skipped}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("pdf_path")
    p.add_argument("--entity", required=True)
    p.add_argument("--doc-type", required=True, choices=["10K", "annual_report", "sebi_quarterly", "sebi_annual", "investor_deck"])
    p.add_argument("--fiscal-year", required=True)
    p.add_argument("--period", required=True)
    p.add_argument("--statement", required=True, choices=["balance_sheet", "income_statement", "cash_flow"])
    p.add_argument("--pages", required=True)
    p.add_argument("--db", default="data/financials.db")
    p.add_argument("--model", default="hermes3:8b")
    p.add_argument("--skip-llm-cleanup", action="store_true")
    args = p.parse_args()

    ingest(args.pdf_path, args.entity, args.doc_type, args.fiscal_year,
           args.period, args.statement, [int(x) for x in args.pages.split(",")],
           args.db, args.model, args.skip_llm_cleanup)
