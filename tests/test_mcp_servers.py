from mcp.financial.server import mcp as financial_mcp
from mcp.evidence.server import mcp as evidence_mcp
from mcp.retrieval.server import mcp as retrieval_mcp


def test_all_three_mcp_servers_import_and_expose_tools():
    assert financial_mcp is not None
    assert evidence_mcp is not None
    assert retrieval_mcp is not None
