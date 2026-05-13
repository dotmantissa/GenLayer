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


class PlagiarismDisputeResolver(gl.Contract):
    """Resolves plagiarism claims by semantic overlap analysis of fetched source text."""

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
    def create_case(self, document_hash: str, source_url: str, threshold_pct: int) -> str:
        """Create plagiarism dispute case.

        Parameters:
            document_hash: Hash or identifier of the claimant document.
            source_url: Suspected source URL to inspect.
            threshold_pct: Overlap percent threshold to confirm plagiarism.

        Returns:
            Case id string.
        """
        doc_hash = str(document_hash).strip().lower()
        url = str(source_url).strip()

        if len(doc_hash) < 16:
            _raise_user_error(f"{ERROR_EXPECTED} invalid document_hash")
        if not (url.startswith("http://") or url.startswith("https://")):
            _raise_user_error(f"{ERROR_EXPECTED} invalid source_url")
        if threshold_pct < 10 or threshold_pct > 100:
            _raise_user_error(f"{ERROR_EXPECTED} threshold_pct out of range")

        case_id = str(self.next_case_id)
        self.next_case_id += 1

        cases = json.loads(self.cases)
        cases[case_id] = {
            "case_id": case_id,
            "requester": str(gl.message.sender_account),
            "document_hash": doc_hash,
            "source_url": url,
            "threshold_pct": int(threshold_pct),
            "status": "PENDING",
            "overlap_pct": 0,
            "plagiarism_confirmed": False,
            "reason": "",
            "resolved_at": "",
        }
        self.cases = json.dumps(cases)
        return case_id

    @gl.public.write
    def resolve_case(self, case_id: str, document_excerpt: str) -> bool:
        """Resolve plagiarism case by fetching source and running LLM semantic comparison.

        Parameters:
            case_id: Case id string.
            document_excerpt: Claimant excerpt text used for semantic comparison.

        Returns:
            Boolean whether plagiarism is confirmed.
        """
        cases = json.loads(self.cases)
        key = str(case_id)
        if key not in cases:
            _raise_user_error(f"{ERROR_EXPECTED} case not found")

        c = cases[key]
        if c["status"] != "PENDING":
            _raise_user_error(f"{ERROR_EXPECTED} case already resolved")

        excerpt = str(document_excerpt).strip()
        if len(excerpt) < 40:
            _raise_user_error(f"{ERROR_EXPECTED} document_excerpt too short")

        def fetch_and_judge() -> str:
            src = gl.nondet.web.get(c["source_url"])
            status = int(src.status)
            if status >= 400 and status < 500:
                _raise_user_error(f"{ERROR_EXTERNAL} source client error: {status}")
            if status >= 500:
                _raise_user_error(f"{ERROR_EXTERNAL} source server error: {status}")

            source_body = src.body.decode("utf-8") if src.body is not None else ""
            if len(source_body.strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty source body")

            prompt = f"""
You are an academic integrity adjudicator.
Compare claimant excerpt against suspected source content for textual and conceptual overlap.
Return JSON only.

Rules:
1) Estimate semantic overlap percentage from 0 to 100.
2) confirmed is true only if overlap_pct is at least threshold.
3) Focus on meaningful phrase reuse, structure, and idea sequencing.
4) Ignore boilerplate legal text and common generic language.

Threshold percent: {c['threshold_pct']}
Document hash reference: {c['document_hash']}

Return exactly:
{{
  "overlap_pct": int,
  "confirmed": true_or_false,
  "reason": "string"
}}

Claimant excerpt:
{excerpt[:4000]}

Suspected source content:
{source_body[:7000]}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            overlap = int(parsed.get("overlap_pct", 0))
            if overlap < 0:
                overlap = 0
            if overlap > 100:
                overlap = 100

            confirmed = bool(parsed.get("confirmed", False))
            confirmed = confirmed and overlap >= int(c["threshold_pct"])
            reason = str(parsed.get("reason", ""))[:500]

            return json.dumps(
                {
                    "overlap_pct": overlap,
                    "confirmed": bool(confirmed),
                    "reason": reason,
                }
            )

        principle = "Equivalent when overlap_pct differs by at most 10 points and confirmed matches exactly."
        result_json = _run_prompt_consensus(fetch_and_judge, principle)
        result = json.loads(result_json)

        c["overlap_pct"] = int(result.get("overlap_pct", 0))
        c["plagiarism_confirmed"] = bool(result.get("confirmed", False))
        c["reason"] = str(result.get("reason", ""))
        c["status"] = "CONFIRMED" if c["plagiarism_confirmed"] else "DISMISSED"
        c["resolved_at"] = str(gl.block.timestamp)

        cases[key] = c
        self.cases = json.dumps(cases)
        return bool(c["plagiarism_confirmed"])

    @gl.public.view
    def get_case(self, case_id: str) -> str:
        """Read one case record.

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
        """Read all case records.

        Parameters:
            None.

        Returns:
            Cases map JSON string.
        """
        return self.cases
