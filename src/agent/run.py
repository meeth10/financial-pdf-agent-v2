"""Ollama financial agent backed by deterministic MCP tool implementations."""

from __future__ import annotations

import argparse
import sqlite3

from .runtime import DEFAULT_MODEL, ask as _ask, MAX_TURNS, TOOL_SCHEMAS, DISPATCH


def ask(conn: sqlite3.Connection, question: str, model: str = DEFAULT_MODEL,
        host: str = "http://localhost:11434", entity: str | None = None) -> str:
    """Backward-compatible wrapper around the MCP-backed runtime.

    The local Flask host should call src.agent.runtime.ask directly with an
    explicit db_path. CLI callers retain the historical connection-based API.
    """
    if entity is None:
        raise ValueError("entity is required for the MCP-backed agent runtime")
    row = conn.execute("PRAGMA database_list").fetchone()
    db_path = row[2] if row and row[2] else "data/financials.db"
    return _ask(question, entity=entity, db_path=db_path, model=model, host=host)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    parser.add_argument("question", nargs="+")
    parser.add_argument("--entity", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--host", default="http://localhost:11434")
    args = parser.parse_args()

    from src.store.schema import init_db
    conn = init_db(args.db_path)
    print(_ask(" ".join(args.question), entity=args.entity, db_path=args.db_path,
               model=args.model, host=args.host))
