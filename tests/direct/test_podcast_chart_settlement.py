"""Direct tests for podcast_chart_settlement.py."""

import json
import pytest

SPONSOR = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
PODCASTER = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
OTHER = "0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("podcast_chart_settlement.py")


def _create(contract, direct_vm, mode="both", payout=500):
    direct_vm.sender = SPONSOR
    contract.top_up_balance(2000)
    return contract.create_deal(
        PODCASTER,
        "Ritual Radio",
        "2026-Q2",
        50,
        60,
        10000,
        payout,
        mode,
    )


def _mock_data(direct_vm, spotify_rank=40, apple_rank=55, listeners=12000, kpi_met=False):
    direct_vm.mock_web(r"spotify", {"status": 200, "body": "spotify chart payload"})
    direct_vm.mock_web(r"apple", {"status": 200, "body": "apple chart payload"})
    direct_vm.mock_llm(
        r"You are a podcast KPI adjudicator",
        {
            "spotify_rank": spotify_rank,
            "apple_rank": apple_rank,
            "estimated_listeners": listeners,
            "kpi_met": kpi_met,
            "reason": "context adjusted",
        },
    )


def test_top_up_and_balance(contract, direct_vm):
    direct_vm.sender = SPONSOR
    contract.top_up_balance(100)
    assert contract.balance_of(SPONSOR) == 100


def test_create_deal_happy_path(contract, direct_vm):
    did = _create(contract, direct_vm)
    d = json.loads(contract.get_deal(did))

    assert d["status"] == "ACTIVE"
    assert contract.balance_of(SPONSOR) == 1500


def test_create_deal_invalid_source_mode(contract, direct_vm):
    direct_vm.sender = SPONSOR
    contract.top_up_balance(1000)

    with pytest.raises(Exception, match="unsupported source mode"):
        contract.create_deal(PODCASTER, "Show", "2026-Q2", 50, 50, 1000, 500, "bad")


def test_create_deal_insufficient_balance(contract, direct_vm):
    direct_vm.sender = SPONSOR
    contract.top_up_balance(10)

    with pytest.raises(Exception, match="insufficient sponsor balance"):
        contract.create_deal(PODCASTER, "Show", "2026-Q2", 50, 50, 1000, 500, "both")


def test_settle_paid_when_numeric_kpi_met(contract, direct_vm):
    did = _create(contract, direct_vm)
    _mock_data(direct_vm, spotify_rank=45, apple_rank=50, listeners=15000, kpi_met=False)

    out = contract.settle_deal(did)
    assert out == "SETTLED_PAID"
    assert contract.balance_of(PODCASTER) == 500


def test_settle_denied_refunds_sponsor(contract, direct_vm):
    did = _create(contract, direct_vm)
    _mock_data(direct_vm, spotify_rank=99, apple_rank=120, listeners=2000, kpi_met=False)

    out = contract.settle_deal(did)
    assert out == "SETTLED_DENIED"
    assert contract.balance_of(SPONSOR) == 2000


def test_settle_paid_when_llm_context_accepts(contract, direct_vm):
    did = _create(contract, direct_vm)
    _mock_data(direct_vm, spotify_rank=80, apple_rank=100, listeners=7000, kpi_met=True)

    out = contract.settle_deal(did)
    assert out == "SETTLED_PAID"
    assert contract.balance_of(PODCASTER) == 500


def test_any_account_can_settle_without_redirecting_funds(contract, direct_vm):
    did = _create(contract, direct_vm)
    _mock_data(direct_vm, spotify_rank=40, apple_rank=40, listeners=20000, kpi_met=False)

    direct_vm.sender = OTHER
    out = contract.settle_deal(did)

    assert out == "SETTLED_PAID"
    assert contract.balance_of(OTHER) == 0
    assert contract.balance_of(PODCASTER) == 500


def test_spotify_only_mode_ignores_apple_rank(contract, direct_vm):
    did = _create(contract, direct_vm, mode="spotify")
    _mock_data(direct_vm, spotify_rank=30, apple_rank=999, listeners=12000, kpi_met=False)

    out = contract.settle_deal(did)
    assert out == "SETTLED_PAID"


def test_provider_client_error_reverts(contract, direct_vm):
    did = _create(contract, direct_vm, mode="spotify")
    direct_vm.mock_web(r"spotify", {"status": 404, "body": "missing"})

    with pytest.raises(Exception, match="spotify client error"):
        contract.settle_deal(did)


def test_cannot_settle_twice(contract, direct_vm):
    did = _create(contract, direct_vm)
    _mock_data(direct_vm, spotify_rank=30, apple_rank=30, listeners=30000, kpi_met=False)
    contract.settle_deal(did)

    with pytest.raises(Exception, match="deal is not active"):
        contract.settle_deal(did)
