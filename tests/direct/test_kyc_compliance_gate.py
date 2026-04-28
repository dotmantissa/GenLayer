"""Direct tests for kyc_compliance_gate.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
OFFICER = "0x9999999999999999999999999999999999999999"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy(
        "kyc_compliance_gate.py",
        OFFICER,
        "enc:kyc_key",
        "enc:sanctions_key",
        '["IR", "KP"]',
    )


def _mock_kyc_pass(direct_vm, provider_prefix="jumio", country="US", risk="LOW"):
    if provider_prefix == "jumio":
        direct_vm.mock_web(r"api\.jumio\.com", {"status": 200, "body": {"session": "ok"}})
    elif provider_prefix == "onfido":
        direct_vm.mock_web(r"api\.onfido\.com", {"status": 200, "body": {"check": "ok"}})
    else:
        direct_vm.mock_web(r"withpersona\.com", {"status": 200, "body": {"inquiry": "ok"}})

    direct_vm.mock_llm(
        r"Normalize this KYC session JSON",
        {"passed": True, "risk_level": risk, "country": country},
    )


def _mock_sanctions(direct_vm, sanctioned=False):
    direct_vm.mock_web(r"chainalysis\.com", {"status": 200, "body": {"risk": "low"}})
    direct_vm.mock_web(r"trmlabs\.com", {"status": 200, "body": {"sanctions": False}})
    direct_vm.mock_llm(
        r"Given sanctions screening JSON results",
        {"sanctioned": sanctioned, "source": "chainalysis"},
    )


def test_kyc_pass_issues_badge(contract, direct_vm):
    direct_vm.sender = ALICE
    _mock_kyc_pass(direct_vm, "jumio", "US", "LOW")
    _mock_sanctions(direct_vm, False)

    ok = contract.initiate_kyc("jumio_session_abc")
    assert ok is True
    assert contract.is_compliant(ALICE) is True

    badge = json.loads(contract.get_badge_status(ALICE))
    assert badge["provider"] == "jumio"
    assert badge["country"] == "US"


def test_kyc_fail_returns_false(contract, direct_vm):
    direct_vm.sender = ALICE
    direct_vm.mock_web(r"api\.jumio\.com", {"status": 200, "body": {"session": "ok"}})
    direct_vm.mock_llm(
        r"Normalize this KYC session JSON",
        {"passed": False, "risk_level": "LOW", "country": "US"},
    )
    _mock_sanctions(direct_vm, False)

    ok = contract.initiate_kyc("jumio_session_fail")
    assert ok is False
    assert contract.is_compliant(ALICE) is False


def test_blocked_country_fails(contract, direct_vm):
    direct_vm.sender = ALICE
    _mock_kyc_pass(direct_vm, "persona", "IR", "LOW")
    _mock_sanctions(direct_vm, False)

    ok = contract.initiate_kyc("persona_session_1")
    assert ok is False
    assert contract.is_compliant(ALICE) is False


def test_sanctioned_wallet_fails(contract, direct_vm):
    direct_vm.sender = ALICE
    _mock_kyc_pass(direct_vm, "onfido", "US", "LOW")
    _mock_sanctions(direct_vm, True)

    ok = contract.initiate_kyc("onfido_session_1")
    assert ok is False


def test_badge_expires_after_365_days(contract, direct_vm):
    direct_vm.sender = ALICE
    _mock_kyc_pass(direct_vm, "jumio", "US", "LOW")
    _mock_sanctions(direct_vm, False)
    contract.initiate_kyc("jumio_session_live")

    assert contract.is_compliant(ALICE) is True
    direct_vm.timestamp += 365 * 24 * 60 * 60 + 1
    assert contract.is_compliant(ALICE) is False


def test_renewal_extends_expiry(contract, direct_vm):
    direct_vm.sender = ALICE
    _mock_kyc_pass(direct_vm, "jumio", "US", "LOW")
    _mock_sanctions(direct_vm, False)
    contract.initiate_kyc("jumio_session_old")

    first = json.loads(contract.get_badge_status(ALICE))["expires_at"]

    direct_vm.timestamp += 10
    _mock_kyc_pass(direct_vm, "jumio", "US", "LOW")
    _mock_sanctions(direct_vm, False)
    contract.renew_kyc("jumio_session_new")

    second = json.loads(contract.get_badge_status(ALICE))["expires_at"]
    assert second > first


def test_only_officer_can_revoke(contract, direct_vm):
    direct_vm.sender = ALICE
    _mock_kyc_pass(direct_vm, "jumio", "US", "LOW")
    _mock_sanctions(direct_vm, False)
    contract.initiate_kyc("jumio_session_ok")

    with pytest.raises(Exception, match="only compliance officer"):
        contract.revoke_badge(ALICE, "manual review")

    direct_vm.sender = OFFICER
    contract.revoke_badge(ALICE, "sanctions update")
    assert contract.is_compliant(ALICE) is False


def test_no_pii_is_stored(contract, direct_vm):
    direct_vm.sender = ALICE
    _mock_kyc_pass(direct_vm, "jumio", "US", "LOW")
    _mock_sanctions(direct_vm, False)
    contract.initiate_kyc("jumio_sensitive_session_123456789")

    badge = json.loads(contract.get_badge_status(ALICE))
    # only session ref prefix is stored, no names or documents
    assert len(badge["last_session_ref"]) <= 32
    assert "name" not in badge
    assert "document" not in badge
