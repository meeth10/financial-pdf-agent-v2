"""Shared MCP configuration."""

from __future__ import annotations

import os


def env(name: str, default: str) -> str:
    return os.getenv(name, default)


FINANCIAL_DB_PATH = env("FINANCIAL_DB_PATH", "data/financials.db")
MCP_RETRIEVAL_CACHE = env("MCP_RETRIEVAL_CACHE", "data/retrieval_cache")
