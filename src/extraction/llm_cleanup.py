"""LLM-assisted cleanup for extracted financial tables.

Financial numbers are immutable. Camelot/pdfplumber own the numbers; the LLM
is used only to clean/normalize labels. If the LLM fails, deterministic
parsing still returns the extracted rows.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ollama import Client

from src.extraction.pdf_router import _looks_year  # reuse the same year/FY pattern the quality scorer already applies

DEFAULT_MODEL = "mistral-small3.2:24b"

_NUMBER_RE = re.compile(
    r"(?<![A-Za-z])(?:\$|€|£|₹)?\s*"
    r"(?:\(\s*\d{1,3}(?:,\d{3})+(?:\.\d+)?\s*\)"
    r"|\(\s*\d+(?:\.\d+)?\s*\)"
    r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?"
    r"|\d+(?:\.\d+)?)"
)


def _clean_cell(cell: Any) -> str:
    return re.sub(r"\s+", " ", str(cell or "")).strip()


def _parse_number(token: str) -> float | int | None:
    token = token.strip().replace("$", "").replace("€", "").replace("£", "").replace("₹", "")
    negative = token.startswith("(") and token.endswith(")")
    token = token.strip("() ").replace(",", "")
    if not token:
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    if negative:
        value = -value
    return int(value) if value.is_integer() else value


def _last_number(text: str) -> float | int | None:
    matches = list(_NUMBER_RE.finditer(text))
    if not matches:
        return None
    return _parse_number(matches[-1].group(0))


def _all_numbers(text: str) -> list[float | int]:
    return [n for n in (_parse_number(m.group(0)) for m in _NUMBER_RE.finditer(text)) if n is not None]


def _strip_numeric_tail(text: str) -> str:
    text = _clean_cell(text)
    matches = list(_NUMBER_RE.finditer(text))
    if not matches:
        return text
    label = text[:matches[-1].start()].strip(" $€£₹\t")
    return label.rstrip(" .:,-")


_HEADER_LABEL_WORDS = {"particulars", "description", "details", "particulars (rs. in lakhs)", ""}


def _detect_year_header(rows: list[list[str]], scan_rows: int = 4) -> tuple[int, dict[int, str]] | None:
    """Look for a header row, among the first few rows, where non-label
    columns look like a year/FY/date token — e.g. a balance sheet's
    "Particulars | 2025 | 2024" row. Returns (header_row_index,
    {column_index: raw_period_text}), or None if no row clears the bar.

    Bar: 2+ year-like columns by default, so a single stray year
    mentioned in running text is never mistaken for a header. Relaxed
    to 1+ when the row's own label cell is a generic non-metric header
    word ("Particulars", "Description", blank) — a real line item is
    essentially never literally "Particulars", so a single year next
    to one of those words is still solid evidence, not a guess.
    """
    best: tuple[int, dict[int, str]] | None = None
    for row_index, row in enumerate(rows[:scan_rows]):
        cells = [_clean_cell(c) for c in (row or [])]
        if len(cells) < 2:
            continue
        candidate: dict[int, str] = {}
        for col_index, cell in enumerate(cells[1:], start=1):
            if cell and _looks_year(cell):
                candidate[col_index] = cell
        min_hits = 1 if cells[0].strip().lower() in _HEADER_LABEL_WORDS else 2
        if len(candidate) < min_hits:
            continue
        if best is None or len(candidate) > len(best[1]):
            best = (row_index, candidate)
    return best


def _deterministic_rows(rows: list[list[str]]) -> list[dict]:
    """Parse a raw table's rows into label + value entries.

    Rule 4 (never mix periods) and Rule 2 (never silently infer) both
    bear on the same failure mode: a multi-year statement puts more
    than one numeric token in a row (current year, prior year,
    sometimes restated), and picking "the last one" with nothing to
    check it against is a guess dressed up as parsing.

    Preferred path: a year/FY header row is detected (Particulars |
    2025 | 2024) — every data row then yields ONE entry per populated
    column, each carrying that column's own `period_raw`, instead of
    being collapsed into a single value. This is what actually lets a
    2-3 year statement — the normal case for an annual report — get
    ingested correctly in one pass.

    Fallback: no confident header row. Same conservative behavior as
    before — take the row's last numeric token as the value, and flag
    the row `ambiguous_multi_period` when more than one candidate
    value was present, so callers can skip rather than guess.
    """
    header = _detect_year_header(rows)
    parsed: list[dict] = []

    if header:
        header_row_index, columns = header
        for row_index, row in enumerate(rows):
            if row_index == header_row_index:
                continue
            cells = [_clean_cell(c) for c in (row or [])]
            if not cells:
                continue
            label = _strip_numeric_tail(cells[0])
            if not label:
                continue
            for col_index, period_raw in columns.items():
                if col_index >= len(cells):
                    continue
                value = _last_number(cells[col_index])
                if value is None:
                    continue
                parsed.append({
                    "row_id": row_index, "metric_raw": label, "value": value,
                    "period_raw": period_raw, "ambiguous_multi_period": False, "all_values": None,
                })
        if parsed:
            return parsed
        # Header row detected but nothing usable aligned under it (e.g. every
        # data row was a single merged cell) — fall through to the legacy path.

    for row_index, row in enumerate(rows):
        cells = [_clean_cell(c) for c in (row or [])]
        if not cells:
            continue

        label = _strip_numeric_tail(cells[0])
        if not label:
            continue

        numbers_in_label_cell = _all_numbers(cells[0])
        other_cell_numbers = [n for cell in cells[1:] for n in _all_numbers(cell)]
        all_numbers = numbers_in_label_cell + other_cell_numbers

        value = all_numbers[-1] if all_numbers else None
        parsed.append({
            "row_id": row_index,
            "metric_raw": label,
            "value": value,
            "period_raw": None,
            "ambiguous_multi_period": len(all_numbers) > 1,
            "all_values": all_numbers if len(all_numbers) > 1 else None,
        })
    return parsed


LABEL_PROMPT = """You clean labels from a financial statement table.

IMPORTANT:
- Do NOT change, infer, calculate, or reproduce any numeric value.
- Do NOT merge rows.
- Do NOT create rows.
- Preserve every row_id.
- Return ONLY a JSON array.
- Each object MUST be exactly:
  {\"row_id\": <integer>, \"metric_raw\": \"<cleaned label>\"}

Rows:
{rows}
"""


def _normalize_labels_with_llm(parsed: list[dict], model: str, host: str) -> dict[int, str]:
    if not parsed:
        return {}

    client = Client(host=host)
    # A header-mode row appears once per populated column (same row_id,
    # same label, different value) — dedupe before sending so the LLM
    # sees each row once, matching what its own instructions ("preserve
    # every row_id") assume.
    seen: dict[int, str] = {}
    for r in parsed:
        seen.setdefault(r["row_id"], r["metric_raw"])
    payload = [{"row_id": row_id, "metric_raw": label} for row_id, label in seen.items()]

    response = client.chat(
        model=model,
        messages=[{
            "role": "user",
            "content": LABEL_PROMPT.format(rows=json.dumps(payload, ensure_ascii=False)),
        }],
        options={"temperature": 0.0},
    )

    content = response["message"]["content"].strip()
    if "<think>" in content and "</think>" in content:
        content = content.split("</think>", 1)[1].strip()

    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError("LLM cleanup response was not a JSON array")

    original_ids = {r["row_id"] for r in parsed}
    out: dict[int, str] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        row_id = item.get("row_id")
        label = item.get("metric_raw")
        if isinstance(row_id, int) and row_id in original_ids and isinstance(label, str) and label.strip():
            out[row_id] = label.strip()
    return out


def cleanup_table(rows: list[list[str]], model: str = DEFAULT_MODEL,
                  host: str = "http://localhost:11434") -> list[dict]:
    parsed = _deterministic_rows(rows)
    if not parsed:
        return []

    try:
        labels = _normalize_labels_with_llm(parsed, model=model, host=host)
    except Exception:
        labels = {}

    return [
        {"metric_raw": labels.get(r["row_id"], r["metric_raw"]), "value": r["value"], "unit": None,
         "period_raw": r.get("period_raw"),
         "ambiguous_multi_period": r["ambiguous_multi_period"], "all_values": r["all_values"]}
        for r in parsed
    ]
