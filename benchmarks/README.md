# Financial Agent Benchmark

This benchmark is designed for Phase 0 and reused after Phase 1/2/3.

## Workflow

1. Run the web extractor on one real annual report and ingest it into `data/financials.db`.
2. Build a deterministic benchmark from the normalized store:

```bash
python scripts/build_benchmark.py --db data/financials.db --entity "HDFC BANK" --period FY2025 --count 40 --output benchmarks/generated.json
```

3. Run the agent against the generated questions:

```bash
python scripts/run_benchmark.py --db data/financials.db --benchmark benchmarks/generated.json --output benchmarks/results.json
```

## Failure stages

- `agent_reasoning`: the expected value exists in the evidence store, but the agent does not return the correct status/value.
- `derivation`: the requested case is a deterministic derived metric and the deterministic engine disagrees with the agent result.
- `extraction_or_discovery`: the benchmark case cannot be built because the underlying filing did not produce the required line item. This requires review against the extractor's statement-discovery output to split the failure into discovery vs extraction.
- `pass`: correct evidence/status/value.

The benchmark deliberately does not pretend it can distinguish discovery from extraction from SQLite alone. Use the extractor JSON emitted by `/extract` or `extract_financial_statements()` to make that final split.

## Scoring

Numeric answers are compared using a relative tolerance of 0.5% by default. Status mismatches always fail. Conflict and unavailable cases are scored by status rather than a guessed number.
