"""Direct tests for temporal_web_replay_validator.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("temporal_web_replay_validator.py")


def test_create_scenario_and_get(contract, direct_vm):
    direct_vm.sender = ALICE
    sid = contract.create_scenario("Price verdict", "verdict", "allow")
    scenario = json.loads(contract.get_scenario(sid))

    assert scenario["name"] == "Price verdict"
    assert scenario["verdict_json_key"] == "verdict"


def test_add_historical_point(contract, direct_vm):
    direct_vm.sender = ALICE
    sid = contract.create_scenario("Scenario A", "verdict", "allow")
    contract.add_historical_point(sid, "2026-01-01", json.dumps({"verdict": "allow"}))

    scenario = json.loads(contract.get_scenario(sid))
    assert len(scenario["points"]) == 1


def test_run_replay_detects_brittleness(contract, direct_vm):
    direct_vm.sender = ALICE
    sid = contract.create_scenario("Temporal drift", "decision", "allow")
    contract.add_historical_point(sid, "t1", json.dumps({"decision": "allow"}))
    contract.add_historical_point(sid, "t2", json.dumps({"decision": "deny"}))
    contract.add_historical_point(sid, "t3", json.dumps({"decision": "maybe"}))

    rid = contract.run_replay(sid)
    report = json.loads(contract.get_report(rid))

    assert report["distinct_verdict_count"] == 3
    assert report["temporal_brittle"] is True


def test_run_replay_stable(contract, direct_vm):
    direct_vm.sender = ALICE
    sid = contract.create_scenario("Stable", "decision", "allow")
    contract.add_historical_point(sid, "a", json.dumps({"decision": "allow"}))
    contract.add_historical_point(sid, "b", json.dumps({"decision": "allow"}))

    rid = contract.run_replay(sid)
    report = json.loads(contract.get_report(rid))

    assert report["mismatch_count"] == 0
    assert report["drift_rate_bps"] == 0
    assert report["temporal_brittle"] is False


def test_run_replay_missing_key(contract, direct_vm):
    direct_vm.sender = ALICE
    sid = contract.create_scenario("Missing key", "decision", "allow")
    contract.add_historical_point(sid, "a", json.dumps({"decision": "allow"}))
    contract.add_historical_point(sid, "b", json.dumps({"other": "deny"}))

    rid = contract.run_replay(sid)
    report = json.loads(contract.get_report(rid))
    assert report["missing_key_count"] == 1
    assert report["temporal_brittle"] is True


def test_insufficient_points_reverts(contract, direct_vm):
    direct_vm.sender = ALICE
    sid = contract.create_scenario("Too few", "decision", "allow")
    contract.add_historical_point(sid, "only", json.dumps({"decision": "allow"}))

    with pytest.raises(Exception, match="insufficient historical points"):
        contract.run_replay(sid)


def test_invalid_web_response_json(contract, direct_vm):
    direct_vm.sender = ALICE
    sid = contract.create_scenario("Valid name", "decision", "allow")

    with pytest.raises(Exception, match="invalid web_response_json"):
        contract.add_historical_point(sid, "t1", "not-json")


def test_missing_report_reverts(contract):
    with pytest.raises(Exception, match="report not found"):
        contract.get_report("999")


def test_missing_scenario_reverts(contract):
    with pytest.raises(Exception, match="scenario not found"):
        contract.get_scenario("999")


def test_get_all_reports_contains_created_report(contract, direct_vm):
    direct_vm.sender = ALICE
    sid = contract.create_scenario("All reports", "decision", "allow")
    contract.add_historical_point(sid, "a", json.dumps({"decision": "allow"}))
    contract.add_historical_point(sid, "b", json.dumps({"decision": "deny"}))

    rid = contract.run_replay(sid)
    all_reports = json.loads(contract.get_all_reports())
    assert rid in all_reports
