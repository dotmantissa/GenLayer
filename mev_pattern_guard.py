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


class MevPatternGuard(gl.Contract):
    """Detects harmful MEV patterns in transaction batches using public data and LLM consensus."""

    cases: str
    next_case_id: u256
    latest_verdict_by_batch: str

    def __init__(self):
        """Initialize state.

        Parameters:
            None.

        Returns:
            None.
        """
        self.cases = "{}"
        self.next_case_id = 1
        self.latest_verdict_by_batch = "{}"

    @gl.public.write
    def create_case(self, batch_id: str, max_harmful_patterns: int, min_confidence: int) -> str:
        """Create a MEV classification case.

        Parameters:
            batch_id: Flashbots bundle or batch identifier.
            max_harmful_patterns: Max tolerated harmful patterns before harmful verdict.
            min_confidence: Minimum confidence threshold from model output.

        Returns:
            Case id string.
        """
        b = str(batch_id).strip().lower()
        if len(b) < 3:
            _raise_user_error(f"{ERROR_EXPECTED} invalid batch_id")
        if max_harmful_patterns < 0 or max_harmful_patterns > 100:
            _raise_user_error(f"{ERROR_EXPECTED} max_harmful_patterns out of range")
        if min_confidence < 0 or min_confidence > 100:
            _raise_user_error(f"{ERROR_EXPECTED} min_confidence out of range")

        cid = str(self.next_case_id)
        self.next_case_id += 1

        cases = json.loads(self.cases)
        cases[cid] = {
            "case_id": cid,
            "creator": str(gl.message.sender_account),
            "batch_id": b,
            "max_harmful_patterns": int(max_harmful_patterns),
            "min_confidence": int(min_confidence),
            "status": "PENDING",
            "verdict": "",
            "harmful_patterns": 0,
            "confidence": 0,
            "consensus_sources": 0,
            "reason": "",
            "resolved_at": "",
        }
        self.cases = json.dumps(cases)
        return cid

    @gl.public.write
    def resolve_case(self, case_id: str) -> str:
        """Resolve MEV risk verdict for one transaction batch.

        Parameters:
            case_id: Case id string.

        Returns:
            Verdict string SAFE or HARMFUL.
        """
        cases = json.loads(self.cases)
        key = str(case_id)
        if key not in cases:
            _raise_user_error(f"{ERROR_EXPECTED} case not found")

        c = cases[key]
        if c["status"] != "PENDING":
            _raise_user_error(f"{ERROR_EXPECTED} case already resolved")

        def fetch_and_classify() -> str:
            flashbots_url = f"https://blocks.flashbots.net/v1/bundles?bundle_hash={c['batch_id']}"
            backup_url = f"https://api.mevboost.org/v1/batch/{c['batch_id']}"

            r1 = gl.nondet.web.get(flashbots_url)
            r2 = gl.nondet.web.get(backup_url)

            for name, res in [("flashbots", r1), ("backup", r2)]:
                status = int(res.status)
                if status >= 400 and status < 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} client error: {status}")
                if status >= 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} server error: {status}")

            b1 = r1.body.decode("utf-8") if r1.body is not None else ""
            b2 = r2.body.decode("utf-8") if r2.body is not None else ""
            if len((b1 + b2).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty mev payload")

            prompt = f"""
You are a MEV abuse analyst.
Return JSON only.

Case:
- batch_id: {c['batch_id']}
- max_harmful_patterns: {c['max_harmful_patterns']}
- min_confidence: {c['min_confidence']}

Rules:
1) Inspect sequence patterns for sandwiching or front running indicators.
2) Count harmful_patterns as integer.
3) confidence is integer from 0 to 100.
4) consensus_sources is number of sources that support the same directional conclusion.
5) verdict is HARMFUL when harmful_patterns exceeds max_harmful_patterns and confidence is at least min_confidence.

Return exactly:
{{
  "verdict": "SAFE_or_HARMFUL",
  "harmful_patterns": int,
  "confidence": int,
  "consensus_sources": int,
  "reason": "string"
}}

Inputs:
{json.dumps({"flashbots": b1[:7000], "backup": b2[:7000]})}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            harmful = int(parsed.get("harmful_patterns", 0))
            confidence = int(parsed.get("confidence", 0))
            if harmful < 0 or confidence < 0:
                _raise_user_error(f"{ERROR_EXTERNAL} invalid parsed metrics")
            if confidence > 100:
                confidence = 100

            verdict = str(parsed.get("verdict", "SAFE")).strip().upper()
            if verdict not in ["SAFE", "HARMFUL"]:
                verdict = "SAFE"

            objective_harmful = harmful > int(c["max_harmful_patterns"]) and confidence >= int(c["min_confidence"])
            verdict = "HARMFUL" if objective_harmful else "SAFE"

            sources = int(parsed.get("consensus_sources", 0))
            sources = max(0, min(2, sources))
            if sources < 1:
                verdict = "SAFE"

            return json.dumps(
                {
                    "verdict": verdict,
                    "harmful_patterns": harmful,
                    "confidence": confidence,
                    "consensus_sources": sources,
                    "reason": str(parsed.get("reason", ""))[:500],
                }
            )

        principle = "Equivalent when verdict matches and harmful_patterns differs by no more than one."
        result_json = _run_prompt_consensus(fetch_and_classify, principle)
        result = json.loads(result_json)

        c["verdict"] = str(result.get("verdict", "SAFE"))
        c["harmful_patterns"] = int(result.get("harmful_patterns", 0))
        c["confidence"] = int(result.get("confidence", 0))
        c["consensus_sources"] = int(result.get("consensus_sources", 0))
        c["reason"] = str(result.get("reason", ""))
        c["status"] = "RESOLVED"
        c["resolved_at"] = str(gl.block.timestamp)

        cases[key] = c
        self.cases = json.dumps(cases)

        latest = json.loads(self.latest_verdict_by_batch)
        latest[str(c["batch_id"]).lower()] = c["verdict"]
        self.latest_verdict_by_batch = json.dumps(latest)

        return c["verdict"]

    @gl.public.view
    def get_latest_verdict(self, batch_id: str) -> str:
        """Read latest verdict for a batch id.

        Parameters:
            batch_id: Batch id string.

        Returns:
            SAFE HARMFUL or UNKNOWN.
        """
        latest = json.loads(self.latest_verdict_by_batch)
        return str(latest.get(str(batch_id).strip().lower(), "UNKNOWN"))

    @gl.public.view
    def get_case(self, case_id: str) -> str:
        """Read one case.

        Parameters:
            case_id: Case id string.

        Returns:
            Case JSON string.
        """
        cases = json.loads(self.cases)
        key = str(case_id)
        if key not in cases:
            _raise_user_error(f"{ERROR_EXPECTED} case not found")
        return json.dumps(cases[key])

    @gl.public.view
    def get_all_cases(self) -> str:
        """Read all cases.

        Parameters:
            None.

        Returns:
            Cases map JSON string.
        """
        return self.cases
