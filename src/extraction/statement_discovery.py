"""Deterministic three-gate financial-statement page discovery."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import re
from typing import Iterable

import pdfplumber

from src.extraction.pdf_router import _looks_numeric, extract_page_tables


@dataclass(frozen=True)
class StatementCandidate:
    statement: str
    page: int
    score: float
    matched_terms: tuple[str, ...]
    text_preview: str
    status: str
    title_match: str | None = None
    gate2_label_hits: int = 0
    gate2_structured_rows: int = 0
    gate3_numeric_columns: int = 0
    gate3_garbage_ratio: float = 1.0
    main_cluster: bool = False
    outside_cluster_duplicate: bool = False
    review_flag: str | None = None


BALANCE_SHEET_TITLES = (
    "CONSOLIDATED BALANCE SHEETS", "CONSOLIDATED BALANCE SHEET", "BALANCE SHEET",
    "STATEMENT OF FINANCIAL POSITION", "CONSOLIDATED STATEMENTS OF FINANCIAL POSITION",
    "STANDALONE BALANCE SHEET",
)
INCOME_STATEMENT_TITLES = (
    "CONSOLIDATED STATEMENT OF PROFIT AND LOSS", "STATEMENT OF PROFIT AND LOSS",
    "CONSOLIDATED INCOME STATEMENT", "INCOME STATEMENT", "STATEMENT OF OPERATIONS",
    "CONSOLIDATED STATEMENTS OF OPERATIONS", "CONSOLIDATED STATEMENTS OF INCOME",
)
CASH_FLOW_TITLES = (
    "CONSOLIDATED STATEMENT OF CASH FLOWS", "STATEMENT OF CASH FLOWS",
    "CASH FLOW STATEMENT", "CONSOLIDATED CASH FLOW STATEMENT",
)

TITLE_LISTS = {
    "balance_sheet": BALANCE_SHEET_TITLES,
    "income_statement": INCOME_STATEMENT_TITLES,
    "cash_flow": CASH_FLOW_TITLES,
}

STATEMENT_LABELS: dict[str, tuple[str, ...]] = {
    "balance_sheet": (
        "cash and cash equivalents", "total assets", "total liabilities", "total equity",
        "shareholders' equity", "current assets", "current liabilities", "accounts receivable",
        "accounts payable", "borrowings", "debt", "inventory",
    ),
    "income_statement": (
        "revenue", "net sales", "gross profit", "operating income", "operating profit",
        "profit before tax", "profit after tax", "net income", "profit for the year", "ebitda",
        "finance cost", "income tax",
    ),
    "cash_flow": (
        "net cash from operating activities", "cash generated from operations",
        "operating activities", "investing activities", "financing activities",
        "net cash", "capital expenditures", "purchase of property, plant and equipment",
        "depreciation", "cash and cash equivalents",
    ),
}

STATEMENT_ORDER = ("balance_sheet", "income_statement", "cash_flow")
CLUSTER_RADIUS = 5


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _title_pattern(title: str) -> re.Pattern[str]:
    # Flexible whitespace covers OCR line breaks without making the title gate fuzzy.
    parts = [re.escape(x) for x in _normalise(title).split(" ")]
    return re.compile(r"\b" + r"\s+".join(parts) + r"\b", re.IGNORECASE)


def _match_title(text: str, statement: str) -> str | None:
    for title in TITLE_LISTS[statement]:
        if _title_pattern(title).search(text):
            return title
    return None


def _best_table(page_number: int, pdf_path: str):
    tables = extract_page_tables(pdf_path, page_number)
    if not tables:
        return None
    return max(tables, key=lambda t: (t.quality_score, t.confidence))


def _gate2(rows: list[list[str]], statement: str) -> tuple[int, int]:
    labels = {_normalise(x) for x in STATEMENT_LABELS[statement]}
    label_hits = 0
    structured_rows = 0
    for row in rows:
        if not row:
            continue
        cells = [str(x or "").strip() for x in row]
        if not cells or not any(ch.isalpha() for ch in cells[0]):
            continue
        trailing_numeric = sum(_looks_numeric(c) for c in cells[1:])
        if trailing_numeric:
            structured_rows += 1
            label = _normalise(cells[0])
            if any(term in label or label in term for term in labels):
                label_hits += 1
    return label_hits, structured_rows


def _gate3(rows: list[list[str]]) -> tuple[int, float]:
    structured = []
    for row in rows:
        if not row or not any(ch.isalpha() for ch in str(row[0] or "")):
            continue
        trailing = [str(x or "").strip() for x in row[1:]]
        if trailing:
            structured.append(trailing)
    if not structured:
        return 0, 1.0

    max_cols = max(len(r) for r in structured)
    numeric_by_col = [0] * max_cols
    nonempty_by_col = [0] * max_cols
    garbage = 0
    nonempty = 0
    for row in structured:
        for i, cell in enumerate(row):
            if not cell:
                continue
            nonempty += 1
            nonempty_by_col[i] += 1
            if _looks_numeric(cell):
                numeric_by_col[i] += 1
            else:
                garbage += 1
    min_rows = max(2, int(len(structured) * 0.5 + 0.999))
    numeric_columns = sum(n >= min_rows for n in numeric_by_col)
    garbage_ratio = garbage / max(nonempty, 1)
    return numeric_columns, garbage_ratio


def _evaluate_page(pdf_path: str, page: int, statement: str, text: str) -> StatementCandidate | None:
    title = _match_title(text, statement)
    if not title:
        return None

    table = _best_table(page, pdf_path)
    labels = structured_rows = numeric_columns = 0
    garbage_ratio = 1.0
    score = 10.0
    if table is not None:
        labels, structured_rows = _gate2(table.rows, statement)
        numeric_columns, garbage_ratio = _gate3(table.rows)
        score += min(labels, 6) * 1.5
        score += min(structured_rows, 10) * 0.2
        score += min(numeric_columns, 4) * 1.5
        score += max(0.0, 1.0 - garbage_ratio) * 2.0

    gate2_pass = labels >= 3 and structured_rows >= 3
    gate3_pass = numeric_columns >= 2 and garbage_ratio <= 0.25
    status = "CONFIRMED" if gate2_pass and gate3_pass else "TITLE_ONLY"
    if status == "TITLE_ONLY":
        score -= 4.0

    return StatementCandidate(
        statement=statement,
        page=page,
        score=round(score, 2),
        matched_terms=(title,),
        text_preview=" ".join(text.split())[:300],
        status=status,
        title_match=title,
        gate2_label_hits=labels,
        gate2_structured_rows=structured_rows,
        gate3_numeric_columns=numeric_columns,
        gate3_garbage_ratio=round(garbage_ratio, 3),
    )


def discover_statement_statuses(pdf_path: str) -> dict[str, list[dict]]:
    """Return per-page gate status for every statement type."""
    with pdfplumber.open(pdf_path) as pdf:
        page_text = [page.extract_text() or "" for page in pdf.pages]

    out: dict[str, list[dict]] = {s: [] for s in STATEMENT_ORDER}
    for statement in STATEMENT_ORDER:
        for page_number, text in enumerate(page_text, start=1):
            title = _match_title(text, statement)
            if not title:
                out[statement].append({"page": page_number, "status": "NOT_A_STATEMENT_PAGE"})
                continue
            candidate = _evaluate_page(pdf_path, page_number, statement, text)
            out[statement].append(asdict(candidate) if candidate else {
                "page": page_number, "status": "TITLE_ONLY", "title_match": title
            })
    return out


def discover_statement_pages(
    pdf_path: str,
    *,
    top_k: int = 3,
    min_score: float = 10.0,
    cluster_radius: int = CLUSTER_RADIUS,
) -> dict[str, list[StatementCandidate]]:
    """Find statement pages using hard title/structure/numeric gates plus proximity clustering."""
    with pdfplumber.open(pdf_path) as pdf:
        page_text = [page.extract_text() or "" for page in pdf.pages]

    all_candidates: dict[str, list[StatementCandidate]] = {s: [] for s in STATEMENT_ORDER}
    for statement in STATEMENT_ORDER:
        for page_number, text in enumerate(page_text, start=1):
            if not text.strip():
                continue
            candidate = _evaluate_page(pdf_path, page_number, statement, text)
            if candidate is not None:
                all_candidates[statement].append(candidate)

    confirmed_pages = [c.page for cs in all_candidates.values() for c in cs if c.status == "CONFIRMED"]
    cluster_pages: set[int] = set()
    for page in confirmed_pages:
        cluster_pages.update(range(max(1, page - cluster_radius), page + cluster_radius + 1))

    result: dict[str, list[StatementCandidate]] = {s: [] for s in STATEMENT_ORDER}
    for statement, candidates in all_candidates.items():
        confirmed = [c for c in candidates if c.status == "CONFIRMED"]
        enriched = []
        for c in candidates:
            in_cluster = c.page in cluster_pages if cluster_pages else True
            outside_duplicate = c.status == "CONFIRMED" and bool(cluster_pages) and not in_cluster
            review = "CONFIRMED_OUTSIDE_MAIN_CLUSTER" if outside_duplicate else None
            enriched.append(StatementCandidate(
                **{**asdict(c), "main_cluster": in_cluster,
                   "outside_cluster_duplicate": outside_duplicate, "review_flag": review}
            ))
        enriched.sort(key=lambda c: (
            c.status != "CONFIRMED", not c.main_cluster, -c.score, c.page
        ))
        # Keep every confirmed instance so restated/duplicate statements are never silently replaced.
        limit = max(top_k, len([c for c in enriched if c.status == "CONFIRMED"]))
        result[statement] = enriched[:limit]

    return result


def discover_pages(pdf_path: str, *, top_k: int = 3) -> dict[str, list[int]]:
    discovered = discover_statement_pages(pdf_path, top_k=top_k)
    return {statement: [c.page for c in rows if c.status == "CONFIRMED"] for statement, rows in discovered.items()}


def candidates_as_dict(candidates: dict[str, Iterable[StatementCandidate]]) -> dict[str, list[dict]]:
    return {key: [asdict(item) for item in value] for key, value in candidates.items()}
