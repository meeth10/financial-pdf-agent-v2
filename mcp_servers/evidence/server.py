"""Evidence MCP server: provenance and conflict classification."""

from __future__ import annotations

from mcp.server import MCPServer

from . import tools

mcp = MCPServer(
    "Evidence MCP",
    instructions=(
        "Evidence is authoritative only when sourced from stored documents. "
        "Preserve provenance and surface conflicts; never silently choose a conflicting value."
    ),
)


@mcp.tool()
def get_evidence(entity: str, metric: str, period: str, statement: str | None = None,
                 consolidated: bool | None = None) -> dict:
    """Retrieve stored evidence and provenance without collapsing conflicts."""
    return tools.get_evidence(entity, metric, period, statement, consolidated)


@mcp.tool()
def list_available_periods(entity: str) -> dict:
    """List periods represented in stored evidence."""
    return tools.list_available_periods(entity)


@mcp.tool()
def list_available_metrics(entity: str, statement: str | None = None) -> dict:
    """List metrics represented in stored evidence."""
    return tools.list_available_metrics(entity, statement)


@mcp.tool()
def compare_evidence(entity: str, metric: str, period: str,
                     candidates: list[dict] | None = None,
                     statement: str | None = None,
                     consolidated: bool | None = None) -> dict:
    """Classify evidence as AGREES, ROUNDING_DIFFERENCE, UNIT_DIFFERENCE, RESTATED,
    SCOPE_DIFFERENCE, or TRUE_CONFLICT and preserve all candidates."""
    return tools.compare_evidence(entity, metric, period, candidates, statement, consolidated)


if __name__ == "__main__":
    mcp.run()
