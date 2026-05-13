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


class VendorReviewQualityOracle(gl.Contract):
    """Aggregates cross platform reviews and outputs quality verdict for escrow conditions."""

    cases: str
    next_case_id: u256

    def __init__(self):
        """Initialize storage state.

        Parameters:
            None.

        Returns:
            None.
        """
        self.cases = "{}"
        self.next_case_id = 1

    @gl.public.write
    def create_case(self, vendor_slug: str, min_quality_score: int) -> str:
        """Create review quality evaluation case.

        Parameters:
            vendor_slug: Vendor identifier string.
            min_quality_score: Minimum score threshold for PASS verdict.

        Returns:
            Case id string.
        """
        slug = str(vendor_slug).strip().lower()
        if len(slug) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid vendor_slug")
        if min_quality_score < 1 or min_quality_score > 100:
            _raise_user_error(f"{ERROR_EXPECTED} min_quality_score out of range")

        case_id = str(self.next_case_id)
        self.next_case_id += 1

        cases = json.loads(self.cases)
        cases[case_id] = {
            "case_id": case_id,
            "requester": str(gl.message.sender_account),
            "vendor_slug": slug,
            "min_quality_score": int(min_quality_score),
            "status": "PENDING",
            "quality_score": 0,
            "fake_review_risk": "UNKNOWN",
            "verdict": "",
            "reason": "",
            "resolved_at": "",
        }
        self.cases = json.dumps(cases)
        return case_id

    @gl.public.write
    def resolve_case(self, case_id: str) -> str:
        """Resolve case from Trustpilot, G2, and Google review sources.

        Parameters:
            case_id: Case id string.

        Returns:
            PASS or FAIL verdict string.
        """
        cases = json.loads(self.cases)
        key = str(case_id)
        if key not in cases:
            _raise_user_error(f"{ERROR_EXPECTED} case not found")

        c = cases[key]
        if c["status"] != "PENDING":
            _raise_user_error(f"{ERROR_EXPECTED} case already resolved")

        def fetch_and_score() -> str:
            trustpilot_url = f"https://www.trustpilot.com/review/{c['vendor_slug']}"
            g2_url = f"https://www.g2.com/products/{c['vendor_slug']}/reviews"
            google_url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={c['vendor_slug']}+reviews"

            trustpilot = gl.nondet.web.get(trustpilot_url)
            g2 = gl.nondet.web.get(g2_url)
            google = gl.nondet.web.get(google_url)

            for name, res in [("trustpilot", trustpilot), ("g2", g2), ("google", google)]:
                status = int(res.status)
                if status >= 400 and status < 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} client error: {status}")
                if status >= 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} server error: {status}")

            tp_body = trustpilot.body.decode("utf-8") if trustpilot.body is not None else ""
            g2_body = g2.body.decode("utf-8") if g2.body is not None else ""
            gg_body = google.body.decode("utf-8") if google.body is not None else ""
            if len((tp_body + g2_body + gg_body).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty review payload")

            prompt = f"""
You are a review integrity analyst.
Aggregate product or service reviews across platforms and detect fake review patterns.
Return JSON only.

Vendor slug: {c['vendor_slug']}
Minimum quality score for pass: {c['min_quality_score']}

Rules:
1) Estimate normalized quality_score from 0 to 100 using all three sources.
2) Estimate fake_review_risk as LOW MEDIUM or HIGH from contextual patterns.
3) If fake_review_risk is HIGH, verdict must be FAIL.
4) Otherwise PASS only when quality_score >= minimum threshold.

Return exactly:
{{
  "quality_score": int,
  "fake_review_risk": "LOW_or_MEDIUM_or_HIGH",
  "verdict": "PASS_or_FAIL",
  "reason": "string"
}}

Inputs:
{json.dumps({"trustpilot": tp_body[:5000], "g2": g2_body[:5000], "google": gg_body[:5000]})}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            quality = int(parsed.get("quality_score", 0))
            quality = max(0, min(100, quality))

            risk = str(parsed.get("fake_review_risk", "MEDIUM")).strip().upper()
            if risk not in ["LOW", "MEDIUM", "HIGH"]:
                risk = "MEDIUM"

            verdict = str(parsed.get("verdict", "FAIL")).strip().upper()
            if verdict not in ["PASS", "FAIL"]:
                verdict = "FAIL"

            if risk == "HIGH":
                verdict = "FAIL"
            elif quality < int(c["min_quality_score"]):
                verdict = "FAIL"
            else:
                verdict = "PASS"

            reason = str(parsed.get("reason", ""))[:500]

            return json.dumps(
                {
                    "quality_score": quality,
                    "fake_review_risk": risk,
                    "verdict": verdict,
                    "reason": reason,
                }
            )

        principle = "Equivalent when verdict matches and quality_score differs by at most 12 points with same risk class."
        result_json = _run_prompt_consensus(fetch_and_score, principle)
        result = json.loads(result_json)

        c["quality_score"] = int(result.get("quality_score", 0))
        c["fake_review_risk"] = str(result.get("fake_review_risk", "MEDIUM"))
        c["verdict"] = str(result.get("verdict", "FAIL"))
        c["reason"] = str(result.get("reason", ""))
        c["status"] = "RESOLVED"
        c["resolved_at"] = str(gl.block.timestamp)

        cases[key] = c
        self.cases = json.dumps(cases)

        return c["verdict"]

    @gl.public.view
    def get_case(self, case_id: str) -> str:
        """Read one review quality case.

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
        """Read all review quality cases.

        Parameters:
            None.

        Returns:
            Cases map JSON string.
        """
        return self.cases
