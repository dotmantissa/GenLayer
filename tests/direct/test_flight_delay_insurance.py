"""Direct tests for flight_delay_insurance.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("flight_delay_insurance.py")


def _create_policy(contract, direct_vm, threshold=30, provider="aviationstack"):
    direct_vm.sender = ALICE
    contract.top_up_balance(1_000)
    return contract.create_policy(
        "UA100",
        "SFO",
        "JFK",
        "2026-05-20T08:00:00Z",
        "2026-05-20T16:00:00Z",
        threshold,
        100,
        500,
        provider,
        "test_key",
    )


def _mock_delay_result(direct_vm, dep_delay, arr_delay, covered_event):
    direct_vm.mock_web(
        r"aviationstack|flightaware",
        {
            "status": 200,
            "body": json.dumps(
                {
                    "data": [
                        {
                            "flight": {"iata": "UA100"},
                            "departure": {"iata": "SFO", "delay": dep_delay},
                            "arrival": {"iata": "JFK", "delay": arr_delay},
                        }
                    ]
                }
            ),
        },
    )
    direct_vm.mock_llm(
        r"You are a flight claims adjudicator",
        {
            "departure_delay_minutes": dep_delay,
            "arrival_delay_minutes": arr_delay,
            "ambiguous_delay_code": "",
            "covered_event": covered_event,
            "reason": "parsed",
        },
    )


def test_create_policy_and_read(contract, direct_vm):
    policy_id = _create_policy(contract, direct_vm)
    policy = json.loads(contract.get_policy(policy_id))

    assert policy["flight_number"] == "UA100"
    assert policy["status"] == "ACTIVE"
    assert contract.balance_of(ALICE) == 900


def test_reject_invalid_iata(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.top_up_balance(500)

    with pytest.raises(Exception, match="invalid IATA code"):
        contract.create_policy(
            "UA100",
            "SF",
            "JFK",
            "2026-05-20T08:00:00Z",
            "2026-05-20T16:00:00Z",
            30,
            100,
            500,
            "aviationstack",
            "test_key",
        )


def test_reject_unsupported_provider(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.top_up_balance(500)

    with pytest.raises(Exception, match="unsupported provider"):
        contract.create_policy(
            "UA100",
            "SFO",
            "JFK",
            "2026-05-20T08:00:00Z",
            "2026-05-20T16:00:00Z",
            30,
            100,
            500,
            "unknown",
            "x",
        )


def test_reject_insufficient_balance(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.top_up_balance(50)

    with pytest.raises(Exception, match="insufficient balance"):
        contract.create_policy(
            "UA100",
            "SFO",
            "JFK",
            "2026-05-20T08:00:00Z",
            "2026-05-20T16:00:00Z",
            30,
            100,
            500,
            "aviationstack",
            "test_key",
        )


def test_settles_paid_when_delay_exceeds_threshold(contract, direct_vm):
    policy_id = _create_policy(contract, direct_vm, threshold=30)
    _mock_delay_result(direct_vm, dep_delay=45, arr_delay=20, covered_event=False)

    status = contract.resolve_policy(policy_id)
    policy = json.loads(contract.get_policy(policy_id))

    assert status == "SETTLED_PAID"
    assert policy["covered"] is True
    assert contract.balance_of(ALICE) == 1400


def test_settles_denied_when_below_threshold(contract, direct_vm):
    policy_id = _create_policy(contract, direct_vm, threshold=30)
    _mock_delay_result(direct_vm, dep_delay=10, arr_delay=12, covered_event=False)

    status = contract.resolve_policy(policy_id)
    policy = json.loads(contract.get_policy(policy_id))

    assert status == "SETTLED_DENIED"
    assert policy["covered"] is False
    assert contract.balance_of(ALICE) == 900


def test_ambiguous_code_can_trigger_covered_event(contract, direct_vm):
    policy_id = _create_policy(contract, direct_vm, threshold=60)

    direct_vm.mock_web(
        r"aviationstack|flightaware",
        {"status": 200, "body": json.dumps({"data": [{"delay_code": "ATC-HOLD"}]})},
    )
    direct_vm.mock_llm(
        r"You are a flight claims adjudicator",
        {
            "departure_delay_minutes": 5,
            "arrival_delay_minutes": 8,
            "ambiguous_delay_code": "ATC-HOLD",
            "covered_event": True,
            "reason": "operations delay code interpreted as covered",
        },
    )

    status = contract.resolve_policy(policy_id)
    assert status == "SETTLED_PAID"
    assert contract.balance_of(ALICE) == 1400


def test_cannot_resolve_policy_twice(contract, direct_vm):
    policy_id = _create_policy(contract, direct_vm)
    _mock_delay_result(direct_vm, dep_delay=35, arr_delay=0, covered_event=False)
    contract.resolve_policy(policy_id)

    with pytest.raises(Exception, match="policy is not active"):
        contract.resolve_policy(policy_id)


def test_non_holder_can_resolve_but_payout_goes_to_holder(contract, direct_vm):
    policy_id = _create_policy(contract, direct_vm)
    _mock_delay_result(direct_vm, dep_delay=50, arr_delay=0, covered_event=False)

    direct_vm.sender = BOB
    status = contract.resolve_policy(policy_id)

    assert status == "SETTLED_PAID"
    assert contract.balance_of(ALICE) == 1400
    assert contract.balance_of(BOB) == 0


def test_provider_client_error_reverts(contract, direct_vm):
    policy_id = _create_policy(contract, direct_vm)
    direct_vm.mock_web(r"aviationstack|flightaware", {"status": 404, "body": "not found"})

    with pytest.raises(Exception, match="provider client error"):
        contract.resolve_policy(policy_id)
