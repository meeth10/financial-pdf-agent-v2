import sqlite3

from src.agent.derivation import calculate_metric, calculate_growth
from src.store.schema import SCHEMA
from mcp_servers.financial import tools


def make_db(tmp_path):
    db_path = tmp_path / "financials.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO documents (entity, doc_type, fiscal_year, filepath) VALUES (?, ?, ?, ?)",
                 ("TestCo", "annual_report", "FY2025", "test.pdf"))
    rows = [
        ("FY2025", "income_statement", "revenue", "Revenue", 1200, 10),
        ("FY2024", "income_statement", "revenue", "Revenue", 1000, 11),
        ("FY2025", "income_statement", "ebit", "EBIT", 1000, 12),
        ("FY2025", "income_statement", "depreciation", "Depreciation", 150, 12),
        ("FY2025", "income_statement", "amortisation", "Amortisation", 50, 12),
    ]
    for period, statement, metric, raw, value, page in rows:
        conn.execute("""INSERT INTO line_items
            (document_id, entity, period, statement, metric, metric_raw, value, unit,
             consolidated, source_page, source_table, extraction_method, extraction_confidence)
            VALUES (1, ?, ?, ?, ?, ?, ?, 'INR crore', 1, ?, 'test', 'test', 0.95)""",
                     ("TestCo", period, statement, metric, raw, value, page))
    conn.commit(); conn.close()
    return db_path


def test_financial_mcp_core_cases(tmp_path, monkeypatch):
    db_path = make_db(tmp_path)
    monkeypatch.setenv("FINANCIAL_DB_PATH", str(db_path))

    direct = tools.get_line_item("TestCo", "revenue", "FY2025", "income_statement", True)
    assert direct["status"] == "REPORTED" and direct["value"] == 1200

    ebitda = tools.calculate_metric("TestCo", "EBITDA", "FY2025", "income_statement", True)
    assert ebitda["status"] == "DERIVED" and ebitda["value"] == 1200
    assert ebitda["formula"]
    assert ebitda["input_provenance"]

    growth = tools.calculate_growth("TestCo", "revenue", "FY2025", "FY2024", "income_statement", True)
    assert growth["status"] == "DERIVED" and round(growth["value"], 2) == 20.0

    periods = tools.list_available_periods("TestCo")
    assert periods["periods"] == ["FY2024", "FY2025"]
