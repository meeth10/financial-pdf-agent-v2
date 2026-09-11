"""Run the financial-agent benchmark and produce stage-tagged results."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

from src.agent.runtime import ask
from mcp_servers.financial.tools import calculate_metric, get_line_item


def _parse(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in str(text or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip().lower()] = value.strip()
    return result


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.replace(",", "").replace("₹", "").replace("$", "")
    token = cleaned.split()[0] if cleaned.split() else ""
    try:
        return float(token.replace("%", ""))
    except ValueError:
        return None


def _matches(case: dict, answer: str) -> tuple[bool, str]:
    parsed = _parse(answer)
    expected_status = str(case.get("expected_status") or "").upper()
    actual_status = str(parsed.get("status") or "").upper()
    if actual_status != expected_status:
        return False, "status_mismatch"
    if expected_status in {"UNAVAILABLE", "SOURCE_UNAVAILABLE", "CONFLICTED"}:
        return True, "status_only"
    expected = case.get("expected_value")
    actual = _number(parsed.get("value"))
    if expected is None or actual is None:
        return False, "value_parse_failure"
    tolerance = max(abs(float(expected)) * 0.005, 1e-9)
    if abs(actual - float(expected)) <= tolerance:
        return True, "numeric_match"
    return False, "numeric_mismatch"


def _failure_stage(case: dict, reason: str) -> str:
    if reason == "status_only":
        return "pass"
    try:
        if case.get("expected_status") == "REPORTED":
            evidence = get_line_item(case["entity"], case["metric"], case["period"])
            if evidence.get("status") == "REPORTED":
                return "agent_reasoning"
            return "extraction_or_discovery"
        if case.get("expected_status") == "DERIVED":
            return "derivation"
    except Exception:
        pass
    return "agent_reasoning"


def run(db_path: str, benchmark_path: str, model: str | None = None) -> dict:
    os.environ["FINANCIAL_DB_PATH"] = db_path
    payload = json.loads(Path(benchmark_path).read_text(encoding="utf-8"))
    rows: list[dict] = []
    failures: dict[str, int] = {}
    for case in payload["cases"]:
        try:
            answer = ask(case["question"], entity=case["entity"], db_path=db_path, model=model or "mistral-small3.2:24b")
            passed, reason = _matches(case, answer)
            stage = "pass" if passed else _failure_stage(case, reason)
        except Exception as exc:
            answer = ""
            passed = False
            reason = f"runtime_error:{exc}"
            stage = "agent_reasoning"
        failures[stage] = failures.get(stage, 0) + (0 if passed else 1)
        rows.append({**case, "answer": answer, "passed": passed, "reason": reason, "failure_stage": stage})
    total = len(rows)
    passed_count = sum(1 for row in rows if row["passed"])
    return {
        "entity": payload.get("entity"),
        "period": payload.get("period"),
        "total": total,
        "passed": passed_count,
        "accuracy": passed_count / total if total else 0.0,
        "failures_by_stage": failures,
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/financials.db")
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="mistral-small3.2:24b")
    args = parser.parse_args()
    result = run(args.db, args.benchmark, args.model)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("total", "passed", "accuracy", "failures_by_stage")}, indent=2))


if __name__ == "__main__":
    main()
