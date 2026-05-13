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


class PodcastChartSettlement(gl.Contract):
    """Settles podcast ad or royalty deals from chart rank and audience metrics."""

    deals: str
    balances: str
    next_deal_id: u256

    def __init__(self):
        """Initialize empty deal and balance storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.deals = "{}"
        self.balances = "{}"
        self.next_deal_id = 1

    @gl.public.write
    def top_up_balance(self, amount: int) -> None:
        """Credit sender balance.

        Parameters:
            amount: Positive integer amount.

        Returns:
            None.
        """
        if amount <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} amount must be positive")

        sender = str(gl.message.sender_account)
        balances = json.loads(self.balances)
        balances[sender] = int(balances.get(sender, 0)) + int(amount)
        self.balances = json.dumps(balances)

    @gl.public.view
    def balance_of(self, account: str) -> int:
        """Read balance by account.

        Parameters:
            account: Address string.

        Returns:
            Integer balance.
        """
        balances = json.loads(self.balances)
        return int(balances.get(str(account), 0))

    @gl.public.write
    def create_deal(
        self,
        podcaster_wallet: str,
        show_name: str,
        period_label: str,
        min_spotify_rank: int,
        min_apple_rank: int,
        min_listeners: int,
        payout_amount: int,
        source_mode: str,
    ) -> str:
        """Create podcast settlement deal and escrow payout.

        Parameters:
            podcaster_wallet: Payout receiver address.
            show_name: Podcast show title.
            period_label: Settlement period label.
            min_spotify_rank: Required best Spotify chart rank where lower is better.
            min_apple_rank: Required best Apple chart rank where lower is better.
            min_listeners: Required estimated listeners.
            payout_amount: Escrow amount paid on success.
            source_mode: spotify apple or both.

        Returns:
            Deal id string.
        """
        sponsor = str(gl.message.sender_account)
        podcaster = str(podcaster_wallet).strip()
        show = str(show_name).strip()
        period = str(period_label).strip()
        mode = str(source_mode).strip().lower()

        if len(podcaster) < 10:
            _raise_user_error(f"{ERROR_EXPECTED} invalid podcaster wallet")
        if len(show) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid show name")
        if len(period) < 3:
            _raise_user_error(f"{ERROR_EXPECTED} invalid period label")
        if min_spotify_rank < 1 or min_apple_rank < 1:
            _raise_user_error(f"{ERROR_EXPECTED} rank thresholds must be >= 1")
        if min_listeners < 0:
            _raise_user_error(f"{ERROR_EXPECTED} listeners threshold must be >= 0")
        if payout_amount <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} payout must be positive")
        if mode not in ["spotify", "apple", "both"]:
            _raise_user_error(f"{ERROR_EXPECTED} unsupported source mode")

        balances = json.loads(self.balances)
        if int(balances.get(sponsor, 0)) < int(payout_amount):
            _raise_user_error(f"{ERROR_EXPECTED} insufficient sponsor balance")

        balances[sponsor] = int(balances.get(sponsor, 0)) - int(payout_amount)
        self.balances = json.dumps(balances)

        deal_id = str(self.next_deal_id)
        self.next_deal_id += 1

        deals = json.loads(self.deals)
        deals[deal_id] = {
            "deal_id": deal_id,
            "sponsor": sponsor,
            "podcaster_wallet": podcaster,
            "show_name": show,
            "period_label": period,
            "min_spotify_rank": int(min_spotify_rank),
            "min_apple_rank": int(min_apple_rank),
            "min_listeners": int(min_listeners),
            "payout_amount": int(payout_amount),
            "source_mode": mode,
            "status": "ACTIVE",
            "kpi_met": False,
            "result": {},
            "resolved_at": "",
        }
        self.deals = json.dumps(deals)
        return deal_id

    @gl.public.write
    def settle_deal(self, deal_id: str) -> str:
        """Settle deal using public chart and audience data synthesis.

        Parameters:
            deal_id: Deal id string.

        Returns:
            Settlement status string.
        """
        deals = json.loads(self.deals)
        key = str(deal_id)
        if key not in deals:
            _raise_user_error(f"{ERROR_EXPECTED} deal not found")

        d = deals[key]
        if d["status"] != "ACTIVE":
            _raise_user_error(f"{ERROR_EXPECTED} deal is not active")

        spotify_url = (
            "https://charts.spotify.com/charts/view/regional-global-daily/latest"
            f"?q={d['show_name']}"
        )
        apple_url = (
            "https://podcasts.apple.com/us/charts"
            f"?q={d['show_name']}"
        )

        def fetch_and_assess() -> str:
            payload = {}

            if d["source_mode"] in ["spotify", "both"]:
                sp = gl.nondet.web.get(spotify_url)
                if int(sp.status) >= 400 and int(sp.status) < 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} spotify client error: {sp.status}")
                if int(sp.status) >= 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} spotify server error: {sp.status}")
                payload["spotify"] = sp.body.decode("utf-8") if sp.body is not None else ""

            if d["source_mode"] in ["apple", "both"]:
                ap = gl.nondet.web.get(apple_url)
                if int(ap.status) >= 400 and int(ap.status) < 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} apple client error: {ap.status}")
                if int(ap.status) >= 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} apple server error: {ap.status}")
                payload["apple"] = ap.body.decode("utf-8") if ap.body is not None else ""

            if len(json.dumps(payload).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty chart payload")

            prompt = f"""
You are a podcast KPI adjudicator.
Synthesize Spotify and Apple chart data with seasonal context.
Return JSON only.

Deal:
- show_name: {d['show_name']}
- period_label: {d['period_label']}
- min_spotify_rank: {d['min_spotify_rank']}
- min_apple_rank: {d['min_apple_rank']}
- min_listeners: {d['min_listeners']}

Rules:
1) Lower rank is better. Rank 1 is best.
2) Extract best observed Spotify rank and Apple rank where available.
3) Estimate listeners and apply context for seasonal chart swings.
4) kpi_met true only when thresholds are met after contextual adjustment.

Return exactly:
{{
  "spotify_rank": int,
  "apple_rank": int,
  "estimated_listeners": int,
  "kpi_met": bool,
  "reason": "string"
}}

Payload:
{json.dumps(payload)[:12000]}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            spotify_rank = int(parsed.get("spotify_rank", 999999))
            apple_rank = int(parsed.get("apple_rank", 999999))
            listeners = int(parsed.get("estimated_listeners", 0))
            llm_met = bool(parsed.get("kpi_met", False))

            numeric_met = True
            if d["source_mode"] in ["spotify", "both"]:
                numeric_met = numeric_met and (spotify_rank <= int(d["min_spotify_rank"]))
            if d["source_mode"] in ["apple", "both"]:
                numeric_met = numeric_met and (apple_rank <= int(d["min_apple_rank"]))
            numeric_met = numeric_met and (listeners >= int(d["min_listeners"]))

            return json.dumps(
                {
                    "spotify_rank": spotify_rank,
                    "apple_rank": apple_rank,
                    "estimated_listeners": listeners,
                    "kpi_met": bool(llm_met or numeric_met),
                    "reason": str(parsed.get("reason", ""))[:500],
                }
            )

        principle = "Equivalent when kpi_met matches and ranks differ by at most 15 and listeners by at most 20 percent."
        result_json = _run_prompt_consensus(fetch_and_assess, principle)
        result = json.loads(result_json)

        d["result"] = result
        d["kpi_met"] = bool(result.get("kpi_met", False))
        d["resolved_at"] = str(gl.block.timestamp)

        balances = json.loads(self.balances)
        if d["kpi_met"]:
            podcaster = str(d["podcaster_wallet"])
            balances[podcaster] = int(balances.get(podcaster, 0)) + int(d["payout_amount"])
            d["status"] = "SETTLED_PAID"
        else:
            sponsor = str(d["sponsor"])
            balances[sponsor] = int(balances.get(sponsor, 0)) + int(d["payout_amount"])
            d["status"] = "SETTLED_DENIED"

        deals[key] = d
        self.deals = json.dumps(deals)
        self.balances = json.dumps(balances)

        return d["status"]

    @gl.public.view
    def get_deal(self, deal_id: str) -> str:
        """Read one deal by id.

        Parameters:
            deal_id: Deal id string.

        Returns:
            Deal JSON string.
        """
        deals = json.loads(self.deals)
        key = str(deal_id)
        if key not in deals:
            _raise_user_error(f"{ERROR_EXPECTED} deal not found")
        return json.dumps(deals[key])

    @gl.public.view
    def get_all_deals(self) -> str:
        """Read all deals.

        Parameters:
            None.

        Returns:
            Deals map JSON string.
        """
        return self.deals
