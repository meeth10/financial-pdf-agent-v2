"""Provenance helpers shared by MCP layers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _one(result: dict[str, Any], entity: str | None = None) -> dict[str, Any]:
    out = dict(result)
    prov = dict(out.get("provenance") or {})
    if entity and out.get("entity") is None:
        out["entity"] = entity
        prov.setdefault("entity", entity)
    if out.get("source_page") is not None:
        prov.setdefault("source_page", out["source_page"])
    if out.get("source_table") is not None:
        prov.setdefault("source_table", out["source_table"])
    if out.get("extraction_confidence") is not None:
        prov.setdefault("extraction_confidence", out["extraction_confidence"])
    if out.get("metric") is not None:
        prov.setdefault("metric", out["metric"])
    if out.get("period") is not None:
        prov.setdefault("period", out["period"])
    if prov:
        out["provenance"] = prov
    return out


def attach_provenance(result: dict[str, Any], entity: str | None = None) -> dict[str, Any]:
    """Add a stable provenance object without changing financial semantics."""
    out = _one(result, entity)
    if out.get("status") == "DERIVED":
        inputs = []
        for item in out.get("inputs") or []:
            if isinstance(item, dict):
                inputs.append(_one(item))
        out["input_provenance"] = inputs
    out.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    return out
