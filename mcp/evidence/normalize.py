"""Deterministic normalization for financial evidence."""

from __future__ import annotations

from typing import Any

from src.agent.derivation import canonicalize_metric
from src.agent.periods import canonicalize_period
from src.agent.units import parse_unit, to_absolute


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    out = dict(record)
    if out.get("metric"):
        out["metric"] = canonicalize_metric(str(out["metric"]))
    if out.get("period"):
        out["period"] = canonicalize_period(str(out["period"]))
    if out.get("unit"):
        currency, scale = parse_unit(str(out["unit"]))
        out["currency"] = currency
        out["scale"] = scale
        if out.get("value") is not None:
            out["absolute_value"] = to_absolute(float(out["value"]), str(out["unit"]))[0]
    else:
        out["currency"] = None
        out["scale"] = 1.0
    return out


def same_financial_scope(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (
        a.get("entity") == b.get("entity")
        and a.get("metric") == b.get("metric")
        and a.get("period") == b.get("period")
        and a.get("statement") == b.get("statement")
        and a.get("consolidated") == b.get("consolidated")
    )
