"""Minimal SEC EDGAR retrieval adapter.

EDGAR is used here as one high-authority source adapter. The adapter is
intentionally narrow: discover public filings, fetch a filing document, and
split the retrieved document into evidence chunks for downstream selection.
Raw source text is explicitly marked as untrusted data; nothing in it is
interpreted as an instruction to the model.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from shared.config import MCP_RETRIEVAL_CACHE

SEC_BASE = "https://www.sec.gov/"
SEC_API = "https://data.sec.gov/"
USER_AGENT = "financial-pdf-agent/2.0 contact=local"
_ALLOWED_HOSTS = {"www.sec.gov", "sec.gov", "data.sec.gov"}


class _HTMLTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            value = re.sub(r"\s+", " ", data).strip()
            if value:
                self.parts.append(value)


def _safe_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        raise ValueError("Retrieval source is restricted to SEC HTTPS hosts")
    return url


def _get(url: str) -> bytes:
    request = Request(_safe_url(url), headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    with urlopen(request, timeout=30) as response:
        return response.read()


def _cache_dir() -> Path:
    path = Path(MCP_RETRIEVAL_CACHE); path.mkdir(parents=True, exist_ok=True); return path


def _text_from_html(raw: bytes) -> str:
    parser = _HTMLTextParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    return "\n".join(parser.parts)


def _company_tickers() -> list[dict[str, Any]]:
    payload = json.loads(_get(urljoin(SEC_BASE, "files/company_tickers.json")))
    return list(payload.values())


def search_filings(company: str, document_type: str = "10-K", period: str | None = None) -> list[dict[str, Any]]:
    target = company.strip().lower()
    matches = [x for x in _company_tickers() if target in str(x.get("title", "")).lower()]
    if not matches:
        return []
    company_row = matches[0]
    cik = str(company_row["cik_str"]).zfill(10)
    submissions = json.loads(_get(urljoin(SEC_API, f"submissions/CIK{cik}.json")))
    recent = submissions.get("filings", {}).get("recent", {})
    out: list[dict[str, Any]] = []
    forms = recent.get("form", [])
    for i, form in enumerate(forms):
        if document_type and form.upper() != document_type.upper():
            continue
        filing_date = recent.get("filingDate", [None])[i]
        report_date = recent.get("reportDate", [None])[i]
        accession = recent.get("accessionNumber", [None])[i]
        primary = recent.get("primaryDocument", [None])[i]
        if period and str(period).lower() not in f"{filing_date} {report_date}".lower():
            continue
        if not accession or not primary:
            continue
        accession_nodash = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{primary}"
        out.append({
            "company": submissions.get("name", company_row.get("title")),
            "cik": cik,
            "form": form,
            "filing_date": filing_date,
            "report_date": report_date,
            "accession": accession,
            "document": primary,
            "url": url,
            "source_type": "SEC_EDGAR",
        })
        if len(out) >= 10:
            break
    return out


def fetch_document(url: str) -> dict[str, Any]:
    raw = _get(url)
    parsed = urlparse(url)
    name = Path(parsed.path).name or "filing"
    cache_path = _cache_dir() / re.sub(r"[^A-Za-z0-9._-]", "_", name)
    cache_path.write_bytes(raw)
    text = _text_from_html(raw)
    return {
        "status": "REPORTED",
        "source": "SEC_EDGAR",
        "url": url,
        "retrieval_timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "document_title": name,
        "content_path": str(cache_path),
        "content_type": "text/html" if raw.lstrip().startswith(b"<") else "application/octet-stream",
        "text_length": len(text),
        "text": text,
    }


def find_relevant_pages(document: dict[str, Any], query: str, max_pages: int = 5) -> list[dict[str, Any]]:
    text = str(document.get("text") or "")
    if not text:
        return []
    chunks = [text[i:i + 8000] for i in range(0, len(text), 8000)]
    terms = [t.lower() for t in re.findall(r"[A-Za-z0-9%]+", query) if len(t) > 2]
    scored: list[tuple[int, int, str]] = []
    for idx, chunk in enumerate(chunks, start=1):
        low = chunk.lower()
        score = sum(low.count(term) for term in terms)
        if score:
            scored.append((score, idx, chunk))
    scored.sort(reverse=True)
    return [{
        "page": page,
        "score": score,
        "source": document.get("source"),
        "url": document.get("url"),
        "retrieval_timestamp": document.get("retrieval_timestamp"),
        "document_title": document.get("document_title"),
        "extraction_method": "sec_html_chunk",
        "untrusted_source_text": chunk,
    } for score, page, chunk in scored[:max_pages]]
