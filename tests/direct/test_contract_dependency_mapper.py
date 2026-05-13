"""Direct tests for contract_dependency_mapper.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("contract_dependency_mapper.py")


def test_analyze_system_happy_path(contract, direct_vm):
    direct_vm.sender = ALICE
    spec = {
        "contracts": ["Vault", "Router", "Oracle"],
        "calls": [
            {"from": "Router", "to": "Vault", "mode": "write", "data": "rebalance"},
            {"from": "Vault", "to": "Oracle", "mode": "read", "data": "price"},
        ],
    }

    aid = contract.analyze_system(json.dumps(spec))
    out = json.loads(contract.get_analysis(aid))

    assert out["dependency_chain_count"] == 2
    assert out["has_circular_dependency"] is False


def test_analyze_system_detects_cycle(contract, direct_vm):
    direct_vm.sender = ALICE
    spec = {
        "contracts": ["AA", "BB", "CC"],
        "calls": [
            {"from": "BB", "to": "CC", "mode": "write", "data": "x"},
            {"from": "CC", "to": "BB", "mode": "write", "data": "y"},
        ],
    }

    aid = contract.analyze_system(json.dumps(spec))
    out = json.loads(contract.get_analysis(aid))

    assert out["has_circular_dependency"] is True
    assert len(out["reentrancy_risk_signals"]) >= 1


def test_invalid_json(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid system_spec_json"):
        contract.analyze_system("not-json")


def test_unknown_contract_call(contract, direct_vm):
    direct_vm.sender = ALICE
    spec = {
        "contracts": ["AA", "BB"],
        "calls": [{"from": "AA", "to": "CC", "mode": "read", "data": "x"}],
    }
    with pytest.raises(Exception, match="unknown contract in call"):
        contract.analyze_system(json.dumps(spec))


def test_invalid_mode(contract, direct_vm):
    direct_vm.sender = ALICE
    spec = {
        "contracts": ["AA", "BB"],
        "calls": [{"from": "AA", "to": "BB", "mode": "exec", "data": "x"}],
    }
    with pytest.raises(Exception, match="invalid call mode"):
        contract.analyze_system(json.dumps(spec))


def test_analysis_not_found(contract):
    with pytest.raises(Exception, match="analysis not found"):
        contract.get_analysis("999")
