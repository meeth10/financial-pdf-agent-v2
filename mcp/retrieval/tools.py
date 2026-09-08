"""MCP-facing retrieval tools."""

from __future__ import annotations

from typing import Any

from .sources import find_relevant_pages as _find_relevant_pages
from .sources import fetch_document as _fetch_document
from .sources import search_filings as _search_filings


def search_filings(company: str, document_type: str = "10-K", period: str | None = None) -> dict[str, Any]:
    try:
        filings = _search_filings(company, document_type, period)
        return {"status": "REPORTED" if filings else "UNAVAILABLE", "company": company, "filings": filings}
    except Exception as exc:
        return {"status": "SOURCE_ERROR", "company": company, "error": str(exc)}


def fetch_document(url: str) -> dict[str, Any]:
    try:
        result = _fetch_document(url)
        # Keep the full text in-process for page discovery, but return only a bounded
        # preview to MCP callers so a large filing does not flood the model context.
        result["text_preview"] = result.pop("text", "")[:4000]
        return result
    except Exception as exc:
        return {"status": "SOURCE_ERROR", "url": url, "error": str(exc)}


def find_relevant_pages(url: str, query: str, max_pages: int = 5) -> dict[str, Any]:
    try:
        document = _fetch_document(url)
        pages = _find_relevant_pages(document, query, max_pages)
        return {"status": "REPORTED" if pages else "UNAVAILABLE", "url": url, "query": query, "pages": pages}
    except Exception as exc:
        return {"status": "SOURCE_ERROR", "url": url, "error": str(exc)}
