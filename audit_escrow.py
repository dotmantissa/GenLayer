# v0.1.0
# { "Depends": "py-genlayer:test" }

from genlayer import *
import json

ERROR_EXPECTED  = "[EXPECTED]"
ERROR_EXTERNAL  = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM       = "[LLM_ERROR]"

DISPUTE_WINDOW    = 259200  # 72 hours
DEFAULT_DRAFT_PCT = 30      # % of payment released for an accepted draft


@allow_storage
@dataclass
class Escrow:
    escrow_id: str
    client: str
    auditor: str
    contract_to_audit: str
    payment_wei: u256
    draft_pct: u256            # % of payment for the draft milestone (0 = no draft)
    report_url_placeholder: str
    completion_criteria: str
    draft_report_url: str      # empty until submit_draft_report
    final_report_url: str      # empty until submit_report
    draft_paid_wei: u256       # amount already released for the draft
    status: str                # FUNDED | DRAFT_ACCEPTED | REPORT_SUBMITTED | DISPUTED | RELEASED | CANCELLED
    created_at: u256
    submitted_at: u256         # timestamp of final report submission (0 if not yet)
    dispute_reason: str


class AuditEscrow(gl.Contract):
    admin: Address
    counter: u256
    escrows: TreeMap[str, Escrow]
    escrow_order: DynArray[str]
    balances: TreeMap[str, u256]  # address -> claimable wei

    def __init__(self):
        self.admin   = gl.message.sender_account
        self.counter = u256(0)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _next_id(self, prefix: str) -> str:
        n = int(self.counter) + 1
        self.counter = u256(n)
        return f"{prefix}-{n}"

    def _require_admin(self) -> None:
        if gl.message.sender_account != self.admin:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Only admin can call this")

    def _credit(self, wallet: str, amount: int) -> None:
        prev = int(self.balances[wallet]) if wallet in self.balances else 0
        self.balances[wallet] = u256(prev + amount)

    def _fetch_report(self, url: str) -> str:
        try:
            res = gl.nondet.web.get(url)
            if res.status == 404:
                raise gl.vm.UserError(f"{ERROR_EXTERNAL} Report URL returned 404")
            if res.status >= 500:
                raise gl.vm.UserError(
                    f"{ERROR_TRANSIENT} Report host unavailable ({res.status})"
                )
            if res.status >= 400:
                raise gl.vm.UserError(f"{ERROR_EXTERNAL} Report URL error ({res.status})")
            return res.body.decode("utf-8", errors="replace")[:8000]
        except gl.vm.UserError:
            raise
        except Exception as e:
            raise gl.vm.UserError(f"{ERROR_TRANSIENT} Failed to fetch report: {e}")

    # ── Write methods ─────────────────────────────────────────────────────────

    @gl.public.write
    def create_audit_escrow(
        self,
        auditor_address: str,
        contract_to_audit_address: str,
        payment_wei: int,
        report_url_placeholder: str,
        completion_criteria: str,
        draft_pct: int = DEFAULT_DRAFT_PCT,
    ) -> str:
        """
        Client creates an escrow for a smart contract audit.
        draft_pct: percentage (0-100) of payment released when a draft is accepted.
        Returns escrow_id.
        Emits: [EscrowCreated]
        """
        auditor = str(auditor_address).strip().lower()
        target  = str(contract_to_audit_address).strip().lower()
        if not auditor:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} auditor_address cannot be empty")
        if not target:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} contract_to_audit_address cannot be empty"
            )
        if int(payment_wei) <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} payment_wei must be positive")

        pct       = max(0, min(100, int(draft_pct)))
        escrow_id = self._next_id("escrow")
        client    = str(gl.message.sender_account).lower()
        now       = int(gl.block.timestamp)

        self.escrows[escrow_id] = Escrow(
            escrow_id=escrow_id,
            client=client,
            auditor=auditor,
            contract_to_audit=target,
            payment_wei=u256(int(payment_wei)),
            draft_pct=u256(pct),
            report_url_placeholder=str(report_url_placeholder).strip(),
            completion_criteria=str(completion_criteria).strip(),
            draft_report_url="",
            final_report_url="",
            draft_paid_wei=u256(0),
            status="FUNDED",
            created_at=u256(now),
            submitted_at=u256(0),
            dispute_reason="",
        )
        self.escrow_order.append(escrow_id)
        print(
            f"[EscrowCreated] id={escrow_id} client={client} "
            f"auditor={auditor} payment_wei={payment_wei} draft_pct={pct}"
        )
        return escrow_id

    @gl.public.write
    def submit_draft_report(self, escrow_id: str, draft_report_url: str) -> str:
        """
        Auditor submits a draft report URL. AI checks for the core structure
        (methodology, findings, recommendations, contract reference, severity labels).
        If acceptable, releases draft_pct% of the payment immediately.
        Emits: [ReportSubmitted], [PaymentReleased] (if draft accepted)
        Returns JSON verification summary.
        """
        eid = str(escrow_id).strip()
        if eid not in self.escrows:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Escrow {eid} not found")
        escrow = self.escrows[eid]

        caller = str(gl.message.sender_account).lower()
        if caller != escrow.auditor:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Only the assigned auditor can submit reports"
            )
        if escrow.status != "FUNDED":
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Draft can only be submitted for FUNDED escrows "
                f"(status: {escrow.status})"
            )
        url = str(draft_report_url).strip()
        if not url:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} draft_report_url cannot be empty")

        target_addr = escrow.contract_to_audit
        criteria    = escrow.completion_criteria

        def verify() -> dict:
            text   = self._fetch_report(url)
            prompt = f"""You are a smart contract security audit verifier.

Draft report text (first 6000 chars):
{text[:6000]}

Contract being audited: {target_addr}
Client completion criteria: {criteria}

This is a DRAFT check — relaxed standards (work-in-progress is acceptable).
Verify:
1. Does the report include a methodology section?
2. Does the report include a findings section?
3. Does the report include a recommendations section?
4. Is the contract address "{target_addr}" referenced anywhere?
5. Are severity levels (critical/high/medium/low/informational) mentioned?

Respond ONLY with valid JSON (no markdown):
{{
  "has_methodology": true/false,
  "has_findings": true/false,
  "has_recommendations": true/false,
  "contract_referenced": true/false,
  "has_severity_levels": true/false,
  "draft_acceptable": true/false,
  "rejection_reason": "brief reason if not acceptable, else empty string"
}}"""
            raw = gl.nondet.exec_prompt(prompt, response_format="json")
            if not isinstance(raw, dict):
                raise gl.vm.UserError(f"{ERROR_LLM} Expected dict from LLM")

            return {
                "acceptable":        bool(raw.get("draft_acceptable", False)),
                "has_methodology":   bool(raw.get("has_methodology", False)),
                "has_findings":      bool(raw.get("has_findings", False)),
                "has_recommendations": bool(raw.get("has_recommendations", False)),
                "contract_referenced": bool(raw.get("contract_referenced", False)),
                "has_severity":      bool(raw.get("has_severity_levels", False)),
                "rejection_reason":  str(raw.get("rejection_reason", "")),
            }

        def validator(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                leader_msg = getattr(leaders_res, "message", "")
                try:
                    verify()
                    return False
                except gl.vm.UserError as e:
                    vmsg = getattr(e, "message", str(e))
                    if vmsg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
                        return True
                    return vmsg == leader_msg
                except Exception:
                    return False
            try:
                val = verify()
            except Exception:
                return False
            ld = leaders_res.calldata
            return ld.get("acceptable") == val.get("acceptable")

        result = gl.vm.run_nondet_unsafe(verify, validator)

        escrow = self.escrows[eid]
        escrow.draft_report_url = url
        print(f"[ReportSubmitted] escrow={eid} type=draft url={url[:80]}")

        if result["acceptable"]:
            draft_pct     = int(escrow.draft_pct)
            total         = int(escrow.payment_wei)
            draft_release = (total * draft_pct) // 100
            if draft_release > 0:
                self._credit(escrow.auditor, draft_release)
                escrow.draft_paid_wei = u256(draft_release)
                print(
                    f"[PaymentReleased] escrow={eid} type=draft "
                    f"amount_wei={draft_release} to={escrow.auditor}"
                )
            escrow.status = "DRAFT_ACCEPTED"

        self.escrows[eid] = escrow
        return json.dumps({
            "escrow_id":        eid,
            "draft_acceptable": result["acceptable"],
            "rejection_reason": result["rejection_reason"],
            "draft_paid_wei":   int(escrow.draft_paid_wei),
        })

    @gl.public.write
    def submit_report(self, escrow_id: str, report_url: str) -> str:
        """
        Auditor submits the final audit report URL. AI fetches the report and
        verifies: methodology present, findings listed, recommendations present,
        contract address explicitly referenced, severity findings quantified,
        and client completion criteria addressed.
        If all checks pass, remaining payment is released automatically.
        Emits: [ReportSubmitted], [ReportVerified], [PaymentReleased] (if verified)
        Returns JSON verification summary.
        """
        eid = str(escrow_id).strip()
        if eid not in self.escrows:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Escrow {eid} not found")
        escrow = self.escrows[eid]

        caller = str(gl.message.sender_account).lower()
        if caller != escrow.auditor:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Only the assigned auditor can submit reports"
            )
        if escrow.status not in ("FUNDED", "DRAFT_ACCEPTED"):
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Final report requires FUNDED or DRAFT_ACCEPTED status "
                f"(status: {escrow.status})"
            )
        url = str(report_url).strip()
        if not url:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} report_url cannot be empty")

        target_addr = escrow.contract_to_audit
        criteria    = escrow.completion_criteria

        def verify() -> dict:
            text   = self._fetch_report(url)
            prompt = f"""You are a smart contract security audit verifier.

Final report text (first 6000 chars):
{text[:6000]}

Contract being audited: {target_addr}
Client completion criteria: {criteria}

Verify ALL of the following for a FINAL security audit report:
1. Does the report contain a methodology section describing how the audit was conducted?
2. Does the report contain a findings section listing vulnerabilities?
3. Does the report contain a recommendations section?
4. Is the contract address "{target_addr}" explicitly referenced in the report?
5. Are severity findings quantified (e.g. "2 Critical, 3 High, 5 Medium, 2 Low")?
6. Are the client's completion criteria addressed: "{criteria}"?

Respond ONLY with valid JSON (no markdown):
{{
  "has_methodology": true/false,
  "has_findings": true/false,
  "has_recommendations": true/false,
  "contract_referenced": true/false,
  "severity_quantified": true/false,
  "criteria_met": true/false,
  "all_criteria_met": true/false,
  "rejection_reason": "brief reason if not all criteria met, else empty string"
}}"""
            raw = gl.nondet.exec_prompt(prompt, response_format="json")
            if not isinstance(raw, dict):
                raise gl.vm.UserError(f"{ERROR_LLM} Expected dict from LLM")

            return {
                "all_criteria_met":    bool(raw.get("all_criteria_met", False)),
                "has_methodology":     bool(raw.get("has_methodology", False)),
                "has_findings":        bool(raw.get("has_findings", False)),
                "has_recommendations": bool(raw.get("has_recommendations", False)),
                "contract_referenced": bool(raw.get("contract_referenced", False)),
                "severity_quantified": bool(raw.get("severity_quantified", False)),
                "criteria_met":        bool(raw.get("criteria_met", False)),
                "rejection_reason":    str(raw.get("rejection_reason", "")),
            }

        def validator(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                leader_msg = getattr(leaders_res, "message", "")
                try:
                    verify()
                    return False
                except gl.vm.UserError as e:
                    vmsg = getattr(e, "message", str(e))
                    if vmsg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
                        return True
                    return vmsg == leader_msg
                except Exception:
                    return False
            try:
                val = verify()
            except Exception:
                return False
            ld = leaders_res.calldata
            return ld.get("all_criteria_met") == val.get("all_criteria_met")

        result = gl.vm.run_nondet_unsafe(verify, validator)
        now    = int(gl.block.timestamp)

        escrow = self.escrows[eid]
        escrow.final_report_url = url
        escrow.submitted_at     = u256(now)
        print(f"[ReportSubmitted] escrow={eid} type=final url={url[:80]}")

        if result["all_criteria_met"]:
            already_paid = int(escrow.draft_paid_wei)
            total        = int(escrow.payment_wei)
            remaining    = total - already_paid
            if remaining > 0:
                self._credit(escrow.auditor, remaining)
            escrow.status = "RELEASED"
            self.escrows[eid] = escrow
            print(f"[ReportVerified] escrow={eid} url={url[:80]}")
            print(
                f"[PaymentReleased] escrow={eid} type=final "
                f"amount_wei={remaining} to={escrow.auditor}"
            )
        else:
            escrow.status = "REPORT_SUBMITTED"
            self.escrows[eid] = escrow

        return json.dumps({
            "escrow_id":           eid,
            "verified":            result["all_criteria_met"],
            "rejection_reason":    result["rejection_reason"],
            "has_methodology":     result["has_methodology"],
            "has_findings":        result["has_findings"],
            "has_recommendations": result["has_recommendations"],
            "severity_quantified": result["severity_quantified"],
            "contract_referenced": result["contract_referenced"],
        })

    @gl.public.write
    def dispute_report(self, escrow_id: str, reason: str) -> None:
        """
        Client disputes a submitted report within 72 hours of final submission.
        Also allowed on DRAFT_ACCEPTED status (client can push back on draft quality).
        Admin resolves via resolve_dispute().
        Emits: [ReportDisputed]
        """
        eid = str(escrow_id).strip()
        if eid not in self.escrows:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Escrow {eid} not found")
        escrow = self.escrows[eid]

        caller = str(gl.message.sender_account).lower()
        if caller != escrow.client:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Only the client can raise a dispute")
        if escrow.status not in ("DRAFT_ACCEPTED", "REPORT_SUBMITTED"):
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Can only dispute in DRAFT_ACCEPTED or REPORT_SUBMITTED state "
                f"(status: {escrow.status})"
            )
        if not str(reason).strip():
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Dispute reason cannot be empty")

        now = int(gl.block.timestamp)
        if int(escrow.submitted_at) > 0 and now > int(escrow.submitted_at) + DISPUTE_WINDOW:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Dispute window has closed")

        escrow.status         = "DISPUTED"
        escrow.dispute_reason = str(reason).strip()[:500]
        self.escrows[eid]     = escrow
        print(f"[ReportDisputed] escrow={eid} by={caller}")

    @gl.public.write
    def resolve_dispute(self, escrow_id: str, release_to_auditor: bool) -> None:
        """
        Admin resolves a dispute.
        release_to_auditor=True → remaining payment goes to auditor.
        release_to_auditor=False → remaining payment refunded to client.
        Emits: [PaymentReleased], [DisputeResolved]
        """
        self._require_admin()
        eid = str(escrow_id).strip()
        if eid not in self.escrows:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Escrow {eid} not found")
        escrow = self.escrows[eid]
        if escrow.status != "DISPUTED":
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Escrow is not DISPUTED (status: {escrow.status})"
            )

        already_paid = int(escrow.draft_paid_wei)
        total        = int(escrow.payment_wei)
        remaining    = total - already_paid

        if release_to_auditor:
            if remaining > 0:
                self._credit(escrow.auditor, remaining)
            escrow.status = "RELEASED"
            print(
                f"[PaymentReleased] escrow={eid} type=dispute_resolved "
                f"amount_wei={remaining} to={escrow.auditor}"
            )
        else:
            if remaining > 0:
                self._credit(escrow.client, remaining)
            escrow.status = "CANCELLED"
            print(
                f"[PaymentReleased] escrow={eid} type=dispute_refund "
                f"amount_wei={remaining} to={escrow.client}"
            )

        self.escrows[eid] = escrow
        print(
            f"[DisputeResolved] escrow={eid} "
            f"outcome={'released' if release_to_auditor else 'refunded'}"
        )

    @gl.public.write
    def withdraw(self, wallet: str) -> None:
        """Claim and zero out an address's credited balance. Emits: [Withdrawn]"""
        w = str(wallet).strip().lower()
        if w not in self.balances or int(self.balances[w]) == 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} No claimable balance for {w}")
        amount = int(self.balances[w])
        self.balances[w] = u256(0)
        print(f"[Withdrawn] wallet={w} amount_wei={amount}")

    # ── View methods ──────────────────────────────────────────────────────────

    @gl.public.view
    def get_escrow(self, escrow_id: str) -> str:
        eid = str(escrow_id).strip()
        if eid not in self.escrows:
            return json.dumps({"error": "not found"})
        e = self.escrows[eid]
        return json.dumps({
            "escrow_id":              e.escrow_id,
            "client":                 e.client,
            "auditor":                e.auditor,
            "contract_to_audit":      e.contract_to_audit,
            "payment_wei":            int(e.payment_wei),
            "draft_pct":              int(e.draft_pct),
            "draft_paid_wei":         int(e.draft_paid_wei),
            "report_url_placeholder": e.report_url_placeholder,
            "completion_criteria":    e.completion_criteria,
            "draft_report_url":       e.draft_report_url,
            "final_report_url":       e.final_report_url,
            "status":                 e.status,
            "created_at":             int(e.created_at),
            "submitted_at":           int(e.submitted_at),
            "dispute_reason":         e.dispute_reason,
        })

    @gl.public.view
    def get_balance(self, wallet: str) -> int:
        w = str(wallet).strip().lower()
        return int(self.balances[w]) if w in self.balances else 0

    @gl.public.view
    def list_escrows(self) -> str:
        result = []
        for i in range(len(self.escrow_order)):
            eid = self.escrow_order[i]
            if eid in self.escrows:
                e = self.escrows[eid]
                result.append({
                    "escrow_id":   e.escrow_id,
                    "client":      e.client,
                    "auditor":     e.auditor,
                    "payment_wei": int(e.payment_wei),
                    "status":      e.status,
                    "created_at":  int(e.created_at),
                })
        return json.dumps(result)
