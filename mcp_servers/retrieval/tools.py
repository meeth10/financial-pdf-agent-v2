"""MCP-facing retrieval tools for SEC and Indian exchange filings."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time
from typing import Any

import requests

from src.agent.periods import canonicalize_period
from src.auto_ingest import auto_ingest
from src.store.schema import init_db
from .xbrl import ingest_xbrl

_LAST_NETWORK_CALL = 0.0
_MIN_NETWORK_INTERVAL = 0.75


def search_filings(company: str, document_type: str = "10-K", period: str | None = None) -> dict[str, Any]:
    try:
        from .sources import search_filings as _search_filings
        filings = _search_filings(company, document_type, period)
        return {"status": "REPORTED" if filings else "UNAVAILABLE", "company": company, "filings": filings}
    except Exception as exc:
        return {"status": "SOURCE_ERROR", "company": company, "error": str(exc)}


def fetch_document(url: str) -> dict[str, Any]:
    try:
        from .sources import fetch_document as _fetch_document
        result = _fetch_document(url)
        result["text_preview"] = result.pop("text", "")[:4000]
        return result
    except Exception as exc:
        return {"status": "SOURCE_ERROR", "url": url, "error": str(exc)}


def find_relevant_pages(url: str, query: str, max_pages: int = 5) -> dict[str, Any]:
    try:
        from .sources import find_relevant_pages as _find_relevant_pages
        from .sources import fetch_document as _fetch_document
        document = _fetch_document(url)
        pages = _find_relevant_pages(document, query, max_pages)
        return {"status": "REPORTED" if pages else "UNAVAILABLE", "url": url, "query": query, "pages": pages}
    except Exception as exc:
        return {"status": "SOURCE_ERROR", "url": url, "error": str(exc)}


def _db_path() -> str:
    return os.getenv("FINANCIAL_DB_PATH", "data/financials.db")


def _db_has_period(entity: str, period: str, consolidated: bool) -> bool:
    conn = init_db(_db_path())
    try:
        row = conn.execute(
            "SELECT 1 FROM line_items WHERE entity = ? AND period = ? AND consolidated = ? LIMIT 1",
            (entity, period, int(consolidated)),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _cache_path(url: str, exchange: str, format_name: str = "PDF") -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    is_structured = format_name in {"IXBRL", "XBRL"}
    source_name = Path(url.split("?", 1)[0]).name or ("financial_result.html" if is_structured else "financial_result.pdf")
    if is_structured and not source_name.lower().endswith((".html", ".htm", ".xhtml", ".xml")):
        source_name += ".html"
    safe = "".join(c if c.isalnum() or c in ".-_" else "_" for c in source_name)
    return Path(os.getenv("MCP_RETRIEVAL_CACHE", "data/retrieval_cache")) / "india" / exchange.lower() / f"{digest}_{safe}"


def _throttle() -> None:
    global _LAST_NETWORK_CALL
    now = time.monotonic()
    wait = _MIN_NETWORK_INTERVAL - (now - _LAST_NETWORK_CALL)
    if wait > 0:
        time.sleep(wait)
    _LAST_NETWORK_CALL = time.monotonic()


def _download_attachment(url: str, exchange: str, format_name: str = "PDF") -> Path:
    _throttle()
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml,application/xml,application/pdf,*/*"},
        timeout=45,
        allow_redirects=True,
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").lower()
    if format_name == "PDF":
        if not response.content.startswith(b"%PDF") and "pdf" not in content_type:
            raise RuntimeError(f"{exchange} attachment was not a PDF")
    elif "html" not in content_type and "xml" not in content_type and b"<html" not in response.content[:1024].lower():
        raise RuntimeError(f"{exchange} XBRL attachment was not HTML/XML")
    path = _cache_path(url, exchange, format_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size != len(response.content):
        path.write_bytes(response.content)
    return path


def _download_pdf(url: str, exchange: str) -> Path:
    return _download_attachment(url, exchange, "PDF")


def _search_india(company: str, period: str | None, exchange: str, consolidated: bool) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    candidates: list[dict[str, Any]] = []
    exchanges = [exchange] if exchange in {"NSE", "BSE"} else ["NSE", "BSE"]
    from .sources_bse import BSESourceError, search_financial_results as _search_bse
    from .sources_nse import NSESourceError, search_financial_results as _search_nse
    for item_exchange in exchanges:
        try:
            _throttle()
            found = _search_nse(company, period) if item_exchange == "NSE" else _search_bse(company, period, consolidated=consolidated)
            candidates.extend(found)
        except (NSESourceError, BSESourceError, requests.RequestException) as exc:
            errors.append(f"{item_exchange}: {exc}")
        except Exception as exc:
            errors.append(f"{item_exchange}: {exc}")

    def rank(item: dict[str, Any]) -> tuple[int, int, int, str]:
        scope = item.get("scope_asserted")
        format_name = str(item.get("format") or "PDF").upper()
        scope_score = 2 if scope is consolidated else 1 if scope is None else 0
        structured_score = 2 if format_name in {"IXBRL", "XBRL"} else 1
        same_period_score = 0
        if period and canonicalize_period(str(item.get("period") or "")) == canonicalize_period(period):
            same_period_score = 1
        return (scope_score, same_period_score, structured_score, str(item.get("filing_date") or ""))

    candidates.sort(key=rank, reverse=True)
    return candidates, errors


def _select_candidate(candidates: list[dict[str, Any]], consolidated: bool, period: str | None = None) -> dict[str, Any] | None:
    """Prefer exact scope, exact period, then structured XBRL/iXBRL, then newest filing."""
    scoped = [c for c in candidates if c.get("scope_asserted") is consolidated]
    if not scoped:
        return None
    if period:
        exact = [c for c in scoped if canonicalize_period(str(c.get("period") or "")) == canonicalize_period(period)]
        if exact:
            scoped = exact
    structured = [c for c in scoped if str(c.get("format") or "PDF").upper() in {"IXBRL", "XBRL"}]
    pool = structured or scoped
    return sorted(pool, key=lambda c: str(c.get("filing_date") or ""), reverse=True)[0]


def get_or_fetch_financials(entity: str, period: str | None = None,
                            consolidated: bool = True,
                            exchange: str = "BOTH") -> dict[str, Any]:
    """Store-first Indian financial retrieval; prefer XBRL/iXBRL when available."""
    canonical_period = canonicalize_period(period) if period else ""
    if canonical_period and _db_has_period(entity, canonical_period, consolidated):
        return {"status": "STORE_HIT", "entity": entity, "period": canonical_period,
                "consolidated": consolidated, "source_type": "LOCAL_STORE"}

    candidates, errors = _search_india(entity, canonical_period or None, exchange.upper(), consolidated)
    if not candidates:
        return {"status": "SOURCE_UNAVAILABLE", "entity": entity,
                "period": canonical_period or None, "consolidated": consolidated,
                "reason": "No matching NSE/BSE financial-results filing was found.",
                "source_errors": errors}

    selected = _select_candidate(candidates, consolidated, canonical_period or None)
    if selected is None:
        return {"status": "SOURCE_UNAVAILABLE", "entity": entity,
                "period": canonical_period or None, "consolidated": consolidated,
                "reason": "Matching exchange filings were found, but none explicitly asserted the requested consolidation scope.",
                "candidates": candidates[:5], "source_errors": errors}

    selected_period = canonicalize_period(selected.get("period") or canonical_period)
    if not selected_period:
        return {"status": "UNAVAILABLE", "entity": entity,
                "reason": "The exchange returned a financial-results filing but its reporting period could not be determined.",
                "candidates": candidates[:5], "source_errors": errors}

    try:
        format_name = str(selected.get("format") or "PDF").upper()
        source_type = f"{selected.get('exchange', 'INDIA')}_AUTO_RETRIEVED"
        content_path = _download_attachment(str(selected["url"]), str(selected.get("exchange") or "INDIA"), format_name)
        if format_name in {"IXBRL", "XBRL"}:
            summary = ingest_xbrl(content_path, entity=entity, fiscal_year=selected_period,
                                  consolidated=consolidated, db_path=_db_path(), source_type=source_type)
        else:
            doc_type = "sebi_annual" if selected_period.startswith("FY") else "sebi_quarterly"
            summary = auto_ingest(str(content_path), entity, doc_type, selected_period, selected_period,
                                  _db_path(), top_k=5, consolidated=consolidated, source_type=source_type)
        stored = int(summary.get("line_items_stored", 0))
        if stored == 0:
            return {"status": "SOURCE_UNAVAILABLE", "entity": entity, "period": selected_period,
                    "consolidated": consolidated,
                    "reason": "The filing was fetched, but the ingestion path stored no usable line items.",
                    "source": selected, "ingestion": summary}
        return {"status": "FETCHED_AND_INGESTED", "entity": entity, "period": selected_period,
                "consolidated": consolidated, "source_type": source_type, "source_format": format_name,
                "source": selected, "content_path": str(content_path), "ingestion": summary}
    except Exception as exc:
        return {"status": "SOURCE_UNAVAILABLE", "entity": entity, "period": selected_period,
                "consolidated": consolidated,
                "reason": f"Could not fetch or ingest the selected filing: {exc}",
                "source": selected, "source_errors": errors}
