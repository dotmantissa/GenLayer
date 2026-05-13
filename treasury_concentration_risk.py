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


class TreasuryConcentrationRisk(gl.Contract):
    """Evaluates DAO treasury concentration risk from holdings prices and market context."""

    reports: str
    next_report_id: u256
    latest_risk_by_treasury: str

    def __init__(self):
        """Initialize storage state.

        Parameters:
            None.

        Returns:
            None.
        """
        self.reports = "{}"
        self.next_report_id = 1
        self.latest_risk_by_treasury = "{}"

    @gl.public.write
    def create_report(
        self,
        treasury_label: str,
        holdings_json: str,
        max_asset_weight_pct: int,
        max_top2_weight_pct: int,
    ) -> str:
        """Create concentration risk report request.

        Parameters:
            treasury_label: Treasury name key.
            holdings_json: JSON list of holdings objects with symbol and amount.
            max_asset_weight_pct: Maximum allowed single asset share.
            max_top2_weight_pct: Maximum allowed top two asset share.

        Returns:
            Report id string.
        """
        label = str(treasury_label).strip().lower()
        if len(label) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid treasury label")
        if max_asset_weight_pct < 1 or max_asset_weight_pct > 100:
            _raise_user_error(f"{ERROR_EXPECTED} max_asset_weight_pct out of range")
        if max_top2_weight_pct < 1 or max_top2_weight_pct > 100:
            _raise_user_error(f"{ERROR_EXPECTED} max_top2_weight_pct out of range")

        try:
            holdings = json.loads(holdings_json)
        except Exception:
            _raise_user_error(f"{ERROR_EXPECTED} invalid holdings json")

        if not isinstance(holdings, list) or len(holdings) == 0:
            _raise_user_error(f"{ERROR_EXPECTED} holdings must be a non empty list")

        normalized = []
        for h in holdings:
            if not isinstance(h, dict):
                _raise_user_error(f"{ERROR_EXPECTED} holding must be object")
            symbol = str(h.get("symbol", "")).strip().upper()
            amount = float(h.get("amount", 0))
            if len(symbol) < 2 or amount <= 0:
                _raise_user_error(f"{ERROR_EXPECTED} invalid holding entry")
            normalized.append({"symbol": symbol, "amount": amount})

        report_id = str(self.next_report_id)
        self.next_report_id += 1

        reports = json.loads(self.reports)
        reports[report_id] = {
            "report_id": report_id,
            "creator": str(gl.message.sender_account),
            "treasury_label": label,
            "holdings": normalized,
            "max_asset_weight_pct": int(max_asset_weight_pct),
            "max_top2_weight_pct": int(max_top2_weight_pct),
            "status": "PENDING",
            "risk": "",
            "top_asset_weight_pct": 0,
            "top2_weight_pct": 0,
            "recommendation": "",
            "reason": "",
            "resolved_at": "",
        }
        self.reports = json.dumps(reports)
        return report_id

    @gl.public.write
    def resolve_report(self, report_id: str) -> str:
        """Resolve concentration risk report using live price data and LLM context.

        Parameters:
            report_id: Report id string.

        Returns:
            Risk label string.
        """
        reports = json.loads(self.reports)
        key = str(report_id)
        if key not in reports:
            _raise_user_error(f"{ERROR_EXPECTED} report not found")

        r = reports[key]
        if r["status"] != "PENDING":
            _raise_user_error(f"{ERROR_EXPECTED} report already resolved")

        symbols = ",".join([h["symbol"] for h in r["holdings"]])
        prices_url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbols}&vs_currencies=usd"
        market_url = "https://api.coingecko.com/api/v3/global"

        def fetch_and_assess() -> str:
            p = gl.nondet.web.get(prices_url)
            m = gl.nondet.web.get(market_url)

            if int(p.status) >= 400 and int(p.status) < 500:
                _raise_user_error(f"{ERROR_EXTERNAL} prices client error: {p.status}")
            if int(p.status) >= 500:
                _raise_user_error(f"{ERROR_EXTERNAL} prices server error: {p.status}")
            if int(m.status) >= 400 and int(m.status) < 500:
                _raise_user_error(f"{ERROR_EXTERNAL} market client error: {m.status}")
            if int(m.status) >= 500:
                _raise_user_error(f"{ERROR_EXTERNAL} market server error: {m.status}")

            p_body = p.body.decode("utf-8") if p.body is not None else ""
            m_body = m.body.decode("utf-8") if m.body is not None else ""
            if len((p_body + m_body).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty market payload")

            prompt = f"""
You are a treasury risk analyst.
Assess concentration risk considering asset weights and current market context.
Return JSON only.

Thresholds:
- max_asset_weight_pct: {r['max_asset_weight_pct']}
- max_top2_weight_pct: {r['max_top2_weight_pct']}

Rules:
1) Compute top_asset_weight_pct and top2_weight_pct from holdings and prices.
2) Classify risk as LOW MEDIUM or HIGH.
3) If either threshold is exceeded strongly classify HIGH.
4) Provide practical rebalancing recommendation.

Return exactly:
{{
  "risk": "LOW_or_MEDIUM_or_HIGH",
  "top_asset_weight_pct": int,
  "top2_weight_pct": int,
  "recommendation": "string",
  "reason": "string"
}}

Input:
{json.dumps({"holdings": r['holdings'], "prices": p_body[:5000], "market": m_body[:5000]})}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            risk = str(parsed.get("risk", "MEDIUM")).strip().upper()
            if risk not in ["LOW", "MEDIUM", "HIGH"]:
                risk = "MEDIUM"

            top_asset = int(parsed.get("top_asset_weight_pct", 0))
            top2 = int(parsed.get("top2_weight_pct", 0))

            if top_asset > int(r["max_asset_weight_pct"]) or top2 > int(r["max_top2_weight_pct"]):
                risk = "HIGH"
            elif top_asset <= int(r["max_asset_weight_pct"]) and top2 <= int(r["max_top2_weight_pct"]):
                if risk == "HIGH":
                    risk = "MEDIUM"

            return json.dumps(
                {
                    "risk": risk,
                    "top_asset_weight_pct": top_asset,
                    "top2_weight_pct": top2,
                    "recommendation": str(parsed.get("recommendation", ""))[:500],
                    "reason": str(parsed.get("reason", ""))[:500],
                }
            )

        principle = "Equivalent when risk matches and weight metrics differ by at most 10 percentage points."
        result_json = _run_prompt_consensus(fetch_and_assess, principle)
        result = json.loads(result_json)

        r["risk"] = str(result.get("risk", "MEDIUM"))
        r["top_asset_weight_pct"] = int(result.get("top_asset_weight_pct", 0))
        r["top2_weight_pct"] = int(result.get("top2_weight_pct", 0))
        r["recommendation"] = str(result.get("recommendation", ""))
        r["reason"] = str(result.get("reason", ""))
        r["status"] = "RESOLVED"
        r["resolved_at"] = str(gl.block.timestamp)

        reports[key] = r
        self.reports = json.dumps(reports)

        latest = json.loads(self.latest_risk_by_treasury)
        latest[str(r["treasury_label"]).lower()] = r["risk"]
        self.latest_risk_by_treasury = json.dumps(latest)

        return r["risk"]

    @gl.public.view
    def get_report(self, report_id: str) -> str:
        """Read one report.

        Parameters:
            report_id: Report id string.

        Returns:
            Report JSON string.
        """
        reports = json.loads(self.reports)
        key = str(report_id)
        if key not in reports:
            _raise_user_error(f"{ERROR_EXPECTED} report not found")
        return json.dumps(reports[key])

    @gl.public.view
    def get_all_reports(self) -> str:
        """Read all reports.

        Parameters:
            None.

        Returns:
            Reports map JSON string.
        """
        return self.reports

    @gl.public.view
    def get_latest_risk(self, treasury_label: str) -> str:
        """Read latest risk for treasury label.

        Parameters:
            treasury_label: Treasury label string.

        Returns:
            LOW MEDIUM HIGH or UNKNOWN.
        """
        latest = json.loads(self.latest_risk_by_treasury)
        return str(latest.get(str(treasury_label).strip().lower(), "UNKNOWN"))
