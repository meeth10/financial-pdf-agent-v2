"""NSE financial-results retrieval adapter.

This adapter uses the public NSE corporate-announcements/search endpoints,
bootstrapping a browser-like session first because NSE commonly requires
session cookies and browser headers before API access. It only returns
announcement metadata and direct attachment URLs; PDF contents are handled
by the existing extraction pipeline.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import html
import re
from typing import Any
from urllib.parse import urlencode

import requests

from shared.config import MCP_RETRIEVAL_CACHE
from src.agent.periods import canonicalize_period

NSE_HOME = "https://www.nseindia.com/"
NSE_ANNOUNCEMENTS = "https://www.nseindia.com/api/corporate-announcements"
NSE_AUTOCOMPLETE = "https://www.nseindia.com/api/search/autocomplete"
NSE_ARCHIVE_HOST = "https://nsearchives.nseindia.com"
CACHE_SUBDIR = "india/nse"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


class NSESourceError(RuntimeError):
    """NSE source is unavailable or returned an unusable response."""


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": NSE_HOME,
        "Origin": "https://www.nseindia.com",
        "Connection": "keep-alive",
    })
    return session


def _resolve_symbol(session: requests.Session, company: str) -> dict[str, Any] | None:
    target = company.strip().lower()
    response = session.get(NSE_AUTOCOMPLETE, params={"q": company}, timeout=15)
    response.raise_for_status()
    payload = response.json() or {}
    symbols = payload.get("symbols") or []
    if not symbols:
        return None
    ranked: list[tuple[int, dict[str, Any]]] = []
    for item in symbols:
        symbol = str(item.get("symbol") or "").strip()
        info = str(item.get("symbol_info") or "").strip()
        low_symbol = symbol.lower()
        low_info = info.lower()
        score = 0
        if target == low_symbol:
            score += 100
        if target == low_info:
            score += 90
        if target in low_info:
            score += 60
        if target in low_symbol:
            score += 40
        if str(item.get("result_type")) == "symbol":
            score += 5
        if str(item.get("result_sub_type")) == "equity":
            score += 5
        if symbol:
            ranked.append((score, item))
    ranked.sort(key=lambda x: (-x[0], str(x[1].get("symbol", ""))))
    return ranked[0][1] if ranked else None


def _period_from_text(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", text, flags=re.IGNORECASE)
    m = re.search(r"(?:year|period)\s+ended\s+(?:on\s+)?(?:March|Mar)\s+31,?\s+(20\d{2})", normalized, re.I)
    if m:
        return f"FY{m.group(1)}"
    m = re.search(r"(?:quarter|half[- ]year|nine[- ]months?)\s+ended\s+(?:on\s+)?(?:June|Jun|September|Sep|December|Dec)\s+\d{1,2},?\s+(20\d{2})", normalized, re.I)
    if not m:
        m = re.search(r"(?:quarter|half[- ]year|nine[- ]months?)\s+ended\s+(?:on\s+)?(?:March|Mar)\s+31,?\s+(20\d{2})", normalized, re.I)
    if m:
        match_text = m.group(0).lower()
        year = int(m.group(1))
        if any(x in match_text for x in ("june", "jun")):
            return f"Q1FY{year + 1}"
        if any(x in match_text for x in ("september", "sep")):
            return f"Q2FY{year + 1}"
        if any(x in match_text for x in ("december", "dec")):
            return f"Q3FY{year + 1}"
        return f"FY{year}"
    return None


def _infer_period(record: dict[str, Any]) -> str | None:
    text = " ".join(
        str(record.get(key) or "")
        for key in ("desc", "subject", "attchmntText", "details")
    )
    period = _period_from_text(text)
    if period:
        return canonicalize_period(period)
    return None


def _date_window() -> tuple[str, str]:
    today = date.today()
    start = today - timedelta(days=800)
    return start.strftime("%d-%m-%Y"), today.strftime("%d-%m-%Y")


def search_financial_results(company: str, period: str | None = None) -> list[dict[str, Any]]:
    session = _session()
    try:
        session.get(NSE_HOME, timeout=15).raise_for_status()
        symbol_row = _resolve_symbol(session, company)
        if not symbol_row:
            return []
        symbol = str(symbol_row.get("symbol") or "").strip()
        if not symbol:
            return []
        from_date, to_date = _date_window()
        params = {
            "index": "equities",
            "symbol": symbol,
            "from_date": from_date,
            "to_date": to_date,
        }
        response = session.get(NSE_ANNOUNCEMENTS, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
        records = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            return []

        wanted = canonicalize_period(period) if period else None
        out: list[dict[str, Any]] = []
        for record in records:
            desc = str(record.get("desc") or record.get("subject") or "").strip()
            attached = str(record.get("attchmntFile") or record.get("attachment") or "").strip()
            text = " ".join(str(record.get(k) or "") for k in ("desc", "attchmntText", "subject"))
            lower = text.lower()
            if not attached:
                continue
            if "financial result" not in lower and "financials" not in lower:
                continue
            inferred = _infer_period(record)
            if wanted and inferred and inferred != wanted:
                continue
            if wanted and inferred is None:
                continue
            if not attached.lower().startswith("http"):
                attached = NSE_ARCHIVE_HOST + (attached if attached.startswith("/") else "/" + attached)
            out.append({
                "company": str(record.get("sm_name") or symbol_row.get("symbol_info") or company),
                "symbol": symbol,
                "period": inferred,
                "filing_date": record.get("an_dt") or record.get("dt"),
                "subject": desc,
                "url": attached,
                "source_type": "NSE_AUTO_RETRIEVED",
                "exchange": "NSE",
            })
        out.sort(key=lambda x: str(x.get("filing_date") or ""), reverse=True)
        return out[:10]
    except requests.RequestException as exc:
        raise NSESourceError(f"NSE request failed: {exc}") from exc
    except ValueError as exc:
        raise NSESourceError(f"NSE returned an invalid response: {exc}") from exc
    finally:
        session.close()


def cache_path_for(url: str) -> str:
    from pathlib import Path
    import hashlib

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    name = Path(url.split("?", 1)[0]).name or "financial_result.pdf"
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return str(Path(MCP_RETRIEVAL_CACHE) / CACHE_SUBDIR / f"{digest}_{safe}")
