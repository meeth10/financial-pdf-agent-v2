import math

import pytest

from src.valuation.dcf import DCFInputs, DCFValidationError, run_dcf


def test_dcf_present_value_bridge_and_sensitivity():
    inputs = DCFInputs(
        revenue=[1000.0, 1100.0, 1200.0],
        ebit_margin=[0.20, 0.21, 0.22],
        tax_rate=[0.25, 0.25, 0.25],
        da=[80.0, 85.0, 90.0],
        capex=[90.0, 95.0, 100.0],
        delta_nwc=[20.0, 20.0, 22.0],
        shares_outstanding=100.0,
        cash=150.0,
        debt=300.0,
        wacc=0.10,
        terminal_growth=0.03,
    )
    result = run_dcf(inputs)
    assert result.terminal_method == "PERPETUITY_GROWTH"
    assert result.enterprise_value > 0
    assert result.equity_value == pytest.approx(result.enterprise_value + 150.0 - 300.0)
    assert result.value_per_share == pytest.approx(result.equity_value / 100.0)
    assert len(result.sensitivity) == 5
    assert len(result.sensitivity["0.1000"]) == 5
    assert math.isfinite(result.sensitivity["0.1000"]["0.0300"])


def test_dcf_rejects_terminal_growth_at_or_above_wacc():
    inputs = DCFInputs(
        revenue=[1000.0], ebit_margin=[0.2], tax_rate=[0.25], da=[50.0],
        capex=[50.0], delta_nwc=[10.0], shares_outstanding=100,
        cash=0, debt=0, wacc=0.08, terminal_growth=0.08,
    )
    with pytest.raises(DCFValidationError):
        run_dcf(inputs)


def test_dcf_capm_wacc_path():
    inputs = DCFInputs(
        revenue=[1000.0], ebit_margin=[0.2], tax_rate=[0.25], da=[50.0],
        capex=[50.0], delta_nwc=[10.0], shares_outstanding=100,
        cash=0, debt=100, risk_free_rate=0.07, beta=1.0,
        equity_risk_premium=0.06, pre_tax_cost_of_debt=0.08,
        effective_tax_rate=0.25, debt_weight=0.2, terminal_growth=0.02,
    )
    result = run_dcf(inputs)
    expected_wacc = 0.8 * (0.07 + 1.0 * 0.06) + 0.2 * 0.08 * (1 - 0.25)
    assert result.wacc == pytest.approx(expected_wacc)
