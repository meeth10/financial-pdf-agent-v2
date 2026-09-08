from src.agent.runtime import DISPATCH, TOOL_SCHEMAS
from src.webapp import app


def test_runtime_uses_financial_and_evidence_tools():
    assert "get_line_item" in DISPATCH
    assert "calculate_metric" in DISPATCH
    assert "get_evidence" in DISPATCH
    assert "compare_evidence" in DISPATCH
    assert len(TOOL_SCHEMAS) >= 10


def test_webapp_exposes_agent_routes():
    routes = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/" in routes
    assert "/extract" in routes
    assert "/ask" in routes
