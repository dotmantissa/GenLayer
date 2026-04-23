"""Direct-mode tests for debugger.py (ContractDebugger contract)."""

import json
import pytest

from conftest import _state

NODE = "http://test-node.example.com"
TARGET = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
ABI = json.dumps({"methods": {"transfer": {"args": ["to", "amount"]}}})


def _deploy(direct_deploy, node_url=NODE):
    return direct_deploy("debugger.py", node_url)


def _mock_node_success(direct_vm, result=None):
    body = result if result is not None else {"ok": True, "result": "0x1"}
    direct_vm.mock_web(r"test-node\.example\.com/contract/call", {"status": 200, "body": body})


def _mock_node_error(direct_vm, error="insufficient balance", status=200):
    direct_vm.mock_web(
        r"test-node\.example\.com/contract/call",
        {"status": status, "body": {"error": error}},
    )


def _mock_trace_llm(direct_vm, success=True, patterns=None, root_cause="Call executed."):
    direct_vm.mock_llm(
        r"GenLayer smart contract debugger",
        {
            "steps":      ["checked args", "executed transfer", "emitted event"],
            "summary":    "Transfer completed." if success else "Transfer failed.",
            "patterns":   patterns or [],
            "root_cause": root_cause,
        },
    )


def _mock_revert_llm(direct_vm, pattern="wrong_permissions", diagnosis="Caller is not owner."):
    direct_vm.mock_llm(
        r"debugging a transaction revert",
        {
            "pattern":     pattern,
            "diagnosis":   diagnosis,
            "suggestions": ["check msg.sender", "verify admin address"],
            "severity":    "HIGH",
        },
    )


def _mock_optimize_llm(direct_vm, issues=None):
    direct_vm.mock_llm(
        r"optimization expert",
        {
            "issues":  issues if issues is not None else [
                {
                    "priority":    "HIGH",
                    "category":    "wrong_storage_type",
                    "location":    "MyContract.__init__",
                    "description": "Using dict instead of TreeMap",
                    "fix":         "Replace dict with TreeMap[str, str]",
                }
            ],
            "summary": "Found 1 issue.",
        },
    )


def _mock_scenario_llm(direct_vm):
    direct_vm.mock_llm(
        r"testing expert",
        {
            "test_cases": [
                {
                    "name":      "happy_transfer",
                    "type":      "happy_path",
                    "setup":     "deploy with balance 1000",
                    "call":      {"function": "transfer", "args": ["0xBOB", 100], "caller": "owner"},
                    "expected":  "Transfer event emitted",
                    "rationale": "Basic success flow",
                },
                {
                    "name":      "transfer_unauthorized",
                    "type":      "expected_revert",
                    "setup":     "deploy normally",
                    "call":      {"function": "transfer", "args": ["0xBOB", 100], "caller": "stranger"},
                    "expected":  "[EXPECTED] Only owner",
                    "rationale": "Access control check",
                },
            ],
            "coverage_notes": "Covers main path and access control.",
        },
    )


# ── __init__ / node_url ───────────────────────────────────────────────────────

class TestInit:
    def test_node_url_stored(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        assert contract.node_url == NODE

    def test_default_node_url_empty(self, direct_vm, direct_deploy):
        contract = direct_deploy("debugger.py")  # no node_url arg
        assert contract.node_url == ""

    def test_missing_node_url_raises_on_trace(self, direct_vm, direct_deploy, direct_alice):
        contract = direct_deploy("debugger.py")
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("node_url not configured"):
            contract.trace_call(TARGET, "transfer", "[]", 0)


# ── trace_call ────────────────────────────────────────────────────────────────

class TestTraceCall:
    def test_successful_call_returns_trace_id(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_node_success(direct_vm)
        _mock_trace_llm(direct_vm, success=True)

        direct_vm.sender = direct_alice
        trace_id = contract.trace_call(TARGET, "transfer", '["0xBOB", 100]', 0)
        assert trace_id == "trace-1"

    def test_trace_stored_with_correct_fields(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_node_success(direct_vm)
        _mock_trace_llm(direct_vm)

        direct_vm.sender = direct_alice
        trace_id = contract.trace_call(TARGET, "transfer", "[]", 500)

        t = json.loads(contract.get_trace(trace_id))
        assert t["target_address"] == TARGET
        assert t["function_name"] == "transfer"
        assert t["value_wei"] == 500
        assert t["success"] is True

    def test_failed_call_recorded_as_failure(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_node_error(direct_vm, "insufficient balance")
        _mock_trace_llm(direct_vm, success=False, patterns=["insufficient_balance"])

        direct_vm.sender = direct_alice
        trace_id = contract.trace_call(TARGET, "transfer", "[]", 0)

        t = json.loads(contract.get_trace(trace_id))
        assert t["success"] is False

    def test_patterns_stored(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_node_error(direct_vm)
        _mock_trace_llm(direct_vm, success=False, patterns=["wrong_permissions"])

        direct_vm.sender = direct_alice
        trace_id = contract.trace_call(TARGET, "onlyOwner", "[]", 0)

        t = json.loads(contract.get_trace(trace_id))
        assert "wrong_permissions" in t["patterns"]

    def test_explanation_includes_root_cause(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_node_success(direct_vm)
        _mock_trace_llm(direct_vm, root_cause="Transfer went through successfully.")

        direct_vm.sender = direct_alice
        trace_id = contract.trace_call(TARGET, "transfer", "[]", 0)

        t = json.loads(contract.get_trace(trace_id))
        assert "Transfer went through" in t["explanation"]

    def test_node_500_raises_transient(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_node_error(direct_vm, status=503)
        _mock_trace_llm(direct_vm, success=False)

        direct_vm.sender = direct_alice
        # 500 error is caught inside run() and stored as failure, not raised
        trace_id = contract.trace_call(TARGET, "transfer", "[]", 0)
        t = json.loads(contract.get_trace(trace_id))
        assert t["success"] is False

    def test_node_404_stores_failure(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_node_error(direct_vm, status=404)
        _mock_trace_llm(direct_vm, success=False)

        direct_vm.sender = direct_alice
        trace_id = contract.trace_call(TARGET, "transfer", "[]", 0)
        t = json.loads(contract.get_trace(trace_id))
        assert t["success"] is False

    def test_sequential_trace_ids(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_node_success(direct_vm)
        _mock_trace_llm(direct_vm)

        direct_vm.sender = direct_alice
        t1 = contract.trace_call(TARGET, "fn1", "[]", 0)
        t2 = contract.trace_call(TARGET, "fn2", "[]", 0)
        assert t1 == "trace-1"
        assert t2 == "trace-2"

    def test_appears_in_list_traces(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_node_success(direct_vm)
        _mock_trace_llm(direct_vm)

        direct_vm.sender = direct_alice
        trace_id = contract.trace_call(TARGET, "transfer", "[]", 0)

        listing = json.loads(contract.list_traces())
        assert any(t["trace_id"] == trace_id for t in listing)

    def test_get_trace_not_found(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        r = json.loads(contract.get_trace("trace-999"))
        assert r["error"] == "not found"


# ── analyze_revert ────────────────────────────────────────────────────────────

class TestAnalyzeRevert:
    def test_returns_analysis_id(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_revert_llm(direct_vm)

        direct_vm.sender = direct_alice
        aid = contract.analyze_revert(
            "[EXPECTED] Only owner can call this",
            "transferOwnership",
            '["0xBOB"]',
            "",
        )
        assert aid == "analysis-1"

    def test_diagnosis_stored(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_revert_llm(direct_vm, diagnosis="Caller is not the owner address.")

        direct_vm.sender = direct_alice
        aid = contract.analyze_revert(
            "[EXPECTED] Only owner",
            "setConfig",
            "[]",
            "Token contract",
        )
        a = json.loads(contract.get_analysis(aid))
        assert "not the owner" in a["diagnosis"]
        assert a["kind"] == "revert"

    def test_suggestions_stored(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_revert_llm(direct_vm)

        direct_vm.sender = direct_alice
        aid = contract.analyze_revert("[EXPECTED] Only owner", "fn", "[]", "")
        a = json.loads(contract.get_analysis(aid))
        assert isinstance(a["suggestions"], list)
        assert len(a["suggestions"]) > 0

    def test_empty_error_raises(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("cannot be empty"):
            contract.analyze_revert("", "fn", "[]", "")

    def test_analysis_appears_in_list(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_revert_llm(direct_vm)

        direct_vm.sender = direct_alice
        aid = contract.analyze_revert("[EXPECTED] Only owner", "fn", "[]", "")
        listing = json.loads(contract.list_analyses())
        assert any(a["analysis_id"] == aid for a in listing)

    def test_get_analysis_not_found(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        r = json.loads(contract.get_analysis("analysis-999"))
        assert r["error"] == "not found"


# ── optimize_contract ─────────────────────────────────────────────────────────

class TestOptimizeContract:
    def test_returns_analysis_id(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_optimize_llm(direct_vm)

        direct_vm.sender = direct_alice
        aid = contract.optimize_contract("class MyContract(gl.Contract):\n    items: dict\n")
        assert aid.startswith("analysis-")

    def test_issues_stored(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_optimize_llm(direct_vm)

        direct_vm.sender = direct_alice
        aid = contract.optimize_contract("class MyContract(gl.Contract):\n    data: dict\n")
        a = json.loads(contract.get_analysis(aid))
        assert isinstance(a["suggestions"], list)
        assert len(a["suggestions"]) == 1
        assert a["suggestions"][0]["priority"] == "HIGH"
        assert a["kind"] == "optimize"

    def test_summary_stored_as_diagnosis(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_optimize_llm(direct_vm)

        direct_vm.sender = direct_alice
        aid = contract.optimize_contract("class MyContract(gl.Contract):\n    x: dict\n")
        a = json.loads(contract.get_analysis(aid))
        assert a["diagnosis"] == "Found 1 issue."

    def test_empty_code_raises(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("cannot be empty"):
            contract.optimize_contract("")

    def test_no_issues_stored_when_clean_code(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_optimize_llm(direct_vm, issues=[])

        direct_vm.sender = direct_alice
        aid = contract.optimize_contract("class MyContract(gl.Contract):\n    items: TreeMap\n")
        a = json.loads(contract.get_analysis(aid))
        assert a["suggestions"] == []


# ── generate_tests ────────────────────────────────────────────────────────────

class TestGenerateTests:
    def test_returns_scenario_id(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_scenario_llm(direct_vm)

        direct_vm.sender = direct_alice
        sid = contract.generate_tests(TARGET, ABI, "test the transfer function")
        assert sid.startswith("scenario-")

    def test_test_cases_stored(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_scenario_llm(direct_vm)

        direct_vm.sender = direct_alice
        sid = contract.generate_tests(TARGET, ABI, "test transfer with edge cases")
        s = json.loads(contract.get_scenario(sid))
        assert len(s["test_cases"]) == 2
        assert s["test_cases"][0]["type"] == "happy_path"
        assert s["test_cases"][1]["type"] == "expected_revert"

    def test_scenario_description_stored(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_scenario_llm(direct_vm)

        direct_vm.sender = direct_alice
        desc = "test the transfer function thoroughly"
        sid = contract.generate_tests(TARGET, ABI, desc)
        s = json.loads(contract.get_scenario(sid))
        assert s["description"] == desc

    def test_contract_address_stored(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_scenario_llm(direct_vm)

        direct_vm.sender = direct_alice
        sid = contract.generate_tests(TARGET, ABI, "test it")
        s = json.loads(contract.get_scenario(sid))
        assert s["contract_address"] == TARGET

    def test_empty_description_raises(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("cannot be empty"):
            contract.generate_tests(TARGET, ABI, "")

    def test_get_scenario_not_found(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        r = json.loads(contract.get_scenario("scenario-999"))
        assert r["error"] == "not found"


# ── counter increments across all call types ──────────────────────────────────

class TestCounterShared:
    def test_counter_shared_across_types(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_node_success(direct_vm)
        _mock_trace_llm(direct_vm)
        _mock_revert_llm(direct_vm)
        _mock_scenario_llm(direct_vm)

        direct_vm.sender = direct_alice

        t_id = contract.trace_call(TARGET, "fn", "[]", 0)   # counter=1
        a_id = contract.analyze_revert("[EXPECTED] err", "fn", "[]", "")  # counter=2
        s_id = contract.generate_tests(TARGET, ABI, "test it")  # counter=3

        assert t_id == "trace-1"
        assert a_id == "analysis-2"
        assert s_id == "scenario-3"
