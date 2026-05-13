"""Direct tests for vesting_disclosure_verifier.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("vesting_disclosure_verifier.py")


def _create(contract, direct_vm, tolerance=10):
    direct_vm.sender = ALICE
    return contract.create_case(
        "ritual",
        "0x1234567890abcdef1234567890abcdef12345678",
        "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd",
        "https://example.com/vesting-disclosure",
        tolerance,
    )


def _mock_sources_ok(direct_vm):
    direct_vm.mock_web(r"api\.etherscan\.io/api\?module=account&action=tokentx", {"status": 200, "body": '{"ok":true}'})
    direct_vm.mock_web(r"api\.etherscan\.io/api\?module=contract&action=getsourcecode", {"status": 200, "body": '{"ok":true}'})
    direct_vm.mock_web(r"example\.com/vesting-disclosure", {"status": 200, "body": "disclosure text"})


def test_create_case_and_read(contract, direct_vm):
    cid = _create(contract, direct_vm)
    c = json.loads(contract.get_case(cid))

    assert c["status"] == "PENDING"
    assert c["project_key"] == "ritual"


def test_create_invalid_registry(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid registry_contract"):
        contract.create_case("ritual", "bad", "0xabcdefffff", "https://example.com/a", 10)


def test_create_invalid_disclosure_url(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid disclosure_url"):
        contract.create_case("ritual", "0x1234567890", "0xabcdef1234", "ftp://doc", 10)


def test_resolve_compliant(contract, direct_vm):
    cid = _create(contract, direct_vm, tolerance=15)
    _mock_sources_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a vesting compliance analyst",
        {
            "verdict": "COMPLIANT",
            "mismatch_percent": 8,
            "unlock_events_observed": 12,
            "consensus_sources": 3,
            "reason": "unlocks follow disclosed cadence",
        },
    )

    out = contract.resolve_case(cid)
    assert out == "COMPLIANT"
    assert contract.get_latest_status("ritual") == "COMPLIANT"


def test_resolve_violation(contract, direct_vm):
    cid = _create(contract, direct_vm, tolerance=5)
    _mock_sources_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a vesting compliance analyst",
        {
            "verdict": "COMPLIANT",
            "mismatch_percent": 30,
            "unlock_events_observed": 9,
            "consensus_sources": 2,
            "reason": "large timing and amount drift",
        },
    )

    out = contract.resolve_case(cid)
    assert out == "VIOLATION"


def test_provider_error_reverts(contract, direct_vm):
    cid = _create(contract, direct_vm)
    direct_vm.mock_web(r"api\.etherscan\.io/api\?module=account&action=tokentx", {"status": 500, "body": "err"})
    direct_vm.mock_web(r"api\.etherscan\.io/api\?module=contract&action=getsourcecode", {"status": 200, "body": "ok"})
    direct_vm.mock_web(r"example\.com/vesting-disclosure", {"status": 200, "body": "ok"})

    with pytest.raises(Exception, match="onchain server error"):
        contract.resolve_case(cid)


def test_low_consensus_forces_violation(contract, direct_vm):
    cid = _create(contract, direct_vm, tolerance=20)
    _mock_sources_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a vesting compliance analyst",
        {
            "verdict": "COMPLIANT",
            "mismatch_percent": 1,
            "unlock_events_observed": 5,
            "consensus_sources": 0,
            "reason": "single source interpretation",
        },
    )

    out = contract.resolve_case(cid)
    assert out == "VIOLATION"


def test_cannot_resolve_twice(contract, direct_vm):
    cid = _create(contract, direct_vm)
    _mock_sources_ok(direct_vm)
    direct_vm.mock_llm(
        r"You are a vesting compliance analyst",
        {
            "verdict": "COMPLIANT",
            "mismatch_percent": 3,
            "unlock_events_observed": 6,
            "consensus_sources": 2,
            "reason": "aligned",
        },
    )

    contract.resolve_case(cid)
    with pytest.raises(Exception, match="case already resolved"):
        contract.resolve_case(cid)


def test_missing_case_reverts(contract):
    with pytest.raises(Exception, match="case not found"):
        contract.get_case("999")
