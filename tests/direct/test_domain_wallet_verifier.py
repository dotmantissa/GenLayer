"""Direct tests for domain_wallet_verifier.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("domain_wallet_verifier.py")


def _create(contract, direct_vm, domain="example.com", wallet=ALICE):
    direct_vm.sender = ALICE
    return contract.create_claim(domain, wallet)


def _mock_sources(direct_vm):
    direct_vm.mock_web(r"dns\.google/resolve", {"status": 200, "body": "{\"Answer\":[]}"})
    direct_vm.mock_web(r"rdap\.org/domain", {"status": 200, "body": "{\"entities\":[]}"})


def test_create_claim_and_read(contract, direct_vm):
    claim_id = _create(contract, direct_vm)
    claim = json.loads(contract.get_claim(claim_id))

    assert claim["status"] == "PENDING"
    assert claim["domain"] == "example.com"


def test_create_claim_invalid_domain(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid domain"):
        contract.create_claim("bad domain", ALICE)


def test_create_claim_invalid_wallet(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid wallet"):
        contract.create_claim("example.com", "wallet1")


def test_verify_claim_success(contract, direct_vm):
    claim_id = _create(contract, direct_vm)
    _mock_sources(direct_vm)
    direct_vm.mock_llm(
        r"You are an identity verification analyst",
        {
            "verified": True,
            "txt_wallet": ALICE,
            "whois_registrant": "Example Org",
            "reason": "wallet published in txt and registrant present",
        },
    )

    verified = contract.verify_claim(claim_id)
    claim = json.loads(contract.get_claim(claim_id))

    assert verified is True
    assert claim["status"] == "VERIFIED"


def test_verify_claim_rejected_when_wallet_mismatch(contract, direct_vm):
    claim_id = _create(contract, direct_vm)
    _mock_sources(direct_vm)
    direct_vm.mock_llm(
        r"You are an identity verification analyst",
        {
            "verified": True,
            "txt_wallet": "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
            "whois_registrant": "Example Org",
            "reason": "found different wallet",
        },
    )

    verified = contract.verify_claim(claim_id)
    claim = json.loads(contract.get_claim(claim_id))

    assert verified is False
    assert claim["status"] == "REJECTED"


def test_verify_claim_rejected_when_no_whois_identity(contract, direct_vm):
    claim_id = _create(contract, direct_vm)
    _mock_sources(direct_vm)
    direct_vm.mock_llm(
        r"You are an identity verification analyst",
        {
            "verified": True,
            "txt_wallet": ALICE,
            "whois_registrant": "",
            "reason": "wallet present but no ownership context",
        },
    )

    verified = contract.verify_claim(claim_id)
    assert verified is False


def test_provider_error_reverts(contract, direct_vm):
    claim_id = _create(contract, direct_vm)
    direct_vm.mock_web(r"dns\.google/resolve", {"status": 500, "body": "err"})
    direct_vm.mock_web(r"rdap\.org/domain", {"status": 200, "body": "ok"})

    with pytest.raises(Exception, match="dns server error"):
        contract.verify_claim(claim_id)


def test_cannot_verify_twice(contract, direct_vm):
    claim_id = _create(contract, direct_vm)
    _mock_sources(direct_vm)
    direct_vm.mock_llm(
        r"You are an identity verification analyst",
        {
            "verified": False,
            "txt_wallet": "",
            "whois_registrant": "Example Org",
            "reason": "no txt wallet",
        },
    )

    contract.verify_claim(claim_id)
    with pytest.raises(Exception, match="claim already resolved"):
        contract.verify_claim(claim_id)


def test_missing_claim_reverts(contract):
    with pytest.raises(Exception, match="claim not found"):
        contract.get_claim("999")
