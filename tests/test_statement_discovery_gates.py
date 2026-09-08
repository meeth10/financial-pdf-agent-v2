from src.extraction.statement_discovery import _gate2, _gate3, _match_title


def test_title_gate_requires_statement_heading():
    assert _match_title("Management discussion: total assets increased", "balance_sheet") is None
    assert _match_title("CONSOLIDATED\nBALANCE SHEETS", "balance_sheet") == "CONSOLIDATED BALANCE SHEETS"


def test_gate2_requires_financial_rows():
    rows = [
        ["Cash and cash equivalents", "100", "80"],
        ["Total Assets", "500", "450"],
        ["Total Liabilities", "300", "270"],
    ]
    labels, structured = _gate2(rows, "balance_sheet")
    assert labels >= 3
    assert structured == 3


def test_gate3_requires_two_consistent_numeric_columns():
    rows = [
        ["Cash and cash equivalents", "100", "80"],
        ["Total Assets", "500", "450"],
        ["Total Liabilities", "300", "270"],
    ]
    columns, garbage = _gate3(rows)
    assert columns >= 2
    assert garbage == 0


def test_gate3_rejects_collapsed_single_column_table():
    rows = [
        ["Cash and cash equivalents 100 80"],
        ["Total Assets 500 450"],
        ["Total Liabilities 300 270"],
    ]
    columns, garbage = _gate3(rows)
    assert columns == 0
