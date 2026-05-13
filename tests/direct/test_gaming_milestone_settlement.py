"""Direct tests for gaming_milestone_settlement.py."""

import json
import pytest

SPONSOR = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
RECIPIENT = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("gaming_milestone_settlement.py")


def _create_case(contract, direct_vm, target=50, payout=1000):
    direct_vm.sender = SPONSOR
    contract.top_up_balance(5000)
    return contract.create_case(
        "steam",
        "570",
        "76561198000000000",
        "achievements",
        target,
        payout,
        RECIPIENT,
    )


def _mock_platform_ok(direct_vm):
    direct_vm.mock_web(r"api\.steampowered\.com/ISteamUserStats/GetPlayerAchievements", {"status": 200, "body": '{"ok":true}'})
    direct_vm.mock_web(r"api\.steampowered\.com/ISteamUserStats/GetUserStatsForGame", {"status": 200, "body": '{"ok":true}'})


def test_create_case_happy_path(contract, direct_vm):
    case_id = _create_case(contract, direct_vm)
    case = json.loads(contract.get_case(case_id))

    assert case["status"] == "PENDING"
    assert contract.balance_of(SPONSOR) == 4000


def test_create_case_invalid_platform(contract, direct_vm):
    direct_vm.sender = SPONSOR
    contract.top_up_balance(1000)

    with pytest.raises(Exception, match="unsupported platform"):
        contract.create_case("switch", "570", "player", "achievements", 10, 100, RECIPIENT)


def test_create_case_invalid_milestone_type(contract, direct_vm):
    direct_vm.sender = SPONSOR
    contract.top_up_balance(1000)

    with pytest.raises(Exception, match="invalid milestone_type"):
        contract.create_case("steam", "570", "player", "wins", 10, 100, RECIPIENT)


def test_create_case_insufficient_balance(contract, direct_vm):
    direct_vm.sender = SPONSOR

    with pytest.raises(Exception, match="insufficient sponsor balance"):
        contract.create_case("steam", "570", "player", "achievements", 10, 100, RECIPIENT)


def test_resolve_paid_when_milestone_met(contract, direct_vm):
    case_id = _create_case(contract, direct_vm, target=50, payout=1200)
    _mock_platform_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a gaming milestone adjudicator",
        {
            "milestone_met": True,
            "measured_value": 58,
            "consensus_sources": 2,
            "reason": "both sources confirm achievement count",
        },
    )

    out = contract.resolve_case(case_id)
    case = json.loads(contract.get_case(case_id))

    assert out == "PAID"
    assert case["status"] == "PAID"
    assert contract.balance_of(RECIPIENT) == 1200


def test_resolve_not_met_refunds_sponsor(contract, direct_vm):
    case_id = _create_case(contract, direct_vm, target=80, payout=1000)
    _mock_platform_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a gaming milestone adjudicator",
        {
            "milestone_met": False,
            "measured_value": 40,
            "consensus_sources": 2,
            "reason": "threshold not reached",
        },
    )

    out = contract.resolve_case(case_id)

    assert out == "NOT_MET"
    assert contract.balance_of(SPONSOR) == 5000


def test_resolve_provider_error(contract, direct_vm):
    case_id = _create_case(contract, direct_vm)
    direct_vm.mock_web(r"api\.steampowered\.com/ISteamUserStats/GetPlayerAchievements", {"status": 500, "body": "err"})
    direct_vm.mock_web(r"api\.steampowered\.com/ISteamUserStats/GetUserStatsForGame", {"status": 200, "body": "ok"})

    with pytest.raises(Exception, match="primary server error"):
        contract.resolve_case(case_id)


def test_resolve_insufficient_source_consensus(contract, direct_vm):
    case_id = _create_case(contract, direct_vm)
    _mock_platform_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a gaming milestone adjudicator",
        {
            "milestone_met": True,
            "measured_value": 90,
            "consensus_sources": 0,
            "reason": "no agreement",
        },
    )

    with pytest.raises(Exception, match="insufficient source consensus"):
        contract.resolve_case(case_id)


def test_cannot_resolve_twice(contract, direct_vm):
    case_id = _create_case(contract, direct_vm)
    _mock_platform_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a gaming milestone adjudicator",
        {
            "milestone_met": True,
            "measured_value": 60,
            "consensus_sources": 2,
            "reason": "confirmed",
        },
    )

    contract.resolve_case(case_id)
    with pytest.raises(Exception, match="case already resolved"):
        contract.resolve_case(case_id)


def test_missing_case_reverts(contract):
    with pytest.raises(Exception, match="case not found"):
        contract.get_case("999")
