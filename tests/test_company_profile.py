from src.analytics.company_profile import build_company_profile
from src.store.db import LineItem, add_document, add_line_item
from src.store.schema import init_db


def test_company_profile_is_a_read_model_over_stored_evidence(tmp_path):
    db = tmp_path / "financials.db"
    conn = init_db(str(db))
    doc_id = add_document(conn, "TEST CO", "annual_report", "FY2025", "filing.pdf", "MANUAL_UPLOAD")
    add_line_item(conn, doc_id, LineItem(
        entity="TEST CO", period="FY2025", statement="income_statement",
        metric="revenue", metric_raw="Revenue", value=1000, unit="INR crore",
        consolidated=None, source_page=12, source_table="P&L",
        extraction_method="test", extraction_confidence=0.99,
    ))
    profile = build_company_profile(conn, "TEST CO")
    conn.close()
    assert profile["periods"] == ["FY2025"]
    revenue = profile["profile"][0]["statements"]["income_statement"]["revenue"]
    assert revenue["value"] == 1000
    assert revenue["source_page"] == 12
    assert revenue["source_type"] == "MANUAL_UPLOAD"
