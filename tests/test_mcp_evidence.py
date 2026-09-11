import sqlite3

from src.store.schema import SCHEMA
from mcp_servers.evidence.conflicts import classify_pair, classify_candidates
from mcp_servers.evidence import tools


def make_db(tmp_path):
    path = tmp_path / "evidence.db"
    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row; conn.executescript(SCHEMA)
    conn.execute("INSERT INTO documents (entity, doc_type, fiscal_year, filepath) VALUES ('TestCo','annual_report','FY2025','annual.pdf')")
    conn.execute("INSERT INTO documents (entity, doc_type, fiscal_year, filepath) VALUES ('TestCo','exchange_filing','FY2025','exchange.pdf')")
    vals = [
        (1, 'revenue', 1200, 'INR crore', 1, 'income_statement', 10),
        (2, 'revenue', 12000000000, 'INR absolute', 1, 'income_statement', 11),
    ]
    for doc_id, metric, value, unit, consolidated, statement, page in vals:
        conn.execute("""INSERT INTO line_items
            (document_id, entity, period, statement, metric, metric_raw, value, unit, consolidated, source_page,
             source_table, extraction_method, extraction_confidence)
            VALUES (?, 'TestCo','FY2025',?,?,?,?,?, ?,?, 'test','test',0.95)""",
            (doc_id, statement, metric, metric, value, unit, consolidated, page))
    conn.commit(); conn.close(); return path


def test_evidence_lookup_detects_unit_difference(tmp_path, monkeypatch):
    db_path = make_db(tmp_path); monkeypatch.setenv('FINANCIAL_DB_PATH', str(db_path))
    result = tools.get_evidence('TestCo','revenue','FY2025','income_statement',True)
    assert result['status'] == 'CONFLICTED'
    assert result['comparison']['overall'] == 'UNIT_DIFFERENCE'


def test_unit_difference_pair_is_agreement_in_absolute_terms():
    a = {'entity':'T','metric':'revenue','period':'FY2025','statement':'income_statement','consolidated':True,'value':1200,'unit':'INR crore'}
    b = {'entity':'T','metric':'revenue','period':'FY2025','statement':'income_statement','consolidated':True,'value':12000000000,'unit':'INR absolute'}
    assert classify_pair(a,b) == 'UNIT_DIFFERENCE'


def test_true_conflict_is_preserved():
    a = {'entity':'T','metric':'revenue','period':'FY2025','statement':'income_statement','consolidated':True,'value':1200,'unit':'INR crore'}
    b = {'entity':'T','metric':'revenue','period':'FY2025','statement':'income_statement','consolidated':True,'value':1300,'unit':'INR crore'}
    result = classify_candidates([a,b])
    assert result['overall'] == 'TRUE_CONFLICT'
    assert len(result['candidates']) == 2
