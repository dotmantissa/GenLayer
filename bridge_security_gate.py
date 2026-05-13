# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"


def _raise_user_error(message: str) -> None:
    err_cls = getattr(gl, "UserError", Exception)
    raise err_cls(message)


def _run_prompt_consensus(fn, principle: str) -> str:
    eq = getattr(gl, "eq_principle", None)
    if eq is not None and hasattr(eq, "prompt_comparative"):
        return eq.prompt_comparative(fn, principle)
    return fn()


class BridgeSecurityGate(gl.Contract):
    """Assesses bridge security posture before allowing cross chain execution."""

    assessments: str
    next_assessment_id: u256
    gate_status_by_bridge: str

    def __init__(self):
        """Initialize storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.assessments = "{}"
        self.next_assessment_id = 1
        self.gate_status_by_bridge = "{}"

    @gl.public.write
    def create_assessment(
        self,
        bridge_key: str,
        min_tvl_usd: int,
        max_recent_incidents: int,
        lookback_days: int,
    ) -> str:
        """Create a bridge risk assessment request.

        Parameters:
            bridge_key: Bridge name key used for provider matching.
            min_tvl_usd: Minimum acceptable total value locked.
            max_recent_incidents: Maximum tolerated recent incidents.
            lookback_days: Incident lookback window in days.

        Returns:
            Assessment id string.
        """
        key = str(bridge_key).strip().lower()
        if len(key) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid bridge_key")
        if min_tvl_usd <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} min_tvl_usd must be positive")
        if max_recent_incidents < 0 or max_recent_incidents > 100:
            _raise_user_error(f"{ERROR_EXPECTED} max_recent_incidents out of range")
        if lookback_days < 1 or lookback_days > 3650:
            _raise_user_error(f"{ERROR_EXPECTED} lookback_days out of range")

        assessment_id = str(self.next_assessment_id)
        self.next_assessment_id += 1

        assessments = json.loads(self.assessments)
        assessments[assessment_id] = {
            "assessment_id": assessment_id,
            "creator": str(gl.message.sender_account),
            "bridge_key": key,
            "min_tvl_usd": int(min_tvl_usd),
            "max_recent_incidents": int(max_recent_incidents),
            "lookback_days": int(lookback_days),
            "status": "PENDING",
            "decision": "",
            "tvl_usd": 0,
            "recent_incidents": 0,
            "consensus_sources": 0,
            "reason": "",
            "resolved_at": "",
        }
        self.assessments = json.dumps(assessments)
        return assessment_id

    @gl.public.write
    def resolve_assessment(self, assessment_id: str) -> str:
        """Resolve bridge risk from L2Beat and DeFiLlama public data.

        Parameters:
            assessment_id: Assessment id string.

        Returns:
            Decision string ALLOW or BLOCK.
        """
        assessments = json.loads(self.assessments)
        key = str(assessment_id)
        if key not in assessments:
            _raise_user_error(f"{ERROR_EXPECTED} assessment not found")

        a = assessments[key]
        if a["status"] != "PENDING":
            _raise_user_error(f"{ERROR_EXPECTED} assessment already resolved")

        def fetch_and_assess() -> str:
            l2beat_tvl_url = "https://l2beat.com/api/scaling/tvs"
            l2beat_risk_url = "https://l2beat.com/api/bridges"
            defillama_bridge_url = "https://api.llama.fi/bridges"
            defillama_hacks_url = "https://api.llama.fi/hacks"

            r1 = gl.nondet.web.get(l2beat_tvl_url)
            r2 = gl.nondet.web.get(l2beat_risk_url)
            r3 = gl.nondet.web.get(defillama_bridge_url)
            r4 = gl.nondet.web.get(defillama_hacks_url)

            for name, res in [
                ("l2beat_tvl", r1),
                ("l2beat_risk", r2),
                ("defillama_bridges", r3),
                ("defillama_hacks", r4),
            ]:
                status = int(res.status)
                if status >= 400 and status < 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} client error: {status}")
                if status >= 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} server error: {status}")

            b1 = r1.body.decode("utf-8") if r1.body is not None else ""
            b2 = r2.body.decode("utf-8") if r2.body is not None else ""
            b3 = r3.body.decode("utf-8") if r3.body is not None else ""
            b4 = r4.body.decode("utf-8") if r4.body is not None else ""
            if len((b1 + b2 + b3 + b4).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty provider payload")

            prompt = f"""
You are a bridge security analyst.
Return JSON only.

Task:
Evaluate whether this bridge has acceptable security posture for cross chain execution.

Inputs:
- bridge_key: {a['bridge_key']}
- min_tvl_usd: {a['min_tvl_usd']}
- max_recent_incidents: {a['max_recent_incidents']}
- lookback_days: {a['lookback_days']}

Rules:
1) Estimate current tvl_usd from bridge specific records.
2) Count recent_incidents inside lookback window.
3) consensus_sources counts how many independent sources support the same risk direction.
4) decision is ALLOW only when tvl_usd is at least min_tvl_usd and recent_incidents is not above max_recent_incidents.
5) Include architecture and incident context in reason.

Return exactly:
{{
  "decision": "ALLOW_or_BLOCK",
  "tvl_usd": int,
  "recent_incidents": int,
  "consensus_sources": int,
  "reason": "string"
}}

Payloads:
{json.dumps({"l2beat_tvl": b1[:5000], "l2beat_bridges": b2[:5000], "defillama_bridges": b3[:5000], "defillama_hacks": b4[:5000]})}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            tvl = int(parsed.get("tvl_usd", 0))
            incidents = int(parsed.get("recent_incidents", 0))
            if tvl < 0 or incidents < 0:
                _raise_user_error(f"{ERROR_EXTERNAL} invalid parsed metrics")

            decision = str(parsed.get("decision", "BLOCK")).strip().upper()
            if decision not in ["ALLOW", "BLOCK"]:
                decision = "BLOCK"

            objective_allow = tvl >= int(a["min_tvl_usd"]) and incidents <= int(a["max_recent_incidents"])
            if objective_allow:
                decision = "ALLOW"
            else:
                decision = "BLOCK"

            sources = int(parsed.get("consensus_sources", 0))
            sources = max(0, min(4, sources))
            if sources < 2:
                decision = "BLOCK"

            return json.dumps(
                {
                    "decision": decision,
                    "tvl_usd": tvl,
                    "recent_incidents": incidents,
                    "consensus_sources": sources,
                    "reason": str(parsed.get("reason", ""))[:500],
                }
            )

        principle = "Equivalent when decision matches and tvl_usd and recent_incidents are within acceptable variance."
        result_json = _run_prompt_consensus(fetch_and_assess, principle)
        result = json.loads(result_json)

        a["decision"] = str(result.get("decision", "BLOCK"))
        a["tvl_usd"] = int(result.get("tvl_usd", 0))
        a["recent_incidents"] = int(result.get("recent_incidents", 0))
        a["consensus_sources"] = int(result.get("consensus_sources", 0))
        a["reason"] = str(result.get("reason", ""))
        a["status"] = "RESOLVED"
        a["resolved_at"] = str(gl.block.timestamp)

        assessments[key] = a
        self.assessments = json.dumps(assessments)

        gates = json.loads(self.gate_status_by_bridge)
        gates[str(a["bridge_key"]).lower()] = a["decision"]
        self.gate_status_by_bridge = json.dumps(gates)
        return a["decision"]

    @gl.public.view
    def is_bridge_allowed(self, bridge_key: str) -> bool:
        """Read whether bridge is currently allowed.

        Parameters:
            bridge_key: Bridge key string.

        Returns:
            True when current gate status is ALLOW.
        """
        gates = json.loads(self.gate_status_by_bridge)
        return str(gates.get(str(bridge_key).strip().lower(), "BLOCK")) == "ALLOW"

    @gl.public.view
    def get_assessment(self, assessment_id: str) -> str:
        """Read one assessment.

        Parameters:
            assessment_id: Assessment id string.

        Returns:
            Assessment JSON string.
        """
        assessments = json.loads(self.assessments)
        key = str(assessment_id)
        if key not in assessments:
            _raise_user_error(f"{ERROR_EXPECTED} assessment not found")
        return json.dumps(assessments[key])

    @gl.public.view
    def get_all_assessments(self) -> str:
        """Read all assessments.

        Parameters:
            None.

        Returns:
            Assessments map JSON string.
        """
        return self.assessments
