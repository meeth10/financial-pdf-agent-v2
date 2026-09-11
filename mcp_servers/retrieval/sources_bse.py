"""BSE financial-results retrieval adapter."""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urljoin

import requests

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
    m = re.search(r"(20\d{2})-(20\d{2})\s+(?:Consolidated|Standalone)-([A-Za-z]{3})-(\d{2})\s+(Year|Quarter|Half Year)", normalized, re.I)
    if m:
        month = m.group(3).lower()
        end_year = int(m.group(1))
        month_year = int(m.group(4))
        if month == "mar" and m.group(5).lower() == "year":
            return f"FY{2000 + month_year}"
        fy_end = end_year + 1 if month in {"jun", "sep", "dec"} else end_year
        quarter = {"jun": "Q1", "sep": "Q2", "dec": "Q3", "mar": "Q4"}.get(month)
        if quarter and m.group(5).lower() != "year":
            return f"{quarter}FY{fy_end}"
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
            has_consolidated = bool(re.search(r"\bconsolidated\b", text, re.I))
            has_standalone = bool(re.search(r"\bstandalone\b", text, re.I))
            scope = True if has_consolidated and not has_standalone else False if has_standalone and not has_consolidated else None
            if consolidated and scope is not True:
                continue
            if not consolidated and scope is not False:
                continue
            inferred = _period_from_text(text)
            if wanted and inferred != wanted:
                continue
            for pdf_url in row["pdfs"][:2]:
                out.append({
                    "company": company, "symbol": symbol, "scrip_code": scrip_code,
                    "period": inferred, "subject": "BSE financial results", "url": pdf_url,
                    "source_type": "BSE_AUTO_RETRIEVED", "exchange": "BSE",
                    "consolidated": consolidated, "scope_asserted": scope,
                })
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
    return str(Path("data/retrieval_cache") / "india" / "bse" / f"{digest}_{safe}")
