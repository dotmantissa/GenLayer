"""Direct-mode tests for state_migrator.py (StateMigrator contract)."""

import json
import pytest

from conftest import _state

NODE     = "http://test-node.example.com"
OLD_ADDR = "0xoldoldoldoldoldoldoldoldoldoldoldoldold0"
NEW_ADDR = "0xnewnewnewnewnewnewnewnewnewnewnewnewnew0"

OLD_CODE = """\
class MyContract(gl.Contract):
    owner: Address
    balance: u256
"""

NEW_CODE = """\
class MyContract(gl.Contract):
    owner: Address
    balance: u256
    fee_rate: u256
"""

OLD_STATE = {
    "fields": {
        "owner":   "0xAAAA",
        "balance": 1000,
    }
}

STEPS_CLEAN = [
    {
        "index":       1,
        "step_type":   "copy",
        "description": "Copy owner field",
        "old_field":   "owner",
        "new_field":   "owner",
        "transform":   "none",
        "setter_fn":   "set_owner",
        "risk":        "LOW",
    },
    {
        "index":       2,
        "step_type":   "copy",
        "description": "Copy balance field",
        "old_field":   "balance",
        "new_field":   "balance",
        "transform":   "none",
        "setter_fn":   "set_balance",
        "risk":        "LOW",
    },
    {
        "index":       3,
        "step_type":   "set_default",
        "description": "Set fee_rate to default 0",
        "old_field":   None,
        "new_field":   "fee_rate",
        "transform":   "none",
        "setter_fn":   "set_fee_rate",
        "risk":        "LOW",
    },
]

STEPS_BREAKING = [
    {
        "index":       1,
        "step_type":   "transform",
        "description": "Transform balance from u256 to str",
        "old_field":   "balance",
        "new_field":   "balance",
        "transform":   "int to string",
        "setter_fn":   "set_balance",
        "risk":        "HIGH",
    },
]


def _deploy(direct_deploy, node_url=NODE):
    return direct_deploy("state_migrator.py", node_url)


def _mock_state(direct_vm, state=None, status=200):
    body = state if state is not None else OLD_STATE
    direct_vm.mock_web(
        r"test-node\.example\.com/contract/state",
        {"status": status, "body": body},
    )


def _mock_plan_llm(direct_vm, steps=None, breaking=None, complexity=30):
    direct_vm.mock_llm(
        r"migration expert",
        {
            "steps":            steps if steps is not None else STEPS_CLEAN,
            "breaking_changes": breaking if breaking is not None else [],
            "complexity_score": complexity,
            "summary":          "Migration adds fee_rate field with default 0.",
        },
    )


def _mock_step_ok(direct_vm):
    direct_vm.mock_web(
        r"test-node\.example\.com/contract/call",
        {"status": 200, "body": {"ok": True}},
    )


def _mock_step_fail(direct_vm):
    direct_vm.mock_web(
        r"test-node\.example\.com/contract/call",
        {"status": 400, "body": {"error": "field already set"}},
    )


# ── plan_migration ────────────────────────────────────────────────────────────

class TestPlanMigration:
    def test_returns_plan_id(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_state(direct_vm)
        _mock_plan_llm(direct_vm)
        direct_vm.sender = direct_alice
        pid = contract.plan_migration(OLD_ADDR, NEW_CODE)
        assert pid == "plan-1"

    def test_plan_stored_with_correct_fields(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_state(direct_vm)
        _mock_plan_llm(direct_vm, complexity=42)
        direct_vm.sender = direct_alice
        pid = contract.plan_migration(OLD_ADDR, NEW_CODE)

        p = json.loads(contract.get_plan(pid))
        assert p["old_address"] == OLD_ADDR
        assert p["step_count"] == 3
        assert p["complexity_score"] == 42
        assert p["status"] == "PLANNED"

    def test_state_snapshot_stored(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_state(direct_vm)
        _mock_plan_llm(direct_vm)
        direct_vm.sender = direct_alice
        pid = contract.plan_migration(OLD_ADDR, NEW_CODE)

        p = json.loads(contract.get_plan(pid))
        assert "owner" in p["state_snapshot"]

    def test_breaking_changes_stored(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_state(direct_vm)
        _mock_plan_llm(
            direct_vm,
            steps=STEPS_BREAKING,
            breaking=["balance type changed from u256 to str — data loss if non-numeric"],
            complexity=75,
        )
        direct_vm.sender = direct_alice
        pid = contract.plan_migration(OLD_ADDR, NEW_CODE)

        p = json.loads(contract.get_plan(pid))
        assert len(p["breaking_changes"]) == 1
        assert "data loss" in p["breaking_changes"][0]

    def test_steps_stored_correctly(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_state(direct_vm)
        _mock_plan_llm(direct_vm)
        direct_vm.sender = direct_alice
        pid = contract.plan_migration(OLD_ADDR, NEW_CODE)

        p = json.loads(contract.get_plan(pid))
        assert len(p["steps"]) == 3
        assert p["steps"][0]["step_type"] == "copy"
        assert p["steps"][2]["step_type"] == "set_default"

    def test_empty_log_initialised(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_state(direct_vm)
        _mock_plan_llm(direct_vm)
        direct_vm.sender = direct_alice
        pid = contract.plan_migration(OLD_ADDR, NEW_CODE)

        log = json.loads(contract.get_execution_log(pid))
        assert log == []

    def test_node_404_plan_still_created(self, direct_vm, direct_deploy, direct_alice):
        # State fetch fails gracefully — plan is created with empty snapshot
        contract = _deploy(direct_deploy)
        _mock_state(direct_vm, status=404)
        _mock_plan_llm(direct_vm)
        direct_vm.sender = direct_alice
        pid = contract.plan_migration(OLD_ADDR, NEW_CODE)
        assert pid == "plan-1"
        p = json.loads(contract.get_plan(pid))
        assert p["status"] == "PLANNED"

    def test_node_503_plan_still_created(self, direct_vm, direct_deploy, direct_alice):
        # Transient node failure still creates a plan using AI analysis alone
        contract = _deploy(direct_deploy)
        _mock_state(direct_vm, status=503)
        _mock_plan_llm(direct_vm)
        direct_vm.sender = direct_alice
        pid = contract.plan_migration(OLD_ADDR, NEW_CODE)
        assert pid == "plan-1"

    def test_empty_address_raises(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("cannot be empty"):
            contract.plan_migration("", NEW_CODE)

    def test_empty_code_raises(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("cannot be empty"):
            contract.plan_migration(OLD_ADDR, "")

    def test_appears_in_list(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_state(direct_vm)
        _mock_plan_llm(direct_vm)
        direct_vm.sender = direct_alice
        pid = contract.plan_migration(OLD_ADDR, NEW_CODE)

        plans = json.loads(contract.list_plans())
        assert any(p["plan_id"] == pid for p in plans)

    def test_code_hash_stored(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_state(direct_vm)
        _mock_plan_llm(direct_vm)
        direct_vm.sender = direct_alice
        pid = contract.plan_migration(OLD_ADDR, NEW_CODE)

        p = json.loads(contract.get_plan(pid))
        assert len(p["new_code_hash"]) == 16

    def test_sequential_plan_ids(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_state(direct_vm)
        _mock_plan_llm(direct_vm)
        direct_vm.sender = direct_alice
        p1 = contract.plan_migration(OLD_ADDR, NEW_CODE)
        p2 = contract.plan_migration(OLD_ADDR, NEW_CODE + " # v2")
        assert p1 == "plan-1"
        assert p2 == "plan-2"


# ── execute_migration dry_run=True ────────────────────────────────────────────

class TestExecuteMigrationDryRun:
    def _make_plan(self, contract, direct_vm, direct_alice, steps=None):
        _mock_state(direct_vm)
        _mock_plan_llm(direct_vm, steps=steps)
        direct_vm.sender = direct_alice
        return contract.plan_migration(OLD_ADDR, NEW_CODE)

    def test_dry_run_returns_summary(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        pid = self._make_plan(contract, direct_vm, direct_alice)
        direct_vm.sender = direct_alice
        result = json.loads(contract.execute_migration(pid, NEW_ADDR, True))
        assert result["dry_run"] is True
        assert result["plan_id"] == pid

    def test_dry_run_all_steps_succeed(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        pid = self._make_plan(contract, direct_vm, direct_alice)
        direct_vm.sender = direct_alice
        result = json.loads(contract.execute_migration(pid, NEW_ADDR, True))
        assert result["all_success"] is True
        assert result["steps_run"] == 3

    def test_dry_run_status_stays_planned(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        pid = self._make_plan(contract, direct_vm, direct_alice)
        direct_vm.sender = direct_alice
        contract.execute_migration(pid, NEW_ADDR, True)

        p = json.loads(contract.get_plan(pid))
        assert p["status"] == "PLANNED"

    def test_dry_run_logs_recorded(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        pid = self._make_plan(contract, direct_vm, direct_alice)
        direct_vm.sender = direct_alice
        contract.execute_migration(pid, NEW_ADDR, True)

        log = json.loads(contract.get_execution_log(pid))
        assert len(log) == 3
        assert all(e["dry_run"] is True for e in log)
        assert all(e["success"] is True for e in log)

    def test_dry_run_messages_say_dry_run(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        pid = self._make_plan(contract, direct_vm, direct_alice)
        direct_vm.sender = direct_alice
        contract.execute_migration(pid, NEW_ADDR, True)

        log = json.loads(contract.get_execution_log(pid))
        # Steps with setters get [DRY RUN] prefix
        setter_steps = [e for e in log if "DRY RUN" in e["message"]]
        assert len(setter_steps) > 0

    def test_skip_steps_succeed_without_rpc(self, direct_vm, direct_deploy, direct_alice):
        skip_steps = [
            {
                "index":     1,
                "step_type": "skip",
                "description": "Nothing to do",
                "old_field": None,
                "new_field": "removed_field",
                "transform": "none",
                "setter_fn": None,
                "risk":      "LOW",
            }
        ]
        contract = _deploy(direct_deploy)
        pid = self._make_plan(contract, direct_vm, direct_alice, steps=skip_steps)
        direct_vm.sender = direct_alice
        result = json.loads(contract.execute_migration(pid, NEW_ADDR, True))
        assert result["all_success"] is True
        assert result["steps_run"] == 1

    def test_plan_not_found_raises(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("not found"):
            contract.execute_migration("plan-999", NEW_ADDR, True)

    def test_complete_plan_cannot_rerun(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_state(direct_vm)
        _mock_plan_llm(direct_vm)
        direct_vm.sender = direct_alice
        pid = contract.plan_migration(OLD_ADDR, NEW_CODE)

        _mock_step_ok(direct_vm)
        contract.execute_migration(pid, NEW_ADDR, False)   # makes it COMPLETE

        with direct_vm.expect_revert("PLANNED or FAILED"):
            contract.execute_migration(pid, NEW_ADDR, True)


# ── execute_migration dry_run=False ───────────────────────────────────────────

class TestExecuteMigrationLive:
    def _make_plan(self, contract, direct_vm, direct_alice, steps=None):
        _mock_state(direct_vm)
        _mock_plan_llm(direct_vm, steps=steps)
        direct_vm.sender = direct_alice
        return contract.plan_migration(OLD_ADDR, NEW_CODE)

    def test_live_run_all_ok_sets_complete(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        pid = self._make_plan(contract, direct_vm, direct_alice)
        _mock_step_ok(direct_vm)

        direct_vm.sender = direct_alice
        result = json.loads(contract.execute_migration(pid, NEW_ADDR, False))
        assert result["all_success"] is True
        assert result["dry_run"] is False

        p = json.loads(contract.get_plan(pid))
        assert p["status"] == "COMPLETE"

    def test_live_run_failure_sets_failed(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        pid = self._make_plan(contract, direct_vm, direct_alice)
        _mock_step_fail(direct_vm)

        direct_vm.sender = direct_alice
        contract.execute_migration(pid, NEW_ADDR, False)

        p = json.loads(contract.get_plan(pid))
        assert p["status"] == "FAILED"

    def test_failed_plan_can_retry(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        pid = self._make_plan(contract, direct_vm, direct_alice)

        _mock_step_fail(direct_vm)
        direct_vm.sender = direct_alice
        contract.execute_migration(pid, NEW_ADDR, False)  # FAILED

        direct_vm.clear_mocks()
        _mock_step_ok(direct_vm)
        contract.execute_migration(pid, NEW_ADDR, False)  # retry

        p = json.loads(contract.get_plan(pid))
        assert p["status"] == "COMPLETE"

    def test_no_node_url_raises_on_live_run(self, direct_vm, direct_deploy, direct_alice):
        contract = direct_deploy("state_migrator.py")  # no node_url
        _mock_state(direct_vm)
        _mock_plan_llm(direct_vm)
        direct_vm.sender = direct_alice
        pid = contract.plan_migration(OLD_ADDR, NEW_CODE)

        with direct_vm.expect_revert("node_url not configured"):
            contract.execute_migration(pid, NEW_ADDR, False)

    def test_snapshot_available_flag_in_result(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        pid = self._make_plan(contract, direct_vm, direct_alice)
        _mock_step_ok(direct_vm)

        direct_vm.sender = direct_alice
        result = json.loads(contract.execute_migration(pid, NEW_ADDR, False))
        assert result["snapshot_available"] is True

    def test_live_logs_have_dry_run_false(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        pid = self._make_plan(contract, direct_vm, direct_alice)
        _mock_step_ok(direct_vm)

        direct_vm.sender = direct_alice
        contract.execute_migration(pid, NEW_ADDR, False)

        log = json.loads(contract.get_execution_log(pid))
        assert all(e["dry_run"] is False for e in log)


# ── views ─────────────────────────────────────────────────────────────────────

class TestViews:
    def test_get_plan_not_found(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        r = json.loads(contract.get_plan("plan-999"))
        assert r["error"] == "not found"

    def test_get_execution_log_empty(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        assert json.loads(contract.get_execution_log("plan-999")) == []

    def test_list_plans_empty(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        assert json.loads(contract.list_plans()) == []

    def test_list_plans_breaking_count(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        _mock_state(direct_vm)
        _mock_plan_llm(
            direct_vm,
            breaking=["field removed", "type changed"],
        )
        direct_vm.sender = direct_alice
        pid = contract.plan_migration(OLD_ADDR, NEW_CODE)

        plans = json.loads(contract.list_plans())
        entry = next(p for p in plans if p["plan_id"] == pid)
        assert entry["breaking_changes"] == 2

    def test_default_node_url_empty(self, direct_vm, direct_deploy):
        contract = direct_deploy("state_migrator.py")
        assert contract.node_url == ""
