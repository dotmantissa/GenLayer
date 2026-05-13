"""Direct tests for nondet_cost_estimator.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("nondet_cost_estimator.py")


def test_estimate_happy_path(contract, direct_vm):
    direct_vm.sender = ALICE
    ops = [
        {"type": "web_fetch", "label": "fetch_prices", "estimated_response_kb": 12},
        {"type": "llm_call", "label": "classify", "estimated_prompt_tokens": 800, "estimated_output_tokens": 200},
    ]

    rid = contract.estimate_contract_cost(json.dumps(ops), 50000)
    report = json.loads(contract.get_report(rid))

    assert report["total_estimated_units"] > 0
    assert len(report["breakdown"]) == 2
    assert report["over_budget"] is False


def test_over_budget_flag(contract, direct_vm):
    direct_vm.sender = ALICE
    ops = [
        {"type": "llm_call", "label": "heavy", "estimated_prompt_tokens": 5000, "estimated_output_tokens": 2000},
    ]

    rid = contract.estimate_contract_cost(json.dumps(ops), 1000)
    report = json.loads(contract.get_report(rid))
    assert report["over_budget"] is True


def test_expensive_operation_flag(contract, direct_vm):
    direct_vm.sender = ALICE
    ops = [
        {"type": "llm_call", "label": "expensive_llm", "estimated_prompt_tokens": 2000, "estimated_output_tokens": 1000},
    ]

    rid = contract.estimate_contract_cost(json.dumps(ops), 50000)
    report = json.loads(contract.get_report(rid))
    assert "expensive_llm" in report["expensive_operations"]


def test_invalid_budget(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="budget_units must be positive"):
        contract.estimate_contract_cost("[]", 0)


def test_invalid_operations_json(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid operations_json"):
        contract.estimate_contract_cost("not-json", 1000)


def test_unsupported_operation_type(contract, direct_vm):
    direct_vm.sender = ALICE
    ops = [{"type": "storage_write", "label": "x"}]
    with pytest.raises(Exception, match="unsupported operation type"):
        contract.estimate_contract_cost(json.dumps(ops), 1000)


def test_report_not_found(contract):
    with pytest.raises(Exception, match="report not found"):
        contract.get_report("999")
