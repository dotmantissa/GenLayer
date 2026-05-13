"""Direct tests for dao_governance_health.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("dao_governance_health.py")


def _create(contract, direct_vm, unique=50, top_share=35):
    direct_vm.sender = ALICE
    return contract.create_analysis("arbitrum", "ethereum", 30, unique, top_share)


def _mock_analysis(direct_vm, health="WATCHLIST", unique_voters=40, top_share=45, risk="MEDIUM"):
    direct_vm.mock_web(r"tally", {"status": 200, "body": "tally governance data"})
    direct_vm.mock_web(r"snapshot", {"status": 200, "body": "snapshot governance data"})
    direct_vm.mock_llm(
        r"You are a governance health analyst",
        {
            "health": health,
            "unique_voters": unique_voters,
            "top_delegate_share_pct": top_share,
            "coalition_capture_risk": risk,
            "reason": "voting concentration analysis",
        },
    )


def test_create_analysis_and_read(contract, direct_vm):
    aid = _create(contract, direct_vm)
    a = json.loads(contract.get_analysis(aid))

    assert a["status"] == "PENDING"
    assert a["dao_slug"] == "arbitrum"


def test_create_analysis_invalid_slug(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid dao slug"):
        contract.create_analysis("a", "ethereum", 30, 10, 40)


def test_create_analysis_invalid_bounds(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="lookback_days out of range"):
        contract.create_analysis("dao", "ethereum", 0, 10, 40)


def test_resolve_captured_when_thresholds_fail(contract, direct_vm):
    aid = _create(contract, direct_vm, unique=100, top_share=20)
    _mock_analysis(direct_vm, health="HEALTHY", unique_voters=30, top_share=70, risk="HIGH")

    out = contract.resolve_analysis(aid)
    a = json.loads(contract.get_analysis(aid))

    assert out == "CAPTURED"
    assert a["health"] == "CAPTURED"
    assert contract.get_latest_health("arbitrum") == "CAPTURED"


def test_resolve_healthy_when_thresholds_pass(contract, direct_vm):
    aid = _create(contract, direct_vm, unique=30, top_share=50)
    _mock_analysis(direct_vm, health="WATCHLIST", unique_voters=80, top_share=20, risk="LOW")

    out = contract.resolve_analysis(aid)
    assert out == "HEALTHY"


def test_resolve_watchlist_when_mixed(contract, direct_vm):
    aid = _create(contract, direct_vm, unique=50, top_share=40)
    _mock_analysis(direct_vm, health="WATCHLIST", unique_voters=55, top_share=50, risk="MEDIUM")

    out = contract.resolve_analysis(aid)
    assert out == "WATCHLIST"


def test_any_account_can_resolve_but_health_maps_to_dao(contract, direct_vm):
    aid = _create(contract, direct_vm, unique=20, top_share=60)
    _mock_analysis(direct_vm, health="HEALTHY", unique_voters=25, top_share=50, risk="LOW")

    direct_vm.sender = BOB
    out = contract.resolve_analysis(aid)

    assert out == "HEALTHY"
    assert contract.get_latest_health("arbitrum") == "HEALTHY"


def test_provider_error_reverts(contract, direct_vm):
    aid = _create(contract, direct_vm)
    direct_vm.mock_web(r"tally", {"status": 404, "body": "missing"})
    direct_vm.mock_web(r"snapshot", {"status": 200, "body": "ok"})

    with pytest.raises(Exception, match="tally client error"):
        contract.resolve_analysis(aid)


def test_cannot_resolve_twice(contract, direct_vm):
    aid = _create(contract, direct_vm)
    _mock_analysis(direct_vm, health="WATCHLIST", unique_voters=50, top_share=45, risk="MEDIUM")
    contract.resolve_analysis(aid)

    with pytest.raises(Exception, match="analysis already resolved"):
        contract.resolve_analysis(aid)


def test_missing_analysis_reverts(contract):
    with pytest.raises(Exception, match="analysis not found"):
        contract.get_analysis("999")
