"""Ollama tool-calling agent over the structured financial store."""

import argparse
import json
import sqlite3

from ollama import Client

from .system_prompt import SYSTEM_PROMPT
from .tools import TOOL_SCHEMAS, DISPATCH

# Default local model for financial-language tool calling.
DEFAULT_MODEL = "mistral-small3.2:24b"
MAX_TURNS = 8


def ask(conn: sqlite3.Connection, question: str, model: str = DEFAULT_MODEL,
        host: str = "http://localhost:11434", entity: str | None = None) -> str:
    client = Client(host=host)
    system_prompt = SYSTEM_PROMPT
    if entity:
        system_prompt += (
            f"\n\n# SESSION CONTEXT\nThe structured store for this session contains data for "
            f"exactly one entity: \"{entity}\". Use this exact string as the `entity` argument "
            f"for every tool call unless the question explicitly names a different company.\n"
        )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    for _ in range(MAX_TURNS):
        response = client.chat(
            model=model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            options={"temperature": 0.0},
        )
        message = response["message"]
        messages.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            content = message.get("content", "")
            if "<think>" in content and "</think>" in content:
                content = content.split("</think>", 1)[1].strip()
            return content

        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            fn = DISPATCH.get(name)
            if fn is None:
                result = {"status": "error", "message": f"unknown tool {name}"}
            else:
                try:
                    result = fn(conn, **args)
                except Exception as exc:
                    result = {"status": "error", "message": str(exc)}
            messages.append({"role": "tool", "content": json.dumps(result, default=str)})

    return "Stopped after max turns without a final answer."


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    parser.add_argument("question", nargs="+")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--host", default="http://localhost:11434")
    args = parser.parse_args()

    from src.store.schema import init_db
    conn = init_db(args.db_path)
    print(ask(conn, " ".join(args.question), model=args.model, host=args.host))
