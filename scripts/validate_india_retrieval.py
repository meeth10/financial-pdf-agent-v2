"""Live smoke test for 5-10 previously unseen Indian companies.

This intentionally runs outside CI because NSE/BSE availability and throttling
are external dependencies.
"""

from __future__ import annotations

import argparse
import json

from mcp_servers.retrieval.tools import get_or_fetch_financials

DEFAULT_TICKERS = [
    "BHARTIHEXA", "HDFCBANK", "RELIANCE", "INFY", "ICICIBANK",
    "TCS", "LT", "AXISBANK", "INDIGO", "MARUTI",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="*", default=DEFAULT_TICKERS)
    parser.add_argument("--period", default=None)
    parser.add_argument("--exchange", choices=["NSE", "BSE", "BOTH"], default="BOTH")
    args = parser.parse_args()

    results = []
    for ticker in args.tickers[:10]:
        result = get_or_fetch_financials(ticker, args.period, consolidated=True, exchange=args.exchange)
        results.append({
            "ticker": ticker,
            "status": result.get("status"),
            "period": result.get("period"),
            "source_format": result.get("source_format"),
            "source_type": result.get("source_type"),
            "reason": result.get("reason"),
        })
        print(json.dumps(results[-1], ensure_ascii=False))

    found = sum(1 for row in results if row["status"] in {"FETCHED_AND_INGESTED", "STORE_HIT"})
    print(json.dumps({"tested": len(results), "successful": found, "results": results}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
