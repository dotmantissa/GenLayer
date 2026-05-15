"""Direct tests for dao_constitution_guard.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("dao_constitution_guard.py")


def test_register_constitution_https(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_constitution("dao1", "https://example.org/constitution.txt")
    data = json.loads(contract.get_constitution("dao1"))
    assert data["constitution_url"] == "https://example.org/constitution.txt"


def test_register_constitution_ipfs_normalized(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_constitution("dao2", "ipfs://QmTestCidLongEnough1234567890")
    data = json.loads(contract.get_constitution("dao2"))
    assert data["constitution_url"].startswith("https://ipfs.io/ipfs/")


def test_owner_only_register(contract, direct_vm):
    direct_vm.sender = BOB
    with pytest.raises(Exception, match="only owner"):
        contract.register_constitution("dao1", "https://example.org/x")


def test_evaluate_action_blocked(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_constitution("dao1", "https://example.org/constitution.txt")

    direct_vm.mock_web(
        r"example\.org/constitution\.txt",
        {"status": 200, "body": "This constitution forbids treasury withdrawal without a supermajority vote and independent audit confirmation."},
    )
    direct_vm.mock_llm(
        r"constitutional compliance reviewer",
        {
            "violation": True,
            "violating_clauses": ["Clause 4 treasury controls"],
            "reasoning": "No supermajority evidence was provided.",
            "confidence": 94,
        },
    )

    did = contract.evaluate_action("dao1", "Transfer treasury funds to a private wallet with no vote.")
    decision = json.loads(contract.get_decision(did))
    assert decision["blocked"] is True


def test_evaluate_action_allowed(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_constitution("dao1", "https://example.org/constitution.txt")

    direct_vm.mock_web(
        r"example\.org/constitution\.txt",
        {"status": 200, "body": "Constitution allows routine parameter updates after a quorum vote documented on chain with transparency notes."},
    )
    direct_vm.mock_llm(
        r"constitutional compliance reviewer",
        {
            "violation": False,
            "violating_clauses": [],
            "reasoning": "The proposed change follows the allowed process.",
            "confidence": 87,
        },
    )

    did = contract.evaluate_action("dao1", "Update staking reward parameter by governance vote.")
    decision = json.loads(contract.get_decision(did))
    assert decision["blocked"] is False


def test_invalid_action_text(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid action_text"):
        contract.evaluate_action("dao1", "short")


def test_constitution_not_registered(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="constitution not registered"):
        contract.evaluate_action("daoX", "Long enough governance action text.")


def test_llm_invalid_confidence(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_constitution("dao1", "https://example.org/constitution.txt")

    direct_vm.mock_web(
        r"example\.org/constitution\.txt",
        {"status": 200, "body": "Constitution text long enough to pass validation and includes procedural clauses and rights."},
    )
    direct_vm.mock_llm(
        r"constitutional compliance reviewer",
        {"violation": True, "violating_clauses": ["x"], "reasoning": "bad", "confidence": "oops"},
    )

    with pytest.raises(Exception, match="invalid confidence"):
        contract.evaluate_action("dao1", "Perform governance action with unclear constitutional basis.")


def test_decision_not_found(contract):
    with pytest.raises(Exception, match="decision not found"):
        contract.get_decision("999")


def test_get_all_decisions_contains_created(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_constitution("dao1", "https://example.org/constitution.txt")

    direct_vm.mock_web(
        r"example\.org/constitution\.txt",
        {"status": 200, "body": "Constitution allows ordinary actions with quorum and recorded rationale in governance history entries."},
    )
    direct_vm.mock_llm(
        r"constitutional compliance reviewer",
        {
            "violation": False,
            "violating_clauses": [],
            "reasoning": "Action appears consistent with constitutional process.",
            "confidence": 90,
        },
    )

    did = contract.evaluate_action("dao1", "Execute approved maintenance proposal after quorum vote.")
    all_decisions = json.loads(contract.get_all_decisions())
    assert did in all_decisions
