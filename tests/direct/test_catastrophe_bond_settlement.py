"""Direct tests for catastrophe_bond_settlement.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("catastrophe_bond_settlement.py")


def _fund_and_create(contract, direct_vm, payout=5000):
    direct_vm.sender = ALICE
    contract.top_up_balance(10000)
    return contract.create_bond("Andes Quake", 6.2, -12.05, -77.04, 300.0, payout)


def _mock_feeds(direct_vm):
    direct_vm.mock_web(r"earthquake\.usgs\.gov", {"status": 200, "body": "{\"features\":[]}"})
    direct_vm.mock_web(r"catnet\.swissre\.com", {"status": 200, "body": "{\"events\":[]}"})


def test_top_up_and_balance(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.top_up_balance(2000)
    assert contract.balance_of(ALICE) == 2000


def test_create_bond_happy_path(contract, direct_vm):
    bond_id = _fund_and_create(contract, direct_vm)
    bond = json.loads(contract.get_bond(bond_id))

    assert bond["status"] == "PENDING"
    assert contract.balance_of(ALICE) == 5000


def test_create_bond_invalid_magnitude(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.top_up_balance(1000)
    with pytest.raises(Exception, match="min_magnitude out of range"):
        contract.create_bond("Andes", 2.0, -12.05, -77.04, 300.0, 500)


def test_create_bond_insufficient_balance(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="insufficient sponsor balance"):
        contract.create_bond("Andes", 6.0, -12.05, -77.04, 300.0, 100)


def test_settle_triggered_releases_to_beneficiary(contract, direct_vm):
    bond_id = _fund_and_create(contract, direct_vm, payout=7000)
    _mock_feeds(direct_vm)
    direct_vm.mock_llm(
        r"You are a catastrophe risk assessor",
        {
            "triggered": True,
            "consensus_magnitude": 6.8,
            "consensus_distance_km": 120.0,
            "source_count": 2,
            "reason": "event confirmed across both sources",
        },
    )

    status = contract.settle_bond(bond_id, BOB)
    bond = json.loads(contract.get_bond(bond_id))

    assert status == "TRIGGERED"
    assert bond["settled_to"] == BOB
    assert contract.balance_of(BOB) == 7000


def test_settle_not_triggered_returns_to_sponsor(contract, direct_vm):
    bond_id = _fund_and_create(contract, direct_vm, payout=6000)
    _mock_feeds(direct_vm)
    direct_vm.mock_llm(
        r"You are a catastrophe risk assessor",
        {
            "triggered": False,
            "consensus_magnitude": 5.4,
            "consensus_distance_km": 600.0,
            "source_count": 2,
            "reason": "below contractual criteria",
        },
    )

    status = contract.settle_bond(bond_id, BOB)
    assert status == "NOT_TRIGGERED"
    assert contract.balance_of(ALICE) == 10000


def test_cross_validation_required_even_if_model_says_triggered(contract, direct_vm):
    bond_id = _fund_and_create(contract, direct_vm, payout=4000)
    _mock_feeds(direct_vm)
    direct_vm.mock_llm(
        r"You are a catastrophe risk assessor",
        {
            "triggered": True,
            "consensus_magnitude": 7.1,
            "consensus_distance_km": 80.0,
            "source_count": 1,
            "reason": "single source only",
        },
    )

    status = contract.settle_bond(bond_id, BOB)
    assert status == "NOT_TRIGGERED"
    assert contract.balance_of(BOB) == 0


def test_provider_error_reverts(contract, direct_vm):
    bond_id = _fund_and_create(contract, direct_vm)
    direct_vm.mock_web(r"earthquake\.usgs\.gov", {"status": 500, "body": "err"})
    direct_vm.mock_web(r"catnet\.swissre\.com", {"status": 200, "body": "ok"})

    with pytest.raises(Exception, match="usgs server error"):
        contract.settle_bond(bond_id, BOB)


def test_cannot_settle_twice(contract, direct_vm):
    bond_id = _fund_and_create(contract, direct_vm)
    _mock_feeds(direct_vm)
    direct_vm.mock_llm(
        r"You are a catastrophe risk assessor",
        {
            "triggered": False,
            "consensus_magnitude": 5.9,
            "consensus_distance_km": 500.0,
            "source_count": 2,
            "reason": "not qualifying",
        },
    )

    contract.settle_bond(bond_id, BOB)
    with pytest.raises(Exception, match="bond already settled"):
        contract.settle_bond(bond_id, BOB)
