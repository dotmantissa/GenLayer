"""Direct tests for vendor_review_quality_oracle.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("vendor_review_quality_oracle.py")


def _create(contract, direct_vm, min_score=70):
    direct_vm.sender = ALICE
    return contract.create_case("acme-vendor", min_score)


def _mock_sources(direct_vm):
    direct_vm.mock_web(r"trustpilot\.com/review", {"status": 200, "body": "tp reviews"})
    direct_vm.mock_web(r"g2\.com/products", {"status": 200, "body": "g2 reviews"})
    direct_vm.mock_web(r"maps\.googleapis\.com/maps/api/place/textsearch", {"status": 200, "body": "google reviews"})


def test_create_case_and_read(contract, direct_vm):
    cid = _create(contract, direct_vm)
    case = json.loads(contract.get_case(cid))

    assert case["status"] == "PENDING"
    assert case["vendor_slug"] == "acme-vendor"


def test_create_case_invalid_threshold(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="min_quality_score out of range"):
        contract.create_case("acme", 0)


def test_resolve_pass(contract, direct_vm):
    cid = _create(contract, direct_vm, min_score=70)
    _mock_sources(direct_vm)
    direct_vm.mock_llm(
        r"You are a review integrity analyst",
        {
            "quality_score": 84,
            "fake_review_risk": "LOW",
            "verdict": "PASS",
            "reason": "consistent positive feedback across platforms",
        },
    )

    verdict = contract.resolve_case(cid)
    assert verdict == "PASS"


def test_resolve_fail_due_to_high_fake_risk(contract, direct_vm):
    cid = _create(contract, direct_vm, min_score=60)
    _mock_sources(direct_vm)
    direct_vm.mock_llm(
        r"You are a review integrity analyst",
        {
            "quality_score": 90,
            "fake_review_risk": "HIGH",
            "verdict": "PASS",
            "reason": "suspicious burst patterns",
        },
    )

    verdict = contract.resolve_case(cid)
    assert verdict == "FAIL"


def test_resolve_fail_due_to_threshold(contract, direct_vm):
    cid = _create(contract, direct_vm, min_score=85)
    _mock_sources(direct_vm)
    direct_vm.mock_llm(
        r"You are a review integrity analyst",
        {
            "quality_score": 70,
            "fake_review_risk": "LOW",
            "verdict": "PASS",
            "reason": "quality below contract target",
        },
    )

    verdict = contract.resolve_case(cid)
    assert verdict == "FAIL"


def test_provider_error_reverts(contract, direct_vm):
    cid = _create(contract, direct_vm)
    direct_vm.mock_web(r"trustpilot\.com/review", {"status": 500, "body": "err"})
    direct_vm.mock_web(r"g2\.com/products", {"status": 200, "body": "ok"})
    direct_vm.mock_web(r"maps\.googleapis\.com/maps/api/place/textsearch", {"status": 200, "body": "ok"})

    with pytest.raises(Exception, match="trustpilot server error"):
        contract.resolve_case(cid)


def test_cannot_resolve_twice(contract, direct_vm):
    cid = _create(contract, direct_vm)
    _mock_sources(direct_vm)
    direct_vm.mock_llm(
        r"You are a review integrity analyst",
        {
            "quality_score": 50,
            "fake_review_risk": "MEDIUM",
            "verdict": "FAIL",
            "reason": "mixed and weak reviews",
        },
    )

    contract.resolve_case(cid)
    with pytest.raises(Exception, match="case already resolved"):
        contract.resolve_case(cid)


def test_missing_case_reverts(contract):
    with pytest.raises(Exception, match="case not found"):
        contract.get_case("999")
