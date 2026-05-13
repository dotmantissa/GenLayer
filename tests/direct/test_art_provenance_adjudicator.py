"""Direct tests for art_provenance_adjudicator.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("art_provenance_adjudicator.py")


def _create(contract, direct_vm, min_steps=3):
    direct_vm.sender = ALICE
    return contract.create_dossier("art-123", "Frida Kahlo", min_steps)


def _mock_sources(direct_vm):
    direct_vm.mock_web(r"api\.artsy\.net/api/artworks", {"status": 200, "body": "{\"provenance\":[]}"})
    direct_vm.mock_web(r"mutualart\.com/api/artworks", {"status": 200, "body": "{\"history\":[]}"})


def test_create_dossier_and_read(contract, direct_vm):
    did = _create(contract, direct_vm)
    dossier = json.loads(contract.get_dossier(did))

    assert dossier["status"] == "PENDING"
    assert dossier["artist_name"] == "Frida Kahlo"


def test_create_dossier_invalid_steps(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="min_chain_steps out of range"):
        contract.create_dossier("art-123", "Frida Kahlo", 0)


def test_assess_authentic_happy_path(contract, direct_vm):
    did = _create(contract, direct_vm, min_steps=2)
    _mock_sources(direct_vm)
    direct_vm.mock_llm(
        r"You are a provenance and authenticity specialist",
        {
            "provenance_steps": 5,
            "completeness_pct": 88,
            "authenticity_verdict": "AUTHENTIC",
            "reason": "well documented provenance chain",
        },
    )

    out = contract.assess_dossier(did)
    dossier = json.loads(contract.get_dossier(did))

    assert out == "AUTHENTIC"
    assert dossier["status"] == "ASSESSED"


def test_assess_downgrade_when_steps_below_min(contract, direct_vm):
    did = _create(contract, direct_vm, min_steps=6)
    _mock_sources(direct_vm)
    direct_vm.mock_llm(
        r"You are a provenance and authenticity specialist",
        {
            "provenance_steps": 3,
            "completeness_pct": 80,
            "authenticity_verdict": "AUTHENTIC",
            "reason": "insufficient historical transitions",
        },
    )

    out = contract.assess_dossier(did)
    assert out == "UNCERTAIN"


def test_assess_high_risk(contract, direct_vm):
    did = _create(contract, direct_vm)
    _mock_sources(direct_vm)
    direct_vm.mock_llm(
        r"You are a provenance and authenticity specialist",
        {
            "provenance_steps": 1,
            "completeness_pct": 25,
            "authenticity_verdict": "HIGH_RISK",
            "reason": "conflicting ownership records",
        },
    )

    out = contract.assess_dossier(did)
    assert out == "HIGH_RISK"


def test_provider_error_reverts(contract, direct_vm):
    did = _create(contract, direct_vm)
    direct_vm.mock_web(r"api\.artsy\.net/api/artworks", {"status": 500, "body": "err"})
    direct_vm.mock_web(r"mutualart\.com/api/artworks", {"status": 200, "body": "ok"})

    with pytest.raises(Exception, match="artsy server error"):
        contract.assess_dossier(did)


def test_cannot_assess_twice(contract, direct_vm):
    did = _create(contract, direct_vm)
    _mock_sources(direct_vm)
    direct_vm.mock_llm(
        r"You are a provenance and authenticity specialist",
        {
            "provenance_steps": 2,
            "completeness_pct": 55,
            "authenticity_verdict": "UNCERTAIN",
            "reason": "moderate documentation",
        },
    )

    contract.assess_dossier(did)
    with pytest.raises(Exception, match="dossier already assessed"):
        contract.assess_dossier(did)


def test_missing_dossier_reverts(contract):
    with pytest.raises(Exception, match="dossier not found"):
        contract.get_dossier("999")
