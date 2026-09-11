"""Adapter for the existing Finance Comps Engine HTTP API.

The external repo is `meeth10/Comp_analysis`. Its documented FastAPI surface
provides `/companies`, `/companies/{company_id}/financials`, and
`/comparisons/build`. This adapter translates the comparison response into the
canonical normalized shape used by the stock-analysis platform without copying
its SQLite schema into this repo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import requests


@dataclass(frozen=True)
class ComparableCompany:
    company_id: int
    name: str
    ticker: str | None
    sector: str | None
    period: str | None
    metrics: dict[str, float | None]
    valuation: dict[str, float | None]
    period_mismatch: str | None = None


class CompsEngineError(RuntimeError):
    """Comps engine is unavailable or returned an invalid payload."""


class CompsEngineClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, **params: Any) -> Any:
        try:
            response = requests.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise CompsEngineError(f"Comps engine request failed: {exc}") from exc

    def list_companies(self) -> list[dict[str, Any]]:
        payload = self._get("/companies")
        if not isinstance(payload, list):
            raise CompsEngineError("/companies returned a non-list payload")
        return payload

    def build_comparison(self, company_ids: Iterable[int], *, period_type: str | None = None,
                         fiscal_year: str | None = None, quarter: str | None = None) -> dict[str, Any]:
        payload = self._get(
            "/comparisons/build",
            company_ids=",".join(str(int(x)) for x in company_ids),
            period_type=period_type,
            fiscal_year=fiscal_year,
            quarter=quarter,
        )
        if not isinstance(payload, dict) or "companies" not in payload:
            raise CompsEngineError("/comparisons/build returned an invalid payload")
        return payload

    @staticmethod
    def normalize_comparison(payload: dict[str, Any]) -> list[ComparableCompany]:
        normalized: list[ComparableCompany] = []
        for item in payload.get("companies", []):
            company = item.get("company") or {}
            normalized.append(ComparableCompany(
                company_id=int(company["id"]),
                name=str(company.get("name") or ""),
                ticker=company.get("ticker"),
                sector=company.get("sector"),
                period=item.get("period_label"),
                metrics={str(k): (None if v is None else float(v)) for k, v in (item.get("metrics") or {}).items()},
                valuation={str(k): (None if v is None else float(v)) for k, v in (item.get("valuation") or {}).items()},
                period_mismatch=item.get("period_mismatch"),
            ))
        return normalized

    def run(self, company_ids: Iterable[int], *, period_type: str | None = None,
            fiscal_year: str | None = None, quarter: str | None = None) -> dict[str, Any]:
        raw = self.build_comparison(company_ids, period_type=period_type, fiscal_year=fiscal_year, quarter=quarter)
        companies = self.normalize_comparison(raw)
        return {
            "status": "DERIVED",
            "requested_period": raw.get("requested_period"),
            "companies": [
                {
                    "company_id": c.company_id,
                    "name": c.name,
                    "ticker": c.ticker,
                    "sector": c.sector,
                    "period": c.period,
                    "period_mismatch": c.period_mismatch,
                    "metrics": c.metrics,
                    "valuation": c.valuation,
                }
                for c in companies
            ],
            "metrics": raw.get("metrics", []),
            "valuation_metrics": raw.get("valuation_metrics", []),
            "inference": raw.get("inference"),
            "source": "Comp_analysis",
        }
