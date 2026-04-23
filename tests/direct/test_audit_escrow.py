"""Direct-mode tests for audit_escrow.py (AuditEscrow contract)."""

import json
import pytest

from conftest import _state

AUDITOR  = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
CLIENT   = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
TARGET   = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
CRITERIA = "All critical and high vulnerabilities identified and documented"
REPORT_URL = "https://reports.example.com/audit-final.pdf"
DRAFT_URL  = "https://reports.example.com/audit-draft.pdf"
PAYMENT    = 1_000_000


# ── Deploy / escrow helpers ────────────────────────────────────────────────────

def _deploy(direct_deploy):
    return direct_deploy("audit_escrow.py")


def _create(direct_deploy, direct_vm, client=CLIENT, auditor=AUDITOR,
            payment=PAYMENT, draft_pct=30):
    contract = _deploy(direct_deploy)
    direct_vm.sender = client
    eid = contract.create_audit_escrow(
        auditor, TARGET, payment, REPORT_URL, CRITERIA, draft_pct
    )
    return contract, eid


# ── Mock helpers ──────────────────────────────────────────────────────────────

GOOD_REPORT = (
    "Methodology: We reviewed the code line by line. "
    f"Contract 0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef was analysed. "
    "Findings: 2 Critical, 3 High, 2 Medium. "
    "Recommendations: Fix reentrancy guard. All critical and high identified."
)

BAD_REPORT = "This is a brief note. Nothing much here."


def _mock_report(direct_vm, body=GOOD_REPORT, status=200):
    direct_vm.mock_web(r"reports\.example\.com", {"status": status, "body": body})


def _mock_draft_llm(direct_vm, acceptable=True, rejection=""):
    direct_vm.mock_llm(
        r"DRAFT check",
        {
            "has_methodology": acceptable,
            "has_findings": acceptable,
            "has_recommendations": acceptable,
            "contract_referenced": acceptable,
            "has_severity_levels": acceptable,
            "draft_acceptable": acceptable,
            "rejection_reason": rejection,
        },
    )


def _mock_final_llm(direct_vm, all_met=True, rejection=""):
    direct_vm.mock_llm(
        r"FINAL security audit report",
        {
            "has_methodology": all_met,
            "has_findings": all_met,
            "has_recommendations": all_met,
            "contract_referenced": all_met,
            "severity_quantified": all_met,
            "criteria_met": all_met,
            "all_criteria_met": all_met,
            "rejection_reason": rejection,
        },
    )


# ── create_audit_escrow ───────────────────────────────────────────────────────

class TestCreateAuditEscrow:
    def test_returns_escrow_id(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        assert eid == "escrow-1"

    def test_escrow_stored(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        e = json.loads(contract.get_escrow(eid))
        assert e["auditor"] == AUDITOR.lower()
        assert e["client"] == CLIENT.lower()
        assert e["contract_to_audit"] == TARGET.lower()
        assert e["payment_wei"] == PAYMENT
        assert e["draft_pct"] == 30
        assert e["status"] == "FUNDED"

    def test_empty_auditor_raises(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        direct_vm.sender = CLIENT
        with direct_vm.expect_revert("auditor_address cannot be empty"):
            contract.create_audit_escrow("", TARGET, PAYMENT, REPORT_URL, CRITERIA)

    def test_empty_target_raises(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        direct_vm.sender = CLIENT
        with direct_vm.expect_revert("contract_to_audit_address cannot be empty"):
            contract.create_audit_escrow(AUDITOR, "", PAYMENT, REPORT_URL, CRITERIA)

    def test_zero_payment_raises(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        direct_vm.sender = CLIENT
        with direct_vm.expect_revert("payment_wei must be positive"):
            contract.create_audit_escrow(AUDITOR, TARGET, 0, REPORT_URL, CRITERIA)

    def test_draft_pct_clamped_to_100(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        direct_vm.sender = CLIENT
        eid = contract.create_audit_escrow(AUDITOR, TARGET, PAYMENT, REPORT_URL, CRITERIA, 150)
        e = json.loads(contract.get_escrow(eid))
        assert e["draft_pct"] == 100

    def test_draft_pct_clamped_to_zero(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        direct_vm.sender = CLIENT
        eid = contract.create_audit_escrow(AUDITOR, TARGET, PAYMENT, REPORT_URL, CRITERIA, -5)
        e = json.loads(contract.get_escrow(eid))
        assert e["draft_pct"] == 0

    def test_appears_in_list(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        listing = json.loads(contract.list_escrows())
        assert any(e["escrow_id"] == eid for e in listing)

    def test_sequential_ids(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        direct_vm.sender = CLIENT
        e1 = contract.create_audit_escrow(AUDITOR, TARGET, PAYMENT, REPORT_URL, CRITERIA)
        e2 = contract.create_audit_escrow(AUDITOR, TARGET, PAYMENT, REPORT_URL, CRITERIA)
        assert e1 == "escrow-1"
        assert e2 == "escrow-2"


# ── submit_draft_report ───────────────────────────────────────────────────────

class TestSubmitDraftReport:
    def test_acceptable_draft_sets_draft_accepted(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        _mock_report(direct_vm)
        _mock_draft_llm(direct_vm, acceptable=True)
        direct_vm.sender = AUDITOR
        contract.submit_draft_report(eid, DRAFT_URL)
        e = json.loads(contract.get_escrow(eid))
        assert e["status"] == "DRAFT_ACCEPTED"

    def test_draft_pct_payment_released(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm, draft_pct=30)
        _mock_report(direct_vm)
        _mock_draft_llm(direct_vm, acceptable=True)
        direct_vm.sender = AUDITOR
        contract.submit_draft_report(eid, DRAFT_URL)
        expected = (PAYMENT * 30) // 100
        assert contract.get_balance(AUDITOR.lower()) == expected

    def test_zero_draft_pct_no_payment(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm, draft_pct=0)
        _mock_report(direct_vm)
        _mock_draft_llm(direct_vm, acceptable=True)
        direct_vm.sender = AUDITOR
        contract.submit_draft_report(eid, DRAFT_URL)
        assert contract.get_balance(AUDITOR.lower()) == 0

    def test_rejected_draft_stays_funded(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        _mock_report(direct_vm)
        _mock_draft_llm(direct_vm, acceptable=False, rejection="Missing methodology")
        direct_vm.sender = AUDITOR
        result = json.loads(contract.submit_draft_report(eid, DRAFT_URL))
        e = json.loads(contract.get_escrow(eid))
        assert e["status"] == "FUNDED"
        assert result["draft_acceptable"] is False
        assert "methodology" in result["rejection_reason"].lower()

    def test_draft_url_stored(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        _mock_report(direct_vm)
        _mock_draft_llm(direct_vm, acceptable=True)
        direct_vm.sender = AUDITOR
        contract.submit_draft_report(eid, DRAFT_URL)
        e = json.loads(contract.get_escrow(eid))
        assert e["draft_report_url"] == DRAFT_URL

    def test_non_auditor_cannot_submit_draft(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        direct_vm.sender = CLIENT
        with direct_vm.expect_revert("Only the assigned auditor"):
            contract.submit_draft_report(eid, DRAFT_URL)

    def test_cannot_submit_draft_twice(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        _mock_report(direct_vm)
        _mock_draft_llm(direct_vm, acceptable=True)
        direct_vm.sender = AUDITOR
        contract.submit_draft_report(eid, DRAFT_URL)
        with direct_vm.expect_revert("FUNDED escrows"):
            contract.submit_draft_report(eid, DRAFT_URL)

    def test_empty_url_raises(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        direct_vm.sender = AUDITOR
        with direct_vm.expect_revert("cannot be empty"):
            contract.submit_draft_report(eid, "")

    def test_unknown_escrow_raises(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        direct_vm.sender = AUDITOR
        with direct_vm.expect_revert("not found"):
            contract.submit_draft_report("escrow-999", DRAFT_URL)

    def test_report_404_raises(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        _mock_report(direct_vm, status=404)
        direct_vm.sender = AUDITOR
        with direct_vm.expect_revert("404"):
            contract.submit_draft_report(eid, DRAFT_URL)


# ── submit_report ─────────────────────────────────────────────────────────────

class TestSubmitReport:
    def test_verified_report_releases_full_payment(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm, draft_pct=0)
        _mock_report(direct_vm)
        _mock_final_llm(direct_vm, all_met=True)
        direct_vm.sender = AUDITOR
        result = json.loads(contract.submit_report(eid, REPORT_URL))
        assert result["verified"] is True
        assert contract.get_balance(AUDITOR.lower()) == PAYMENT

    def test_verified_after_draft_releases_remainder(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm, draft_pct=30)
        _mock_report(direct_vm)
        _mock_draft_llm(direct_vm, acceptable=True)
        direct_vm.sender = AUDITOR
        contract.submit_draft_report(eid, DRAFT_URL)

        direct_vm.clear_mocks()
        _mock_report(direct_vm)
        _mock_final_llm(direct_vm, all_met=True)
        contract.submit_report(eid, REPORT_URL)

        expected = PAYMENT  # draft_paid + remainder = total
        assert contract.get_balance(AUDITOR.lower()) == expected

    def test_verified_sets_released_status(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        _mock_report(direct_vm)
        _mock_final_llm(direct_vm, all_met=True)
        direct_vm.sender = AUDITOR
        contract.submit_report(eid, REPORT_URL)
        e = json.loads(contract.get_escrow(eid))
        assert e["status"] == "RELEASED"

    def test_failed_verification_sets_report_submitted(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        _mock_report(direct_vm)
        _mock_final_llm(direct_vm, all_met=False, rejection="severity not quantified")
        direct_vm.sender = AUDITOR
        result = json.loads(contract.submit_report(eid, REPORT_URL))
        assert result["verified"] is False
        e = json.loads(contract.get_escrow(eid))
        assert e["status"] == "REPORT_SUBMITTED"

    def test_final_url_stored(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        _mock_report(direct_vm)
        _mock_final_llm(direct_vm, all_met=True)
        direct_vm.sender = AUDITOR
        contract.submit_report(eid, REPORT_URL)
        e = json.loads(contract.get_escrow(eid))
        assert e["final_report_url"] == REPORT_URL

    def test_submitted_at_set(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        _mock_report(direct_vm)
        _mock_final_llm(direct_vm, all_met=True)
        direct_vm.sender = AUDITOR
        now = _state["timestamp"]
        contract.submit_report(eid, REPORT_URL)
        e = json.loads(contract.get_escrow(eid))
        assert e["submitted_at"] == now

    def test_non_auditor_cannot_submit(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        direct_vm.sender = CLIENT
        with direct_vm.expect_revert("Only the assigned auditor"):
            contract.submit_report(eid, REPORT_URL)

    def test_wrong_status_raises(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        _mock_report(direct_vm)
        _mock_final_llm(direct_vm, all_met=True)
        direct_vm.sender = AUDITOR
        contract.submit_report(eid, REPORT_URL)  # RELEASED now
        with direct_vm.expect_revert("FUNDED or DRAFT_ACCEPTED"):
            contract.submit_report(eid, REPORT_URL)

    def test_empty_url_raises(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        direct_vm.sender = AUDITOR
        with direct_vm.expect_revert("cannot be empty"):
            contract.submit_report(eid, "")

    def test_verification_fields_returned(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        _mock_report(direct_vm)
        _mock_final_llm(direct_vm, all_met=True)
        direct_vm.sender = AUDITOR
        result = json.loads(contract.submit_report(eid, REPORT_URL))
        for field in ("has_methodology", "has_findings", "has_recommendations",
                      "severity_quantified", "contract_referenced"):
            assert field in result


# ── dispute_report ────────────────────────────────────────────────────────────

class TestDisputeReport:
    def _submitted(self, direct_deploy, direct_vm):
        contract, eid = _create(direct_deploy, direct_vm)
        _mock_report(direct_vm)
        _mock_final_llm(direct_vm, all_met=False)
        direct_vm.sender = AUDITOR
        contract.submit_report(eid, REPORT_URL)  # REPORT_SUBMITTED
        return contract, eid

    def test_client_can_dispute(self, direct_vm, direct_deploy):
        contract, eid = self._submitted(direct_deploy, direct_vm)
        direct_vm.sender = CLIENT
        contract.dispute_report(eid, "Report missing severity quantification")
        e = json.loads(contract.get_escrow(eid))
        assert e["status"] == "DISPUTED"
        assert "severity" in e["dispute_reason"]

    def test_non_client_cannot_dispute(self, direct_vm, direct_deploy):
        contract, eid = self._submitted(direct_deploy, direct_vm)
        direct_vm.sender = AUDITOR
        with direct_vm.expect_revert("Only the client"):
            contract.dispute_report(eid, "Bad report")

    def test_wrong_status_raises(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        direct_vm.sender = CLIENT
        with direct_vm.expect_revert("DRAFT_ACCEPTED or REPORT_SUBMITTED"):
            contract.dispute_report(eid, "Too early")

    def test_empty_reason_raises(self, direct_vm, direct_deploy):
        contract, eid = self._submitted(direct_deploy, direct_vm)
        direct_vm.sender = CLIENT
        with direct_vm.expect_revert("cannot be empty"):
            contract.dispute_report(eid, "")

    def test_outside_window_raises(self, direct_vm, direct_deploy):
        contract, eid = self._submitted(direct_deploy, direct_vm)
        _state["timestamp"] += 259201
        direct_vm.sender = CLIENT
        with direct_vm.expect_revert("Dispute window has closed"):
            contract.dispute_report(eid, "Too late")

    def test_client_can_dispute_draft_accepted(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        _mock_report(direct_vm)
        _mock_draft_llm(direct_vm, acceptable=True)
        direct_vm.sender = AUDITOR
        contract.submit_draft_report(eid, DRAFT_URL)
        direct_vm.sender = CLIENT
        contract.dispute_report(eid, "Draft is incomplete")
        e = json.loads(contract.get_escrow(eid))
        assert e["status"] == "DISPUTED"

    def test_unknown_escrow_raises(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        direct_vm.sender = CLIENT
        with direct_vm.expect_revert("not found"):
            contract.dispute_report("escrow-999", "bad")


# ── resolve_dispute ───────────────────────────────────────────────────────────

class TestResolveDispute:
    def _disputed(self, direct_deploy, direct_vm):
        contract, eid = _create(direct_deploy, direct_vm)
        _mock_report(direct_vm)
        _mock_final_llm(direct_vm, all_met=False)
        direct_vm.sender = AUDITOR
        contract.submit_report(eid, REPORT_URL)
        direct_vm.sender = CLIENT
        contract.dispute_report(eid, "Bad report")
        return contract, eid

    def test_admin_releases_to_auditor(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.sender = direct_alice  # admin
        contract, eid = self._disputed(direct_deploy, direct_vm)
        direct_vm.sender = direct_alice
        contract.resolve_dispute(eid, True)
        assert contract.get_balance(AUDITOR.lower()) == PAYMENT
        e = json.loads(contract.get_escrow(eid))
        assert e["status"] == "RELEASED"

    def test_admin_refunds_to_client(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.sender = direct_alice
        contract, eid = self._disputed(direct_deploy, direct_vm)
        direct_vm.sender = direct_alice
        contract.resolve_dispute(eid, False)
        assert contract.get_balance(CLIENT.lower()) == PAYMENT
        e = json.loads(contract.get_escrow(eid))
        assert e["status"] == "CANCELLED"

    def test_refund_after_draft_payment_is_remainder(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.sender = direct_alice
        contract, eid = _create(direct_deploy, direct_vm, draft_pct=30)
        _mock_report(direct_vm)
        _mock_draft_llm(direct_vm, acceptable=True)
        direct_vm.sender = AUDITOR
        contract.submit_draft_report(eid, DRAFT_URL)

        direct_vm.clear_mocks()
        _mock_report(direct_vm)
        _mock_final_llm(direct_vm, all_met=False)
        contract.submit_report(eid, REPORT_URL)
        direct_vm.sender = CLIENT
        contract.dispute_report(eid, "Incomplete")
        direct_vm.sender = direct_alice
        contract.resolve_dispute(eid, False)

        draft_paid = (PAYMENT * 30) // 100
        remaining  = PAYMENT - draft_paid
        assert contract.get_balance(CLIENT.lower()) == remaining

    def test_non_admin_cannot_resolve(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.sender = direct_alice
        contract, eid = self._disputed(direct_deploy, direct_vm)
        direct_vm.sender = CLIENT
        with direct_vm.expect_revert("Only admin"):
            contract.resolve_dispute(eid, True)

    def test_not_disputed_raises(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.sender = direct_alice
        contract, eid = _create(direct_deploy, direct_vm)
        direct_vm.sender = direct_alice  # keep admin as caller
        with direct_vm.expect_revert("not DISPUTED"):
            contract.resolve_dispute(eid, True)

    def test_unknown_escrow_raises(self, direct_vm, direct_deploy, direct_alice):
        direct_vm.sender = direct_alice
        contract = _deploy(direct_deploy)
        with direct_vm.expect_revert("not found"):
            contract.resolve_dispute("escrow-999", True)


# ── withdraw ──────────────────────────────────────────────────────────────────

class TestWithdraw:
    def test_withdraw_clears_balance(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm, draft_pct=0)
        _mock_report(direct_vm)
        _mock_final_llm(direct_vm, all_met=True)
        direct_vm.sender = AUDITOR
        contract.submit_report(eid, REPORT_URL)
        assert contract.get_balance(AUDITOR.lower()) == PAYMENT
        contract.withdraw(AUDITOR)
        assert contract.get_balance(AUDITOR.lower()) == 0

    def test_zero_balance_raises(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        with direct_vm.expect_revert("No claimable balance"):
            contract.withdraw(AUDITOR)

    def test_double_withdraw_raises(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm, draft_pct=0)
        _mock_report(direct_vm)
        _mock_final_llm(direct_vm, all_met=True)
        direct_vm.sender = AUDITOR
        contract.submit_report(eid, REPORT_URL)
        contract.withdraw(AUDITOR)
        with direct_vm.expect_revert("No claimable balance"):
            contract.withdraw(AUDITOR)


# ── view methods ──────────────────────────────────────────────────────────────

class TestViewMethods:
    def test_get_escrow_not_found(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        r = json.loads(contract.get_escrow("escrow-999"))
        assert r["error"] == "not found"

    def test_get_balance_unknown_is_zero(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        assert contract.get_balance("0xDEAD") == 0

    def test_list_escrows_empty(self, direct_vm, direct_deploy):
        contract = _deploy(direct_deploy)
        assert json.loads(contract.list_escrows()) == []

    def test_list_escrows_populated(self, direct_vm, direct_deploy):
        contract, eid = _create(direct_deploy, direct_vm)
        listing = json.loads(contract.list_escrows())
        assert len(listing) == 1
        assert listing[0]["escrow_id"] == eid
