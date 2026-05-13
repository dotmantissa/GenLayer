"""Direct tests for drought_subsidy_disbursement.py."""

import json
import pytest

FARM1 = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
FARM2 = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("drought_subsidy_disbursement.py")


def _enroll(contract, direct_vm, threshold=4, source="noaa"):
    direct_vm.sender = FARM1
    return contract.enroll_farm("06037", FARM1, "pdsi", threshold, 1000, source)


def _mock_assessment(direct_vm, value, triggered, metric="pdsi"):
    direct_vm.mock_web(
        r"ncei\.noaa|usdroughtmonitor",
        {"status": 200, "body": json.dumps({"county": "06037", "series": [{"value": value}]})},
    )
    direct_vm.mock_llm(
        r"You evaluate drought subsidy triggers",
        {
            "metric": metric,
            "value": value,
            "triggered": triggered,
            "context": "normalized",
        },
    )


def test_enroll_and_read_happy_path(contract, direct_vm):
    enrollment_id = _enroll(contract, direct_vm)
    rec = json.loads(contract.get_enrollment(enrollment_id))

    assert rec["county_fips"] == "06037"
    assert rec["status"] == "ACTIVE"


def test_reject_invalid_metric(contract, direct_vm):
    direct_vm.sender = FARM1
    with pytest.raises(Exception, match="unsupported drought metric"):
        contract.enroll_farm("06037", FARM1, "abc", 4, 1000, "noaa")


def test_reject_invalid_source(contract, direct_vm):
    direct_vm.sender = FARM1
    with pytest.raises(Exception, match="unsupported data source"):
        contract.enroll_farm("06037", FARM1, "pdsi", 4, 1000, "other")


def test_reject_non_positive_subsidy(contract, direct_vm):
    direct_vm.sender = FARM1
    with pytest.raises(Exception, match="subsidy must be positive"):
        contract.enroll_farm("06037", FARM1, "pdsi", 4, 0, "noaa")


def test_settled_paid_when_threshold_crosses(contract, direct_vm):
    enrollment_id = _enroll(contract, direct_vm, threshold=4)
    _mock_assessment(direct_vm, value=-5, triggered=False)

    out = contract.evaluate_and_disburse(enrollment_id)
    rec = json.loads(contract.get_enrollment(enrollment_id))

    assert out == "SETTLED_PAID"
    assert rec["triggered"] is True
    assert contract.balance_of(FARM1) == 1000


def test_settled_not_triggered_when_threshold_not_crossed(contract, direct_vm):
    enrollment_id = _enroll(contract, direct_vm, threshold=6)
    _mock_assessment(direct_vm, value=-3, triggered=False)

    out = contract.evaluate_and_disburse(enrollment_id)
    rec = json.loads(contract.get_enrollment(enrollment_id))

    assert out == "SETTLED_NOT_TRIGGERED"
    assert rec["triggered"] is False
    assert contract.balance_of(FARM1) == 0


def test_llm_contextual_trigger_can_force_disbursement(contract, direct_vm):
    enrollment_id = _enroll(contract, direct_vm, threshold=8)
    _mock_assessment(direct_vm, value=-2, triggered=True)

    out = contract.evaluate_and_disburse(enrollment_id)
    assert out == "SETTLED_PAID"
    assert contract.balance_of(FARM1) == 1000


def test_non_owner_caller_cannot_steal_payout(contract, direct_vm):
    enrollment_id = _enroll(contract, direct_vm, threshold=4)
    _mock_assessment(direct_vm, value=-6, triggered=False)

    direct_vm.sender = FARM2
    out = contract.evaluate_and_disburse(enrollment_id)

    assert out == "SETTLED_PAID"
    assert contract.balance_of(FARM1) == 1000
    assert contract.balance_of(FARM2) == 0


def test_cannot_evaluate_twice(contract, direct_vm):
    enrollment_id = _enroll(contract, direct_vm, threshold=4)
    _mock_assessment(direct_vm, value=-7, triggered=False)
    contract.evaluate_and_disburse(enrollment_id)

    with pytest.raises(Exception, match="enrollment is not active"):
        contract.evaluate_and_disburse(enrollment_id)


def test_provider_client_error_reverts(contract, direct_vm):
    enrollment_id = _enroll(contract, direct_vm)
    direct_vm.mock_web(r"ncei\.noaa|usdroughtmonitor", {"status": 404, "body": "missing"})

    with pytest.raises(Exception, match="provider client error"):
        contract.evaluate_and_disburse(enrollment_id)
