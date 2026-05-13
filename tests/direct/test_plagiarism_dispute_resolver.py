"""Direct tests for plagiarism_dispute_resolver.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("plagiarism_dispute_resolver.py")


def _create_case(contract, direct_vm, threshold=60):
    direct_vm.sender = ALICE
    return contract.create_case(
        "0x1234567890abcdef1234567890abcdef",
        "https://example.org/source-article",
        threshold,
    )


def _excerpt():
    return (
        "This research note describes a distributed optimization method that first "
        "builds a sparse semantic index, then iteratively aligns conceptual segments "
        "across candidate sources to measure structural and narrative similarity."
    )


def test_create_case_and_read(contract, direct_vm):
    case_id = _create_case(contract, direct_vm)
    case = json.loads(contract.get_case(case_id))

    assert case["status"] == "PENDING"
    assert case["threshold_pct"] == 60


def test_create_case_invalid_url(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid source_url"):
        contract.create_case("0x1234567890abcdef1234567890abcdef", "ftp://bad", 60)


def test_create_case_invalid_threshold(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="threshold_pct out of range"):
        contract.create_case("0x1234567890abcdef1234567890abcdef", "https://example.org", 5)


def test_resolve_case_confirmed(contract, direct_vm):
    case_id = _create_case(contract, direct_vm, threshold=65)
    direct_vm.mock_web(r"example\.org/source-article", {"status": 200, "body": "suspected source content"})
    direct_vm.mock_llm(
        r"You are an academic integrity adjudicator",
        {"overlap_pct": 78, "confirmed": True, "reason": "strong semantic and structural overlap"},
    )

    confirmed = contract.resolve_case(case_id, _excerpt())
    case = json.loads(contract.get_case(case_id))

    assert confirmed is True
    assert case["status"] == "CONFIRMED"


def test_resolve_case_dismissed(contract, direct_vm):
    case_id = _create_case(contract, direct_vm, threshold=70)
    direct_vm.mock_web(r"example\.org/source-article", {"status": 200, "body": "unrelated topic and framing"})
    direct_vm.mock_llm(
        r"You are an academic integrity adjudicator",
        {"overlap_pct": 25, "confirmed": False, "reason": "limited conceptual overlap"},
    )

    confirmed = contract.resolve_case(case_id, _excerpt())
    case = json.loads(contract.get_case(case_id))

    assert confirmed is False
    assert case["status"] == "DISMISSED"


def test_threshold_gate_overrides_model(contract, direct_vm):
    case_id = _create_case(contract, direct_vm, threshold=80)
    direct_vm.mock_web(r"example\.org/source-article", {"status": 200, "body": "some overlapping text"})
    direct_vm.mock_llm(
        r"You are an academic integrity adjudicator",
        {"overlap_pct": 65, "confirmed": True, "reason": "partial overlap"},
    )

    confirmed = contract.resolve_case(case_id, _excerpt())
    assert confirmed is False


def test_source_error_reverts(contract, direct_vm):
    case_id = _create_case(contract, direct_vm)
    direct_vm.mock_web(r"example\.org/source-article", {"status": 500, "body": "err"})

    with pytest.raises(Exception, match="source server error"):
        contract.resolve_case(case_id, _excerpt())


def test_excerpt_too_short_reverts(contract, direct_vm):
    case_id = _create_case(contract, direct_vm)
    with pytest.raises(Exception, match="document_excerpt too short"):
        contract.resolve_case(case_id, "short")


def test_cannot_resolve_twice(contract, direct_vm):
    case_id = _create_case(contract, direct_vm)
    direct_vm.mock_web(r"example\.org/source-article", {"status": 200, "body": "text"})
    direct_vm.mock_llm(
        r"You are an academic integrity adjudicator",
        {"overlap_pct": 10, "confirmed": False, "reason": "not overlapping"},
    )

    contract.resolve_case(case_id, _excerpt())
    with pytest.raises(Exception, match="case already resolved"):
        contract.resolve_case(case_id, _excerpt())
