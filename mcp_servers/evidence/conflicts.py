"""Explicit evidence conflict classification."""

from __future__ import annotations

from typing import Any

from .normalize import normalize_record, same_financial_scope


def classify_pair(a: dict[str, Any], b: dict[str, Any]) -> str:
    a = normalize_record(a)
    b = normalize_record(b)

    if not same_financial_scope(a, b):
        if (
            a.get("entity") == b.get("entity")
            and a.get("metric") == b.get("metric")
            and a.get("period") == b.get("period")
        ):
            return "SCOPE_DIFFERENCE"
        return "TRUE_CONFLICT"

    if a.get("value") is None or b.get("value") is None:
        return "TRUE_CONFLICT"

    if a.get("absolute_value") == b.get("absolute_value"):
        if str(a.get("unit") or "") != str(b.get("unit") or ""):
            return "UNIT_DIFFERENCE"
        return "AGREES"

    av = float(a["absolute_value"])
    bv = float(b["absolute_value"])
    scale = max(abs(av), abs(bv), 1.0)
    if abs(av - bv) / scale <= 0.005:
        return "ROUNDING_DIFFERENCE"

    if bool(a.get("restated")) or bool(b.get("restated")):
        return "RESTATED"

    return "TRUE_CONFLICT"


def classify_candidates(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = [normalize_record(c) for c in candidates]
    classifications: list[dict[str, Any]] = []
    for i in range(len(normalized)):
        for j in range(i + 1, len(normalized)):
            classifications.append({
                "left_index": i,
                "right_index": j,
                "classification": classify_pair(normalized[i], normalized[j]),
            })
    unique = {x["classification"] for x in classifications}
    overall = "AGREES" if not unique else next(iter(unique)) if len(unique) == 1 else "TRUE_CONFLICT"
    if "TRUE_CONFLICT" in unique:
        overall = "TRUE_CONFLICT"
    elif "SCOPE_DIFFERENCE" in unique:
        overall = "SCOPE_DIFFERENCE"
    elif "RESTATED" in unique:
        overall = "RESTATED"
    elif "UNIT_DIFFERENCE" in unique:
        overall = "UNIT_DIFFERENCE"
    elif "ROUNDING_DIFFERENCE" in unique:
        overall = "ROUNDING_DIFFERENCE"
    return {"overall": overall, "comparisons": classifications, "candidates": normalized}
