"""Direct tests for campaign_kpi_settlement.py."""

import json
import pytest

SPONSOR = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
CREATOR = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
OTHER = "0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("campaign_kpi_settlement.py")


def _create(contract, direct_vm, end_ts=1000, payout=500):
    direct_vm.sender = SPONSOR
    contract.top_up_balance(2_000)
    return contract.create_campaign(
        CREATOR,
        "https://twitter.com/user/status/123",
        end_ts,
        100,
        20,
        5_000,
        payout,
        "x_public",
    )


def _mock_metrics(direct_vm, likes, shares, reach, kpi_met=False):
    direct_vm.mock_web(r"twitter|nitter", {"status": 200, "body": "metrics payload"})
    direct_vm.mock_llm(
        r"You are a campaign KPI adjudicator",
        {
            "likes": likes,
            "shares": shares,
            "reach": reach,
            "kpi_met": kpi_met,
            "reason": "computed",
        },
    )


def test_top_up_and_balance(contract, direct_vm):
    direct_vm.sender = SPONSOR
    contract.top_up_balance(100)
    assert contract.balance_of(SPONSOR) == 100


def test_create_campaign_happy_path(contract, direct_vm):
    cid = _create(contract, direct_vm)
    c = json.loads(contract.get_campaign(cid))

    assert c["status"] == "ACTIVE"
    assert contract.balance_of(SPONSOR) == 1500


def test_create_campaign_invalid_source(contract, direct_vm):
    direct_vm.sender = SPONSOR
    contract.top_up_balance(1000)

    with pytest.raises(Exception, match="unsupported source"):
        contract.create_campaign(CREATOR, "https://x.com/p/1", 1000, 1, 1, 1, 100, "bad")


def test_create_campaign_insufficient_balance(contract, direct_vm):
    direct_vm.sender = SPONSOR
    contract.top_up_balance(10)

    with pytest.raises(Exception, match="insufficient sponsor balance"):
        contract.create_campaign(CREATOR, "https://x.com/p/1", 1000, 1, 1, 1, 100, "x_public")


def test_settle_paid_when_numeric_kpi_met(contract, direct_vm):
    cid = _create(contract, direct_vm, end_ts=1000)
    direct_vm.timestamp = 1200
    _mock_metrics(direct_vm, likes=120, shares=30, reach=6000, kpi_met=False)

    out = contract.settle_campaign(cid)
    c = json.loads(contract.get_campaign(cid))

    assert out == "SETTLED_PAID"
    assert c["kpi_met"] is True
    assert contract.balance_of(CREATOR) == 500


def test_settle_denied_refunds_sponsor(contract, direct_vm):
    cid = _create(contract, direct_vm, end_ts=1000)
    direct_vm.timestamp = 1200
    _mock_metrics(direct_vm, likes=10, shares=2, reach=100, kpi_met=False)

    out = contract.settle_campaign(cid)
    assert out == "SETTLED_DENIED"
    assert contract.balance_of(SPONSOR) == 2000


def test_settle_paid_when_llm_quality_clause_accepts(contract, direct_vm):
    cid = _create(contract, direct_vm, end_ts=1000)
    direct_vm.timestamp = 1200
    _mock_metrics(direct_vm, likes=90, shares=10, reach=4500, kpi_met=True)

    out = contract.settle_campaign(cid)
    assert out == "SETTLED_PAID"
    assert contract.balance_of(CREATOR) == 500


def test_cannot_settle_before_end_date(contract, direct_vm):
    cid = _create(contract, direct_vm, end_ts=2000)
    direct_vm.timestamp = 1500
    _mock_metrics(direct_vm, likes=200, shares=50, reach=10000, kpi_met=True)

    with pytest.raises(Exception, match="campaign end date not reached"):
        contract.settle_campaign(cid)


def test_any_account_can_settle_but_cannot_redirect_funds(contract, direct_vm):
    cid = _create(contract, direct_vm, end_ts=1000)
    direct_vm.timestamp = 1500
    _mock_metrics(direct_vm, likes=120, shares=30, reach=6000, kpi_met=False)

    direct_vm.sender = OTHER
    out = contract.settle_campaign(cid)

    assert out == "SETTLED_PAID"
    assert contract.balance_of(OTHER) == 0
    assert contract.balance_of(CREATOR) == 500


def test_provider_client_error_reverts(contract, direct_vm):
    cid = _create(contract, direct_vm, end_ts=1000)
    direct_vm.timestamp = 1200
    direct_vm.mock_web(r"twitter|nitter", {"status": 404, "body": "missing"})

    with pytest.raises(Exception, match="source client error"):
        contract.settle_campaign(cid)


def test_cannot_settle_twice(contract, direct_vm):
    cid = _create(contract, direct_vm, end_ts=1000)
    direct_vm.timestamp = 1200
    _mock_metrics(direct_vm, likes=150, shares=30, reach=8000, kpi_met=False)
    contract.settle_campaign(cid)

    with pytest.raises(Exception, match="campaign is not active"):
        contract.settle_campaign(cid)
