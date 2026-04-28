"""Direct tests for prediction_market_oracle.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
CAROL = "0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("prediction_market_oracle.py")


def _mock_resolution(direct_vm, outcome="YES", confidence=0.95):
    direct_vm.mock_web(r"example\.com/result", {"status": 200, "body": "Official outcome page text"})
    direct_vm.mock_llm(
        r"adjudicator for prediction markets",
        {"outcome": outcome, "confidence": confidence, "rationale": "criteria satisfied"},
    )


def _create_market(contract, direct_vm, close_ts):
    direct_vm.sender = ALICE
    contract.top_up_balance(200000)
    return contract.create_market(
        "Will candidate X win?",
        "https://example.com/result",
        "YES if official page states candidate X won",
        close_ts,
        100000,
        200,
    )


def test_create_market_and_buy_shares(contract, direct_vm):
    mid = _create_market(contract, direct_vm, 1704067200 + 7200)

    before = contract.get_price_yes(mid)
    direct_vm.sender = BOB
    contract.top_up_balance(50000)
    shares = contract.buy_shares(mid, True, 10000)
    after = contract.get_price_yes(mid)

    assert shares > 0
    assert after < before


def test_resolve_before_buffer_reverts(contract, direct_vm):
    close_ts = 1704067200 + 7200
    mid = _create_market(contract, direct_vm, close_ts)

    direct_vm.timestamp = close_ts + 3500
    _mock_resolution(direct_vm)
    with pytest.raises(Exception, match="resolution buffer not reached"):
        contract.resolve_market(mid)


def test_confident_resolution_finalizes(contract, direct_vm):
    close_ts = 1704067200 + 7200
    mid = _create_market(contract, direct_vm, close_ts)

    direct_vm.sender = BOB
    contract.top_up_balance(50000)
    contract.buy_shares(mid, True, 10000)

    direct_vm.timestamp = close_ts + 3601
    _mock_resolution(direct_vm, outcome="YES", confidence=0.95)
    status = contract.resolve_market(mid)

    m = json.loads(contract.get_market(mid))
    assert status == "RESOLVED"
    assert m["status"] == "RESOLVED"
    assert m["outcome"] == "YES"


def test_low_confidence_opens_dispute(contract, direct_vm):
    close_ts = 1704067200 + 7200
    mid = _create_market(contract, direct_vm, close_ts)

    direct_vm.timestamp = close_ts + 3601
    _mock_resolution(direct_vm, outcome="NO", confidence=0.70)
    status = contract.resolve_market(mid)

    m = json.loads(contract.get_market(mid))
    assert status == "DISPUTE"
    assert m["status"] == "DISPUTE"
    assert m["dispute_deadline"] == direct_vm.timestamp + 172800


def test_claim_payout_after_resolution(contract, direct_vm):
    close_ts = 1704067200 + 7200
    mid = _create_market(contract, direct_vm, close_ts)

    direct_vm.sender = BOB
    contract.top_up_balance(50000)
    contract.buy_shares(mid, True, 10000)

    direct_vm.sender = CAROL
    contract.top_up_balance(50000)
    contract.buy_shares(mid, False, 8000)

    direct_vm.timestamp = close_ts + 3601
    _mock_resolution(direct_vm, outcome="YES", confidence=0.97)
    contract.resolve_market(mid)

    direct_vm.sender = BOB
    payout = contract.claim_payout(mid)
    assert payout > 0


def test_safe_resolution_runs_three_passes(contract, direct_vm):
    close_ts = 1704067200 + 7200
    mid = _create_market(contract, direct_vm, close_ts)

    direct_vm.timestamp = close_ts + 3601
    _mock_resolution(direct_vm, outcome="YES", confidence=0.93)
    contract.resolve_market(mid)

    m = json.loads(contract.get_market(mid))
    assert m["confidence"] == 0.93
