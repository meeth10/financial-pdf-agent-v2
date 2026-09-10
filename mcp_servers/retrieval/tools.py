"""MCP-facing retrieval tools for SEC and Indian exchange filings."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any

import requests

from src.agent.derivation import canonicalize_metric
from src.agent.periods import canonicalize_period
from src.auto_ingest import auto_ingest
from src.store.schema import init_db

from .sources import find_relevant_pages as _find_relevant_pages
from .sources import fetch_document as _fetch_document
from .sources import search_filings as _search_filings
from .sources_bse import BSESourceError, search_financial_results as _search_bse
from .sources_nse import NSESourceError, cache_path_for as _nse_cache_path_for, search_financial_results as _search_nse

INDIA_CACHE = Path("data/retrieval_cache/india")
INDIA_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/151 Safari/537.36"
_LAST_NETWORK_CALL = 0.0
_MIN_NETWORK_INTERVAL = 0.75


def search_filings(company: str, document_type: str = "10-K", period: str | None = None) -> dict[str, Any]:
    try:
        filings = _search_filings(company, document_type, period)
        return {"status": "REPORTED" if filings else "UNAVAILABLE", "company": company, "filings": filings}
    except Exception as exc:
        return {"status": "SOURCE_ERROR", "company": company, "error": str(exc)}


def fetch_document(url: str) -> dict[str, Any]:
    try:
        result = _fetch_document(url)
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


def _db_has_period(entity: str, period: str, consolidated: bool = True) -> bool:
    conn = init_db("data/financials.db")
    try:
        query = "SELECT 1 FROM line_items WHERE entity = ? AND period = ? AND consolidated = ? LIMIT 1"
        return conn.execute(query, (entity, period, int(consolidated))).fetchone() is not None
    finally:
        conn.close()


def _cache_path(url: str, exchange: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    name = Path(url.split("?", 1)[0]).name or "financial_result.pdf"
    safe = "".join(c if c.isalnum() or c in ".-_" else "_" for c in name)
    return INDIA_CACHE / exchange.lower() / f"{digest}_{safe}"


def _download_pdf(url: str, exchange: str) -> Path:
    global _LAST_NETWORK_CALL
    import time
    now = time.monotonic()
    wait = _MIN_NETWORK_INTERVAL - (now - _LAST_NETWORK_CALL)
    if wait > 0:
        time.sleep(wait)
    _LAST_NETWORK_CALL = time.monotonic()

    parsed = requests.Session()
    parsed.headers.update({"User-Agent": INDIA_USER_AGENT, "Accept": "application/pdf,*/*"})
    try:
        response = parsed.get(url, timeout=45, allow_redirects=True)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if not response.content.startswith(b"%PDF") and "pdf" not in content_type:
            raise RuntimeError(f"{exchange} attachment was not a PDF")
        path = _cache_path(url, exchange)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        return path
    finally:
        parsed.close()


def _candidate_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    date_text = str(item.get("filing_date") or "")
    return (1 if item.get("period") else 0, date_text)


def _search_india(company: str, period: str | None, exchange: str) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    candidates: list[dict[str, Any]] = []
    exchanges = [exchange] if exchange in {"NSE", "BSE"} else ["NSE", "BSE"]
    for item_exchange in exchanges:
        try:
            found = _search_nse(company, period) if item_exchange == "NSE" else _search_bse(company, period, consolidated=True)
            candidates.extend(found)
        except (NSESourceError, BSESourceError, requests.RequestException) as exc:
            errors.append(f"{item_exchange}: {exc}")
        except Exception as exc:
            errors.append(f"{item_exchange}: {exc}")
    candidates.sort(key=_candidate_sort_key, reverse=True)
    return candidates, errors


def get_or_fetch_financials(entity: str, period: str | None = None,
                            consolidated: bool = True,
                            exchange: str = "BOTH") -> dict[str, Any]:
    """Return a store hit or fetch + ingest an Indian exchange filing.

    Network retrieval is automatic on a store miss. Retrieved PDFs are cached
    and sent through the existing auto_extract/auto_ingest pipeline. Failures
    degrade gracefully to a structured SOURCE_UNAVAILABLE response.
    """
    canonical_period = canonicalize_period(period) if period else ""

    if canonical_period and _db_has_period(entity, canonical_period, consolidated):
        return {
            "status": "STORE_HIT", "entity": entity, "period": canonical_period,
            "consolidated": consolidated, "source_type": "LOCAL_STORE",
        }

    candidates, errors = _search_india(entity, canonical_period or None, exchange.upper())
    if not candidates:
        return {
            "status": "SOURCE_UNAVAILABLE", "entity": entity,
            "period": canonical_period or None, "consolidated": consolidated,
            "reason": "No matching NSE/BSE financial-results filing was found.",
            "source_errors": errors,
        }

    selected = candidates[0]
    selected_period = canonicalize_period(selected.get("period") or canonical_period)
    if not selected_period:
        return {
            "status": "UNAVAILABLE", "entity": entity,
            "reason": "Source returned a result filing but its reporting period could not be determined.",
            "candidates": candidates[:5], "source_errors": errors,
        }

    url = str(selected.get("url") or "")
    exchange_name = str(selected.get("exchange") or "INDIA")
    try:
        pdf_path = _download_pdf(url, exchange_name)
        is_annual = selected_period.startswith("FY") and not selected_period.startswith("FY20") or selected_period.startswith("FY")
        doc_type = "sebi_annual" if selected_period.startswith("FY") else "sebi_quarterly"
        fiscal_year = selected_period if selected_period.startswith("FY") else selected_period[-4:]
        summary = auto_ingest(
            str(pdf_path), entity, doc_type, fiscal_year, selected_period,
            "data/financials.db", top_k=5, consolidated=consolidated,
            source_type=f"{exchange_name}_AUTO_RETRIEVED",
        )
        if summary.get("line_items_stored", 0) == 0:
            return {
                "status": "SOURCE_UNAVAILABLE", "entity": entity, "period": selected_period,
                "consolidated": consolidated,
                "reason": "Filing was fetched but the existing extractor stored no usable line items.",
                "source": selected, "ingestion": summary,
            }
        return {
            "status": "FETCHED_AND_INGESTED", "entity": entity, "period": selected_period,
            "consolidated": consolidated, "source": selected, "content_path": str(pdf_path),
            "ingestion": summary,
        }
    except Exception as exc:
        return {
            "status": "SOURCE_UNAVAILABLE", "entity": entity, "period": selected_period,
            "consolidated": consolidated,
            "reason": f"Could not fetch or ingest the selected filing: {exc}",
            "source": selected, "source_errors": errors,
        }
