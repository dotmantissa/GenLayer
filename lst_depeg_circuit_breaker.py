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


class LSTDepegCircuitBreaker(gl.Contract):
    """Detects liquid staking token depegs and toggles a protocol circuit breaker."""

    monitors: str
    next_monitor_id: u256

    def __init__(self):
        """Initialize contract storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.monitors = "{}"
        self.next_monitor_id = 1

    @gl.public.write
    def create_monitor(
        self,
        protocol_name: str,
        lst_symbol: str,
        lst_pair_id: str,
        curve_pool_hint: str,
        uniswap_pool_hint: str,
        depeg_bps_threshold: int,
    ) -> str:
        """Create a depeg monitor for one LST backed protocol.

        Parameters:
            protocol_name: Protocol identifier.
            lst_symbol: LST ticker symbol.
            lst_pair_id: Pair id used by price endpoint.
            curve_pool_hint: Curve pool hint string.
            uniswap_pool_hint: Uniswap pool hint string.
            depeg_bps_threshold: Depeg threshold in basis points from ETH.

        Returns:
            Monitor id string.
        """
        p_name = str(protocol_name).strip().lower()
        symbol = str(lst_symbol).strip().upper()
        pair = str(lst_pair_id).strip().lower()
        curve_hint = str(curve_pool_hint).strip().lower()
        uni_hint = str(uniswap_pool_hint).strip().lower()

        if len(p_name) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid protocol_name")
        if len(symbol) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid lst_symbol")
        if len(pair) < 3:
            _raise_user_error(f"{ERROR_EXPECTED} invalid lst_pair_id")
        if len(curve_hint) < 2 or len(uni_hint) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid pool hint")
        if depeg_bps_threshold < 5 or depeg_bps_threshold > 5000:
            _raise_user_error(f"{ERROR_EXPECTED} depeg_bps_threshold out of range")

        monitor_id = str(self.next_monitor_id)
        self.next_monitor_id += 1

        monitors = json.loads(self.monitors)
        monitors[monitor_id] = {
            "monitor_id": monitor_id,
            "owner": str(gl.message.sender_account),
            "protocol_name": p_name,
            "lst_symbol": symbol,
            "lst_pair_id": pair,
            "curve_pool_hint": curve_hint,
            "uniswap_pool_hint": uni_hint,
            "depeg_bps_threshold": int(depeg_bps_threshold),
            "circuit_breaker_active": False,
            "last_curve_price_eth": "",
            "last_uniswap_price_eth": "",
            "last_spot_price_eth": "",
            "last_deviation_bps": 0,
            "last_status": "UNSET",
            "last_checked_at": "",
        }
        self.monitors = json.dumps(monitors)
        return monitor_id

    @gl.public.write
    def evaluate_monitor(self, monitor_id: str) -> str:
        """Evaluate depeg state using multiple feeds and update breaker state.

        Parameters:
            monitor_id: Monitor id string.

        Returns:
            Result status string.
        """
        monitors = json.loads(self.monitors)
        key = str(monitor_id)
        if key not in monitors:
            _raise_user_error(f"{ERROR_EXPECTED} monitor not found")

        m = monitors[key]

        def fetch_and_classify() -> str:
            curve_url = f"https://api.curve.fi/api/getPools/ethereum/{m['curve_pool_hint']}"
            uniswap_url = f"https://api.dexscreener.com/latest/dex/pairs/ethereum/{m['lst_pair_id']}"
            spot_url = "https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT"

            curve_res = gl.nondet.web.get(curve_url)
            uni_res = gl.nondet.web.get(uniswap_url)
            spot_res = gl.nondet.web.get(spot_url)

            for name, res in [
                ("curve", curve_res),
                ("uniswap", uni_res),
                ("spot", spot_res),
            ]:
                status = int(res.status)
                if status >= 400 and status < 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} client error: {status}")
                if status >= 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} server error: {status}")

            curve_body = curve_res.body.decode("utf-8") if curve_res.body is not None else ""
            uni_body = uni_res.body.decode("utf-8") if uni_res.body is not None else ""
            spot_body = spot_res.body.decode("utf-8") if spot_res.body is not None else ""

            if len((curve_body + uni_body + spot_body).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty provider payload")

            prompt = f"""
You are a DeFi market risk analyst.
Interpret prices from Curve and Uniswap for an LST and compare to ETH spot reference.
Return JSON only.

Token: {m['lst_symbol']}
Depeg threshold bps: {m['depeg_bps_threshold']}

Rules:
1) Extract one LST to ETH fair value from each source.
2) Compute average lst_eth_price = mean(curve, uniswap).
3) Compute deviation_bps = abs(1.0 - average lst_eth_price) * 10000 rounded to nearest int.
4) If both feeds indicate deviation_bps >= threshold then status is DEPEG_CONFIRMED.
5) Else if one feed deviates strongly and the other does not then status is INCONCLUSIVE.
6) Else status is PEG_OK.

Return exactly:
{{
  "curve_price_eth": float,
  "uniswap_price_eth": float,
  "spot_eth_usd": float,
  "deviation_bps": int,
  "status": "PEG_OK_or_INCONCLUSIVE_or_DEPEG_CONFIRMED",
  "reason": "string"
}}

Inputs:
{json.dumps({"curve": curve_body[:4000], "uniswap": uni_body[:4000], "spot": spot_body[:4000]})}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            curve_price = float(parsed.get("curve_price_eth", 1.0))
            uni_price = float(parsed.get("uniswap_price_eth", 1.0))
            spot_eth_usd = float(parsed.get("spot_eth_usd", 0.0))
            deviation_bps = int(parsed.get("deviation_bps", 0))
            status = str(parsed.get("status", "INCONCLUSIVE")).strip().upper()
            reason = str(parsed.get("reason", ""))[:500]

            if curve_price <= 0 or uni_price <= 0 or spot_eth_usd <= 0:
                _raise_user_error(f"{ERROR_EXTERNAL} non positive parsed price")

            valid_statuses = ["PEG_OK", "INCONCLUSIVE", "DEPEG_CONFIRMED"]
            if status not in valid_statuses:
                status = "INCONCLUSIVE"

            threshold = int(m["depeg_bps_threshold"])
            curve_bps = int(abs(1.0 - curve_price) * 10000)
            uni_bps = int(abs(1.0 - uni_price) * 10000)
            cross_confirmed = curve_bps >= threshold and uni_bps >= threshold

            if cross_confirmed:
                status = "DEPEG_CONFIRMED"
                deviation_bps = max(deviation_bps, int((curve_bps + uni_bps) / 2))
            elif curve_bps < threshold and uni_bps < threshold:
                status = "PEG_OK"
                deviation_bps = min(deviation_bps, max(curve_bps, uni_bps))
            else:
                status = "INCONCLUSIVE"
                deviation_bps = max(deviation_bps, min(curve_bps, uni_bps))

            return json.dumps(
                {
                    "curve_price_eth": curve_price,
                    "uniswap_price_eth": uni_price,
                    "spot_eth_usd": spot_eth_usd,
                    "deviation_bps": deviation_bps,
                    "status": status,
                    "reason": reason,
                }
            )

        principle = "Equivalent when status matches and deviation_bps differs by at most 25 bps."
        verdict_json = _run_prompt_consensus(fetch_and_classify, principle)
        verdict = json.loads(verdict_json)

        m["last_curve_price_eth"] = str(verdict.get("curve_price_eth", ""))
        m["last_uniswap_price_eth"] = str(verdict.get("uniswap_price_eth", ""))
        m["last_spot_price_eth"] = str(verdict.get("spot_eth_usd", ""))
        m["last_deviation_bps"] = int(verdict.get("deviation_bps", 0))
        m["last_status"] = str(verdict.get("status", "INCONCLUSIVE"))
        m["last_checked_at"] = str(gl.block.timestamp)

        if m["last_status"] == "DEPEG_CONFIRMED":
            m["circuit_breaker_active"] = True
        elif m["last_status"] == "PEG_OK":
            m["circuit_breaker_active"] = False

        monitors[key] = m
        self.monitors = json.dumps(monitors)

        return m["last_status"]

    @gl.public.write
    def set_breaker_manual(self, monitor_id: str, active: bool) -> None:
        """Allow monitor owner to manually toggle circuit breaker.

        Parameters:
            monitor_id: Monitor id string.
            active: Desired breaker state.

        Returns:
            None.
        """
        monitors = json.loads(self.monitors)
        key = str(monitor_id)
        if key not in monitors:
            _raise_user_error(f"{ERROR_EXPECTED} monitor not found")

        m = monitors[key]
        if str(gl.message.sender_account) != str(m["owner"]):
            _raise_user_error(f"{ERROR_EXPECTED} only owner can set breaker")

        m["circuit_breaker_active"] = bool(active)
        monitors[key] = m
        self.monitors = json.dumps(monitors)

    @gl.public.view
    def get_monitor(self, monitor_id: str) -> str:
        """Read one monitor configuration and last evaluation.

        Parameters:
            monitor_id: Monitor id string.

        Returns:
            Monitor JSON string.
        """
        monitors = json.loads(self.monitors)
        key = str(monitor_id)
        if key not in monitors:
            _raise_user_error(f"{ERROR_EXPECTED} monitor not found")
        return json.dumps(monitors[key])

    @gl.public.view
    def get_all_monitors(self) -> str:
        """Read all monitor records.

        Parameters:
            None.

        Returns:
            Monitors map JSON string.
        """
        return self.monitors
