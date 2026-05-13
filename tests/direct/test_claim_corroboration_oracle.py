"""Direct tests for claim_corroboration_oracle.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("claim_corroboration_oracle.py")


def _create(contract, direct_vm, min_sources=2):
    direct_vm.sender = ALICE
    return contract.create_case("The central bank raised rates by 25 basis points this week", min_sources)


def _mock_sources(direct_vm):
    direct_vm.mock_web(r"reuters\.com/world", {"status": 200, "body": "reuters text"})
    direct_vm.mock_web(r"apnews\.com", {"status": 200, "body": "ap text"})
    direct_vm.mock_web(r"bbc\.com/news", {"status": 200, "body": "bbc text"})


def test_create_case_and_read(contract, direct_vm):
    cid = _create(contract, direct_vm)
    case = json.loads(contract.get_case(cid))

    assert case["status"] == "PENDING"


def test_create_case_invalid_min_sources(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="min_sources_required out of range"):
        contract.create_case("valid claim text for testing", 1)


def test_resolve_corroborated(contract, direct_vm):
    cid = _create(contract, direct_vm, min_sources=2)
    _mock_sources(direct_vm)
    direct_vm.mock_llm(
        r"You are a fact corroboration analyst",
        {"corroborating_sources": 3, "verdict": "CORROBORATED", "reason": "all sources align"},
    )

    verdict = contract.resolve_case(cid)
    assert verdict == "CORROBORATED"


def test_resolve_not_corroborated(contract, direct_vm):
    cid = _create(contract, direct_vm, min_sources=3)
    _mock_sources(direct_vm)
    direct_vm.mock_llm(
        r"You are a fact corroboration analyst",
        {"corroborating_sources": 2, "verdict": "CORROBORATED", "reason": "partial evidence"},
    )

    verdict = contract.resolve_case(cid)
    assert verdict == "NOT_CORROBORATED"


def test_provider_error_reverts(contract, direct_vm):
    cid = _create(contract, direct_vm)
    direct_vm.mock_web(r"reuters\.com/world", {"status": 500, "body": "err"})
    direct_vm.mock_web(r"apnews\.com", {"status": 200, "body": "ok"})
    direct_vm.mock_web(r"bbc\.com/news", {"status": 200, "body": "ok"})

    with pytest.raises(Exception, match="reuters server error"):
        contract.resolve_case(cid)


def test_cannot_resolve_twice(contract, direct_vm):
    cid = _create(contract, direct_vm)
    _mock_sources(direct_vm)
    direct_vm.mock_llm(
        r"You are a fact corroboration analyst",
        {"corroborating_sources": 0, "verdict": "NOT_CORROBORATED", "reason": "insufficient support"},
    )

    contract.resolve_case(cid)
    with pytest.raises(Exception, match="case already resolved"):
        contract.resolve_case(cid)


def test_missing_case_reverts(contract):
    with pytest.raises(Exception, match="case not found"):
        contract.get_case("999")
