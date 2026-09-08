"""End-to-end automatic extraction for the three core financial statements."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.extraction.pdf_router import extract_page_tables, page_needs_ocr
from src.extraction.statement_discovery import discover_statement_pages

STATEMENTS = ("balance_sheet", "income_statement", "cash_flow")


def _title_from_matches(statement: str, matched_terms: list[str]) -> str | None:
    """Recover the statement heading from the original high-recall matches."""
    keywords = {
        "balance_sheet": ("balance", "financial position"),
        "income_statement": ("income", "profit", "operations"),
        "cash_flow": ("cash",),
    }[statement]
    for term in matched_terms:
        if any(keyword in term.lower() for keyword in keywords):
            return term
    return matched_terms[0] if matched_terms else None


def extract_financial_statements(pdf_path: str, *, top_k: int = 3) -> dict[str, Any]:
    """Discover statement pages and run the robust table extraction router.

    Page discovery stays high-recall. Validation metadata is attached to the
    candidate and never used to remove a page from extraction.
    """
    discovered = discover_statement_pages(pdf_path, top_k=top_k)
    output: dict[str, Any] = {"pdf": str(Path(pdf_path)), "statements": {}}

    for statement in STATEMENTS:
        pages_out: list[dict[str, Any]] = []
        for candidate in discovered[statement]:
            page = candidate.page
            needs_ocr = page_needs_ocr(pdf_path, page)
            tables: list[dict[str, Any]] = []
            if not needs_ocr:
                tables = [asdict(table) for table in extract_page_tables(pdf_path, page)]

            matched_terms = list(getattr(candidate, "matched_terms", ()) or ())
            title_match = getattr(candidate, "title_match", None) or _title_from_matches(statement, matched_terms)

            existing_status = getattr(candidate, "status", None)
            if existing_status:
                status = existing_status
            elif title_match and tables:
                # Backward-compatible fallback for candidates produced by the
                # original high-recall discovery object.
                populated_rows = sum(
                    1 for table in tables
                    for row in (table.get("rows") or [])
                    if row and any(str(cell or "").strip() for cell in row)
                )
                status = "CONFIRMED" if populated_rows >= 3 else "TITLE_ONLY"
            else:
                status = "TITLE_ONLY" if title_match else "NOT_A_STATEMENT_PAGE"

            page_result: dict[str, Any] = {
                "page": page,
                "score": candidate.score,
                "matched_terms": matched_terms,
                "text_preview": candidate.text_preview,
                "status": status,
                "title_match": title_match,
                "gate2_label_hits": getattr(candidate, "gate2_label_hits", None),
                "gate2_structured_rows": getattr(candidate, "gate2_structured_rows", None),
                "gate3_numeric_columns": getattr(candidate, "gate3_numeric_columns", None),
                "gate3_garbage_ratio": getattr(candidate, "gate3_garbage_ratio", None),
                "main_cluster": getattr(candidate, "main_cluster", True),
                "outside_cluster_duplicate": getattr(candidate, "outside_cluster_duplicate", False),
                "review_flag": getattr(candidate, "review_flag", None),
                "needs_ocr": needs_ocr,
                "tables": tables,
            }
            pages_out.append(page_result)
        output["statements"][statement] = pages_out

    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Automatically find and extract core financial statements")
    parser.add_argument("pdf_path")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    import json
    print(json.dumps(extract_financial_statements(args.pdf_path, top_k=args.top_k), indent=2, ensure_ascii=False))
