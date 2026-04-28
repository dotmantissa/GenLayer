"""
Direct-mode tests for api_usage_billing.py.
"""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def billing(direct_deploy):
    return direct_deploy("api_usage_billing.py")


def test_register_and_subscribe_store_data(billing, direct_vm):
    direct_vm.sender = ALICE
    billing.register_api("weather", 50, 3)
    billing.subscribe("weather", 120, 200)

    report = json.loads(billing.get_usage_report(ALICE))
    assert report["credit_balance_wei"] == 200
    assert report["apis"][0]["api_name"] == "weather"
    assert report["apis"][0]["cost_per_call_wei"] == 50
    assert report["apis"][0]["api_daily_limit_calls"] == 3
    assert report["apis"][0]["consumer_max_daily_spend_wei"] == 120


def test_consume_credit_bills_and_tracks_usage(billing, direct_vm):
    direct_vm.sender = ALICE
    billing.register_api("news", 40, 5)
    billing.subscribe("news", 120, 200)

    billing.consume_credit("news", ALICE)
    billing.consume_credit("news", ALICE)

    report = json.loads(billing.get_usage_report(ALICE))
    api = report["apis"][0]
    assert api["calls_today"] == 2
    assert api["spend_today_wei"] == 80
    assert report["credit_balance_wei"] == 120


def test_daily_call_limit_exceeded_reverts(billing, direct_vm):
    direct_vm.sender = ALICE
    billing.register_api("stocks", 10, 2)
    billing.subscribe("stocks", 100, 200)

    billing.consume_credit("stocks", ALICE)
    billing.consume_credit("stocks", ALICE)
    with pytest.raises(Exception, match="daily call limit exceeded"):
        billing.consume_credit("stocks", ALICE)


def test_daily_spend_limit_exceeded_reverts(billing, direct_vm):
    direct_vm.sender = ALICE
    billing.register_api("sports", 70, 5)
    billing.subscribe("sports", 100, 500)

    billing.consume_credit("sports", ALICE)
    with pytest.raises(Exception, match="max daily spend exceeded"):
        billing.consume_credit("sports", ALICE)


def test_credit_exhaustion_reverts(billing, direct_vm):
    direct_vm.sender = ALICE
    billing.register_api("maps", 60, 5)
    billing.subscribe("maps", 500, 50)

    with pytest.raises(Exception, match="insufficient prepaid credit"):
        billing.consume_credit("maps", ALICE)


def test_top_up_credit_allows_future_billing(billing, direct_vm):
    direct_vm.sender = ALICE
    billing.register_api("translate", 60, 5)
    billing.subscribe("translate", 500, 50)

    with pytest.raises(Exception, match="insufficient prepaid credit"):
        billing.consume_credit("translate", ALICE)

    billing.top_up_credit(100)
    billing.consume_credit("translate", ALICE)

    report = json.loads(billing.get_usage_report(ALICE))
    assert report["credit_balance_wei"] == 90
    assert report["apis"][0]["calls_today"] == 1


def test_usage_resets_at_utc_midnight(billing, direct_vm):
    direct_vm.sender = ALICE
    billing.register_api("oracle", 20, 10)
    billing.subscribe("oracle", 1000, 200)

    direct_vm.timestamp = 1704067200 + 86399
    billing.consume_credit("oracle", ALICE)

    report_before = json.loads(billing.get_usage_report(ALICE))
    assert report_before["apis"][0]["calls_today"] == 1

    direct_vm.timestamp = 1704067200 + 86400
    report_after = json.loads(billing.get_usage_report(ALICE))
    assert report_after["apis"][0]["calls_today"] == 0
    assert report_after["apis"][0]["spend_today_wei"] == 0


def test_consumer_isolated_per_api_and_per_address(billing, direct_vm):
    direct_vm.sender = ALICE
    billing.register_api("api-a", 25, 10)
    billing.register_api("api-b", 30, 10)
    billing.subscribe("api-a", 100, 200)
    billing.subscribe("api-b", 100, 0)

    billing.consume_credit("api-a", ALICE)

    report_alice = json.loads(billing.get_usage_report(ALICE))
    by_api = {r["api_name"]: r for r in report_alice["apis"]}
    assert by_api["api-a"]["calls_today"] == 1
    assert by_api["api-b"]["calls_today"] == 0

    direct_vm.sender = BOB
    billing.subscribe("api-a", 100, 100)
    billing.consume_credit("api-a", BOB)

    report_bob = json.loads(billing.get_usage_report(BOB))
    assert report_bob["apis"][0]["calls_today"] == 1


def test_consumer_integration_example_contains_billing_step(billing):
    text = billing.consumer_integration_example()
    assert "consume_credit" in text
    assert "before gl.nondet.web.get" in text
