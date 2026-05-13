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


class VestingDisclosureVerifier(gl.Contract):
    """Verifies whether observed unlock events match disclosed vesting commitments."""

    cases: str
    next_case_id: u256
    latest_status_by_project: str

    def __init__(self):
        """Initialize storage state.

        Parameters:
            None.

        Returns:
            None.
        """
        self.cases = "{}"
        self.next_case_id = 1
        self.latest_status_by_project = "{}"

    @gl.public.write
    def create_case(
        self,
        project_key: str,
        registry_contract: str,
        wallet_address: str,
        disclosure_url: str,
        tolerance_percent: int,
    ) -> str:
        """Create a vesting disclosure verification case.

        Parameters:
            project_key: Project identifier key.
            registry_contract: On chain vesting registry contract address.
            wallet_address: Team or investor wallet address.
            disclosure_url: Public URL containing vesting commitment statements.
            tolerance_percent: Allowed mismatch percentage between disclosed and observed unlocks.

        Returns:
            Case id string.
        """
        p = str(project_key).strip().lower()
        rc = str(registry_contract).strip().lower()
        wa = str(wallet_address).strip().lower()
        du = str(disclosure_url).strip()

        if len(p) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid project_key")
        if not rc.startswith("0x") or len(rc) < 10:
            _raise_user_error(f"{ERROR_EXPECTED} invalid registry_contract")
        if not wa.startswith("0x") or len(wa) < 10:
            _raise_user_error(f"{ERROR_EXPECTED} invalid wallet_address")
        if not (du.startswith("http://") or du.startswith("https://")):
            _raise_user_error(f"{ERROR_EXPECTED} invalid disclosure_url")
        if tolerance_percent < 0 or tolerance_percent > 100:
            _raise_user_error(f"{ERROR_EXPECTED} tolerance_percent out of range")

        cid = str(self.next_case_id)
        self.next_case_id += 1

        cases = json.loads(self.cases)
        cases[cid] = {
            "case_id": cid,
            "creator": str(gl.message.sender_account),
            "project_key": p,
            "registry_contract": rc,
            "wallet_address": wa,
            "disclosure_url": du,
            "tolerance_percent": int(tolerance_percent),
            "status": "PENDING",
            "verdict": "",
            "mismatch_percent": 0,
            "unlock_events_observed": 0,
            "consensus_sources": 0,
            "reason": "",
            "resolved_at": "",
        }
        self.cases = json.dumps(cases)
        return cid

    @gl.public.write
    def resolve_case(self, case_id: str) -> str:
        """Resolve vesting compliance verdict using on chain and disclosure data.

        Parameters:
            case_id: Case id string.

        Returns:
            Verdict string COMPLIANT or VIOLATION.
        """
        cases = json.loads(self.cases)
        key = str(case_id)
        if key not in cases:
            _raise_user_error(f"{ERROR_EXPECTED} case not found")

        c = cases[key]
        if c["status"] != "PENDING":
            _raise_user_error(f"{ERROR_EXPECTED} case already resolved")

        def fetch_and_verify() -> str:
            onchain_url = f"https://api.etherscan.io/api?module=account&action=tokentx&address={c['wallet_address']}&sort=asc"
            registry_url = f"https://api.etherscan.io/api?module=contract&action=getsourcecode&address={c['registry_contract']}"
            disclosure_url = c["disclosure_url"]

            r1 = gl.nondet.web.get(onchain_url)
            r2 = gl.nondet.web.get(registry_url)
            r3 = gl.nondet.web.get(disclosure_url)

            for name, res in [("onchain", r1), ("registry", r2), ("disclosure", r3)]:
                status = int(res.status)
                if status >= 400 and status < 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} client error: {status}")
                if status >= 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} server error: {status}")

            b1 = r1.body.decode("utf-8") if r1.body is not None else ""
            b2 = r2.body.decode("utf-8") if r2.body is not None else ""
            b3 = r3.body.decode("utf-8") if r3.body is not None else ""
            if len((b1 + b2 + b3).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty vesting payload")

            prompt = f"""
You are a vesting compliance analyst.
Return JSON only.

Case:
- project_key: {c['project_key']}
- wallet_address: {c['wallet_address']}
- tolerance_percent: {c['tolerance_percent']}

Rules:
1) Infer disclosed vesting schedule and lockups from disclosure content.
2) Infer observed unlock events from on chain payloads.
3) Compute mismatch_percent where lower is better.
4) verdict is COMPLIANT when mismatch_percent is less than or equal to tolerance_percent.
5) consensus_sources is number of independent sources that support the same conclusion.

Return exactly:
{{
  "verdict": "COMPLIANT_or_VIOLATION",
  "mismatch_percent": int,
  "unlock_events_observed": int,
  "consensus_sources": int,
  "reason": "string"
}}

Inputs:
{json.dumps({"onchain": b1[:6000], "registry": b2[:6000], "disclosure": b3[:6000]})}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            mismatch = int(parsed.get("mismatch_percent", 0))
            unlocks = int(parsed.get("unlock_events_observed", 0))
            if mismatch < 0 or unlocks < 0:
                _raise_user_error(f"{ERROR_EXTERNAL} invalid parsed metrics")

            verdict = str(parsed.get("verdict", "VIOLATION")).strip().upper()
            if verdict not in ["COMPLIANT", "VIOLATION"]:
                verdict = "VIOLATION"

            verdict = "COMPLIANT" if mismatch <= int(c["tolerance_percent"]) else "VIOLATION"

            sources = int(parsed.get("consensus_sources", 0))
            sources = max(0, min(3, sources))
            if sources < 1:
                verdict = "VIOLATION"

            return json.dumps(
                {
                    "verdict": verdict,
                    "mismatch_percent": mismatch,
                    "unlock_events_observed": unlocks,
                    "consensus_sources": sources,
                    "reason": str(parsed.get("reason", ""))[:500],
                }
            )

        principle = "Equivalent when verdict matches and mismatch_percent differs by no more than 5 points."
        result_json = _run_prompt_consensus(fetch_and_verify, principle)
        result = json.loads(result_json)

        c["verdict"] = str(result.get("verdict", "VIOLATION"))
        c["mismatch_percent"] = int(result.get("mismatch_percent", 0))
        c["unlock_events_observed"] = int(result.get("unlock_events_observed", 0))
        c["consensus_sources"] = int(result.get("consensus_sources", 0))
        c["reason"] = str(result.get("reason", ""))
        c["status"] = "RESOLVED"
        c["resolved_at"] = str(gl.block.timestamp)

        cases[key] = c
        self.cases = json.dumps(cases)

        latest = json.loads(self.latest_status_by_project)
        latest[str(c["project_key"]).lower()] = c["verdict"]
        self.latest_status_by_project = json.dumps(latest)

        return c["verdict"]

    @gl.public.view
    def get_latest_status(self, project_key: str) -> str:
        """Read latest compliance status for project.

        Parameters:
            project_key: Project key.

        Returns:
            COMPLIANT VIOLATION or UNKNOWN.
        """
        latest = json.loads(self.latest_status_by_project)
        return str(latest.get(str(project_key).strip().lower(), "UNKNOWN"))

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
