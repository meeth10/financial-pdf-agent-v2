"""Create a deterministic benchmark manifest from a populated financial store."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from src.agent.derivation import canonicalize_metric
from src.agent.tools import get_line_item as core_get_line_item, calculate_metric_tool
from src.store.schema import init_db

QUESTION_TEMPLATES = [
    "What was {metric} for {entity} in {period}?",
    "Give me {metric} for {entity} for {period}.",
    "Report {metric} in {period} for {entity}.",
    "What is the reported {metric} in {period} for {entity}?",
]
DERIVED_TEMPLATES = [
    "What was {metric} for {entity} in {period}?",
    "Calculate {metric} for {entity} in {period}.",
]


def _rows(db_path: str, entity: str, period: str) -> list[sqlite3.Row]:
    conn = init_db(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT DISTINCT metric FROM line_items WHERE entity=? AND period=? ORDER BY metric",
            (entity, period),
        ).fetchall()
    finally:
        conn.close()


def build(db_path: str, entity: str, period: str, count: int) -> list[dict]:
    metrics = [r[0] for r in _rows(db_path, entity, period)]
    cases: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for metric in metrics:
        canonical = canonicalize_metric(metric)
        expected = get_line_item_for_benchmark(db_path, entity, canonical, period)
        key = (canonical, "reported")
        if expected.get("status") == "REPORTED" and key not in seen:
            seen.add(key)
            cases.append({
                "id": f"reported_{len(cases)+1:03d}",
                "question": QUESTION_TEMPLATES[len(cases) % len(QUESTION_TEMPLATES)].format(
                    metric=canonical.replace("_", " "), entity=entity, period=period
                ),
                "entity": entity,
                "period": period,
                "metric": canonical,
                "expected_status": "REPORTED",
                "expected_value": expected.get("value"),
                "expected_unit": expected.get("unit"),
                "source_stage": "stored_evidence",
            })
        if len(cases) >= count:
            break

    formulas = ["gross_profit", "ebitda", "net_debt", "free_cash_flow", "ebitda_margin", "net_margin", "debt_to_equity", "interest_coverage", "fcf_margin"]
    for metric in formulas:
        if len(cases) >= count:
            break
        expected = calculate_for_benchmark(db_path, entity, metric, period)
        if expected.get("status") not in {"DERIVED", "REPORTED"}:
            continue
        key = (metric, "derived")
        if key in seen:
            continue
        seen.add(key)
        cases.append({
            "id": f"derived_{len(cases)+1:03d}",
            "question": DERIVED_TEMPLATES[len(cases) % len(DERIVED_TEMPLATES)].format(
                metric=metric.replace("_", " "), entity=entity, period=period
            ),
            "entity": entity,
            "period": period,
            "metric": metric,
            "expected_status": expected.get("status"),
            "expected_value": expected.get("value"),
            "expected_unit": expected.get("unit"),
            "source_stage": "deterministic_engine",
        })

    return cases[:count]


def get_line_item_for_benchmark(db_path: str, entity: str, metric: str, period: str) -> dict:
    conn = init_db(db_path)
    try:
        return core_get_line_item(conn, entity, metric, period)
    finally:
        conn.close()


def calculate_for_benchmark(db_path: str, entity: str, metric: str, period: str) -> dict:
    conn = init_db(db_path)
    try:
        return calculate_metric_tool(conn, entity, metric, period)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/financials.db")
    parser.add_argument("--entity", required=True)
    parser.add_argument("--period", required=True)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    cases = build(args.db, args.entity, args.period, args.count)
    if len(cases) < min(args.count, 25):
        raise SystemExit(f"Only generated {len(cases)} cases; populate a richer annual report before benchmarking.")
    payload = {"entity": args.entity, "period": args.period, "count": len(cases), "cases": cases}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(cases)} benchmark cases to {args.output}")


if __name__ == "__main__":
    main()
