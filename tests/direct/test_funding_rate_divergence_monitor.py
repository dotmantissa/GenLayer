"""Direct tests for funding_rate_divergence_monitor.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("funding_rate_divergence_monitor.py", 20, 2)


def _mock_all_venues(direct_vm, b_rate: str, by_rate: str, o_rate: str):
    direct_vm.mock_web(
        r"fapi\.binance\.com/fapi/v1/fundingRate",
        {"status": 200, "body": json.dumps([{"fundingRate": b_rate}])},
    )
    direct_vm.mock_web(
        r"api\.bybit\.com/v5/market/funding/history",
        {"status": 200, "body": json.dumps({"result": {"list": [{"fundingRate": by_rate}]}})},
    )
    direct_vm.mock_web(
        r"okx\.com/api/v5/public/funding-rate",
        {"status": 200, "body": json.dumps({"data": [{"fundingRate": o_rate}]})},
    )


def test_check_divergence_alert_false(contract, direct_vm):
    direct_vm.sender = ALICE
    _mock_all_venues(direct_vm, "0.0008", "0.0009", "0.0010")

    rid = contract.check_divergence("BTCUSDT")
    report = json.loads(contract.get_report(rid))

    assert report["alert"] is False
    assert report["venue_count"] == 3


def test_check_divergence_alert_true(contract, direct_vm):
    direct_vm.sender = ALICE
    _mock_all_venues(direct_vm, "0.0001", "0.0035", "0.0028")

    rid = contract.check_divergence("BTCUSDT")
    report = json.loads(contract.get_report(rid))

    assert report["alert"] is True
    assert report["spread_bps"] >= 20


def test_set_thresholds_owner_only(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.set_thresholds(30, 3)
    thresholds = json.loads(contract.get_thresholds())
    assert thresholds["materiality_bps"] == 30
    assert thresholds["min_venues"] == 3

    direct_vm.sender = BOB
    with pytest.raises(Exception, match="only owner"):
        contract.set_thresholds(25, 2)


def test_invalid_symbol(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid symbol"):
        contract.check_divergence("BTC")


def test_unsupported_symbol_for_okx(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="unsupported symbol"):
        contract.check_divergence("ETHUSD")


def test_threshold_out_of_range(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="materiality_bps out of range"):
        contract.set_thresholds(0, 2)


def test_min_venues_out_of_range(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="min_venues out of range"):
        contract.set_thresholds(20, 1)


def test_api_error_propagates(contract, direct_vm):
    direct_vm.sender = ALICE
    direct_vm.mock_web(
        r"fapi\.binance\.com/fapi/v1/fundingRate",
        {"status": 500, "body": "oops"},
    )
    with pytest.raises(Exception, match="API server error"):
        contract.check_divergence("BTCUSDT")


def test_report_not_found(contract):
    with pytest.raises(Exception, match="report not found"):
        contract.get_report("999")


def test_get_all_reports_contains_created(contract, direct_vm):
    direct_vm.sender = ALICE
    _mock_all_venues(direct_vm, "0.0008", "0.0009", "0.0010")
    rid = contract.check_divergence("BTCUSDT")
    all_reports = json.loads(contract.get_all_reports())
    assert rid in all_reports
