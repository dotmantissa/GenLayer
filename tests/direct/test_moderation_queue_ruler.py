"""Direct tests for moderation_queue_ruler.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("moderation_queue_ruler.py")


def _policy(contract, direct_vm):
    direct_vm.sender = ALICE
    return contract.create_policy(
        "Community Core",
        "No harassment, threats, or explicit hate content. No direct incitement of violence. "
        "Allow criticism and disagreement as long as language remains non abusive and non threatening.",
    )


def _case(contract, direct_vm):
    pid = _policy(contract, direct_vm)
    return contract.create_case(pid, "https://example.org/mod-queue", "post-17")


def test_create_policy_and_case(contract, direct_vm):
    item_id = _case(contract, direct_vm)
    case = json.loads(contract.get_case(item_id))

    assert case["status"] == "PENDING"
    assert case["content_id"] == "post-17"


def test_create_policy_short_guidelines_revert(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="guidelines too short"):
        contract.create_policy("Policy Name", "too short")


def test_create_case_invalid_queue_url(contract, direct_vm):
    pid = _policy(contract, direct_vm)
    with pytest.raises(Exception, match="invalid queue_url"):
        contract.create_case(pid, "ftp://invalid", "post-1")


def test_rule_case_remove(contract, direct_vm):
    item_id = _case(contract, direct_vm)
    direct_vm.mock_web(r"example\.org/mod-queue", {"status": 200, "body": "{\"items\":[{\"id\":\"post-17\",\"text\":\"violent threat\"}]}"})
    direct_vm.mock_llm(
        r"You are a platform trust and safety adjudicator",
        {"ruling": "REMOVE", "confidence": 91, "reason": "explicit threat language detected"},
    )

    ruling = contract.rule_case(item_id)
    case = json.loads(contract.get_case(item_id))

    assert ruling == "REMOVE"
    assert case["status"] == "RULED"


def test_rule_case_reinstate(contract, direct_vm):
    item_id = _case(contract, direct_vm)
    direct_vm.mock_web(r"example\.org/mod-queue", {"status": 200, "body": "{\"items\":[{\"id\":\"post-17\",\"text\":\"strong criticism\"}]}"})
    direct_vm.mock_llm(
        r"You are a platform trust and safety adjudicator",
        {"ruling": "REINSTATE", "confidence": 74, "reason": "critical but not abusive"},
    )

    ruling = contract.rule_case(item_id)
    assert ruling == "REINSTATE"


def test_unknown_ruling_defaults_reinstate(contract, direct_vm):
    item_id = _case(contract, direct_vm)
    direct_vm.mock_web(r"example\.org/mod-queue", {"status": 200, "body": "queue payload"})
    direct_vm.mock_llm(
        r"You are a platform trust and safety adjudicator",
        {"ruling": "MAYBE", "confidence": 20, "reason": "ambiguous"},
    )

    ruling = contract.rule_case(item_id)
    assert ruling == "REINSTATE"


def test_provider_error_reverts(contract, direct_vm):
    item_id = _case(contract, direct_vm)
    direct_vm.mock_web(r"example\.org/mod-queue", {"status": 500, "body": "err"})

    with pytest.raises(Exception, match="queue server error"):
        contract.rule_case(item_id)


def test_cannot_rule_twice(contract, direct_vm):
    item_id = _case(contract, direct_vm)
    direct_vm.mock_web(r"example\.org/mod-queue", {"status": 200, "body": "queue payload"})
    direct_vm.mock_llm(
        r"You are a platform trust and safety adjudicator",
        {"ruling": "REINSTATE", "confidence": 60, "reason": "not a violation"},
    )

    contract.rule_case(item_id)
    with pytest.raises(Exception, match="item already ruled"):
        contract.rule_case(item_id)


def test_missing_item_reverts(contract):
    with pytest.raises(Exception, match="item not found"):
        contract.get_case("999")
