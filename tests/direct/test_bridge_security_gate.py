"""Direct tests for bridge_security_gate.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("bridge_security_gate.py")


def _create(contract, direct_vm, min_tvl=1_000_000, max_incidents=1, lookback=365):
    direct_vm.sender = ALICE
    return contract.create_assessment("stargate", min_tvl, max_incidents, lookback)


def _mock_sources_ok(direct_vm):
    direct_vm.mock_web(r"l2beat\.com/api/scaling/tvs", {"status": 200, "body": '{"ok":true}'})
    direct_vm.mock_web(r"l2beat\.com/api/bridges", {"status": 200, "body": '{"ok":true}'})
    direct_vm.mock_web(r"api\.llama\.fi/bridges", {"status": 200, "body": '{"ok":true}'})
    direct_vm.mock_web(r"api\.llama\.fi/hacks", {"status": 200, "body": '{"ok":true}'})


def test_create_and_read_assessment(contract, direct_vm):
    aid = _create(contract, direct_vm)
    a = json.loads(contract.get_assessment(aid))

    assert a["status"] == "PENDING"
    assert a["bridge_key"] == "stargate"


def test_create_invalid_bridge_key(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid bridge_key"):
        contract.create_assessment("s", 100, 1, 30)


def test_create_invalid_min_tvl(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="min_tvl_usd must be positive"):
        contract.create_assessment("stargate", 0, 1, 30)


def test_resolve_allow_when_thresholds_satisfied(contract, direct_vm):
    aid = _create(contract, direct_vm, min_tvl=1_000_000, max_incidents=2, lookback=365)
    _mock_sources_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a bridge security analyst",
        {
            "decision": "ALLOW",
            "tvl_usd": 5_000_000,
            "recent_incidents": 1,
            "consensus_sources": 3,
            "reason": "healthy posture",
        },
    )

    out = contract.resolve_assessment(aid)
    assert out == "ALLOW"
    assert contract.is_bridge_allowed("stargate") is True


def test_resolve_block_when_incidents_exceed_limit(contract, direct_vm):
    aid = _create(contract, direct_vm, min_tvl=1_000_000, max_incidents=0, lookback=365)
    _mock_sources_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a bridge security analyst",
        {
            "decision": "ALLOW",
            "tvl_usd": 7_000_000,
            "recent_incidents": 2,
            "consensus_sources": 3,
            "reason": "incidents still too high",
        },
    )

    out = contract.resolve_assessment(aid)
    assert out == "BLOCK"
    assert contract.is_bridge_allowed("stargate") is False


def test_resolve_block_when_consensus_too_low(contract, direct_vm):
    aid = _create(contract, direct_vm)
    _mock_sources_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a bridge security analyst",
        {
            "decision": "ALLOW",
            "tvl_usd": 8_000_000,
            "recent_incidents": 0,
            "consensus_sources": 1,
            "reason": "single source confidence only",
        },
    )

    out = contract.resolve_assessment(aid)
    assert out == "BLOCK"


def test_provider_error_reverts(contract, direct_vm):
    aid = _create(contract, direct_vm)
    direct_vm.mock_web(r"l2beat\.com/api/scaling/tvs", {"status": 500, "body": "err"})
    direct_vm.mock_web(r"l2beat\.com/api/bridges", {"status": 200, "body": "ok"})
    direct_vm.mock_web(r"api\.llama\.fi/bridges", {"status": 200, "body": "ok"})
    direct_vm.mock_web(r"api\.llama\.fi/hacks", {"status": 200, "body": "ok"})

    with pytest.raises(Exception, match="l2beat_tvl server error"):
        contract.resolve_assessment(aid)


def test_cannot_resolve_twice(contract, direct_vm):
    aid = _create(contract, direct_vm)
    _mock_sources_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a bridge security analyst",
        {
            "decision": "BLOCK",
            "tvl_usd": 500_000,
            "recent_incidents": 2,
            "consensus_sources": 2,
            "reason": "risk high",
        },
    )

    contract.resolve_assessment(aid)
    with pytest.raises(Exception, match="assessment already resolved"):
        contract.resolve_assessment(aid)


def test_missing_assessment_reverts(contract):
    with pytest.raises(Exception, match="assessment not found"):
        contract.get_assessment("999")
