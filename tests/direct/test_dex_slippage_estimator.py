"""Direct tests for dex_slippage_estimator.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("dex_slippage_estimator.py")


def _create(contract, direct_vm, max_bps=120):
    direct_vm.sender = ALICE
    return contract.create_request("eth-usdc", "ETH", "USDC", 50000, max_bps)


def _mock_sources_ok(direct_vm):
    direct_vm.mock_web(r"api\.thegraph\.com/subgraphs/name/uniswap/uniswap-v3", {"status": 200, "body": '{"ok":true}'})
    direct_vm.mock_web(r"api\.thegraph\.com/subgraphs/name/curvefi/curve", {"status": 200, "body": '{"ok":true}'})
    direct_vm.mock_web(r"api\.llama\.fi/protocol/uniswap", {"status": 200, "body": '{"ok":true}'})


def test_create_request_and_read(contract, direct_vm):
    rid = _create(contract, direct_vm)
    r = json.loads(contract.get_request(rid))

    assert r["status"] == "PENDING"
    assert r["pair_key"] == "eth-usdc"


def test_create_invalid_pair(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid pair_key"):
        contract.create_request("ab", "ETH", "USDC", 1, 50)


def test_create_invalid_bps(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="max_acceptable_bps out of range"):
        contract.create_request("eth-usdc", "ETH", "USDC", 1, 0)


def test_resolve_execute(contract, direct_vm):
    rid = _create(contract, direct_vm, max_bps=150)
    _mock_sources_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a DEX execution risk analyst",
        {
            "estimated_slippage_bps": 90,
            "decision": "EXECUTE",
            "consensus_sources": 3,
            "reason": "sufficient depth",
        },
    )

    out = contract.resolve_request(rid)
    assert out == "EXECUTE"
    assert contract.get_latest_slippage_bps("eth-usdc") == 90


def test_resolve_skip(contract, direct_vm):
    rid = _create(contract, direct_vm, max_bps=50)
    _mock_sources_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a DEX execution risk analyst",
        {
            "estimated_slippage_bps": 220,
            "decision": "EXECUTE",
            "consensus_sources": 2,
            "reason": "thin books",
        },
    )

    out = contract.resolve_request(rid)
    assert out == "SKIP"


def test_provider_error_reverts(contract, direct_vm):
    rid = _create(contract, direct_vm)
    direct_vm.mock_web(r"api\.thegraph\.com/subgraphs/name/uniswap/uniswap-v3", {"status": 500, "body": "err"})
    direct_vm.mock_web(r"api\.thegraph\.com/subgraphs/name/curvefi/curve", {"status": 200, "body": "ok"})
    direct_vm.mock_web(r"api\.llama\.fi/protocol/uniswap", {"status": 200, "body": "ok"})

    with pytest.raises(Exception, match="uniswap server error"):
        contract.resolve_request(rid)


def test_low_consensus_forces_skip(contract, direct_vm):
    rid = _create(contract, direct_vm, max_bps=300)
    _mock_sources_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a DEX execution risk analyst",
        {
            "estimated_slippage_bps": 40,
            "decision": "EXECUTE",
            "consensus_sources": 0,
            "reason": "single source only",
        },
    )

    out = contract.resolve_request(rid)
    assert out == "SKIP"


def test_cannot_resolve_twice(contract, direct_vm):
    rid = _create(contract, direct_vm)
    _mock_sources_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a DEX execution risk analyst",
        {
            "estimated_slippage_bps": 100,
            "decision": "EXECUTE",
            "consensus_sources": 2,
            "reason": "ok",
        },
    )

    contract.resolve_request(rid)
    with pytest.raises(Exception, match="request already resolved"):
        contract.resolve_request(rid)


def test_missing_request_reverts(contract):
    with pytest.raises(Exception, match="request not found"):
        contract.get_request("999")
