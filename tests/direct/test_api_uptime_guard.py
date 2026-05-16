"""Direct tests for api_uptime_guard.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("api_uptime_guard.py")


def _register(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_endpoint(
        "weather_api",
        "https://example.com/weather",
        "temp,humidity",
        3000,
    )


def test_register_endpoint_and_get(contract, direct_vm):
    _register(contract, direct_vm)
    data = json.loads(contract.get_endpoint("weather_api"))
    assert data["endpoint_id"] == "weather_api"
    assert data["enabled"] is True


def test_owner_only_register(contract, direct_vm):
    direct_vm.sender = BOB
    with pytest.raises(Exception, match="only owner"):
        contract.register_endpoint("x1", "https://example.com", "k", 2000)


def test_link_dependency_and_get(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.sender = ALICE
    contract.link_dependency("weather_api", "oracle-1234")
    dep = json.loads(contract.get_dependency("weather_api"))
    assert dep["dependency_contract_id"] == "oracle-1234"


def test_run_health_check_healthy(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.sender = ALICE
    contract.link_dependency("weather_api", "oracle-1234")

    direct_vm.mock_web(
        r"example\.com/weather",
        {"status": 200, "body": '{"temp": 22, "humidity": 66, "ok": true}'},
    )
    direct_vm.mock_llm(
        r"validating an API health check result",
        {"health_status": "healthy", "valid_data": True, "reason": "payload schema is valid"},
    )

    check_id = contract.run_health_check("weather_api")
    rec = json.loads(contract.get_health_check(check_id))
    assert rec["health_status"] == "healthy"
    assert rec["pause_dependent_oracles"] is False


def test_run_health_check_degraded_pauses(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.sender = ALICE

    direct_vm.mock_web(
        r"example\.com/weather",
        {"status": 200, "body": '{"temp": 22}'},
    )
    direct_vm.mock_llm(
        r"validating an API health check result",
        {"health_status": "degraded", "valid_data": False, "reason": "missing required key humidity"},
    )

    check_id = contract.run_health_check("weather_api")
    rec = json.loads(contract.get_health_check(check_id))
    assert rec["health_status"] == "degraded"
    assert rec["pause_dependent_oracles"] is True


def test_set_endpoint_enabled(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.sender = ALICE
    contract.set_endpoint_enabled("weather_api", False)
    endpoint = json.loads(contract.get_endpoint("weather_api"))
    assert endpoint["enabled"] is False


def test_run_health_check_disabled_endpoint(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.sender = ALICE
    contract.set_endpoint_enabled("weather_api", False)
    with pytest.raises(Exception, match="endpoint disabled"):
        contract.run_health_check("weather_api")


def test_invalid_register_inputs(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid endpoint_id"):
        contract.register_endpoint("x", "https://example.com", "a", 2000)
    with pytest.raises(Exception, match="invalid url"):
        contract.register_endpoint("valid_id", "ftp://example.com", "a", 2000)
    with pytest.raises(Exception, match="invalid required_keys_csv"):
        contract.register_endpoint("valid_id", "https://example.com", "", 2000)
    with pytest.raises(Exception, match="invalid timeout_ms"):
        contract.register_endpoint("valid_id", "https://example.com", "a", 50)


def test_upstream_server_error(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.sender = ALICE
    direct_vm.mock_web(r"example\.com/weather", {"status": 500, "body": "err"})
    with pytest.raises(Exception, match="upstream server error"):
        contract.run_health_check("weather_api")


def test_missing_records(contract):
    with pytest.raises(Exception, match="endpoint not found"):
        contract.get_endpoint("missing")
    with pytest.raises(Exception, match="dependency not found"):
        contract.get_dependency("missing")
    with pytest.raises(Exception, match="check not found"):
        contract.get_health_check("999")


def test_get_all_health_checks_contains_created(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.sender = ALICE
    direct_vm.mock_web(
        r"example\.com/weather",
        {"status": 200, "body": '{"temp": 22, "humidity": 66}'},
    )
    direct_vm.mock_llm(
        r"validating an API health check result",
        {"health_status": "healthy", "valid_data": True, "reason": "ok"},
    )
    cid = contract.run_health_check("weather_api")
    all_checks = json.loads(contract.get_all_health_checks())
    assert cid in all_checks
