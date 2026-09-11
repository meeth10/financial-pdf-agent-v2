import requests

from mcp_servers.retrieval import tools
from mcp_servers.retrieval.sources_nse import _period_from_text as nse_period
from mcp_servers.retrieval.sources_nse import _resolve_symbol
from mcp_servers.retrieval.sources_bse import _period_from_text as bse_period, _result_rows
from src.store.db import LineItem, add_document, add_line_item
from src.store.schema import init_db


def test_nse_period_mapping():
    assert nse_period("financial results for the year ended March 31, 2025") == "FY2025"
    assert nse_period("results for the quarter ended June 30, 2025") == "Q1FY2026"
    assert nse_period("quarter ended September 30, 2025") == "Q2FY2026"
    assert nse_period("quarter ended December 31, 2025") == "Q3FY2026"


def test_nse_symbol_fallback_when_autocomplete_is_unavailable():
    response = requests.Response()
    response.status_code = 404
    response.url = "https://www.nseindia.com/api/search/autocomplete?q=BHARTIHEXA"

    class FakeSession:
        def get(self, *args, **kwargs):
            return response

    resolved = _resolve_symbol(FakeSession(), "BHARTIHEXA")
    assert resolved == {"symbol": "BHARTIHEXA", "symbol_info": "BHARTIHEXA"}


def test_bse_period_mapping():
    assert bse_period("2024-2025 Consolidated-Mar-25 Year New") == "FY2025"
    assert bse_period("2025-2026 Consolidated-Jun-25 Quarter New") == "Q1FY2026"
    assert bse_period("2025-2026 Consolidated-Sep-25 Quarter New") == "Q2FY2026"
    assert bse_period("2025-2026 Consolidated-Dec-25 Quarter New") == "Q3FY2026"


def test_bse_result_row_parses_pdf_link_and_scope():
    html = '''<table><tr><td>2024-2025</td><td>Consolidated-Mar-25</td><td>Year</td>
      <td><a href="https://www.bseindia.com/xml-data/corpfiling/AttachLive/example.pdf">View</a></td></tr></table>'''
    rows = _result_rows(html)
    assert len(rows) == 1
    assert rows[0]["pdfs"][0].endswith("example.pdf")
    assert "Consolidated" in rows[0]["text"]


def test_source_type_migration_and_roundtrip(tmp_path):
    db = tmp_path / "financials.db"
    conn = init_db(str(db))
    document_id = add_document(conn, "HDFC BANK", "sebi_quarterly", "Q1FY2026", "x.pdf", "NSE_AUTO_RETRIEVED")
    item_id = add_line_item(conn, document_id, LineItem(
        entity="HDFC BANK", period="Q1FY2026", statement="income_statement",
        metric="revenue", metric_raw="Revenue", value=100.0, unit="INR crore",
        consolidated=True, source_page=3, source_table="results", extraction_method="camelot_stream",
        extraction_confidence=0.91, source_type="NSE_AUTO_RETRIEVED",
    ))
    row = conn.execute("SELECT source_type FROM line_items WHERE id = ?", (item_id,)).fetchone()
    doc = conn.execute("SELECT source_type FROM documents WHERE id = ?", (document_id,)).fetchone()
    conn.close()
    assert row[0] == "NSE_AUTO_RETRIEVED"
    assert doc[0] == "NSE_AUTO_RETRIEVED"


def test_store_hit_avoids_network(tmp_path, monkeypatch):
    db = tmp_path / "financials.db"
    conn = init_db(str(db))
    doc_id = add_document(conn, "HDFC BANK", "sebi_annual", "FY2025", "x.pdf", "BSE_AUTO_RETRIEVED")
    add_line_item(conn, doc_id, LineItem(
        entity="HDFC BANK", period="FY2025", statement="income_statement",
        metric="revenue", metric_raw="Revenue", value=200.0, unit="INR crore",
        consolidated=True, source_page=2, source_table="results", extraction_method="camelot_stream",
        extraction_confidence=0.9, source_type="BSE_AUTO_RETRIEVED",
    ))
    conn.close()
    monkeypatch.setattr(tools, "_db_path", lambda: str(db))
    monkeypatch.setattr(tools, "_search_india", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network search should not run")))
    result = tools.get_or_fetch_financials("HDFC BANK", "FY2025", consolidated=True)
    assert result["status"] == "STORE_HIT"
    assert result["source_type"] == "LOCAL_STORE"


def test_ixbrl_is_preferred_over_pdf_for_same_scope_and_period(tmp_path, monkeypatch):
    db = tmp_path / "financials.db"
    monkeypatch.setattr(tools, "_db_path", lambda: str(db))
    candidates = [
        {"company": "TEST CO", "period": "FY2025", "filing_date": "2026-06-01", "url": "https://nsearchives.nseindia.com/corporate/result.pdf", "format": "PDF", "exchange": "NSE", "scope_asserted": True},
        {"company": "TEST CO", "period": "FY2025", "filing_date": "2026-06-01", "url": "https://nsearchives.nseindia.com/corporate/ixbrl/INTEGRATED_FILING_INDAS_TEST_iXBRL_WEB.html", "format": "IXBRL", "exchange": "NSE", "scope_asserted": True},
    ]
    monkeypatch.setattr(tools, "_search_india", lambda *args, **kwargs: (candidates, []))
    monkeypatch.setattr(tools, "_download_attachment", lambda url, exchange, format_name: tmp_path / ("result.html" if format_name == "IXBRL" else "result.pdf"))
    monkeypatch.setattr(tools, "ingest_xbrl", lambda *args, **kwargs: {"line_items_stored": 3, "facts_found": 3})
    result = tools.get_or_fetch_financials("TEST CO", "FY2025", consolidated=True, exchange="NSE")
    assert result["status"] == "FETCHED_AND_INGESTED"
    assert result["source_format"] == "IXBRL"


def test_scope_can_be_resolved_from_pdf_content(tmp_path, monkeypatch):
    import fitz

    pdf_path = tmp_path / "scope.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Consolidated financial results\nYear ended March 31, 2026")
    document.save(str(pdf_path))
    document.close()

    candidates = [{
        "company": "TEST CO", "period": "FY2026", "filing_date": "2026-05-15",
        "url": "https://nsearchives.nseindia.com/corporate/result.pdf", "format": "PDF",
        "exchange": "NSE", "scope_asserted": None, "subject": "Financial Result Updates",
    }]
    monkeypatch.setattr(tools, "_download_attachment", lambda url, exchange, format_name: pdf_path)
    selected = tools._scope_from_document(candidates, True, "FY2026")
    assert selected is not None
    assert selected["scope_asserted"] is True
    assert selected["scope_source"] == "FILING_SECTION_HEADING"


def test_combined_filing_can_resolve_requested_section_scope(tmp_path):
    import fitz

    pdf_path = tmp_path / "combined_scope.pdf"
    document = fitz.open()
    cover = document.new_page()
    cover.insert_text((72, 72), "Integrated filing including unaudited standalone and consolidated financial results")
    consolidated = document.new_page()
    consolidated.insert_text((72, 72), "CONSOLIDATED FINANCIAL RESULTS")
    standalone = document.new_page()
    standalone.insert_text((72, 72), "STANDALONE FINANCIAL RESULTS")
    document.save(str(pdf_path))
    document.close()

    scopes = tools._pdf_scopes(pdf_path)
    assert scopes == {True, False}

    candidate = {
        "company": "HDFC BANK", "period": "Q3FY2025", "filing_date": "2025-01-22",
        "url": "https://nsearchives.nseindia.com/corporate/hdfcbank.pdf", "format": "PDF",
        "exchange": "NSE", "scope_asserted": None, "subject": "Integrated Filing- Financial",
    }
    selected = tools._scope_from_document([candidate], True, "Q3FY2025")
    assert selected is not None
    assert selected["scope_asserted"] is True
    assert selected["scope_source"] == "FILING_SECTION_HEADING"
