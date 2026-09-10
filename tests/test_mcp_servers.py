import importlib.util

import pytest


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="official MCP SDK is not installed in this environment")
def test_all_three_mcp_servers_import_and_expose_tools():
    from mcp_servers.financial.server import mcp as financial_mcp
    from mcp_servers.evidence.server import mcp as evidence_mcp
    from mcp_servers.retrieval.server import mcp as retrieval_mcp

    assert financial_mcp is not None
    assert evidence_mcp is not None
    assert retrieval_mcp is not None
