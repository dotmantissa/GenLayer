"""Direct tests for travel_disruption_claim_settler.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("travel_disruption_claim_settler.py", 3, 1500)


def _mock_sources(direct_vm, advisory_level: int, cancelled: int, scheduled: int):
    direct_vm.mock_web(
        r"api\.tsa\.gov/travel/advisories",
        {"status": 200, "body": json.dumps({"advisory_level": advisory_level})},
    )
    direct_vm.mock_web(
        r"api\.transportation\.gov/airline/cancellations",
        {"status": 200, "body": json.dumps({"cancelled_flights": cancelled, "scheduled_flights": scheduled})},
    )


def test_evaluate_claim_approved(contract, direct_vm):
    direct_vm.sender = ALICE
    _mock_sources(direct_vm, 4, 40, 100)
    direct_vm.mock_llm(r"insurance claims analyst", {"covered": True, "reason": "Weather disruption covered", "confidence": 92})

    cid = contract.evaluate_claim("JFK", "LAX", "AA", "2026-08-22", "Covers weather and government advisory disruptions.", 1200)
    claim = json.loads(contract.get_claim(cid))

    assert claim["status"] == "approved"
    assert claim["payout_usd"] == 1200


def test_evaluate_claim_denied_when_not_covered(contract, direct_vm):
    direct_vm.sender = ALICE
    _mock_sources(direct_vm, 4, 40, 100)
    direct_vm.mock_llm(r"insurance claims analyst", {"covered": False, "reason": "Policy exclusion", "confidence": 80})

    cid = contract.evaluate_claim("JFK", "LAX", "AA", "2026-08-22", "Covers only medical emergency disruptions.", 900)
    claim = json.loads(contract.get_claim(cid))

    assert claim["status"] == "denied"
    assert claim["payout_usd"] == 0


def test_evaluate_claim_denied_when_no_disruption(contract, direct_vm):
    direct_vm.sender = ALICE
    _mock_sources(direct_vm, 1, 1, 100)
    direct_vm.mock_llm(r"insurance claims analyst", {"covered": True, "reason": "Would be covered if disrupted", "confidence": 88})

    cid = contract.evaluate_claim("JFK", "LAX", "AA", "2026-08-22", "Covers disruptions.", 700)
    claim = json.loads(contract.get_claim(cid))

    assert claim["status"] == "denied"


def test_set_thresholds_owner_only(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.set_thresholds(2, 1000)
    thresholds = json.loads(contract.get_thresholds())
    assert thresholds["min_advisory_level"] == 2
    assert thresholds["min_cancel_rate_bps"] == 1000

    direct_vm.sender = BOB
    with pytest.raises(Exception, match="only owner"):
        contract.set_thresholds(3, 1500)


def test_invalid_airport_code(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid airport code"):
        contract.evaluate_claim("JF", "LAX", "AA", "2026-08-22", "Covers disruptions policy text.", 100)


def test_invalid_policy_text(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid policy_text"):
        contract.evaluate_claim("JFK", "LAX", "AA", "2026-08-22", "short", 100)


def test_invalid_claim_amount(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="claim_amount_usd out of range"):
        contract.evaluate_claim("JFK", "LAX", "AA", "2026-08-22", "This policy has enough length text.", 0)


def test_llm_bad_confidence(contract, direct_vm):
    direct_vm.sender = ALICE
    _mock_sources(direct_vm, 4, 40, 100)
    direct_vm.mock_llm(r"insurance claims analyst", {"covered": True, "reason": "ok", "confidence": "bad"})

    with pytest.raises(Exception, match="invalid confidence"):
        contract.evaluate_claim("JFK", "LAX", "AA", "2026-08-22", "Covers disruptions with full details.", 500)


def test_claim_not_found(contract):
    with pytest.raises(Exception, match="claim not found"):
        contract.get_claim("999")


def test_get_all_claims_contains_created(contract, direct_vm):
    direct_vm.sender = ALICE
    _mock_sources(direct_vm, 4, 40, 100)
    direct_vm.mock_llm(r"insurance claims analyst", {"covered": True, "reason": "Covered", "confidence": 91})

    cid = contract.evaluate_claim("JFK", "LAX", "AA", "2026-08-22", "Covers weather and advisory disruptions by policy.", 1300)
    claims = json.loads(contract.get_all_claims())
    assert cid in claims
