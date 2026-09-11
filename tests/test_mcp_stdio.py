import asyncio
import sys

import pytest


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("mcp") is None,
                  reason="official MCP SDK is not installed")
def test_mcp_servers_expose_tools_over_stdio():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def check(module_name: str) -> set[str]:
        params = StdioServerParameters(command=sys.executable, args=["-m", module_name])
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.list_tools()
                return {tool.name for tool in result.tools}

    financial = asyncio.run(check("mcp_servers.financial.server"))
    evidence = asyncio.run(check("mcp_servers.evidence.server"))
    retrieval = asyncio.run(check("mcp_servers.retrieval.server"))

    assert {"get_line_item", "calculate_metric", "run_validation_checks"}.issubset(financial)
    assert {"get_evidence", "compare_fact_candidates", "compare_evidence"}.issubset(evidence)
    assert {"search_filings", "get_or_fetch_financials"}.issubset(retrieval)
