"""Direct tests for provider_fallback_router.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("provider_fallback_router.py")


def _providers(primary: str, backup: str) -> str:
    return json.dumps(
        [
            {"name": "primary", "url": primary},
            {"name": "backup", "url": backup},
        ]
    )


def _register(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_provider_set(
        "weather",
        _providers("https://p1.example.com/data", "https://p2.example.com/data"),
        2000,
    )


def test_register_and_get_provider_set(contract, direct_vm):
    _register(contract, direct_vm)
    ps = json.loads(contract.get_provider_set("weather"))
    assert ps["data_type"] == "weather"
    assert len(ps["providers"]) == 2


def test_register_owner_only(contract, direct_vm):
    direct_vm.sender = BOB
    with pytest.raises(Exception, match="only owner"):
        contract.register_provider_set("weather", _providers("https://a", "https://b"), 1000)


def test_read_uses_primary_when_valid(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.sender = ALICE
    direct_vm.mock_web(r"p1\.example\.com/data\?q=lagos", {"status": 200, "body": '{"t": 30}'})
    direct_vm.mock_llm(
        r"validate provider payload quality",
        {"valid_data": True, "numeric_value": 30.0, "reason": "ok"},
    )
    rid = contract.read_with_fallback("weather", "lagos")
    rec = json.loads(contract.get_read(rid))
    assert rec["selected_provider"] == "primary"
    assert rec["fallback_used"] is False


def test_fallback_to_backup_on_primary_error(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.sender = ALICE
    direct_vm.mock_web(r"p1\.example\.com/data\?q=lagos", {"status": 404, "body": "nf"})
    direct_vm.mock_web(r"p2\.example\.com/data\?q=lagos", {"status": 200, "body": '{"t": 29}'})
    direct_vm.mock_llm(
        r"validate provider payload quality",
        {"valid_data": True, "numeric_value": 29.0, "reason": "backup ok"},
    )
    rid = contract.read_with_fallback("weather", "lagos")
    rec = json.loads(contract.get_read(rid))
    assert rec["selected_provider"] == "backup"
    assert rec["fallback_used"] is True


def test_fallback_when_primary_invalid_data(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.sender = ALICE
    direct_vm.mock_web(r"p1\.example\.com/data\?q=lagos", {"status": 200, "body": ""})
    direct_vm.mock_web(r"p2\.example\.com/data\?q=lagos", {"status": 200, "body": '{"t": 28}'})
    direct_vm.mock_llm(
        r"validate provider payload quality",
        {"valid_data": True, "numeric_value": 28, "reason": "good"},
    )
    rid = contract.read_with_fallback("weather", "lagos")
    rec = json.loads(contract.get_read(rid))
    assert rec["selected_provider"] == "backup"


def test_set_provider_order(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.sender = ALICE
    contract.set_provider_order(
        "weather",
        json.dumps([
            {"name": "backup", "url": "https://p2.example.com/data"},
            {"name": "primary", "url": "https://p1.example.com/data"},
        ]),
    )
    ps = json.loads(contract.get_provider_set("weather"))
    assert ps["providers"][0]["name"] == "backup"


def test_invalid_registration_inputs(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid data_type"):
        contract.register_provider_set("x", _providers("https://a", "https://b"), 1000)
    with pytest.raises(Exception, match="invalid providers_json"):
        contract.register_provider_set("weather", "not-json", 1000)
    with pytest.raises(Exception, match="providers list required"):
        contract.register_provider_set("weather", "[]", 1000)
    with pytest.raises(Exception, match="invalid tolerance_bps"):
        contract.register_provider_set("weather", _providers("https://a", "https://b"), 0)


def test_unknown_data_type(contract):
    with pytest.raises(Exception, match="provider set not found"):
        contract.read_with_fallback("weather", "lagos")


def test_invalid_query(contract, direct_vm):
    _register(contract, direct_vm)
    with pytest.raises(Exception, match="invalid query"):
        contract.read_with_fallback("weather", "")


def test_all_providers_invalid(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.sender = ALICE
    direct_vm.mock_web(r"p1\.example\.com/data\?q=lagos", {"status": 404, "body": "nf"})
    direct_vm.mock_web(r"p2\.example\.com/data\?q=lagos", {"status": 404, "body": "nf"})
    with pytest.raises(Exception, match="no valid provider response"):
        contract.read_with_fallback("weather", "lagos")


def test_get_all_reads_contains_created(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.sender = ALICE
    direct_vm.mock_web(r"p1\.example\.com/data\?q=lagos", {"status": 200, "body": '{"t": 30}'})
    direct_vm.mock_llm(
        r"validate provider payload quality",
        {"valid_data": True, "numeric_value": 30.0, "reason": "ok"},
    )
    rid = contract.read_with_fallback("weather", "lagos")
    all_reads = json.loads(contract.get_all_reads())
    assert rid in all_reads


def test_get_read_not_found(contract):
    with pytest.raises(Exception, match="read not found"):
        contract.get_read("999")
