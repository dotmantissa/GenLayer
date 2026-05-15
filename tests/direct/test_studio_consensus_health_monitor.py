"""Direct tests for studio_consensus_health_monitor.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("studio_consensus_health_monitor.py")


def test_register_and_list_contracts(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_contract("0xabc123", "custom validator tolerance")

    contracts = json.loads(contract.list_contracts())
    assert contracts == ["0xabc123"]


def test_owner_can_manage_reporters(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.add_reporter(BOB)
    assert contract.is_reporter(BOB) is True

    contract.remove_reporter(BOB)
    assert contract.is_reporter(BOB) is False


def test_non_owner_cannot_add_reporter(contract, direct_vm):
    direct_vm.sender = BOB
    with pytest.raises(Exception, match="only owner"):
        contract.add_reporter(BOB)


def test_submit_snapshot_by_reporter_triggers_threshold_alerts(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.add_reporter(BOB)
    contract.register_contract("0xfeed", "strict_eq")

    direct_vm.sender = BOB
    out = json.loads(contract.submit_snapshot("0xfeed", 10, 2, 2, 2, 60))

    assert out["snapshot_index"] == 0
    assert "consensus_failure_rate_high" in out["alerts"]
    assert "validator_divergence_high" in out["alerts"]
    assert "revert_rate_high" in out["alerts"]


def test_submit_snapshot_detects_spikes(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_contract("0xbeef", "prompt comparative")

    contract.submit_snapshot("0xbeef", 100, 1, 1, 1, 30)
    out = json.loads(contract.submit_snapshot("0xbeef", 100, 8, 8, 1, 30))

    assert "consensus_failure_spike" in out["alerts"]
    assert "validator_divergence_spike" in out["alerts"]


def test_get_contract_status_includes_latest_snapshot(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_contract("0x1234", "custom validator")
    contract.submit_snapshot("0x1234", 20, 1, 2, 1, 15)

    status = json.loads(contract.get_contract_status("0x1234"))
    assert status["contract_id"] == "0x1234"
    assert status["last_snapshot"]["total_transactions"] == 20


def test_get_snapshot_out_of_range_reverts(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_contract("0xface", "strict_eq")
    with pytest.raises(Exception, match="snapshot index out of range"):
        contract.get_snapshot("0xface", 0)


def test_submit_snapshot_rejects_metric_exceeding_total(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_contract("0xcafe", "strict_eq")

    with pytest.raises(Exception, match="metric exceeds total"):
        contract.submit_snapshot("0xcafe", 2, 3, 0, 0, 10)


def test_submit_snapshot_requires_reporter(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_contract("0xfade", "strict_eq")

    direct_vm.sender = BOB
    with pytest.raises(Exception, match="only reporter"):
        contract.submit_snapshot("0xfade", 5, 0, 0, 0, 5)


def test_update_thresholds_and_readback(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.update_thresholds(500, 600, 700, 250, 3)
    data = json.loads(contract.get_thresholds())

    assert data["consensus_failure_rate_bps"] == 500
    assert data["divergence_rate_bps"] == 600
    assert data["revert_rate_bps"] == 700
    assert data["spike_delta_bps"] == 250
    assert data["min_samples"] == 3


def test_register_contract_rejects_duplicate(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_contract("0xdupe", "strict_eq")
    with pytest.raises(Exception, match="contract already registered"):
        contract.register_contract("0xdupe", "strict_eq")
