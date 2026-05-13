"""Direct tests for lst_depeg_circuit_breaker.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("lst_depeg_circuit_breaker.py")


def _create_monitor(contract, direct_vm, threshold=120):
    direct_vm.sender = ALICE
    return contract.create_monitor(
        "lst_lending_vault",
        "stETH",
        "0xabc123pair",
        "main",
        "steth-weth",
        threshold,
    )


def _mock_feeds(direct_vm):
    direct_vm.mock_web(r"api\.curve\.fi", {"status": 200, "body": "{\"data\":\"curve\"}"})
    direct_vm.mock_web(r"api\.dexscreener\.com", {"status": 200, "body": "{\"pairs\":[]}"})
    direct_vm.mock_web(r"api\.binance\.com", {"status": 200, "body": "{\"price\":\"3500\"}"})


def test_create_monitor_and_read(contract, direct_vm):
    monitor_id = _create_monitor(contract, direct_vm)
    monitor = json.loads(contract.get_monitor(monitor_id))

    assert monitor["protocol_name"] == "lst_lending_vault"
    assert monitor["lst_symbol"] == "STETH"
    assert monitor["circuit_breaker_active"] is False


def test_create_monitor_invalid_threshold(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="depeg_bps_threshold out of range"):
        contract.create_monitor("protocol", "stETH", "pairx", "main", "pool", 2)


def test_evaluate_depeg_confirmed_triggers_breaker(contract, direct_vm):
    monitor_id = _create_monitor(contract, direct_vm, threshold=120)
    _mock_feeds(direct_vm)
    direct_vm.mock_llm(
        r"You are a DeFi market risk analyst",
        {
            "curve_price_eth": 0.979,
            "uniswap_price_eth": 0.982,
            "spot_eth_usd": 3510.0,
            "deviation_bps": 195,
            "status": "DEPEG_CONFIRMED",
            "reason": "both feeds beyond threshold",
        },
    )

    status = contract.evaluate_monitor(monitor_id)
    monitor = json.loads(contract.get_monitor(monitor_id))

    assert status == "DEPEG_CONFIRMED"
    assert monitor["circuit_breaker_active"] is True
    assert int(monitor["last_deviation_bps"]) >= 120


def test_evaluate_peg_ok_clears_breaker(contract, direct_vm):
    monitor_id = _create_monitor(contract, direct_vm, threshold=130)

    direct_vm.sender = ALICE
    contract.set_breaker_manual(monitor_id, True)

    _mock_feeds(direct_vm)
    direct_vm.mock_llm(
        r"You are a DeFi market risk analyst",
        {
            "curve_price_eth": 0.998,
            "uniswap_price_eth": 1.001,
            "spot_eth_usd": 3500.0,
            "deviation_bps": 15,
            "status": "PEG_OK",
            "reason": "within expected peg range",
        },
    )

    status = contract.evaluate_monitor(monitor_id)
    monitor = json.loads(contract.get_monitor(monitor_id))

    assert status == "PEG_OK"
    assert monitor["circuit_breaker_active"] is False


def test_evaluate_inconclusive_keeps_existing_breaker_state(contract, direct_vm):
    monitor_id = _create_monitor(contract, direct_vm, threshold=100)

    direct_vm.sender = ALICE
    contract.set_breaker_manual(monitor_id, True)

    _mock_feeds(direct_vm)
    direct_vm.mock_llm(
        r"You are a DeFi market risk analyst",
        {
            "curve_price_eth": 0.99,
            "uniswap_price_eth": 0.997,
            "spot_eth_usd": 3490.0,
            "deviation_bps": 80,
            "status": "INCONCLUSIVE",
            "reason": "one feed deviates less",
        },
    )

    status = contract.evaluate_monitor(monitor_id)
    monitor = json.loads(contract.get_monitor(monitor_id))

    assert status == "INCONCLUSIVE"
    assert monitor["circuit_breaker_active"] is True


def test_manual_breaker_only_owner(contract, direct_vm):
    monitor_id = _create_monitor(contract, direct_vm)

    direct_vm.sender = BOB
    with pytest.raises(Exception, match="only owner can set breaker"):
        contract.set_breaker_manual(monitor_id, True)


def test_provider_error_reverts(contract, direct_vm):
    monitor_id = _create_monitor(contract, direct_vm)

    direct_vm.mock_web(r"api\.curve\.fi", {"status": 500, "body": "err"})
    direct_vm.mock_web(r"api\.dexscreener\.com", {"status": 200, "body": "ok"})
    direct_vm.mock_web(r"api\.binance\.com", {"status": 200, "body": "ok"})

    with pytest.raises(Exception, match="curve server error"):
        contract.evaluate_monitor(monitor_id)


def test_unknown_monitor_reverts(contract):
    with pytest.raises(Exception, match="monitor not found"):
        contract.get_monitor("999")


def test_get_all_monitors_contains_created_monitor(contract, direct_vm):
    monitor_id = _create_monitor(contract, direct_vm)
    monitors = json.loads(contract.get_all_monitors())
    assert monitor_id in monitors
