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


class ClaimCorroborationOracle(gl.Contract):
    """Corroborates factual claims across independent news sources."""

    cases: str
    next_case_id: u256

    def __init__(self):
        """Initialize contract state.

        Parameters:
            None.

        Returns:
            None.
        """
        self.cases = "{}"
        self.next_case_id = 1

    @gl.public.write
    def create_case(self, claim_text: str, min_sources_required: int) -> str:
        """Create claim corroboration case.

        Parameters:
            claim_text: Factual claim to assess.
            min_sources_required: Minimum corroborating source count.

        Returns:
            Case id string.
        """
        claim = str(claim_text).strip()
        if len(claim) < 12:
            _raise_user_error(f"{ERROR_EXPECTED} claim_text too short")
        if min_sources_required < 2 or min_sources_required > 3:
            _raise_user_error(f"{ERROR_EXPECTED} min_sources_required out of range")

        case_id = str(self.next_case_id)
        self.next_case_id += 1

        cases = json.loads(self.cases)
        cases[case_id] = {
            "case_id": case_id,
            "requester": str(gl.message.sender_account),
            "claim_text": claim,
            "min_sources_required": int(min_sources_required),
            "status": "PENDING",
            "corroborating_sources": 0,
            "verdict": "",
            "reason": "",
            "resolved_at": "",
        }
        self.cases = json.dumps(cases)
        return case_id

    @gl.public.write
    def resolve_case(self, case_id: str) -> str:
        """Resolve corroboration case from three independent news sources.

        Parameters:
            case_id: Case id string.

        Returns:
            CORROBORATED or NOT_CORROBORATED.
        """
        cases = json.loads(self.cases)
        key = str(case_id)
        if key not in cases:
            _raise_user_error(f"{ERROR_EXPECTED} case not found")

        c = cases[key]
        if c["status"] != "PENDING":
            _raise_user_error(f"{ERROR_EXPECTED} case already resolved")

        def fetch_and_assess() -> str:
            src1 = gl.nondet.web.get("https://www.reuters.com/world/")
            src2 = gl.nondet.web.get("https://apnews.com/")
            src3 = gl.nondet.web.get("https://www.bbc.com/news")

            for name, res in [("reuters", src1), ("ap", src2), ("bbc", src3)]:
                status = int(res.status)
                if status >= 400 and status < 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} client error: {status}")
                if status >= 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} server error: {status}")

            b1 = src1.body.decode("utf-8") if src1.body is not None else ""
            b2 = src2.body.decode("utf-8") if src2.body is not None else ""
            b3 = src3.body.decode("utf-8") if src3.body is not None else ""
            if len((b1 + b2 + b3).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty news payload")

            prompt = f"""
You are a fact corroboration analyst.
Given a claim, determine if independent sources corroborate it.
Return JSON only.

Claim:
{c['claim_text']}

Minimum corroborating sources required: {c['min_sources_required']}

Rules:
1) Evaluate each source for direct support, contradiction, or no evidence.
2) corroborating_sources is count of sources that support the claim.
3) Verdict is CORROBORATED if corroborating_sources >= minimum and no strong contradiction dominates.
4) Otherwise verdict is NOT_CORROBORATED.

Return exactly:
{{
  "corroborating_sources": int,
  "verdict": "CORROBORATED_or_NOT_CORROBORATED",
  "reason": "string"
}}

Inputs:
{json.dumps({"reuters": b1[:5000], "ap": b2[:5000], "bbc": b3[:5000]})}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            count = int(parsed.get("corroborating_sources", 0))
            count = max(0, min(3, count))

            verdict = str(parsed.get("verdict", "NOT_CORROBORATED")).strip().upper()
            if verdict not in ["CORROBORATED", "NOT_CORROBORATED"]:
                verdict = "NOT_CORROBORATED"

            if count < int(c["min_sources_required"]):
                verdict = "NOT_CORROBORATED"
            else:
                verdict = "CORROBORATED"

            reason = str(parsed.get("reason", ""))[:500]

            return json.dumps(
                {
                    "corroborating_sources": count,
                    "verdict": verdict,
                    "reason": reason,
                }
            )

        principle = "Equivalent when verdict matches and corroborating_sources differ by at most one."
        out_json = _run_prompt_consensus(fetch_and_assess, principle)
        out = json.loads(out_json)

        c["corroborating_sources"] = int(out.get("corroborating_sources", 0))
        c["verdict"] = str(out.get("verdict", "NOT_CORROBORATED"))
        c["reason"] = str(out.get("reason", ""))
        c["status"] = "RESOLVED"
        c["resolved_at"] = str(gl.block.timestamp)

        cases[key] = c
        self.cases = json.dumps(cases)

        return c["verdict"]

    @gl.public.view
    def get_case(self, case_id: str) -> str:
        """Read one claim case.

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
        """Read all claim cases.

        Parameters:
            None.

        Returns:
            Cases map JSON string.
        """
        return self.cases
