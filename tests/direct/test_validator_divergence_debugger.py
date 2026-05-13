"""Direct tests for validator_divergence_debugger.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("validator_divergence_debugger.py")


def test_debug_execution_equivalent_outputs(contract, direct_vm):
    direct_vm.sender = ALICE
    direct_vm.mock_llm(r"You are validator slot", {"decision": "ALLOW", "score": 10})

    run_id = contract.debug_execution("payload", "decision and score must match", 3)
    run = json.loads(contract.get_run(run_id))

    assert run["equivalent"] is True
    assert run["divergence_count"] == 1


def test_debug_execution_divergence_detected(contract, direct_vm):
    direct_vm.sender = ALICE
    direct_vm.mock_llm(r"validator slot 1", {"decision": "ALLOW", "score": 10})
    direct_vm.mock_llm(r"validator slot 2", {"decision": "BLOCK", "score": 20})
    direct_vm.mock_llm(r"validator slot 3", {"decision": "ALLOW", "score": 10})

    run_id = contract.debug_execution("payload", "decision and score must match", 3)
    run = json.loads(contract.get_run(run_id))

    assert run["equivalent"] is False
    assert run["divergence_count"] == 2
    assert "decision" in run["differing_keys"]


def test_debug_invalid_payload(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid execution_payload"):
        contract.debug_execution("", "rule", 3)


def test_debug_invalid_rule(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid equivalence_rule"):
        contract.debug_execution("payload", "", 3)


def test_debug_invalid_validator_count(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="validator_count out of range"):
        contract.debug_execution("payload", "rule", 1)


def test_run_not_found(contract):
    with pytest.raises(Exception, match="run not found"):
        contract.get_run("999")
