# Comps engine integration audit

Audited repository: `meeth10/Comp_analysis`.

The current interface is a local FastAPI service. The main app exposes:

- `GET /companies` — company identity list with id, name, ticker and sector.
- `GET /companies/{company_id}/financials` — normalized financial table for a company.
- `GET /comparisons/build?company_ids=...&period_type=...&fiscal_year=...&quarter=...` — deterministic peer comparison and valuation payload.
- `GET /companies/{company_id}/validations` — validation events.
- `GET /documents` — ingested documents.

The comparison payload contains normalized fundamental metrics such as revenue growth, EBITDA margin, ROE, ROIC, free cash flow, net debt/EBITDA, plus valuation metrics such as P/E, EV/EBITDA, EV/EBIT, EV/Sales, P/B and FCF yield.

The platform integration therefore uses HTTP rather than importing the external repository's SQLite or Python internals. `src/valuation/comps_adapter.py` converts its comparison payload into a stable `ComparableCompany` model.

## Canonical integration rule

The comps engine remains a calculation/data service. The stock-analysis platform owns company identity, filing provenance, and the canonical evidence store. A peer comparison must not silently mix periods; the adapter preserves the external engine's `period_mismatch` field.

## Phase 5 status

- Interface audited: complete.
- Adapter implemented: complete.
- Canonical normalization test: complete.
- Live service integration test: pending a locally running Comp_analysis instance and real peer dataset.

The external repo is deliberately not copied into this repository.
