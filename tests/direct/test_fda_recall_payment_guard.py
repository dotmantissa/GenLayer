"""Direct tests for fda_recall_payment_guard.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("fda_recall_payment_guard.py")


def test_register_product_and_get(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_product("prod-1", "1234-5678", "Acetaminophen")
    p = json.loads(contract.get_product("prod-1"))
    assert p["ndc"] == "1234-5678"


def test_owner_only_register(contract, direct_vm):
    direct_vm.sender = BOB
    with pytest.raises(Exception, match="only owner"):
        contract.register_product("prod-1", "1234", "X")


def test_check_recall_match_halts_payments(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_product("prod-1", "1234-5678", "Acetaminophen")

    direct_vm.mock_web(
        r"api\.fda\.gov/drug/enforcement\.json",
        {
            "status": 200,
            "body": json.dumps(
                {
                    "results": [
                        {
                            "product_description": "Acetaminophen tablets NDC 1234-5678 batch X",
                            "reason_for_recall": "contamination",
                            "classification": "Class II",
                            "status": "Ongoing",
                        }
                    ]
                }
            ),
        },
    )

    cid = contract.check_recall_and_halt("acetaminophen", 10)
    out = json.loads(contract.get_check(cid))

    assert out["halt_payments"] is True
    assert out["compliance_alert"] is True
    assert out["match_count"] >= 1


def test_check_recall_no_match_no_halt(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_product("prod-1", "1234-5678", "Acetaminophen")

    direct_vm.mock_web(
        r"api\.fda\.gov/drug/enforcement\.json",
        {
            "status": 200,
            "body": json.dumps(
                {
                    "results": [
                        {
                            "product_description": "Different product NDC 9999-0000",
                            "reason_for_recall": "label issue",
                            "classification": "Class III",
                            "status": "Completed",
                        }
                    ]
                }
            ),
        },
    )

    cid = contract.check_recall_and_halt("different", 10)
    out = json.loads(contract.get_check(cid))
    assert out["halt_payments"] is False


def test_set_product_active(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_product("prod-1", "1234-5678", "Acetaminophen")
    contract.set_product_active("prod-1", False)
    p = json.loads(contract.get_product("prod-1"))
    assert p["active"] is False


def test_invalid_limit(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid limit"):
        contract.check_recall_and_halt("drug", 0)


def test_openfda_server_error(contract, direct_vm):
    direct_vm.sender = ALICE
    direct_vm.mock_web(r"api\.fda\.gov/drug/enforcement\.json", {"status": 500, "body": "err"})
    with pytest.raises(Exception, match="openfda server error"):
        contract.check_recall_and_halt("drug", 10)


def test_product_not_found(contract):
    with pytest.raises(Exception, match="product not found"):
        contract.get_product("missing")


def test_check_not_found(contract):
    with pytest.raises(Exception, match="check not found"):
        contract.get_check("999")


def test_get_all_checks_contains_created(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_product("prod-1", "1234-5678", "Acetaminophen")
    direct_vm.mock_web(
        r"api\.fda\.gov/drug/enforcement\.json",
        {"status": 200, "body": json.dumps({"results": []})},
    )
    cid = contract.check_recall_and_halt("acetaminophen", 10)
    all_checks = json.loads(contract.get_all_checks())
    assert cid in all_checks
