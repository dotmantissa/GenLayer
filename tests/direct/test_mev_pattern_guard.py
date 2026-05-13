"""Direct tests for mev_pattern_guard.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("mev_pattern_guard.py")


def _create(contract, direct_vm, max_patterns=0, min_conf=70):
    direct_vm.sender = ALICE
    return contract.create_case("0xabc123bundle", max_patterns, min_conf)


def _mock_sources_ok(direct_vm):
    direct_vm.mock_web(r"blocks\.flashbots\.net/v1/bundles", {"status": 200, "body": '{"ok":true}'})
    direct_vm.mock_web(r"api\.mevboost\.org/v1/batch", {"status": 200, "body": '{"ok":true}'})


def test_create_case_and_read(contract, direct_vm):
    cid = _create(contract, direct_vm)
    c = json.loads(contract.get_case(cid))

    assert c["status"] == "PENDING"
    assert c["batch_id"] == "0xabc123bundle"


def test_create_case_invalid_batch(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid batch_id"):
        contract.create_case("ab", 0, 70)


def test_create_case_invalid_bounds(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="max_harmful_patterns out of range"):
        contract.create_case("0xvalid", -1, 70)

    with pytest.raises(Exception, match="min_confidence out of range"):
        contract.create_case("0xvalid", 1, 101)


def test_resolve_harmful(contract, direct_vm):
    cid = _create(contract, direct_vm, max_patterns=1, min_conf=70)
    _mock_sources_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a MEV abuse analyst",
        {
            "verdict": "HARMFUL",
            "harmful_patterns": 3,
            "confidence": 88,
            "consensus_sources": 2,
            "reason": "clear sandwich sequence",
        },
    )

    out = contract.resolve_case(cid)
    assert out == "HARMFUL"
    assert contract.get_latest_verdict("0xabc123bundle") == "HARMFUL"


def test_resolve_safe_when_threshold_not_exceeded(contract, direct_vm):
    cid = _create(contract, direct_vm, max_patterns=4, min_conf=70)
    _mock_sources_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a MEV abuse analyst",
        {
            "verdict": "HARMFUL",
            "harmful_patterns": 2,
            "confidence": 95,
            "consensus_sources": 2,
            "reason": "patterns below limit",
        },
    )

    out = contract.resolve_case(cid)
    assert out == "SAFE"


def test_resolve_safe_on_no_consensus(contract, direct_vm):
    cid = _create(contract, direct_vm, max_patterns=0, min_conf=70)
    _mock_sources_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a MEV abuse analyst",
        {
            "verdict": "HARMFUL",
            "harmful_patterns": 10,
            "confidence": 99,
            "consensus_sources": 0,
            "reason": "single source only",
        },
    )

    out = contract.resolve_case(cid)
    assert out == "SAFE"


def test_provider_error_reverts(contract, direct_vm):
    cid = _create(contract, direct_vm)
    direct_vm.mock_web(r"blocks\.flashbots\.net/v1/bundles", {"status": 500, "body": "err"})
    direct_vm.mock_web(r"api\.mevboost\.org/v1/batch", {"status": 200, "body": "ok"})

    with pytest.raises(Exception, match="flashbots server error"):
        contract.resolve_case(cid)


def test_cannot_resolve_twice(contract, direct_vm):
    cid = _create(contract, direct_vm)
    _mock_sources_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a MEV abuse analyst",
        {
            "verdict": "SAFE",
            "harmful_patterns": 0,
            "confidence": 80,
            "consensus_sources": 2,
            "reason": "clean batch",
        },
    )

    contract.resolve_case(cid)
    with pytest.raises(Exception, match="case already resolved"):
        contract.resolve_case(cid)


def test_missing_case_reverts(contract):
    with pytest.raises(Exception, match="case not found"):
        contract.get_case("999")
