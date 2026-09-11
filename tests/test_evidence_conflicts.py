from mcp_servers.evidence.conflicts import classify_pair, classify_candidates


def base(**overrides):
    record = {
        "entity": "TEST CO",
        "metric": "revenue",
        "period": "FY2025",
        "statement": "income_statement",
        "consolidated": True,
        "value": 100.0,
        "unit": "INR crore",
    }
    record.update(overrides)
    return record


def test_six_way_conflict_taxonomy():
    assert classify_pair(base(), base()) == "AGREES"
    assert classify_pair(base(value=100.0, unit="INR crore"), base(value=100.3, unit="INR crore")) == "ROUNDING_DIFFERENCE"
    assert classify_pair(base(value=100.0, unit="INR crore"), base(value=1000.0, unit="INR million")) == "UNIT_DIFFERENCE"
    assert classify_pair(base(), base(consolidated=False)) == "SCOPE_DIFFERENCE"
    assert classify_pair(base(value=100.0), base(value=140.0, restated=True)) == "RESTATED"
    assert classify_pair(base(), base(value=140.0)) == "TRUE_CONFLICT"


def test_candidates_preserve_all_pairs_and_priority():
    result = classify_candidates([
        base(value=100.0),
        base(value=100.2),
        base(value=140.0),
    ])
    assert len(result["comparisons"]) == 3
    assert result["overall"] == "TRUE_CONFLICT"
