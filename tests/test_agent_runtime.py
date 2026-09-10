from src.agent.runtime import DISPATCH, TOOL_SCHEMAS
from src.webapp import app


def test_runtime_uses_financial_evidence_and_retrieval_tools():
    assert "get_line_item" in DISPATCH
    assert "calculate_metric" in DISPATCH
    assert "get_evidence" in DISPATCH
    assert "compare_evidence" in DISPATCH
    assert "search_filings" in DISPATCH
    assert "fetch_document" in DISPATCH
    assert "find_relevant_pages" in DISPATCH
    assert "get_or_fetch_financials" in DISPATCH
    assert len(TOOL_SCHEMAS) >= 14


def test_webapp_exposes_agent_routes():
    routes = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/" in routes
    assert "/extract" in routes
    assert "/ask" in routes


def test_manual_session_prompt_does_not_force_consolidated_scope(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, host):
            captured["host"] = host

        def chat(self, **kwargs):
            captured["messages"] = kwargs["messages"]
            return {"message": {"role": "assistant", "content": "ok"}}

    monkeypatch.setattr("src.agent.runtime.Client", FakeClient)

    from src.agent.runtime import ask

    assert ask("What is PBT for FY2025?", entity="HDFC") == "ok"
    system_message = captured["messages"][0]["content"]
    assert "manually uploaded filing session" in system_message
    assert "do not force consolidated=true or consolidated=false" in system_message
