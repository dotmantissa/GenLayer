"""Direct tests for rent_dispute_settlement.py."""

import json
import pytest

LANDLORD = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
TENANT = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
CALLER = "0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("rent_dispute_settlement.py")


def _create(contract, direct_vm, proposed=3000, max_fair=2800, escrow=500):
    direct_vm.sender = CALLER
    contract.top_up_balance(2000)
    return contract.create_dispute(
        LANDLORD,
        TENANT,
        "Brooklyn",
        "2 bed 1 bath apartment",
        proposed,
        max_fair,
        escrow,
    )


def _mock_assessment(direct_vm, low=2500, high=2900, resolved_for="tenant"):
    direct_vm.mock_web(r"zillow", {"status": 200, "body": "zillow listings"})
    direct_vm.mock_web(r"apartments", {"status": 200, "body": "apartments listings"})
    direct_vm.mock_llm(
        r"You are a rental adjudicator",
        {
            "fair_rent_low": low,
            "fair_rent_high": high,
            "resolved_for": resolved_for,
            "reason": "comparable analysis",
        },
    )


def test_top_up_and_balance(contract, direct_vm):
    direct_vm.sender = CALLER
    contract.top_up_balance(100)
    assert contract.balance_of(CALLER) == 100


def test_create_dispute_happy_path(contract, direct_vm):
    did = _create(contract, direct_vm)
    d = json.loads(contract.get_dispute(did))

    assert d["status"] == "ACTIVE"
    assert contract.balance_of(CALLER) == 1500


def test_create_dispute_invalid_wallet(contract, direct_vm):
    direct_vm.sender = CALLER
    contract.top_up_balance(1000)

    with pytest.raises(Exception, match="invalid participant wallet"):
        contract.create_dispute("0x1", TENANT, "Brooklyn", "2 bed", 3000, 2800, 500)


def test_create_dispute_insufficient_escrow(contract, direct_vm):
    direct_vm.sender = CALLER
    contract.top_up_balance(100)

    with pytest.raises(Exception, match="insufficient escrow balance"):
        contract.create_dispute(LANDLORD, TENANT, "Brooklyn", "2 bed", 3000, 2800, 500)


def test_settle_for_tenant_when_proposed_above_range(contract, direct_vm):
    did = _create(contract, direct_vm, proposed=3200, max_fair=3000)
    _mock_assessment(direct_vm, low=2500, high=2900, resolved_for="landlord")

    out = contract.settle_dispute(did)
    assert out == "SETTLED_TENANT"
    assert contract.balance_of(TENANT) == 500


def test_settle_for_landlord_when_within_fair_limits(contract, direct_vm):
    did = _create(contract, direct_vm, proposed=2700, max_fair=2800)
    _mock_assessment(direct_vm, low=2500, high=2900, resolved_for="tenant")

    out = contract.settle_dispute(did)
    assert out == "SETTLED_LANDLORD"
    assert contract.balance_of(LANDLORD) == 500


def test_any_caller_can_settle_without_redirecting_payout(contract, direct_vm):
    did = _create(contract, direct_vm, proposed=3300, max_fair=3000)
    _mock_assessment(direct_vm, low=2500, high=2900, resolved_for="tenant")

    direct_vm.sender = LANDLORD
    out = contract.settle_dispute(did)

    assert out == "SETTLED_TENANT"
    assert contract.balance_of(LANDLORD) == 0
    assert contract.balance_of(TENANT) == 500


def test_provider_client_error_reverts(contract, direct_vm):
    did = _create(contract, direct_vm)
    direct_vm.mock_web(r"zillow", {"status": 404, "body": "missing"})
    direct_vm.mock_web(r"apartments", {"status": 200, "body": "ok"})

    with pytest.raises(Exception, match="zillow client error"):
        contract.settle_dispute(did)


def test_cannot_settle_twice(contract, direct_vm):
    did = _create(contract, direct_vm, proposed=2700, max_fair=2800)
    _mock_assessment(direct_vm, low=2500, high=2900, resolved_for="landlord")
    contract.settle_dispute(did)

    with pytest.raises(Exception, match="dispute is not active"):
        contract.settle_dispute(did)


def test_get_missing_dispute_reverts(contract):
    with pytest.raises(Exception, match="dispute not found"):
        contract.get_dispute("999")
