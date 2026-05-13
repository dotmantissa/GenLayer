"""Direct tests for treasury_concentration_risk.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("treasury_concentration_risk.py")


def _create(contract, direct_vm, max_asset=40, max_top2=65):
    direct_vm.sender = ALICE
    holdings = json.dumps([
        {"symbol": "ETH", "amount": 100},
        {"symbol": "BTC", "amount": 50},
        {"symbol": "USDC", "amount": 20000},
    ])
    return contract.create_report("core_treasury", holdings, max_asset, max_top2)


def _mock_assessment(direct_vm, risk="MEDIUM", top_asset=45, top2=70):
    direct_vm.mock_web(r"simple/price", {"status": 200, "body": "{\"eth\":{\"usd\":3000}}"})
    direct_vm.mock_web(r"api/v3/global", {"status": 200, "body": "{\"data\":{\"market_cap_percentage\":{\"btc\":52}}}"})
    direct_vm.mock_llm(
        r"You are a treasury risk analyst",
        {
            "risk": risk,
            "top_asset_weight_pct": top_asset,
            "top2_weight_pct": top2,
            "recommendation": "reduce top positions",
            "reason": "concentration and correlation elevated",
        },
    )


def test_create_report_and_read(contract, direct_vm):
    rid = _create(contract, direct_vm)
    r = json.loads(contract.get_report(rid))

    assert r["status"] == "PENDING"
    assert r["treasury_label"] == "core_treasury"


def test_create_report_invalid_holdings(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid holdings json"):
        contract.create_report("core", "{bad", 40, 65)


def test_create_report_invalid_thresholds(contract, direct_vm):
    direct_vm.sender = ALICE
    holdings = json.dumps([{"symbol": "ETH", "amount": 1}])
    with pytest.raises(Exception, match="max_asset_weight_pct out of range"):
        contract.create_report("core", holdings, 0, 65)


def test_resolve_high_when_thresholds_exceeded(contract, direct_vm):
    rid = _create(contract, direct_vm, max_asset=40, max_top2=60)
    _mock_assessment(direct_vm, risk="LOW", top_asset=55, top2=80)

    out = contract.resolve_report(rid)
    assert out == "HIGH"
    assert contract.get_latest_risk("core_treasury") == "HIGH"


def test_resolve_medium_when_model_high_but_thresholds_ok(contract, direct_vm):
    rid = _create(contract, direct_vm, max_asset=60, max_top2=85)
    _mock_assessment(direct_vm, risk="HIGH", top_asset=40, top2=70)

    out = contract.resolve_report(rid)
    assert out == "MEDIUM"


def test_any_account_can_resolve_and_mapping_updates(contract, direct_vm):
    rid = _create(contract, direct_vm, max_asset=70, max_top2=90)
    _mock_assessment(direct_vm, risk="LOW", top_asset=30, top2=55)

    direct_vm.sender = BOB
    out = contract.resolve_report(rid)

    assert out == "LOW"
    assert contract.get_latest_risk("core_treasury") == "LOW"


def test_provider_client_error_reverts(contract, direct_vm):
    rid = _create(contract, direct_vm)
    direct_vm.mock_web(r"simple/price", {"status": 404, "body": "missing"})
    direct_vm.mock_web(r"api/v3/global", {"status": 200, "body": "ok"})

    with pytest.raises(Exception, match="prices client error"):
        contract.resolve_report(rid)


def test_cannot_resolve_twice(contract, direct_vm):
    rid = _create(contract, direct_vm)
    _mock_assessment(direct_vm)
    contract.resolve_report(rid)

    with pytest.raises(Exception, match="report already resolved"):
        contract.resolve_report(rid)


def test_missing_report_reverts(contract):
    with pytest.raises(Exception, match="report not found"):
        contract.get_report("999")
