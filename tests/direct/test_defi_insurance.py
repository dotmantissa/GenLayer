"""Direct-mode tests for defi_insurance.py (DeFiInsurance contract)."""

import json
import pytest

from conftest import _state

TVL_URL   = "https://api.llama.fi/tvl/uniswap"
AUDIT_URL = "https://audit.example.com/uniswap"
PROTO     = "Uniswap"
PROTO2    = "Aave"


# ── Deploy helpers ────────────────────────────────────────────────────────────

def _deploy(direct_deploy, cooldown=0):
    return direct_deploy("defi_insurance.py", cooldown)


def _deploy_with_proto(direct_deploy, direct_vm, proto=PROTO, tvl=TVL_URL, audit=AUDIT_URL):
    contract = _deploy(direct_deploy)
    contract.register_protocol(proto, tvl, audit)
    return contract


# ── Mock helpers ──────────────────────────────────────────────────────────────

def _mock_tvl(direct_vm, tvl_value=5_000_000):
    direct_vm.mock_web(r"api\.llama\.fi", {"status": 200, "body": str(tvl_value)})


def _mock_hacklab(direct_vm, hit=False):
    body = PROTO.lower() if hit else "some other protocol"
    direct_vm.mock_web(r"raw\.githubusercontent\.com", {"status": 200, "body": body})


def _mock_ai(direct_vm, hack=False, confidence=20, event_type="normal"):
    direct_vm.mock_llm(
        r"DeFi security analyst",
        {
            "hack_detected": hack,
            "confidence": confidence,
            "event_type": event_type,
            "description": "Assessment complete.",
        },
    )


def _mock_all_clear(direct_vm, current_tvl=5_000_000):
    _mock_tvl(direct_vm, current_tvl)
    _mock_hacklab(direct_vm, hit=False)
    _mock_ai(direct_vm, hack=False, confidence=10)


def _mock_all_alarm(direct_vm, current_tvl=1_000):
    _mock_tvl(direct_vm, current_tvl)
    _mock_hacklab(direct_vm, hit=True)
    _mock_ai(direct_vm, hack=True, confidence=95, event_type="exploit")


# ── register_protocol ─────────────────────────────────────────────────────────

class TestRegisterProtocol:
    def test_admin_can_register(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        contract.register_protocol(PROTO, TVL_URL, AUDIT_URL)
        p = json.loads(contract.get_protocol(PROTO))
        assert p["name"] == PROTO
        assert p["status"] == "ACTIVE"

    def test_non_admin_cannot_register(self, direct_vm, direct_deploy, direct_bob):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("Only admin"):
            contract.register_protocol(PROTO, TVL_URL, AUDIT_URL)

    def test_duplicate_raises(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        contract.register_protocol(PROTO, TVL_URL, AUDIT_URL)
        with direct_vm.expect_revert("already registered"):
            contract.register_protocol(PROTO, TVL_URL, AUDIT_URL)

    def test_empty_name_raises(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        with direct_vm.expect_revert("cannot be empty"):
            contract.register_protocol("", TVL_URL, AUDIT_URL)

    def test_appears_in_list(self, direct_vm, direct_deploy):
        contract = _deploy_with_proto(direct_deploy, direct_vm)
        listing = json.loads(contract.list_protocols())
        assert any(p["name"] == PROTO for p in listing)

    def test_proto_policy_ids_initialized(self, direct_vm, direct_deploy):
        contract = _deploy_with_proto(direct_deploy, direct_vm)
        assert json.loads(contract.proto_policy_ids[PROTO]) == []


# ── deposit_policy ────────────────────────────────────────────────────────────

class TestDepositPolicy:
    def test_returns_policy_id(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy_with_proto(direct_deploy, direct_vm)
        direct_vm.sender = direct_alice
        pid = contract.deposit_policy(PROTO, 1_000_000, 10_000)
        assert pid == "policy-1"

    def test_policy_stored(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy_with_proto(direct_deploy, direct_vm)
        direct_vm.sender = direct_alice
        pid = contract.deposit_policy(PROTO, 1_000_000, 10_000)
        p = json.loads(contract.get_policy(pid))
        assert p["protocol"] == PROTO
        assert p["coverage_wei"] == 1_000_000
        assert p["premium_wei"] == 10_000
        assert p["status"] == "ACTIVE"

    def test_reserve_accumulates(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _deploy_with_proto(direct_deploy, direct_vm)
        direct_vm.sender = direct_alice
        contract.deposit_policy(PROTO, 1_000_000, 10_000)
        direct_vm.sender = direct_bob
        contract.deposit_policy(PROTO, 2_000_000, 20_000)
        assert int(contract.reserve_wei) == 30_000

    def test_proto_policy_ids_updated(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy_with_proto(direct_deploy, direct_vm)
        direct_vm.sender = direct_alice
        pid = contract.deposit_policy(PROTO, 1_000_000, 10_000)
        ids = json.loads(contract.proto_policy_ids[PROTO])
        assert pid in ids

    def test_unknown_protocol_raises(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("not registered"):
            contract.deposit_policy("UnknownProto", 1_000_000, 10_000)

    def test_zero_premium_raises(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy_with_proto(direct_deploy, direct_vm)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("premium_wei must be positive"):
            contract.deposit_policy(PROTO, 1_000_000, 0)

    def test_zero_coverage_raises(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy_with_proto(direct_deploy, direct_vm)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("coverage_wei must be positive"):
            contract.deposit_policy(PROTO, 0, 10_000)

    def test_hacked_protocol_rejects_new_policies(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy_with_proto(direct_deploy, direct_vm)
        proto = contract.protocols[PROTO]
        proto.status = "HACKED"
        contract.protocols[PROTO] = proto
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("not accepting new policies"):
            contract.deposit_policy(PROTO, 1_000_000, 10_000)

    def test_holder_set_from_sender(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy_with_proto(direct_deploy, direct_vm)
        direct_vm.sender = direct_alice
        pid = contract.deposit_policy(PROTO, 1_000_000, 10_000)
        p = json.loads(contract.get_policy(pid))
        assert p["holder"] == direct_alice.lower()


# ── assess_risk ───────────────────────────────────────────────────────────────

class TestAssessRisk:
    def _setup(self, direct_deploy, direct_vm, prior_tvl=10_000_000):
        contract = _deploy_with_proto(direct_deploy, direct_vm)
        if prior_tvl > 0:
            proto = contract.protocols[PROTO]
            proto.last_tvl_usd = proto.last_tvl_usd.__class__(prior_tvl)
            contract.protocols[PROTO] = proto
        return contract

    def test_returns_json_summary(self, direct_vm, direct_deploy):
        contract = self._setup(direct_deploy, direct_vm)
        _mock_all_clear(direct_vm)
        result = json.loads(contract.assess_risk(PROTO))
        assert result["protocol"] == PROTO
        assert "sources_confirmed" in result

    def test_no_hack_when_all_clear(self, direct_vm, direct_deploy):
        contract = self._setup(direct_deploy, direct_vm)
        _mock_all_clear(direct_vm, current_tvl=9_000_000)
        result = json.loads(contract.assess_risk(PROTO))
        assert result["hack_triggered"] is False
        assert result["alert_id"] == ""

    def test_hack_triggered_when_all_3_confirm(self, direct_vm, direct_deploy):
        contract = self._setup(direct_deploy, direct_vm, prior_tvl=10_000_000)
        _mock_all_alarm(direct_vm, current_tvl=1_000)
        result = json.loads(contract.assess_risk(PROTO))
        assert result["hack_triggered"] is True
        assert result["alert_id"].startswith("alert-")

    def test_only_2_sources_no_alert(self, direct_vm, direct_deploy):
        contract = self._setup(direct_deploy, direct_vm, prior_tvl=10_000_000)
        _mock_tvl(direct_vm, 100)          # source1: YES (huge drop)
        _mock_hacklab(direct_vm, hit=True) # source2: YES
        _mock_ai(direct_vm, hack=False, confidence=20)  # source3: NO
        result = json.loads(contract.assess_risk(PROTO))
        assert result["hack_triggered"] is False

    def test_tvl_updated_after_assess(self, direct_vm, direct_deploy):
        contract = self._setup(direct_deploy, direct_vm, prior_tvl=0)
        _mock_all_clear(direct_vm, current_tvl=8_000_000)
        contract.assess_risk(PROTO)
        proto = json.loads(contract.get_protocol(PROTO))
        assert proto["last_tvl_usd"] == 8_000_000

    def test_last_assessed_at_updated(self, direct_vm, direct_deploy):
        contract = self._setup(direct_deploy, direct_vm)
        _mock_all_clear(direct_vm)
        now = _state["timestamp"]
        contract.assess_risk(PROTO)
        proto = json.loads(contract.get_protocol(PROTO))
        assert proto["last_assessed_at"] == now

    def test_cooldown_enforced(self, direct_vm, direct_deploy):
        contract = _deploy_with_proto(direct_deploy, direct_vm)
        _state["timestamp"] = 1_000_000
        contract2 = _deploy(direct_deploy, cooldown=3600)
        contract2.register_protocol(PROTO, TVL_URL, AUDIT_URL)
        proto = contract2.protocols[PROTO]
        proto.last_assessed_at = proto.last_assessed_at.__class__(999_000)  # 1000s ago < 3600s
        contract2.protocols[PROTO] = proto
        with direct_vm.expect_revert("Cooldown active"):
            contract2.assess_risk(PROTO)

    def test_unknown_protocol_raises(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        with direct_vm.expect_revert("not registered"):
            contract.assess_risk("NoProto")

    def test_hack_alert_has_correct_fields(self, direct_vm, direct_deploy):
        contract = self._setup(direct_deploy, direct_vm, prior_tvl=10_000_000)
        _mock_all_alarm(direct_vm, current_tvl=1_000)
        now = _state["timestamp"]
        result = json.loads(contract.assess_risk(PROTO))
        alert_id = result["alert_id"]
        a = json.loads(contract.get_alert(alert_id))
        assert a["protocol"] == PROTO
        assert a["status"] == "PENDING"
        assert a["dispute_deadline"] == now + 259200
        assert a["sources"] == 3

    def test_protocol_status_set_to_hacked(self, direct_vm, direct_deploy):
        contract = self._setup(direct_deploy, direct_vm, prior_tvl=10_000_000)
        _mock_all_alarm(direct_vm, current_tvl=1_000)
        contract.assess_risk(PROTO)
        proto = json.loads(contract.get_protocol(PROTO))
        assert proto["status"] == "HACKED"

    def test_alert_appears_in_list(self, direct_vm, direct_deploy):
        contract = self._setup(direct_deploy, direct_vm, prior_tvl=10_000_000)
        _mock_all_alarm(direct_vm, current_tvl=1_000)
        result = json.loads(contract.assess_risk(PROTO))
        aid = result["alert_id"]
        listing = json.loads(contract.list_alerts())
        assert any(a["alert_id"] == aid for a in listing)


# ── dispute_alert ─────────────────────────────────────────────────────────────

class TestDisputeAlert:
    def _create_alert(self, direct_deploy, direct_vm):
        contract = TestAssessRisk()._setup(direct_deploy, direct_vm, prior_tvl=10_000_000)
        _mock_all_alarm(direct_vm, current_tvl=1_000)
        result = json.loads(contract.assess_risk(PROTO))
        return contract, result["alert_id"]

    def test_dispute_within_window(self, direct_vm, direct_deploy, direct_bob):
        contract, aid = self._create_alert(direct_deploy, direct_vm)
        direct_vm.sender = direct_bob
        contract.dispute_alert(aid)
        a = json.loads(contract.get_alert(aid))
        assert a["status"] == "DISPUTED"
        assert a["resolved_by"] == direct_bob.lower()

    def test_dispute_outside_window_raises(self, direct_vm, direct_deploy):
        contract, aid = self._create_alert(direct_deploy, direct_vm)
        _state["timestamp"] += 259201  # past the 72h window
        with direct_vm.expect_revert("Dispute window has closed"):
            contract.dispute_alert(aid)

    def test_dispute_non_pending_raises(self, direct_vm, direct_deploy):
        contract, aid = self._create_alert(direct_deploy, direct_vm)
        contract.dispute_alert(aid)  # sets to DISPUTED
        with direct_vm.expect_revert("Alert is not PENDING"):
            contract.dispute_alert(aid)

    def test_unknown_alert_raises(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        with direct_vm.expect_revert("not found"):
            contract.dispute_alert("alert-999")


# ── resolve_dispute ───────────────────────────────────────────────────────────

class TestResolveDispute:
    def _create_disputed(self, direct_deploy, direct_vm, direct_bob):
        contract = TestAssessRisk()._setup(direct_deploy, direct_vm, prior_tvl=10_000_000)
        _mock_all_alarm(direct_vm, current_tvl=1_000)
        result = json.loads(contract.assess_risk(PROTO))
        aid = result["alert_id"]
        direct_vm.sender = direct_bob
        contract.dispute_alert(aid)
        return contract, aid

    def test_admin_can_confirm(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        direct_vm.sender = direct_alice
        contract, aid = self._create_disputed(direct_deploy, direct_vm, direct_bob)
        direct_vm.sender = direct_alice
        contract.resolve_dispute(aid, True)
        a = json.loads(contract.get_alert(aid))
        assert a["status"] == "CONFIRMED"

    def test_admin_can_reject(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        direct_vm.sender = direct_alice
        contract, aid = self._create_disputed(direct_deploy, direct_vm, direct_bob)
        direct_vm.sender = direct_alice
        contract.resolve_dispute(aid, False)
        a = json.loads(contract.get_alert(aid))
        assert a["status"] == "REJECTED"

    def test_non_admin_cannot_resolve(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        direct_vm.sender = direct_alice
        contract, aid = self._create_disputed(direct_deploy, direct_vm, direct_bob)
        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("Only admin"):
            contract.resolve_dispute(aid, True)

    def test_non_disputed_raises(self, direct_vm, direct_deploy, direct_alice):
        contract = TestAssessRisk()._setup(direct_deploy, direct_vm, prior_tvl=10_000_000)
        _mock_all_alarm(direct_vm, current_tvl=1_000)
        direct_vm.sender = direct_alice
        result = json.loads(contract.assess_risk(PROTO))
        aid = result["alert_id"]
        with direct_vm.expect_revert("not DISPUTED"):
            contract.resolve_dispute(aid, True)

    def test_unknown_alert_raises(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        with direct_vm.expect_revert("not found"):
            contract.resolve_dispute("alert-999", True)


# ── trigger_payout ────────────────────────────────────────────────────────────

class TestTriggerPayout:
    def _setup_for_payout(self, direct_deploy, direct_vm, direct_alice, direct_bob):
        """Create protocol, two policies, and a PENDING alert past the window."""
        contract = TestAssessRisk()._setup(direct_deploy, direct_vm, prior_tvl=10_000_000)

        # Buy two policies
        direct_vm.sender = direct_alice
        contract.deposit_policy(PROTO, 1_000_000, 100_000)
        direct_vm.sender = direct_bob
        contract.deposit_policy(PROTO, 1_000_000, 100_000)

        # Trigger hack alert
        _mock_all_alarm(direct_vm, current_tvl=1_000)
        contract.assess_risk(PROTO)
        alert_listing = json.loads(contract.list_alerts())
        aid = alert_listing[0]["alert_id"]

        # Advance time past 72-hour dispute window
        _state["timestamp"] += 259201
        return contract, aid

    def test_payout_after_window(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract, aid = self._setup_for_payout(direct_deploy, direct_vm, direct_alice, direct_bob)
        result = json.loads(contract.trigger_payout(aid))
        assert result["alert_id"] == aid
        assert result["total_paid_wei"] > 0

    def test_policies_set_to_paid_out(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract, aid = self._setup_for_payout(direct_deploy, direct_vm, direct_alice, direct_bob)
        contract.trigger_payout(aid)
        for pid in contract.policy_order:
            p = json.loads(contract.get_policy(pid))
            assert p["status"] == "PAID_OUT"

    def test_balances_credited(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract, aid = self._setup_for_payout(direct_deploy, direct_vm, direct_alice, direct_bob)
        contract.trigger_payout(aid)
        alice_bal = contract.get_balance(direct_alice.lower())
        bob_bal   = contract.get_balance(direct_bob.lower())
        assert alice_bal > 0
        assert bob_bal > 0

    def test_proportional_payout(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        # Equal coverage => equal payouts
        contract, aid = self._setup_for_payout(direct_deploy, direct_vm, direct_alice, direct_bob)
        contract.trigger_payout(aid)
        alice_bal = contract.get_balance(direct_alice.lower())
        bob_bal   = contract.get_balance(direct_bob.lower())
        assert alice_bal == bob_bal

    def test_payout_within_window_raises(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = TestAssessRisk()._setup(direct_deploy, direct_vm, prior_tvl=10_000_000)
        _mock_all_alarm(direct_vm, current_tvl=1_000)
        contract.assess_risk(PROTO)
        listing = json.loads(contract.list_alerts())
        aid = listing[0]["alert_id"]
        # Still inside dispute window
        with direct_vm.expect_revert("Dispute window still open"):
            contract.trigger_payout(aid)

    def test_payout_after_confirmed(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        """Admin confirms dispute => trigger_payout works immediately."""
        contract = TestAssessRisk()._setup(direct_deploy, direct_vm, prior_tvl=10_000_000)
        direct_vm.sender = direct_alice
        contract.deposit_policy(PROTO, 1_000_000, 100_000)
        _mock_all_alarm(direct_vm, current_tvl=1_000)
        result_json = contract.assess_risk(PROTO)
        aid = json.loads(result_json)["alert_id"]

        # Alice (admin) disputes then confirms (admin resolves)
        direct_vm.sender = direct_bob
        contract.dispute_alert(aid)
        direct_vm.sender = direct_alice  # admin
        contract.resolve_dispute(aid, True)

        result = json.loads(contract.trigger_payout(aid))
        assert result["total_paid_wei"] > 0

    def test_payout_rejected_alert_raises(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = TestAssessRisk()._setup(direct_deploy, direct_vm, prior_tvl=10_000_000)
        direct_vm.sender = direct_alice
        _mock_all_alarm(direct_vm, current_tvl=1_000)
        result_json = contract.assess_risk(PROTO)
        aid = json.loads(result_json)["alert_id"]

        direct_vm.sender = direct_bob
        contract.dispute_alert(aid)
        direct_vm.sender = direct_alice
        contract.resolve_dispute(aid, False)  # rejected

        with direct_vm.expect_revert("cannot be paid out"):
            contract.trigger_payout(aid)

    def test_unknown_alert_raises(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        with direct_vm.expect_revert("not found"):
            contract.trigger_payout("alert-999")


# ── withdraw ──────────────────────────────────────────────────────────────────

class TestWithdraw:
    def _setup_with_balance(self, direct_deploy, direct_vm, direct_alice, direct_bob):
        contract, aid = TestTriggerPayout()._setup_for_payout(
            direct_deploy, direct_vm, direct_alice, direct_bob
        )
        contract.trigger_payout(aid)
        return contract

    def test_withdraw_clears_balance(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = self._setup_with_balance(direct_deploy, direct_vm, direct_alice, direct_bob)
        assert contract.get_balance(direct_alice.lower()) > 0
        contract.withdraw(direct_alice)
        assert contract.get_balance(direct_alice.lower()) == 0

    def test_zero_balance_raises(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        with direct_vm.expect_revert("No claimable balance"):
            contract.withdraw(direct_alice)

    def test_double_withdraw_raises(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = self._setup_with_balance(direct_deploy, direct_vm, direct_alice, direct_bob)
        contract.withdraw(direct_alice)
        with direct_vm.expect_revert("No claimable balance"):
            contract.withdraw(direct_alice)


# ── view methods ──────────────────────────────────────────────────────────────

class TestViewMethods:
    def test_get_protocol_not_found(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        r = json.loads(contract.get_protocol("NoProto"))
        assert r["error"] == "not found"

    def test_get_policy_not_found(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        r = json.loads(contract.get_policy("policy-999"))
        assert r["error"] == "not found"

    def test_get_alert_not_found(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        r = json.loads(contract.get_alert("alert-999"))
        assert r["error"] == "not found"

    def test_get_balance_unknown_returns_zero(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        assert contract.get_balance("0xDEAD") == 0

    def test_list_protocols_empty(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        assert json.loads(contract.list_protocols()) == []

    def test_list_protocols_after_register(self, direct_vm, direct_deploy):
        contract = _deploy_with_proto(direct_deploy, direct_vm)
        listing = json.loads(contract.list_protocols())
        assert len(listing) == 1
        assert listing[0]["name"] == PROTO

    def test_list_alerts_empty(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        assert json.loads(contract.list_alerts()) == []

    def test_list_alerts_after_hack(self, direct_vm, direct_deploy):
        contract = TestAssessRisk()._setup(direct_deploy, direct_vm, prior_tvl=10_000_000)
        _mock_all_alarm(direct_vm, current_tvl=1_000)
        contract.assess_risk(PROTO)
        listing = json.loads(contract.list_alerts())
        assert len(listing) == 1
        assert listing[0]["protocol"] == PROTO

    def test_counter_increments_across_types(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy_with_proto(direct_deploy, direct_vm)
        direct_vm.sender = direct_alice
        pid1 = contract.deposit_policy(PROTO, 1_000_000, 10_000)  # counter=1
        pid2 = contract.deposit_policy(PROTO, 2_000_000, 20_000)  # counter=2
        assert pid1 == "policy-1"
        assert pid2 == "policy-2"
