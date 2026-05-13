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


class DexSlippageEstimator(gl.Contract):
    """Estimates execution slippage from live DEX pool and depth data."""

    requests: str
    next_request_id: u256
    latest_slippage_by_pair: str

    def __init__(self):
        """Initialize state.

        Parameters:
            None.

        Returns:
            None.
        """
        self.requests = "{}"
        self.next_request_id = 1
        self.latest_slippage_by_pair = "{}"

    @gl.public.write
    def create_request(
        self,
        pair_key: str,
        token_in: str,
        token_out: str,
        amount_in_usd: int,
        max_acceptable_bps: int,
    ) -> str:
        """Create slippage estimation request.

        Parameters:
            pair_key: Pair label key.
            token_in: Input token symbol.
            token_out: Output token symbol.
            amount_in_usd: Estimated trade notional in USD.
            max_acceptable_bps: User threshold in basis points.

        Returns:
            Request id string.
        """
        pk = str(pair_key).strip().lower()
        tin = str(token_in).strip().upper()
        tout = str(token_out).strip().upper()

        if len(pk) < 3:
            _raise_user_error(f"{ERROR_EXPECTED} invalid pair_key")
        if len(tin) < 2 or len(tout) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid token symbol")
        if amount_in_usd <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} amount_in_usd must be positive")
        if max_acceptable_bps < 1 or max_acceptable_bps > 10000:
            _raise_user_error(f"{ERROR_EXPECTED} max_acceptable_bps out of range")

        rid = str(self.next_request_id)
        self.next_request_id += 1

        requests = json.loads(self.requests)
        requests[rid] = {
            "request_id": rid,
            "creator": str(gl.message.sender_account),
            "pair_key": pk,
            "token_in": tin,
            "token_out": tout,
            "amount_in_usd": int(amount_in_usd),
            "max_acceptable_bps": int(max_acceptable_bps),
            "status": "PENDING",
            "estimated_slippage_bps": 0,
            "decision": "",
            "consensus_sources": 0,
            "reason": "",
            "resolved_at": "",
        }
        self.requests = json.dumps(requests)
        return rid

    @gl.public.write
    def resolve_request(self, request_id: str) -> str:
        """Resolve slippage estimate and execution decision.

        Parameters:
            request_id: Request id string.

        Returns:
            Decision string EXECUTE or SKIP.
        """
        requests = json.loads(self.requests)
        key = str(request_id)
        if key not in requests:
            _raise_user_error(f"{ERROR_EXPECTED} request not found")

        r = requests[key]
        if r["status"] != "PENDING":
            _raise_user_error(f"{ERROR_EXPECTED} request already resolved")

        def fetch_and_estimate() -> str:
            uni_url = "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3"
            curve_url = "https://api.thegraph.com/subgraphs/name/curvefi/curve"
            backup_url = "https://api.llama.fi/protocol/uniswap"

            u = gl.nondet.web.get(uni_url)
            c = gl.nondet.web.get(curve_url)
            b = gl.nondet.web.get(backup_url)

            for name, res in [("uniswap", u), ("curve", c), ("backup", b)]:
                status = int(res.status)
                if status >= 400 and status < 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} client error: {status}")
                if status >= 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} server error: {status}")

            ub = u.body.decode("utf-8") if u.body is not None else ""
            cb = c.body.decode("utf-8") if c.body is not None else ""
            bb = b.body.decode("utf-8") if b.body is not None else ""
            if len((ub + cb + bb).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty liquidity payload")

            prompt = f"""
You are a DEX execution risk analyst.
Return JSON only.

Request:
- pair_key: {r['pair_key']}
- token_in: {r['token_in']}
- token_out: {r['token_out']}
- amount_in_usd: {r['amount_in_usd']}
- max_acceptable_bps: {r['max_acceptable_bps']}

Rules:
1) Infer live liquidity and depth for this pair from the sources.
2) Estimate slippage in basis points for the requested amount.
3) decision is EXECUTE when estimated_slippage_bps is less than or equal to max_acceptable_bps.
4) consensus_sources is number of sources aligned on slippage direction.

Return exactly:
{{
  "estimated_slippage_bps": int,
  "decision": "EXECUTE_or_SKIP",
  "consensus_sources": int,
  "reason": "string"
}}

Inputs:
{json.dumps({"uniswap": ub[:6000], "curve": cb[:6000], "backup": bb[:6000]})}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            bps = int(parsed.get("estimated_slippage_bps", 0))
            if bps < 0:
                _raise_user_error(f"{ERROR_EXTERNAL} invalid estimated_slippage_bps")

            decision = "EXECUTE" if bps <= int(r["max_acceptable_bps"]) else "SKIP"

            sources = int(parsed.get("consensus_sources", 0))
            sources = max(0, min(3, sources))
            if sources < 1:
                decision = "SKIP"

            return json.dumps(
                {
                    "estimated_slippage_bps": bps,
                    "decision": decision,
                    "consensus_sources": sources,
                    "reason": str(parsed.get("reason", ""))[:500],
                }
            )

        principle = "Equivalent when decision matches and estimated_slippage_bps differs by no more than 30 bps."
        result_json = _run_prompt_consensus(fetch_and_estimate, principle)
        result = json.loads(result_json)

        r["estimated_slippage_bps"] = int(result.get("estimated_slippage_bps", 0))
        r["decision"] = str(result.get("decision", "SKIP"))
        r["consensus_sources"] = int(result.get("consensus_sources", 0))
        r["reason"] = str(result.get("reason", ""))
        r["status"] = "RESOLVED"
        r["resolved_at"] = str(gl.block.timestamp)

        requests[key] = r
        self.requests = json.dumps(requests)

        latest = json.loads(self.latest_slippage_by_pair)
        latest[str(r["pair_key"]).lower()] = int(r["estimated_slippage_bps"])
        self.latest_slippage_by_pair = json.dumps(latest)

        return r["decision"]

    @gl.public.view
    def get_latest_slippage_bps(self, pair_key: str) -> int:
        """Read latest slippage estimate by pair.

        Parameters:
            pair_key: Pair key string.

        Returns:
            Slippage estimate in basis points.
        """
        latest = json.loads(self.latest_slippage_by_pair)
        return int(latest.get(str(pair_key).strip().lower(), 0))

    @gl.public.view
    def get_request(self, request_id: str) -> str:
        """Read one request.

        Parameters:
            request_id: Request id string.

        Returns:
            Request JSON string.
        """
        requests = json.loads(self.requests)
        key = str(request_id)
        if key not in requests:
            _raise_user_error(f"{ERROR_EXPECTED} request not found")
        return json.dumps(requests[key])

    @gl.public.view
    def get_all_requests(self) -> str:
        """Read all requests.

        Parameters:
            None.

        Returns:
            Requests map JSON string.
        """
        return self.requests
