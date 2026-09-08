"""End-to-end automatic extraction for the three core financial statements."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.extraction.pdf_router import extract_page_tables, page_needs_ocr
from src.extraction.statement_discovery import discover_statement_pages

STATEMENTS = ("balance_sheet", "income_statement", "cash_flow")


def extract_financial_statements(pdf_path: str, *, top_k: int = 3) -> dict[str, Any]:
    """Discover statement pages and run the robust table extraction router."""
    discovered = discover_statement_pages(pdf_path, top_k=top_k)
    output: dict[str, Any] = {"pdf": str(Path(pdf_path)), "statements": {}}

    for statement in STATEMENTS:
        pages_out: list[dict[str, Any]] = []
        for candidate in discovered[statement]:
            page = candidate.page
            needs_ocr = page_needs_ocr(pdf_path, page)
            page_result: dict[str, Any] = {
                "page": page,
                "score": candidate.score,
                "matched_terms": list(candidate.matched_terms),
                "text_preview": candidate.text_preview,
                "status": candidate.status,
                "title_match": candidate.title_match,
                "gate2_label_hits": candidate.gate2_label_hits,
                "gate2_structured_rows": candidate.gate2_structured_rows,
                "gate3_numeric_columns": candidate.gate3_numeric_columns,
                "gate3_garbage_ratio": candidate.gate3_garbage_ratio,
                "main_cluster": candidate.main_cluster,
                "outside_cluster_duplicate": candidate.outside_cluster_duplicate,
                "review_flag": candidate.review_flag,
                "needs_ocr": needs_ocr,
                "tables": [],
            }
            if not needs_ocr:
                for table in extract_page_tables(pdf_path, page):
                    page_result["tables"].append(asdict(table))
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
