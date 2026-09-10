import sqlite3

from mcp_servers.retrieval.sources_nse import _period_from_text as nse_period
from mcp_servers.retrieval.sources_bse import _period_from_text as bse_period, _result_rows
from src.store.db import LineItem, add_document, add_line_item
from src.store.schema import init_db


def test_nse_period_mapping():
    assert nse_period("financial results for the year ended March 31, 2025") == "FY2025"
    assert nse_period("results for the quarter ended June 30, 2025") == "Q1FY2026"
    assert nse_period("quarter ended September 30, 2025") == "Q2FY2026"
    assert nse_period("quarter ended December 31, 2025") == "Q3FY2026"


def test_bse_period_mapping():
    assert bse_period("2024-2025 Consolidated-Mar-25 Year New") == "FY2025"
    assert bse_period("2025-2026 Consolidated-Jun-25 Quarter New") is None


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
