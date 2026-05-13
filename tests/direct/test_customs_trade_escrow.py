"""Direct tests for customs_trade_escrow.py."""

import json
import pytest

SELLER = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BUYER = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
OTHER = "0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("customs_trade_escrow.py")


def _create(contract, direct_vm, carrier="dhl", amount=500):
    direct_vm.sender = BUYER
    contract.top_up_balance(2000)
    return contract.create_trade(SELLER, BUYER, carrier, "ABC1234567", amount)


def _mock_status(direct_vm, customs=True, delivered=True, resolve_to="seller"):
    direct_vm.mock_web(r"dhl|ups|fedex", {"status": 200, "body": "carrier status payload"})
    direct_vm.mock_llm(
        r"You are a shipment status adjudicator",
        {
            "customs_cleared": customs,
            "delivered": delivered,
            "resolve_to": resolve_to,
            "reason": "status code interpretation",
        },
    )


def test_top_up_and_balance(contract, direct_vm):
    direct_vm.sender = BUYER
    contract.top_up_balance(100)
    assert contract.balance_of(BUYER) == 100


def test_create_trade_happy_path(contract, direct_vm):
    tid = _create(contract, direct_vm)
    t = json.loads(contract.get_trade(tid))

    assert t["status"] == "ACTIVE"
    assert contract.balance_of(BUYER) == 1500


def test_create_trade_invalid_carrier(contract, direct_vm):
    direct_vm.sender = BUYER
    contract.top_up_balance(1000)

    with pytest.raises(Exception, match="unsupported carrier"):
        contract.create_trade(SELLER, BUYER, "other", "ABC123", 500)


def test_create_trade_requires_buyer_creator(contract, direct_vm):
    direct_vm.sender = OTHER
    contract.top_up_balance(1000)

    with pytest.raises(Exception, match="trade creator must be buyer"):
        contract.create_trade(SELLER, BUYER, "dhl", "ABC123", 500)


def test_create_trade_insufficient_balance(contract, direct_vm):
    direct_vm.sender = BUYER
    contract.top_up_balance(100)

    with pytest.raises(Exception, match="insufficient buyer balance"):
        contract.create_trade(SELLER, BUYER, "dhl", "ABC123", 500)


def test_settle_seller_when_cleared_and_delivered(contract, direct_vm):
    tid = _create(contract, direct_vm)
    _mock_status(direct_vm, customs=True, delivered=True, resolve_to="buyer")

    out = contract.settle_trade(tid)
    assert out == "SETTLED_SELLER"
    assert contract.balance_of(SELLER) == 500


def test_settle_buyer_when_not_delivered(contract, direct_vm):
    tid = _create(contract, direct_vm)
    _mock_status(direct_vm, customs=True, delivered=False, resolve_to="seller")

    out = contract.settle_trade(tid)
    assert out == "SETTLED_BUYER"
    assert contract.balance_of(BUYER) == 2000


def test_any_account_can_trigger_settlement_without_redirecting(contract, direct_vm):
    tid = _create(contract, direct_vm)
    _mock_status(direct_vm, customs=True, delivered=True, resolve_to="seller")

    direct_vm.sender = OTHER
    out = contract.settle_trade(tid)

    assert out == "SETTLED_SELLER"
    assert contract.balance_of(OTHER) == 0
    assert contract.balance_of(SELLER) == 500


def test_provider_error_reverts(contract, direct_vm):
    tid = _create(contract, direct_vm)
    direct_vm.mock_web(r"dhl|ups|fedex", {"status": 404, "body": "missing"})

    with pytest.raises(Exception, match="carrier client error"):
        contract.settle_trade(tid)


def test_cannot_settle_twice(contract, direct_vm):
    tid = _create(contract, direct_vm)
    _mock_status(direct_vm, customs=True, delivered=True)
    contract.settle_trade(tid)

    with pytest.raises(Exception, match="trade is not active"):
        contract.settle_trade(tid)
