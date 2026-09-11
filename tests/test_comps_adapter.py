from src.valuation.comps_adapter import CompsEngineClient


def test_comps_payload_normalizes_to_platform_schema():
    payload = {
        "requested_period": {"period_type": "annual", "fiscal_year": "FY2025", "quarter": None},
        "companies": [
            {
                "company": {"id": 1, "name": "Alpha Ltd", "ticker": "ALPHA", "sector": "Telecom"},
                "period_label": "FY2025",
                "period_mismatch": None,
                "metrics": {"ebitda_margin": 42.1, "net_debt_to_ebitda": 1.2},
                "valuation": {"p_e": 20.0},
            }
        ],
        "metrics": ["ebitda_margin"],
        "valuation_metrics": ["p_e"],
        "inference": "deterministic",
    }
    result = CompsEngineClient.normalize_comparison(payload)
    assert result[0].name == "Alpha Ltd"
    assert result[0].ticker == "ALPHA"
    assert result[0].metrics["ebitda_margin"] == 42.1
    assert result[0].valuation["p_e"] == 20.0
