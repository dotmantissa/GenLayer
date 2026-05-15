"""Direct tests for equivalence_principle_advisor.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("equivalence_principle_advisor.py")


def test_recommend_strict_eq_for_deterministic(contract, direct_vm):
    direct_vm.sender = ALICE
    rid = contract.recommend_principle(
        "deterministic", True, False, False, True, "OracleGuard"
    )
    rec = json.loads(contract.get_recommendation(rid))

    assert rec["recommended_variant"] == "strict_eq"
    assert "strict_eq" in rec["annotated_template"]


def test_recommend_custom_for_numeric_tolerance(contract, direct_vm):
    direct_vm.sender = ALICE
    rid = contract.recommend_principle(
        "numeric", True, True, False, False, "PriceBand"
    )
    rec = json.loads(contract.get_recommendation(rid))

    assert rec["recommended_variant"] == "run_nondet_unsafe_custom_validator"
    assert "run_nondet_unsafe" in rec["annotated_template"]


def test_recommend_prompt_comparative_for_subjective(contract, direct_vm):
    direct_vm.sender = ALICE
    rid = contract.recommend_principle(
        "subjective", False, False, True, False, "LegalReasoner"
    )
    rec = json.loads(contract.get_recommendation(rid))

    assert rec["recommended_variant"] == "prompt_comparative"
    assert "prompt_comparative" in rec["annotated_template"]


def test_recommend_boolean(contract, direct_vm):
    direct_vm.sender = ALICE
    rid = contract.recommend_principle(
        "boolean", True, False, False, True, "Gate"
    )
    rec = json.loads(contract.get_recommendation(rid))

    assert rec["recommended_variant"] == "strict_eq"


def test_invalid_output_type(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid output_type"):
        contract.recommend_principle("text", True, False, False, True, "Xy")


def test_invalid_contract_name(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid contract_name"):
        contract.recommend_principle("deterministic", True, False, False, True, "")


def test_numeric_requires_choice(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="numeric output requires tolerance or exact match decision"):
        contract.recommend_principle("numeric", True, False, False, False, "NumRule")


def test_subjective_requires_flag(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="subjective output requires subjective_judgment true"):
        contract.recommend_principle("subjective", False, False, False, False, "Judge")


def test_subjective_exact_conflict(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="subjective judgment conflicts with exact match"):
        contract.recommend_principle("subjective", False, False, True, True, "Judge")


def test_recommendation_not_found(contract):
    with pytest.raises(Exception, match="recommendation not found"):
        contract.get_recommendation("999")


def test_get_all_recommendations_contains_created(contract, direct_vm):
    direct_vm.sender = ALICE
    rid = contract.recommend_principle(
        "deterministic", True, False, False, True, "OracleGuard"
    )
    all_rec = json.loads(contract.get_all_recommendations())
    assert rid in all_rec
