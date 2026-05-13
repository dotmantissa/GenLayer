"""Direct tests for sports_result_consensus.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("sports_result_consensus.py")


def _create_case(contract, direct_vm, tolerance=1):
    direct_vm.sender = ALICE
    return contract.create_match_case("nba", "lal-vs-bos-2026-05-13", "Lakers", "Celtics", tolerance)


def _mock_score_feeds_ok(direct_vm):
    direct_vm.mock_web(r"site\.api\.espn\.com/apis/site/v2/sports/.*/scoreboard", {"status": 200, "body": '{"events":[{"name":"LAL @ BOS"}]}'})
    direct_vm.mock_web(r"api\.sportsdata\.io/v3/.*/scores/json/Games", {"status": 200, "body": '[{"GameID":123}]'})
    direct_vm.mock_web(r"thesportsdb\.com/api/v1/json/3/searchfilename\.php", {"status": 200, "body": '{"event":[{"idEvent":"abc"}]}'})


def test_create_match_case_and_read(contract, direct_vm):
    case_id = _create_case(contract, direct_vm)
    case = json.loads(contract.get_match_case(case_id))

    assert case["status"] == "PENDING"
    assert case["league_code"] == "nba"


def test_create_match_case_invalid_tolerance(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="tolerance_points out of range"):
        contract.create_match_case("nba", "evt-1", "Lakers", "Celtics", 11)


def test_create_match_case_invalid_league(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid league_code"):
        contract.create_match_case("n", "evt-1", "Lakers", "Celtics", 1)


def test_resolve_match_case_home_wins(contract, direct_vm):
    case_id = _create_case(contract, direct_vm, tolerance=2)
    _mock_score_feeds_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a sports result verifier",
        {
            "home_score": 112,
            "away_score": 106,
            "winner": "HOME",
            "consensus_sources": 2,
            "reason": "two feeds align on final score",
        },
    )

    result = contract.resolve_match_case(case_id)
    case = json.loads(contract.get_match_case(case_id))

    assert result == "Lakers"
    assert case["status"] == "RESOLVED"
    assert case["home_score"] == 112
    assert case["away_score"] == 106
    assert case["winner"] == "Lakers"


def test_resolve_match_case_draw(contract, direct_vm):
    case_id = _create_case(contract, direct_vm, tolerance=1)
    _mock_score_feeds_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a sports result verifier",
        {
            "home_score": 1,
            "away_score": 1,
            "winner": "DRAW",
            "consensus_sources": 3,
            "reason": "all feeds report equal score",
        },
    )

    result = contract.resolve_match_case(case_id)
    assert result == "DRAW"


def test_resolve_requires_two_sources_minimum(contract, direct_vm):
    case_id = _create_case(contract, direct_vm)
    _mock_score_feeds_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a sports result verifier",
        {
            "home_score": 99,
            "away_score": 98,
            "winner": "HOME",
            "consensus_sources": 1,
            "reason": "single source observed",
        },
    )

    with pytest.raises(Exception, match="insufficient source consensus"):
        contract.resolve_match_case(case_id)


def test_provider_error_reverts(contract, direct_vm):
    case_id = _create_case(contract, direct_vm)
    direct_vm.mock_web(r"site\.api\.espn\.com/apis/site/v2/sports/.*/scoreboard", {"status": 500, "body": "err"})
    direct_vm.mock_web(r"api\.sportsdata\.io/v3/.*/scores/json/Games", {"status": 200, "body": "ok"})
    direct_vm.mock_web(r"thesportsdb\.com/api/v1/json/3/searchfilename\.php", {"status": 200, "body": "ok"})

    with pytest.raises(Exception, match="espn server error"):
        contract.resolve_match_case(case_id)


def test_cannot_resolve_twice(contract, direct_vm):
    case_id = _create_case(contract, direct_vm)
    _mock_score_feeds_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a sports result verifier",
        {
            "home_score": 105,
            "away_score": 100,
            "winner": "HOME",
            "consensus_sources": 2,
            "reason": "consensus reached",
        },
    )

    contract.resolve_match_case(case_id)
    with pytest.raises(Exception, match="case already resolved"):
        contract.resolve_match_case(case_id)


def test_missing_case_reverts(contract):
    with pytest.raises(Exception, match="case not found"):
        contract.get_match_case("999")
