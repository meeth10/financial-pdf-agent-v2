"""Financial MCP server: deterministic calculations exposed over MCP."""

from __future__ import annotations

from mcp.server import MCPServer

from . import tools

mcp = MCPServer(
    "Financial MCP",
    instructions=(
        "Deterministic financial tools over the structured store. "
        "Never invent values; use reported evidence and rule-book calculations."
    ),
)


@mcp.tool()
def get_line_item(entity: str, metric: str, period: str, statement: str | None = None,
                  consolidated: bool | None = None) -> dict:
    """Retrieve one directly reported financial line item with provenance."""
    return tools.get_line_item(entity, metric, period, statement, consolidated)


@mcp.tool()
def list_available_periods(entity: str) -> dict:
    """List normalized periods available for an entity."""
    return tools.list_available_periods(entity)


@mcp.tool()
def list_available_metrics(entity: str, statement: str | None = None) -> dict:
    """List normalized metrics available for an entity and optional statement."""
    return tools.list_available_metrics(entity, statement)


@mcp.tool()
def calculate_metric(entity: str, metric: str, period: str, statement: str | None = None,
                     consolidated: bool | None = None) -> dict:
    """Calculate a rule-book metric deterministically; direct reported values win."""
    return tools.calculate_metric(entity, metric, period, statement, consolidated)


@mcp.tool()
def calculate_growth(entity: str, metric: str, current_period: str, prior_period: str,
                     statement: str | None = None, consolidated: bool | None = None) -> dict:
    """Calculate rule-book growth across two normalized periods."""
    return tools.calculate_growth(entity, metric, current_period, prior_period, statement, consolidated)


@mcp.tool()
def calculate_return_ratio(entity: str, ratio: str, period: str, prior_period: str | None = None,
                           statement: str | None = None, consolidated: bool | None = None) -> dict:
    """Calculate ROA or ROE using the existing rule-book implementation."""
    return tools.calculate_return_ratio(entity, ratio, period, prior_period, statement, consolidated)


@mcp.tool()
def calculate_cagr(entity: str, metric: str, start_period: str, end_period: str, n_years: float,
                   statement: str | None = None, consolidated: bool | None = None) -> dict:
    """Calculate CAGR under the existing positive-start rule."""
    return tools.calculate_cagr(entity, metric, start_period, end_period, n_years, statement, consolidated)


@mcp.tool()
def run_validation_checks(entity: str, period: str, consolidated: bool | None = None) -> dict:
    """Run all available accounting validation/reconciliation checks."""
    return tools.run_validation_checks(entity, period, consolidated)


@mcp.tool()
def export_to_excel(entity: str, output_path: str) -> dict:
    """Export one entity's structured financial data to Excel."""
    return tools.export_to_excel(entity, output_path)


if __name__ == "__main__":
    mcp.run()
