# v0.1.0
# { "Depends": "py-genlayer:test" }

from genlayer import *
import json

ERROR_EXPECTED  = "[EXPECTED]"
ERROR_EXTERNAL  = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM       = "[LLM_ERROR]"

COOLDOWN_DEFAULT   = u256(3600)    # 1 hour between assessments
DISPUTE_WINDOW     = 259200        # 72 hours in seconds
HACK_THRESHOLD     = 80            # minimum AI confidence % to flag a hack
TVL_DROP_THRESHOLD = 50            # % TVL drop considered suspicious
MIN_SOURCES        = 3             # independent confirmations required

HACKLAB_URL = (
    "https://raw.githubusercontent.com/SunWeb3Sec/DeFiHackLabs/main/README.md"
)


@allow_storage
@dataclass
class Protocol:
    name: str
    tvl_api_endpoint: str   # e.g. https://api.llama.fi/tvl/{slug}
    audit_status_url: str
    last_assessed_at: u256
    status: str             # ACTIVE | HACKED | PAUSED
    last_tvl_usd: u256      # last known TVL in whole USD


@allow_storage
@dataclass
class Policy:
    policy_id: str
    holder: str             # lowercase address
    protocol_name: str
    premium_wei: u256
    coverage_wei: u256
    created_at: u256
    status: str             # ACTIVE | PAID_OUT | CANCELLED


@allow_storage
@dataclass
class HackAlert:
    alert_id: str
    protocol_name: str
    confidence: u256        # 0-100 from AI
    sources: u256           # number of independent sources that confirmed
    description: str
    detected_at: u256
    dispute_deadline: u256  # detected_at + DISPUTE_WINDOW
    status: str             # PENDING | CONFIRMED | DISPUTED | REJECTED
    resolved_by: str        # address that resolved dispute (or "")


class DeFiInsurance(gl.Contract):
    admin: Address
    cooldown: u256
    reserve_wei: u256
    counter: u256
    protocols: TreeMap[str, Protocol]
    protocol_list: DynArray[str]
    policies: TreeMap[str, Policy]
    policy_order: DynArray[str]
    proto_policy_ids: TreeMap[str, str]   # protocol_name -> JSON list of policy_ids
    alerts: TreeMap[str, HackAlert]
    alert_order: DynArray[str]
    balances: TreeMap[str, u256]          # holder_address -> claimable wei

    def __init__(self, cooldown_seconds: int = 3600):
        self.admin    = gl.message.sender_account
        self.cooldown = u256(max(0, int(cooldown_seconds)))
        self.reserve_wei = u256(0)
        self.counter  = u256(0)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _next_id(self, prefix: str) -> str:
        n = int(self.counter) + 1
        self.counter = u256(n)
        return f"{prefix}-{n}"

    def _require_admin(self) -> None:
        if gl.message.sender_account != self.admin:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Only admin can call this")

    def _fetch_tvl(self, endpoint: str) -> int:
        try:
            res = gl.nondet.web.get(endpoint)
            if res.status == 404:
                raise gl.vm.UserError(f"{ERROR_EXTERNAL} TVL endpoint returned 404")
            if res.status >= 500:
                raise gl.vm.UserError(f"{ERROR_TRANSIENT} TVL API unavailable ({res.status})")
            if res.status >= 400:
                raise gl.vm.UserError(f"{ERROR_EXTERNAL} TVL API error ({res.status})")
            body = res.body.decode("utf-8").strip()
            # DefiLlama returns bare float or JSON object with tvl key
            try:
                val = json.loads(body)
                if isinstance(val, (int, float)):
                    return max(0, int(val))
                if isinstance(val, dict):
                    return max(0, int(val.get("tvl", val.get("total", 0))))
            except Exception:
                return max(0, int(float(body)))
            return 0
        except gl.vm.UserError:
            raise
        except Exception as e:
            raise gl.vm.UserError(f"{ERROR_TRANSIENT} Failed to fetch TVL: {e}")

    def _fetch_hack_alerts(self, protocol_name: str) -> bool:
        """Return True if protocol appears in the DeFiHackLabs incident list."""
        try:
            res = gl.nondet.web.get(HACKLAB_URL)
            if res.status >= 400:
                return False
            text = res.body.decode("utf-8", errors="replace").lower()
            return protocol_name.lower() in text
        except Exception:
            return False

    def _active_policies(self, protocol_name: str) -> list:
        ids  = json.loads(self.proto_policy_ids.get(protocol_name, "[]"))
        return [
            self.policies[pid]
            for pid in ids
            if pid in self.policies and self.policies[pid].status == "ACTIVE"
        ]

    def _do_payout(self, protocol_name: str, alert_id: str) -> int:
        active = self._active_policies(protocol_name)
        if not active:
            return 0

        total_coverage = sum(int(p.coverage_wei) for p in active)
        reserve        = int(self.reserve_wei)
        total_paid     = 0

        for p in active:
            if total_coverage == 0:
                break
            share  = (int(p.coverage_wei) * reserve) // total_coverage
            payout = min(int(p.coverage_wei), share)
            if payout <= 0:
                continue

            holder = p.holder
            prev   = int(self.balances[holder]) if holder in self.balances else 0
            self.balances[holder] = u256(prev + payout)
            total_paid += payout

            p.status = "PAID_OUT"
            self.policies[p.policy_id] = p
            print(
                f"[PayoutTriggered] alert={alert_id} holder={holder} "
                f"amount_wei={payout}"
            )

        self.reserve_wei = u256(max(0, reserve - total_paid))
        return total_paid

    # ── Admin methods ─────────────────────────────────────────────────────────

    @gl.public.write
    def register_protocol(
        self,
        protocol_name: str,
        tvl_api_endpoint: str,
        audit_status_url: str,
    ) -> None:
        """
        Admin registers a DeFi protocol for coverage eligibility.
        Emits: [ProtocolRegistered]
        """
        self._require_admin()
        name = str(protocol_name).strip()
        if not name:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} protocol_name cannot be empty")
        if name in self.protocols:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Protocol {name} already registered")

        self.protocols[name] = Protocol(
            name=name,
            tvl_api_endpoint=str(tvl_api_endpoint).strip(),
            audit_status_url=str(audit_status_url).strip(),
            last_assessed_at=u256(0),
            status="ACTIVE",
            last_tvl_usd=u256(0),
        )
        self.protocol_list.append(name)
        self.proto_policy_ids[name] = "[]"
        print(f"[ProtocolRegistered] name={name}")

    @gl.public.write
    def resolve_dispute(self, alert_id: str, confirmed: bool) -> None:
        """
        Admin resolves a disputed alert.
        confirmed=True → proceed to payout; confirmed=False → reject.
        Emits: [DisputeResolved]
        """
        self._require_admin()
        aid = str(alert_id).strip()
        if aid not in self.alerts:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Alert {aid} not found")
        alert = self.alerts[aid]
        if alert.status != "DISPUTED":
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Alert is not DISPUTED (status: {alert.status})"
            )

        caller = str(gl.message.sender_account).lower()
        alert.status      = "CONFIRMED" if confirmed else "REJECTED"
        alert.resolved_by = caller
        self.alerts[aid]  = alert
        print(
            f"[DisputeResolved] alert={aid} outcome={alert.status} "
            f"resolver={caller}"
        )

    # ── Public write methods ──────────────────────────────────────────────────

    @gl.public.write
    def deposit_policy(
        self,
        protocol_name: str,
        coverage_wei: int,
        premium_wei: int,
    ) -> str:
        """
        User pays a premium to get coverage on a registered protocol.
        Returns policy_id.
        Emits: [PolicyCreated]
        """
        name = str(protocol_name).strip()
        if name not in self.protocols:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Protocol {name} not registered")
        if self.protocols[name].status != "ACTIVE":
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Protocol {name} is not accepting new policies"
            )
        if int(premium_wei) <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} premium_wei must be positive")
        if int(coverage_wei) <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} coverage_wei must be positive")

        policy_id = self._next_id("policy")
        holder    = str(gl.message.sender_account).lower()
        now       = int(gl.block.timestamp)

        self.policies[policy_id] = Policy(
            policy_id=policy_id,
            holder=holder,
            protocol_name=name,
            premium_wei=u256(int(premium_wei)),
            coverage_wei=u256(int(coverage_wei)),
            created_at=u256(now),
            status="ACTIVE",
        )
        self.policy_order.append(policy_id)

        existing = json.loads(self.proto_policy_ids.get(name, "[]"))
        existing.append(policy_id)
        self.proto_policy_ids[name] = json.dumps(existing)

        self.reserve_wei = u256(int(self.reserve_wei) + int(premium_wei))
        print(
            f"[PolicyCreated] id={policy_id} holder={holder} "
            f"protocol={name} coverage_wei={coverage_wei} premium_wei={premium_wei}"
        )
        return policy_id

    @gl.public.write
    def assess_risk(self, protocol_name: str) -> str:
        """
        Publicly callable risk check. Fetches live TVL, queries the DeFiHackLabs
        incident database, and uses AI to evaluate the combined signal.
        Requires 3 independent source confirmations before creating a HackAlert.
        Subject to cooldown between calls.
        Emits: [RiskAssessed], [HackDetected]
        Returns JSON risk summary.
        """
        name = str(protocol_name).strip()
        if name not in self.protocols:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Protocol {name} not registered")

        proto = self.protocols[name]
        now   = int(gl.block.timestamp)
        if int(proto.last_assessed_at) + int(self.cooldown) > now:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Cooldown active — wait "
                f"{int(proto.last_assessed_at) + int(self.cooldown) - now}s"
            )

        tvl_endpoint = proto.tvl_api_endpoint
        prev_tvl     = int(proto.last_tvl_usd)

        def run() -> dict:
            # Source 1: TVL from DefiLlama (or configured endpoint)
            current_tvl = 0
            tvl_error   = ""
            try:
                current_tvl = self._fetch_tvl(tvl_endpoint)
            except gl.vm.UserError as e:
                tvl_error = getattr(e, "message", str(e))
            except Exception as e:
                tvl_error = str(e)

            tvl_drop_pct = 0.0
            if prev_tvl > 0 and current_tvl >= 0:
                tvl_drop_pct = max(0.0, (prev_tvl - current_tvl) / prev_tvl * 100)
            source1_alert = prev_tvl > 0 and tvl_drop_pct >= TVL_DROP_THRESHOLD

            # Source 2: DeFiHackLabs incident list
            source2_alert = self._fetch_hack_alerts(name)

            # Source 3: AI synthesis
            prompt = f"""You are a DeFi security analyst monitoring {name}.

Current data:
  Previous TVL : ${prev_tvl:,} USD
  Current TVL  : ${current_tvl:,} USD
  TVL drop     : {tvl_drop_pct:.1f}%
  TVL alert    : {"YES — drop exceeds 50%" if source1_alert else "no"}
  Hack database: {"YES — {name} found in recent incidents" if source2_alert else "no match"}
  TVL fetch error: {tvl_error or "none"}

Evaluate whether this protocol is currently being exploited, rug-pulled,
or is insolvent. Consider both the TVL data and the hack database result.

Respond ONLY with valid JSON (no markdown):
{{
  "hack_detected": true/false,
  "confidence": 0-100,
  "event_type": "exploit|rug_pull|insolvency|normal",
  "description": "one-sentence assessment"
}}"""

            raw = gl.nondet.exec_prompt(prompt, response_format="json")
            if not isinstance(raw, dict):
                raise gl.vm.UserError(f"{ERROR_LLM} Expected dict from LLM")

            hack_detected = bool(raw.get("hack_detected", False))
            confidence    = max(0, min(100, int(raw.get("confidence", 0))))
            event_type    = str(raw.get("event_type", "normal"))
            description   = str(raw.get("description", ""))
            source3_alert = hack_detected and confidence >= HACK_THRESHOLD

            sources_confirmed = sum([source1_alert, source2_alert, source3_alert])

            return {
                "current_tvl":        current_tvl,
                "tvl_drop_pct":       round(tvl_drop_pct, 2),
                "source1_tvl_alert":  source1_alert,
                "source2_hack_alert": source2_alert,
                "source3_ai_alert":   source3_alert,
                "sources_confirmed":  sources_confirmed,
                "confidence":         confidence,
                "event_type":         event_type,
                "description":        description,
                "hack_triggered":     sources_confirmed >= MIN_SOURCES,
            }

        def validator(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                leader_msg = getattr(leaders_res, "message", "")
                try:
                    run()
                    return False
                except gl.vm.UserError as e:
                    vmsg = getattr(e, "message", str(e))
                    if vmsg.startswith(ERROR_EXPECTED) or vmsg.startswith(ERROR_EXTERNAL):
                        return vmsg == leader_msg
                    if vmsg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
                        return True
                    return False
                except Exception:
                    return False
            try:
                val = run()
            except Exception:
                return False
            ld = leaders_res.calldata
            # Agree on the binary outcome and source count — AI confidence may vary
            return (
                ld.get("hack_triggered") == val.get("hack_triggered")
                and ld.get("sources_confirmed") == val.get("sources_confirmed")
            )

        result = gl.vm.run_nondet_unsafe(run, validator)
        now    = int(gl.block.timestamp)

        # Update stored TVL
        proto = self.protocols[name]
        proto.last_assessed_at = u256(now)
        if result["current_tvl"] > 0:
            proto.last_tvl_usd = u256(result["current_tvl"])
        self.protocols[name] = proto

        print(
            f"[RiskAssessed] protocol={name} tvl=${result['current_tvl']:,} "
            f"drop={result['tvl_drop_pct']}% sources={result['sources_confirmed']}/3 "
            f"confidence={result['confidence']}"
        )

        # Create HackAlert if all 3 sources confirm
        alert_id = ""
        if result["hack_triggered"]:
            alert_id = self._next_id("alert")
            self.alerts[alert_id] = HackAlert(
                alert_id=alert_id,
                protocol_name=name,
                confidence=u256(result["confidence"]),
                sources=u256(result["sources_confirmed"]),
                description=result["description"][:300],
                detected_at=u256(now),
                dispute_deadline=u256(now + DISPUTE_WINDOW),
                status="PENDING",
                resolved_by="",
            )
            self.alert_order.append(alert_id)
            proto = self.protocols[name]
            proto.status = "HACKED"
            self.protocols[name] = proto
            print(
                f"[HackDetected] alert={alert_id} protocol={name} "
                f"confidence={result['confidence']} type={result['event_type']}"
            )

        return json.dumps({
            "protocol":         name,
            "tvl_usd":          result["current_tvl"],
            "tvl_drop_pct":     result["tvl_drop_pct"],
            "sources_confirmed": result["sources_confirmed"],
            "confidence":       result["confidence"],
            "hack_triggered":   result["hack_triggered"],
            "alert_id":         alert_id,
        })

    @gl.public.write
    def dispute_alert(self, alert_id: str) -> None:
        """
        Any address can dispute a PENDING alert within the 72-hour window.
        Admin then resolves via resolve_dispute().
        Emits: [AlertDisputed]
        """
        aid = str(alert_id).strip()
        if aid not in self.alerts:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Alert {aid} not found")
        alert = self.alerts[aid]
        if alert.status != "PENDING":
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Alert is not PENDING (status: {alert.status})"
            )
        if int(gl.block.timestamp) > int(alert.dispute_deadline):
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Dispute window has closed"
            )

        alert.status     = "DISPUTED"
        alert.resolved_by = str(gl.message.sender_account).lower()
        self.alerts[aid] = alert
        print(
            f"[AlertDisputed] alert={aid} by={alert.resolved_by}"
        )

    @gl.public.write
    def trigger_payout(self, alert_id: str) -> str:
        """
        Finalises a hack alert and pays out all active policy holders proportionally.
        Can be called after:
          (a) alert is PENDING and the 72-hour dispute window has passed, or
          (b) admin has set the alert to CONFIRMED via resolve_dispute().
        Emits: [PayoutTriggered] per policy, [MigrationComplete]
        Returns JSON payout summary.
        """
        aid = str(alert_id).strip()
        if aid not in self.alerts:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Alert {aid} not found")
        alert = self.alerts[aid]

        now = int(gl.block.timestamp)
        if alert.status == "PENDING":
            if now <= int(alert.dispute_deadline):
                raise gl.vm.UserError(
                    f"{ERROR_EXPECTED} Dispute window still open — "
                    f"{int(alert.dispute_deadline) - now}s remaining"
                )
        elif alert.status != "CONFIRMED":
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Alert cannot be paid out (status: {alert.status})"
            )

        proto_name    = alert.protocol_name
        total_paid    = self._do_payout(proto_name, aid)

        alert.status     = "CONFIRMED"
        self.alerts[aid] = alert

        print(
            f"[MigrationComplete] alert={aid} protocol={proto_name} "
            f"total_paid_wei={total_paid}"
        )
        return json.dumps({
            "alert_id":       aid,
            "protocol":       proto_name,
            "total_paid_wei": total_paid,
        })

    @gl.public.write
    def withdraw(self, wallet: str) -> None:
        """Claim and zero out an address's payout balance. Emits: [Withdrawn]"""
        w = str(wallet).strip().lower()
        if w not in self.balances or int(self.balances[w]) == 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} No claimable balance for {w}")
        amount = int(self.balances[w])
        self.balances[w] = u256(0)
        print(f"[Withdrawn] wallet={w} amount_wei={amount}")

    # ── View methods ──────────────────────────────────────────────────────────

    @gl.public.view
    def get_protocol(self, protocol_name: str) -> str:
        name = str(protocol_name).strip()
        if name not in self.protocols:
            return json.dumps({"error": "not found"})
        p = self.protocols[name]
        return json.dumps({
            "name":             p.name,
            "tvl_api_endpoint": p.tvl_api_endpoint,
            "audit_status_url": p.audit_status_url,
            "last_assessed_at": int(p.last_assessed_at),
            "status":           p.status,
            "last_tvl_usd":     int(p.last_tvl_usd),
        })

    @gl.public.view
    def get_policy(self, policy_id: str) -> str:
        pid = str(policy_id).strip()
        if pid not in self.policies:
            return json.dumps({"error": "not found"})
        p = self.policies[pid]
        return json.dumps({
            "policy_id":    p.policy_id,
            "holder":       p.holder,
            "protocol":     p.protocol_name,
            "premium_wei":  int(p.premium_wei),
            "coverage_wei": int(p.coverage_wei),
            "created_at":   int(p.created_at),
            "status":       p.status,
        })

    @gl.public.view
    def get_alert(self, alert_id: str) -> str:
        aid = str(alert_id).strip()
        if aid not in self.alerts:
            return json.dumps({"error": "not found"})
        a = self.alerts[aid]
        return json.dumps({
            "alert_id":        a.alert_id,
            "protocol":        a.protocol_name,
            "confidence":      int(a.confidence),
            "sources":         int(a.sources),
            "description":     a.description,
            "detected_at":     int(a.detected_at),
            "dispute_deadline": int(a.dispute_deadline),
            "status":          a.status,
            "resolved_by":     a.resolved_by,
        })

    @gl.public.view
    def get_balance(self, wallet: str) -> int:
        w = str(wallet).strip().lower()
        return int(self.balances[w]) if w in self.balances else 0

    @gl.public.view
    def list_protocols(self) -> str:
        result = []
        for i in range(len(self.protocol_list)):
            name = self.protocol_list[i]
            if name in self.protocols:
                p = self.protocols[name]
                result.append({
                    "name":         p.name,
                    "status":       p.status,
                    "last_tvl_usd": int(p.last_tvl_usd),
                })
        return json.dumps(result)

    @gl.public.view
    def list_alerts(self) -> str:
        result = []
        for i in range(len(self.alert_order)):
            aid = self.alert_order[i]
            if aid in self.alerts:
                a = self.alerts[aid]
                result.append({
                    "alert_id":   a.alert_id,
                    "protocol":   a.protocol_name,
                    "confidence": int(a.confidence),
                    "status":     a.status,
                    "detected_at": int(a.detected_at),
                })
        return json.dumps(result)
