# V3 Phase 0-5 implementation status

Branch: `v3-phase0-5` (branched from `mcp-rewire`)

## Phase 0 — Baseline validation

Implemented:
- `mistral-small3.2:24b` is the runtime default.
- Benchmark manifest generation: `scripts/build_benchmark.py`.
- Benchmark runner: `scripts/run_benchmark.py`.
- Failure-stage labels for stored-evidence, derivation, extraction/discovery and agent reasoning.
- Benchmark documentation in `benchmarks/README.md`.

Not yet empirically completed in this environment:
- A 25–50 question run against one real annual report.
- A numeric stage-by-stage accuracy result.

The harness intentionally does not claim to distinguish discovery from extraction using SQLite alone; the extractor output must be inspected for that final split.

## Phase 1 — Extraction + Evidence/Financial MCP

Implemented:
- Title-gated statement discovery before expensive table scoring.
- ±2-page locality preference around discovered statement anchors, with a full-scan fallback for recall.
- Six-way evidence taxonomy exposed as `compare_fact_candidates` with the legacy `compare_evidence` alias.
- Provenance carried through evidence results.
- Standalone MCP stdio test harness.
- CI workflow for the full pytest suite on V3 branches.
- Canonical company financial profile as a downstream read model.
- Existing Flask UI uses the MCP-backed runtime path.

Not yet empirically completed:
- Verified standalone stdio execution on the user's exact local environment.
- Merged to `main`; this branch deliberately remains isolated until the empirical benchmark passes.

## Phase 2 — Retrieval MCP

Implemented:
- BSE/NSE-first architecture remains the default.
- Store-first retrieval with conservative scope matching.
- India retrieval provenance (`NSE_AUTO_RETRIEVED`, `BSE_AUTO_RETRIEVED`).
- Retrieval cache under `data/retrieval_cache/india/...`.
- Conservative XBRL/iXBRL parser for core statement facts.
- NSE iXBRL candidate detection and structured-data preference when an exchange filing exposes an iXBRL URL.
- Retrieval tests for store-hit behavior and iXBRL preference.

External-source validation remains a local-run task because CI should not depend on live NSE/BSE availability.

NSE's current public Financial Results page exposes an XBRL-to-Excel workflow, and its XBRL information page lists Regulation 33 Financial Results XBRL formats. The current integrated-filing iXBRL pages expose structured financial results and explicitly state consolidation scope. citeturn538405search0turn538405search10turn538405search3

## Phase 3 — Orchestration

Partially implemented:
- Agent runtime can call retrieval, evidence, financial and deterministic DCF tools in a single tool loop.
- The canonical company profile exists as the read model for downstream analytics.

Not yet empirically completed:
- 100-question benchmark.
- 95% exit-bar measurement.
- Fully automatic "unseen ticker → retrieval → extraction → evidence → financial → agent" validation across 5–10 fresh companies.

## Phase 4 — DCF

Implemented:
- Deterministic DCF engine in `src/valuation/dcf.py`.
- FCFF forecast, terminal value, EV → equity → per-share bridge.
- Direct WACC input or CAPM + debt-cost/capital-structure inputs.
- WACC × terminal-growth sensitivity matrix.
- DCF exposed through the Financial MCP and agent runtime.
- Unit tests for valuation bridge, terminal-growth validation and CAPM WACC.

Important guardrail: the DCF accepts explicit forecast assumptions; the LLM is not permitted to become the arithmetic engine.

## Phase 5 — Comps integration

Implemented:
- Interface audit of `meeth10/Comp_analysis` in `docs/COMPS_INTEGRATION.md`.
- HTTP adapter in `src/valuation/comps_adapter.py`.
- Normalized `ComparableCompany` representation.
- Period mismatch is preserved rather than silently mixed.
- Adapter unit test.

Pending:
- Live-service integration test against a running Comp_analysis instance with a populated peer set.
- Final platform-level peer-set schema once Phase 3 canonical company profile is empirically validated.

## Important engineering rule

Phase 4 and Phase 5 consume normalized evidence; they do not create a parallel financial truth source. The future Screener-style interface should read the same company profile/evidence model.

## Current exit state

The branch is a substantial Phase 0-5 implementation, but it is **not** being represented as having cleared the 95% Phase 3 bar. The remaining work is empirical validation on real filings and a live comps-engine instance, not another architectural rewrite.
