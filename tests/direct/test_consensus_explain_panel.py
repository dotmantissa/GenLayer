"""Direct tests for consensus_explain_panel.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("consensus_explain_panel.py")


def test_explain_consensus_agreed_and_correct(contract, direct_vm):
    direct_vm.sender = ALICE
    payload = [
        {"validator": 1, "output": {"decision": "allow", "score": 8}},
        {"validator": 2, "output": {"decision": "allow", "score": 8}},
        {"validator": 3, "output": {"decision": "allow", "score": 8}},
    ]

    sid = contract.explain_consensus(
        "decision and score must match",
        json.dumps(payload),
        "allow",
    )
    out = json.loads(contract.get_session(sid))

    assert out["agreed"] is True
    assert out["outcome_correct"] is True
    assert out["majority_decision"] == "allow"


def test_explain_consensus_disagreed_and_incorrect(contract, direct_vm):
    direct_vm.sender = ALICE
    payload = [
        {"validator": 1, "output": {"decision": "allow", "score": 8}},
        {"validator": 2, "output": {"decision": "deny", "score": 3}},
        {"validator": 3, "output": {"decision": "deny", "score": 2}},
    ]

    sid = contract.explain_consensus(
        "decision and score must match",
        json.dumps(payload),
        "allow",
    )
    out = json.loads(contract.get_session(sid))

    assert out["agreed"] is False
    assert out["outcome_correct"] is False
    assert out["majority_decision"] == "deny"
    assert len(out["plain_english_explanation"]) > 20


def test_differing_keys_captured(contract, direct_vm):
    direct_vm.sender = ALICE
    payload = [
        {"validator": 1, "output": {"decision": "allow", "reason": "a"}},
        {"validator": 2, "output": {"decision": "allow", "reason": "b"}},
    ]

    sid = contract.explain_consensus("reason may vary", json.dumps(payload), "allow")
    out = json.loads(contract.get_session(sid))
    assert "reason" in out["differing_keys"]


def test_invalid_equivalence_rule(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid equivalence_principle"):
        contract.explain_consensus("x", json.dumps([]), "allow")


def test_invalid_expected_outcome(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid expected_outcome"):
        contract.explain_consensus("valid rule", json.dumps([{"output": {"decision": "allow"}}, {"output": {"decision": "allow"}}]), "maybe")


def test_invalid_validator_outputs_json(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid validator_outputs_json"):
        contract.explain_consensus("valid rule", "not-json", "allow")


def test_too_few_validator_outputs(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="validator_outputs must have at least two entries"):
        contract.explain_consensus("valid rule", json.dumps([{"output": {"decision": "allow"}}]), "allow")


def test_validator_output_must_be_object(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="validator output must be object"):
        contract.explain_consensus("valid rule", json.dumps([1, 2]), "allow")


def test_session_not_found(contract):
    with pytest.raises(Exception, match="session not found"):
        contract.get_session("999")


def test_get_all_sessions_contains_created(contract, direct_vm):
    direct_vm.sender = ALICE
    payload = [
        {"validator": 1, "output": {"decision": "allow"}},
        {"validator": 2, "output": {"decision": "allow"}},
    ]
    sid = contract.explain_consensus("decision match", json.dumps(payload), "allow")
    all_sessions = json.loads(contract.get_all_sessions())
    assert sid in all_sessions
