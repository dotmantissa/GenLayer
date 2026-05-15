"""Direct tests for hospital_capacity_oracle.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("hospital_capacity_oracle.py", 8500, 9000)


def test_capture_capacity_alerts(contract, direct_vm):
    direct_vm.sender = ALICE
    direct_vm.mock_web(
        r"health\.state\.gov/dashboard",
        {"status": 200, "body": "Dashboard text with bed and icu numbers in a non standard table format."},
    )
    direct_vm.mock_llm(
        r"Extract hospital capacity metrics",
        {
            "bed_occupied": 920,
            "bed_total": 1000,
            "icu_occupied": 190,
            "icu_total": 200,
            "source_note": "parsed from chart",
        },
    )

    rid = contract.capture_capacity("CA", "https://health.state.gov/dashboard")
    report = json.loads(contract.get_report(rid))

    assert report["risk_bucket"] == 2
    assert report["bed_alert"] is True
    assert report["icu_alert"] is True


def test_capture_capacity_no_alert(contract, direct_vm):
    direct_vm.sender = ALICE
    direct_vm.mock_web(
        r"health\.state\.gov/dashboard",
        {"status": 200, "body": "Another valid dashboard payload text that is long enough for parsing."},
    )
    direct_vm.mock_llm(
        r"Extract hospital capacity metrics",
        {
            "bed_occupied": 700,
            "bed_total": 1000,
            "icu_occupied": 120,
            "icu_total": 200,
            "source_note": "parsed",
        },
    )

    rid = contract.capture_capacity("CA", "https://health.state.gov/dashboard")
    report = json.loads(contract.get_report(rid))
    assert report["risk_bucket"] == 0


def test_set_thresholds_owner_only(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.set_thresholds(8000, 8500)
    t = json.loads(contract.get_thresholds())
    assert t["occupancy_alert_bps"] == 8000

    direct_vm.sender = BOB
    with pytest.raises(Exception, match="only owner"):
        contract.set_thresholds(7500, 8500)


def test_invalid_state_code(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid state_code"):
        contract.capture_capacity("CAL", "https://health.state.gov/dashboard")


def test_invalid_dashboard_url(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid dashboard_url"):
        contract.capture_capacity("CA", "ftp://bad")


def test_llm_invalid_totals(contract, direct_vm):
    direct_vm.sender = ALICE
    direct_vm.mock_web(
        r"health\.state\.gov/dashboard",
        {"status": 200, "body": "Dashboard content valid but parser returns bad totals."},
    )
    direct_vm.mock_llm(
        r"Extract hospital capacity metrics",
        {
            "bed_occupied": 10,
            "bed_total": 0,
            "icu_occupied": 5,
            "icu_total": 0,
            "source_note": "bad",
        },
    )
    with pytest.raises(Exception, match="invalid totals"):
        contract.capture_capacity("CA", "https://health.state.gov/dashboard")


def test_dashboard_server_error(contract, direct_vm):
    direct_vm.sender = ALICE
    direct_vm.mock_web(
        r"health\.state\.gov/dashboard",
        {"status": 500, "body": "err"},
    )
    with pytest.raises(Exception, match="dashboard server error"):
        contract.capture_capacity("CA", "https://health.state.gov/dashboard")


def test_report_not_found(contract):
    with pytest.raises(Exception, match="report not found"):
        contract.get_report("999")


def test_get_all_reports_contains_created(contract, direct_vm):
    direct_vm.sender = ALICE
    direct_vm.mock_web(
        r"health\.state\.gov/dashboard",
        {"status": 200, "body": "Dashboard text with enough characters for parser."},
    )
    direct_vm.mock_llm(
        r"Extract hospital capacity metrics",
        {
            "bed_occupied": 900,
            "bed_total": 1000,
            "icu_occupied": 150,
            "icu_total": 200,
            "source_note": "ok",
        },
    )
    rid = contract.capture_capacity("CA", "https://health.state.gov/dashboard")
    all_reports = json.loads(contract.get_all_reports())
    assert rid in all_reports
