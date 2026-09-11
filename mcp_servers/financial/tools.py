"""Thin MCP adapters around the existing deterministic financial engine."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict
from typing import Any

from src.agent import tools as core
from shared.provenance import attach_provenance
from src.valuation.dcf import DCFInputs, run_dcf as run_dcf_engine

DEFAULT_DB_PATH = "data/financials.db"


def _db_path() -> str:
    return os.getenv("FINANCIAL_DB_PATH", DEFAULT_DB_PATH)


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def get_line_item(entity: str, metric: str, period: str, statement: str | None = None,
                  consolidated: bool | None = None) -> dict[str, Any]:
    with open_db() as conn:
        result = core.get_line_item(conn, entity, metric, period, statement, consolidated)
    return attach_provenance(result, entity=entity)


def list_available_periods(entity: str) -> dict[str, Any]:
    with open_db() as conn:
        return core.list_available_periods(conn, entity)


def list_available_metrics(entity: str, statement: str | None = None) -> dict[str, Any]:
    with open_db() as conn:
        return core.list_available_metrics(conn, entity, statement)


def calculate_metric(entity: str, metric: str, period: str, statement: str | None = None,
                     consolidated: bool | None = None) -> dict[str, Any]:
    with open_db() as conn:
        result = core.calculate_metric_tool(conn, entity, metric, period, statement, consolidated)
    return attach_provenance(result, entity=entity)


def calculate_growth(entity: str, metric: str, current_period: str, prior_period: str,
                     statement: str | None = None, consolidated: bool | None = None) -> dict[str, Any]:
    with open_db() as conn:
        result = core.calculate_growth_tool(conn, entity, metric, current_period, prior_period, statement, consolidated)
    return attach_provenance(result, entity=entity)


def calculate_return_ratio(entity: str, ratio: str, period: str, prior_period: str | None = None,
                           statement: str | None = None, consolidated: bool | None = None) -> dict[str, Any]:
    with open_db() as conn:
        result = core.calculate_return_ratio_tool(conn, entity, ratio, period, prior_period, statement, consolidated)
    return attach_provenance(result, entity=entity)


def calculate_cagr(entity: str, metric: str, start_period: str, end_period: str, n_years: float,
                   statement: str | None = None, consolidated: bool | None = None) -> dict[str, Any]:
    with open_db() as conn:
        result = core.calculate_cagr_tool(conn, entity, metric, start_period, end_period, n_years, statement, consolidated)
    return attach_provenance(result, entity=entity)


def run_validation_checks(entity: str, period: str, consolidated: bool | None = None) -> dict[str, Any]:
    with open_db() as conn:
        result = core.run_validation_checks(conn, entity, period, consolidated)
    return attach_provenance(result, entity=entity)


def run_dcf(**kwargs: Any) -> dict[str, Any]:
    """Run DCF outside the LLM using the deterministic valuation engine."""
    result = run_dcf_engine(DCFInputs(**kwargs))
    return {"status": "DERIVED", **asdict(result)}


def export_to_excel(entity: str, output_path: str) -> dict[str, Any]:
    with open_db() as conn:
        return core.export_to_excel(conn, entity, output_path)
