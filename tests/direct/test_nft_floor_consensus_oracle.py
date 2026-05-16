"""Direct tests for nft_floor_consensus_oracle.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("nft_floor_consensus_oracle.py")


def _register(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_collection(
        "pudgypenguins",
        "https://opensea.example/api",
        "https://blur.example/api",
        "https://magiceden.example/api",
        2500,
    )


def test_register_and_get_collection(contract, direct_vm):
    _register(contract, direct_vm)
    c = json.loads(contract.get_collection("pudgypenguins"))
    assert c["max_spread_bps"] == 2500


def test_owner_only_register(contract, direct_vm):
    direct_vm.sender = BOB
    with pytest.raises(Exception, match="only owner"):
        contract.register_collection("x1", "https://a", "https://b", "https://c", 2000)


def test_compute_consensus_floor_happy_path(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.mock_web(r"opensea\.example/api", {"status": 200, "body": "opensea payload"})
    direct_vm.mock_web(r"blur\.example/api", {"status": 200, "body": "blur payload"})
    direct_vm.mock_web(r"magiceden\.example/api", {"status": 200, "body": "magiceden payload"})

    direct_vm.mock_llm(
        r"Market: opensea",
        {"floor_eth": 10.0, "confidence": 90, "wash_risk": "low", "rationale": "clean"},
    )
    direct_vm.mock_llm(
        r"Market: blur",
        {"floor_eth": 10.2, "confidence": 88, "wash_risk": "medium", "rationale": "ok"},
    )
    direct_vm.mock_llm(
        r"Market: magiceden",
        {"floor_eth": 9.9, "confidence": 85, "wash_risk": "low", "rationale": "clean"},
    )

    rid = contract.compute_consensus_floor("pudgypenguins")
    r = json.loads(contract.get_report(rid))
    assert r["stale"] is False
    assert r["consensus_floor_eth"] > 0


def test_compute_marks_stale_when_spread_high(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.mock_web(r"opensea\.example/api", {"status": 200, "body": "opensea payload"})
    direct_vm.mock_web(r"blur\.example/api", {"status": 200, "body": "blur payload"})
    direct_vm.mock_web(r"magiceden\.example/api", {"status": 200, "body": "magiceden payload"})

    direct_vm.mock_llm(r"Market: opensea", {"floor_eth": 10.0, "confidence": 90, "wash_risk": "low", "rationale": "clean"})
    direct_vm.mock_llm(r"Market: blur", {"floor_eth": 15.0, "confidence": 90, "wash_risk": "low", "rationale": "clean"})
    direct_vm.mock_llm(r"Market: magiceden", {"floor_eth": 9.0, "confidence": 90, "wash_risk": "low", "rationale": "clean"})

    rid = contract.compute_consensus_floor("pudgypenguins")
    r = json.loads(contract.get_report(rid))
    assert r["stale"] is True


def test_invalid_registration_inputs(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid collection_id"):
        contract.register_collection("x", "https://a", "https://b", "https://c", 2000)
    with pytest.raises(Exception, match="invalid market url"):
        contract.register_collection("xx", "ftp://a", "https://b", "https://c", 2000)
    with pytest.raises(Exception, match="invalid max_spread_bps"):
        contract.register_collection("xx", "https://a", "https://b", "https://c", 1)


def test_collection_not_found(contract):
    with pytest.raises(Exception, match="collection not found"):
        contract.compute_consensus_floor("missing")


def test_market_server_error(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.mock_web(r"opensea\.example/api", {"status": 500, "body": "err"})
    with pytest.raises(Exception, match="market server error"):
        contract.compute_consensus_floor("pudgypenguins")


def test_report_not_found(contract):
    with pytest.raises(Exception, match="report not found"):
        contract.get_report("999")


def test_get_all_reports_contains_created(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.mock_web(r"opensea\.example/api", {"status": 200, "body": "opensea payload"})
    direct_vm.mock_web(r"blur\.example/api", {"status": 200, "body": "blur payload"})
    direct_vm.mock_web(r"magiceden\.example/api", {"status": 200, "body": "magiceden payload"})

    direct_vm.mock_llm(r"Market: opensea", {"floor_eth": 10.0, "confidence": 90, "wash_risk": "low", "rationale": "clean"})
    direct_vm.mock_llm(r"Market: blur", {"floor_eth": 10.1, "confidence": 90, "wash_risk": "low", "rationale": "clean"})
    direct_vm.mock_llm(r"Market: magiceden", {"floor_eth": 9.9, "confidence": 90, "wash_risk": "low", "rationale": "clean"})

    rid = contract.compute_consensus_floor("pudgypenguins")
    all_reports = json.loads(contract.get_all_reports())
    assert rid in all_reports
