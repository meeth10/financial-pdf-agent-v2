"""Ollama runtime backed by deterministic financial, evidence, retrieval and valuation tools."""

from __future__ import annotations

import json
import os
from typing import Any

from ollama import Client

from .system_prompt import SYSTEM_PROMPT
from mcp_servers.financial import tools as financial_tools
from mcp_servers.evidence import tools as evidence_tools
from mcp_servers.retrieval import tools as retrieval_tools

DEFAULT_MODEL = "mistral-small3.2:24b"
MAX_TURNS = 8

FINANCIAL_SCHEMAS = [
    {"type":"function","function":{"name":"get_line_item","description":"Retrieve one directly reported financial line item. Always prefer this before deriving.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"metric":{"type":"string"},"period":{"type":"string"},"statement":{"type":"string","enum":["balance_sheet","income_statement","cash_flow"]},"consolidated":{"type":"boolean"}},"required":["entity","metric","period"]}}},
    {"type":"function","function":{"name":"list_available_periods","description":"List periods available in the financial store.","parameters":{"type":"object","properties":{"entity":{"type":"string"}},"required":["entity"]}}},
    {"type":"function","function":{"name":"list_available_metrics","description":"List normalized financial metrics available in the store.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"statement":{"type":"string","enum":["balance_sheet","income_statement","cash_flow"]}},"required":["entity"]}}},
    {"type":"function","function":{"name":"calculate_metric","description":"Calculate a rule-book metric. Directly reported values take precedence over derived values.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"metric":{"type":"string"},"period":{"type":"string"},"statement":{"type":"string","enum":["balance_sheet","income_statement","cash_flow"]},"consolidated":{"type":"boolean"}},"required":["entity","metric","period"]}}},
    {"type":"function","function":{"name":"calculate_growth","description":"Calculate rule-book growth for a metric using current and prior periods.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"metric":{"type":"string"},"current_period":{"type":"string"},"prior_period":{"type":"string"},"statement":{"type":"string","enum":["balance_sheet","income_statement","cash_flow"]},"consolidated":{"type":"boolean"}},"required":["entity","metric","current_period","prior_period"]}}},
    {"type":"function","function":{"name":"calculate_return_ratio","description":"Calculate ROA or ROE under the deterministic rule book.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"ratio":{"type":"string","enum":["roa","roe"]},"period":{"type":"string"},"prior_period":{"type":"string"},"statement":{"type":"string","enum":["balance_sheet","income_statement","cash_flow"]},"consolidated":{"type":"boolean"}},"required":["entity","ratio","period"]}}},
    {"type":"function","function":{"name":"calculate_cagr","description":"Calculate CAGR under the deterministic rule book.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"metric":{"type":"string"},"start_period":{"type":"string"},"end_period":{"type":"string"},"n_years":{"type":"number"},"statement":{"type":"string","enum":["balance_sheet","income_statement","cash_flow"]},"consolidated":{"type":"boolean"}},"required":["entity","metric","start_period","end_period","n_years"]}}},
    {"type":"function","function":{"name":"run_validation_checks","description":"Run deterministic accounting reconciliation checks for one entity and period.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"period":{"type":"string"},"consolidated":{"type":"boolean"}},"required":["entity","period"]}}},
    {"type":"function","function":{"name":"run_dcf","description":"Run the deterministic discounted-cash-flow engine from explicit forecast and capital-structure inputs. The model performs no arithmetic itself.","parameters":{"type":"object","properties":{"revenue":{"type":"array","items":{"type":"number"}},"ebit_margin":{"type":"array","items":{"type":"number"}},"tax_rate":{"type":"array","items":{"type":"number"}},"da":{"type":"array","items":{"type":"number"}},"capex":{"type":"array","items":{"type":"number"}},"delta_nwc":{"type":"array","items":{"type":"number"}},"shares_outstanding":{"type":"number"},"cash":{"type":"number"},"debt":{"type":"number"},"wacc":{"type":"number"},"terminal_growth":{"type":"number","default":0.03},"exit_multiple":{"type":"number"},"risk_free_rate":{"type":"number"},"beta":{"type":"number"},"equity_risk_premium":{"type":"number"},"pre_tax_cost_of_debt":{"type":"number"},"effective_tax_rate":{"type":"number"},"debt_weight":{"type":"number"}},"required":["revenue","ebit_margin","tax_rate","da","capex","delta_nwc","shares_outstanding","cash","debt"]}}},
]

EVIDENCE_SCHEMAS = [
    {"type":"function","function":{"name":"get_evidence","description":"Retrieve stored evidence and provenance. Multiple candidates return CONFLICTED rather than silently choosing.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"metric":{"type":"string"},"period":{"type":"string"},"statement":{"type":"string"},"consolidated":{"type":"boolean"}},"required":["entity","metric","period"]}}},
    {"type":"function","function":{"name":"compare_fact_candidates","description":"Classify fact candidates as AGREES, ROUNDING_DIFFERENCE, RESTATED, SCOPE_DIFFERENCE, UNIT_DIFFERENCE or TRUE_CONFLICT.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"metric":{"type":"string"},"period":{"type":"string"},"candidates":{"type":"array","items":{"type":"object"}},"statement":{"type":"string"},"consolidated":{"type":"boolean"}},"required":["entity","metric","period"]}}},
    {"type":"function","function":{"name":"compare_evidence","description":"Backward-compatible alias for compare_fact_candidates.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"metric":{"type":"string"},"period":{"type":"string"},"candidates":{"type":"array","items":{"type":"object"}},"statement":{"type":"string"},"consolidated":{"type":"boolean"}},"required":["entity","metric","period"]}}},
]

RETRIEVAL_SCHEMAS = [
    {"type":"function","function":{"name":"search_filings","description":"Search SEC EDGAR for public-company filings.","parameters":{"type":"object","properties":{"company":{"type":"string"},"document_type":{"type":"string"},"period":{"type":"string"}},"required":["company"]}}},
    {"type":"function","function":{"name":"fetch_document","description":"Fetch an allowlisted SEC filing document.","parameters":{"type":"object","properties":{"url":{"type":"string"}},"required":["url"]}}},
    {"type":"function","function":{"name":"find_relevant_pages","description":"Find high-scoring evidence chunks in an SEC filing.","parameters":{"type":"object","properties":{"url":{"type":"string"},"query":{"type":"string"},"max_pages":{"type":"integer"}},"required":["url","query"]}}},
    {"type":"function","function":{"name":"get_or_fetch_financials","description":"Store-first retrieval for Indian NSE/BSE financial results. Use this when the requested Indian company/period is not already in the local store. It automatically searches the exchanges, caches a matching filing PDF, and sends it through the existing deterministic extraction/ingestion pipeline.","parameters":{"type":"object","properties":{"entity":{"type":"string"},"period":{"type":"string"},"consolidated":{"type":"boolean","default":True},"exchange":{"type":"string","enum":["NSE","BSE","BOTH"],"default":"BOTH"}},"required":["entity"]}}},
]

TOOL_SCHEMAS = FINANCIAL_SCHEMAS + EVIDENCE_SCHEMAS + RETRIEVAL_SCHEMAS
DISPATCH = {
    "get_line_item": financial_tools.get_line_item,
    "list_available_periods": financial_tools.list_available_periods,
    "list_available_metrics": financial_tools.list_available_metrics,
    "calculate_metric": financial_tools.calculate_metric,
    "calculate_growth": financial_tools.calculate_growth,
    "calculate_return_ratio": financial_tools.calculate_return_ratio,
    "calculate_cagr": financial_tools.calculate_cagr,
    "run_validation_checks": financial_tools.run_validation_checks,
    "run_dcf": financial_tools.run_dcf,
    "get_evidence": evidence_tools.get_evidence,
    "compare_fact_candidates": evidence_tools.compare_fact_candidates,
    "compare_evidence": evidence_tools.compare_evidence,
    "search_filings": retrieval_tools.search_filings,
    "fetch_document": retrieval_tools.fetch_document,
    "find_relevant_pages": retrieval_tools.find_relevant_pages,
    "get_or_fetch_financials": retrieval_tools.get_or_fetch_financials,
}


def ask(question: str, *, entity: str, db_path: str = "data/financials.db",
        model: str = DEFAULT_MODEL, host: str = "http://localhost:11434") -> str:
    previous = os.environ.get("FINANCIAL_DB_PATH")
    os.environ["FINANCIAL_DB_PATH"] = db_path
    try:
        client = Client(host=host)
        prompt = SYSTEM_PROMPT + (
            f"\n\n# SESSION CONTEXT\nThe structured store for this session contains data for exactly one entity: "
            f"{entity!r}. Use this exact entity string for tool calls. Do not invent a different entity."
            "\nThis is a manually uploaded filing session. Consolidated/standalone scope was not captured by the web form, so do not force consolidated=true or consolidated=false for local evidence unless the user explicitly specifies the scope; leave the scope filter unset and report unknown scope when the stored evidence is unknown."
            "\nIf a requested Indian financial metric/period is unavailable locally, use get_or_fetch_financials before concluding UNAVAILABLE."
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": question},
        ]
        for _ in range(MAX_TURNS):
            response = client.chat(model=model, messages=messages, tools=TOOL_SCHEMAS,
                                    options={"temperature": 0.0})
            message = response["message"]
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                content = message.get("content", "")
                if "<think>" in content and "</think>" in content:
                    content = content.split("</think>", 1)[1].strip()
                return content
            for call in calls:
                name = call["function"]["name"]
                args = call["function"].get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                fn = DISPATCH.get(name)
                if fn is None:
                    result = {"status": "UNAVAILABLE", "reason": f"Unknown tool: {name}"}
                else:
                    try:
                        result = fn(**args)
                    except Exception as exc:
                        result = {"status": "UNAVAILABLE", "reason": str(exc)}
                messages.append({"role": "tool", "content": json.dumps(result, default=str)})
        return "UNAVAILABLE\nReason: Agent stopped after the maximum tool turns."
    finally:
        if previous is None:
            os.environ.pop("FINANCIAL_DB_PATH", None)
        else:
            os.environ["FINANCIAL_DB_PATH"] = previous
