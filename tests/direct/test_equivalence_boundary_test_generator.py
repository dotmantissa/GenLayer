"""Direct tests for equivalence_boundary_test_generator.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("equivalence_boundary_test_generator.py")


def test_generate_suite_happy_path(contract, direct_vm):
    direct_vm.sender = ALICE
    sid = contract.generate_suite("price", 100000, 2, 2)
    suite = json.loads(contract.get_suite(sid))

    assert suite["metric_label"] == "price"
    assert suite["tolerance_percent"] == 2
    assert len(suite["vectors"]) == 5


def test_generate_suite_contains_boundary_labels(contract, direct_vm):
    direct_vm.sender = ALICE
    sid = contract.generate_suite("score", 1000, 2, 2)
    suite = json.loads(contract.get_suite(sid))

    deltas = [v["percent_delta"] for v in suite["vectors"]]
    expected = [1.8, 1.9, 2.0, 2.1, 2.2]
    assert deltas == expected

    labels = [v["expected"] for v in suite["vectors"]]
    assert labels[0] == "EQUIVALENT"
    assert labels[2] == "EQUIVALENT"
    assert labels[3] == "DIVERGENT"


def test_invalid_metric_label(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid metric_label"):
        contract.generate_suite("", 100, 2, 2)


def test_invalid_baseline(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="baseline_value must be positive"):
        contract.generate_suite("price", 0, 2, 2)


def test_invalid_tolerance(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="tolerance_percent out of range"):
        contract.generate_suite("price", 100, 0, 2)


def test_invalid_step(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="step_tenths_percent out of range"):
        contract.generate_suite("price", 100, 2, 0)


def test_suite_not_found(contract):
    with pytest.raises(Exception, match="suite not found"):
        contract.get_suite("999")
