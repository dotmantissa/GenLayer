"""Direct tests for cyber_breach_insurance.py."""

import json
import pytest

INSURER = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
OTHER = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("cyber_breach_insurance.py")


def _create(contract, direct_vm, payout=500):
    direct_vm.sender = INSURER
    contract.top_up_balance(2000)
    return contract.create_policy("example.com", payout, "test_hibp_key")


def _mock_breach(direct_vm, confirmed=True, resolve_to="policyholder"):
    direct_vm.mock_web(r"haveibeenpwned|ocrportal\.hhs", {"status": 200, "body": "breach dataset"})
    direct_vm.mock_llm(
        r"You are a cyber breach insurance adjudicator",
        {
            "breach_confirmed": confirmed,
            "resolve_to": resolve_to,
            "reason": "domain matched known breach",
        },
    )


def test_top_up_and_balance(contract, direct_vm):
    direct_vm.sender = INSURER
    contract.top_up_balance(100)
    assert contract.balance_of(INSURER) == 100


def test_create_policy_happy_path(contract, direct_vm):
    pid = _create(contract, direct_vm)
    p = json.loads(contract.get_policy(pid))

    assert p["status"] == "ACTIVE"
    assert contract.balance_of(INSURER) == 1500


def test_create_policy_invalid_domain(contract, direct_vm):
    direct_vm.sender = INSURER
    contract.top_up_balance(1000)

    with pytest.raises(Exception, match="invalid policyholder domain"):
        contract.create_policy("bad", 500, "key")


def test_create_policy_insufficient_reserve(contract, direct_vm):
    direct_vm.sender = INSURER
    contract.top_up_balance(100)

    with pytest.raises(Exception, match="insufficient reserve balance"):
        contract.create_policy("example.com", 500, "key")


def test_settle_policyholder_when_breach_confirmed(contract, direct_vm):
    pid = _create(contract, direct_vm)
    _mock_breach(direct_vm, confirmed=True, resolve_to="insurer")

    out = contract.settle_policy(pid)
    assert out == "SETTLED_POLICYHOLDER"
    assert contract.balance_of(INSURER) == 2000


def test_settle_insurer_when_not_confirmed(contract, direct_vm):
    pid = _create(contract, direct_vm)
    _mock_breach(direct_vm, confirmed=False, resolve_to="policyholder")

    out = contract.settle_policy(pid)
    assert out == "SETTLED_INSURER"
    assert contract.balance_of(INSURER) == 2000


def test_any_account_can_trigger_settlement_without_redirecting(contract, direct_vm):
    pid = _create(contract, direct_vm)
    _mock_breach(direct_vm, confirmed=True, resolve_to="policyholder")

    direct_vm.sender = OTHER
    out = contract.settle_policy(pid)

    assert out == "SETTLED_POLICYHOLDER"
    assert contract.balance_of(OTHER) == 0
    assert contract.balance_of(INSURER) == 2000


def test_provider_error_reverts(contract, direct_vm):
    pid = _create(contract, direct_vm)
    direct_vm.mock_web(r"haveibeenpwned", {"status": 404, "body": "missing"})
    direct_vm.mock_web(r"ocrportal\.hhs", {"status": 200, "body": "ok"})

    with pytest.raises(Exception, match="hibp client error"):
        contract.settle_policy(pid)


def test_cannot_settle_twice(contract, direct_vm):
    pid = _create(contract, direct_vm)
    _mock_breach(direct_vm, confirmed=True)
    contract.settle_policy(pid)

    with pytest.raises(Exception, match="policy is not active"):
        contract.settle_policy(pid)
