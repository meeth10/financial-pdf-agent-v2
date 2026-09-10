"""BSE financial-results retrieval adapter.

BSE exposes a public financial-results page keyed by scrip code. The adapter
resolves a user-supplied company/symbol to a scrip code using BSE's public
smart-search endpoint, then inspects the official results page for matching
financial-result PDF attachments. If BSE changes its page structure or blocks
access, the adapter raises a source-specific error and the retrieval layer can
fall back to NSE or the existing local store.
"""

from __future__ import annotations

from datetime import date, timedelta
import html
import re
from typing import Any
from urllib.parse import urljoin

import requests

from shared.config import MCP_RETRIEVAL_CACHE
from src.agent.periods import canonicalize_period

BSE_HOME = "https://www.bseindia.com/"
BSE_LOOKUP = "https://api.bseindia.com/BseIndiaAPI/api/PeerSmartSearch/w"
BSE_RESULTS = "https://www.bseindia.com/corporates/Comp_Results.aspx"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


class BSESourceError(RuntimeError):
    """BSE source is unavailable or returned an unusable response."""


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": BSE_HOME,
        "Origin": BSE_HOME.rstrip("/"),
        "Connection": "keep-alive",
    })
    return session


def _lookup_scrip(session: requests.Session, company: str) -> tuple[str, str] | None:
    response = session.get(BSE_LOOKUP, params={"Type": "SS", "text": company}, timeout=15)
    response.raise_for_status()
    body = html.unescape(response.text.replace("&nbsp;", " "))
    target = company.strip().upper()

    # BSE's public smart-search response renders symbol/ISIN/scrip-code rows.
    patterns = [
        rf"<\w+>({re.escape(target)})</\w+>\s+[^<]+?\s+(\d{{6}})",
        r"<\w+>([A-Z0-9.&-]+)</\w+>\s+[^<]+?\s+(\d{6})",
        r"\b([A-Z0-9.&-]+)\b\s+\b(\d{6})\b",
    ]
    candidates: list[tuple[int, str, str]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, body, re.I):
            symbol, code = match.group(1).upper(), match.group(2)
            score = 100 if symbol == target else 10
            candidates.append((score, symbol, code))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], x[1], x[2]))
    _, symbol, code = candidates[0]
    return symbol, code


def _period_from_text(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", text)
    m = re.search(r"(?:year|period)\s*(?:ended|ending)\s+(?:on\s+)?(?:March|Mar)\s+31,?\s+(20\d{2})", normalized, re.I)
    if m:
        return f"FY{m.group(1)}"
    m = re.search(r"(?:quarter|half[- ]year|nine[- ]months?)\s*(?:ended|ending)\s+(?:on\s+)?(?:June|Jun|September|Sep|December|Dec)\s+\d{1,2},?\s+(20\d{2})", normalized, re.I)
    if m:
        text_lower = m.group(0).lower()
        year = int(m.group(1))
        if "june" in text_lower or "jun" in text_lower:
            return f"Q1FY{year + 1}"
        if "september" in text_lower or "sep" in text_lower:
            return f"Q2FY{year + 1}"
        return f"Q3FY{year + 1}"
    return None


def _result_rows(page_html: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_row in re.findall(r"<tr\b.*?</tr>", page_html, flags=re.I | re.S):
        clean = re.sub(r"<[^>]+>", " ", html.unescape(raw_row))
        clean = re.sub(r"\s+", " ", clean).strip()
        hrefs = re.findall(r"href\s*=\s*[\"']([^\"']+)[\"']", raw_row, flags=re.I)
        pdfs = [urljoin(BSE_HOME, h) for h in hrefs if re.search(r"\.pdf(?:$|\?)", h, re.I)]
        if not pdfs:
            # Some BSE result pages use JavaScript/onclick links; retain any
            # direct AttachLive/AttachHis URL that appears in the row.
            urls = re.findall(r"(?:https?:)?//[^\"'\s]+/xml-data/corpfiling/(?:AttachLive|AttachHis)/[^\"'\s]+", raw_row, flags=re.I)
            pdfs = [u if u.startswith("http") else "https:" + u for u in urls]
        if pdfs:
            rows.append({"text": clean, "pdfs": pdfs})
    return rows


def search_financial_results(company: str, period: str | None = None,
                             consolidated: bool = True) -> list[dict[str, Any]]:
    session = _session()
    try:
        session.get(BSE_HOME, timeout=15).raise_for_status()
        lookup = _lookup_scrip(session, company)
        if not lookup:
            return []
        symbol, scrip_code = lookup
        page = session.get(BSE_RESULTS, params={"Code": scrip_code}, timeout=20)
        page.raise_for_status()
        wanted = canonicalize_period(period) if period else None
        out: list[dict[str, Any]] = []
        for row in _result_rows(page.text):
            text = row["text"]
            low = text.lower()
            if "year" not in low and "quarter" not in low and "financial" not in low:
                continue
            if consolidated and "consolidated" not in low:
                continue
            inferred = _period_from_text(text)
            if wanted and inferred and inferred != wanted:
                continue
            if wanted and inferred is None:
                continue
            for pdf_url in row["pdfs"][:2]:
                out.append({
                    "company": company,
                    "symbol": symbol,
                    "scrip_code": scrip_code,
                    "period": inferred,
                    "subject": "BSE financial results",
                    "url": pdf_url,
                    "source_type": "BSE_AUTO_RETRIEVED",
                    "exchange": "BSE",
                    "consolidated": consolidated,
                })
        # The page is ordered newest-first in normal BSE usage.
        return out[:10]
    except requests.RequestException as exc:
        raise BSESourceError(f"BSE request failed: {exc}") from exc
    finally:
        session.close()


def cache_path_for(url: str) -> str:
    from pathlib import Path
    import hashlib

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    name = Path(url.split("?", 1)[0]).name or "financial_result.pdf"
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return str(Path(MCP_RETRIEVAL_CACHE) / "india" / "bse" / f"{digest}_{safe}")
