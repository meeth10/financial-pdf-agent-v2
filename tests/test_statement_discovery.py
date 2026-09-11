from src.extraction.statement_discovery import (
    StatementCandidate,
    _local_pages,
    _rank_key,
)


def test_locality_window_is_plus_minus_two_pages():
    assert _local_pages([100], 200, radius=2) == {98, 99, 100, 101, 102}


def test_local_candidate_beats_higher_scoring_distant_candidate():
    local = StatementCandidate("income_statement", 101, 15.0, (), "")
    distant = StatementCandidate("income_statement", 140, 19.0, (), "")
    pages = _local_pages([100], 200, radius=2)
    assert _rank_key(local, pages) > _rank_key(distant, pages)
