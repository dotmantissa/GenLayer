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


class CampaignKpiSettlement(gl.Contract):
    """Settles sponsor payments when social campaign KPI terms are met."""

    campaigns: str
    balances: str
    next_campaign_id: u256

    def __init__(self):
        """Initialize campaign and balance storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.campaigns = "{}"
        self.balances = "{}"
        self.next_campaign_id = 1

    @gl.public.write
    def top_up_balance(self, amount: int) -> None:
        """Top up sender internal balance.

        Parameters:
            amount: Positive amount to credit.

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
        """Get account balance.

        Parameters:
            account: Account address string.

        Returns:
            Current integer balance.
        """
        balances = json.loads(self.balances)
        return int(balances.get(str(account), 0))

    @gl.public.write
    def create_campaign(
        self,
        creator_wallet: str,
        post_url: str,
        end_timestamp: int,
        min_likes: int,
        min_shares: int,
        min_reach: int,
        payout_amount: int,
        source: str,
    ) -> str:
        """Create a sponsored campaign with KPI settlement terms.

        Parameters:
            creator_wallet: Creator address receiving payout.
            post_url: URL of verified campaign post.
            end_timestamp: Campaign end unix timestamp.
            min_likes: Minimum likes KPI.
            min_shares: Minimum shares KPI.
            min_reach: Minimum reach KPI.
            payout_amount: Payout amount on success.
            source: Data source selector x_public or nitter.

        Returns:
            Campaign id string.
        """
        sponsor = str(gl.message.sender_account)
        creator = str(creator_wallet).strip()
        post = str(post_url).strip()
        src = str(source).strip().lower()

        if len(creator) < 10:
            _raise_user_error(f"{ERROR_EXPECTED} invalid creator wallet")
        if len(post) < 8:
            _raise_user_error(f"{ERROR_EXPECTED} invalid post url")
        if end_timestamp <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} invalid end timestamp")
        if min_likes < 0 or min_shares < 0 or min_reach < 0:
            _raise_user_error(f"{ERROR_EXPECTED} kpi thresholds must be >= 0")
        if payout_amount <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} payout must be positive")
        if src not in ["x_public", "nitter"]:
            _raise_user_error(f"{ERROR_EXPECTED} unsupported source")

        balances = json.loads(self.balances)
        if int(balances.get(sponsor, 0)) < int(payout_amount):
            _raise_user_error(f"{ERROR_EXPECTED} insufficient sponsor balance")

        balances[sponsor] = int(balances.get(sponsor, 0)) - int(payout_amount)
        self.balances = json.dumps(balances)

        campaign_id = str(self.next_campaign_id)
        self.next_campaign_id += 1

        campaigns = json.loads(self.campaigns)
        campaigns[campaign_id] = {
            "campaign_id": campaign_id,
            "sponsor": sponsor,
            "creator_wallet": creator,
            "post_url": post,
            "end_timestamp": int(end_timestamp),
            "min_likes": int(min_likes),
            "min_shares": int(min_shares),
            "min_reach": int(min_reach),
            "payout_amount": int(payout_amount),
            "source": src,
            "status": "ACTIVE",
            "kpi_met": False,
            "result": {},
            "resolved_at": "",
        }
        self.campaigns = json.dumps(campaigns)
        return campaign_id

    @gl.public.write
    def settle_campaign(self, campaign_id: str) -> str:
        """Settle campaign based on engagement metrics and LLM KPI interpretation.

        Parameters:
            campaign_id: Campaign id string.

        Returns:
            Settlement status string.
        """
        campaigns = json.loads(self.campaigns)
        key = str(campaign_id)
        if key not in campaigns:
            _raise_user_error(f"{ERROR_EXPECTED} campaign not found")

        c = campaigns[key]
        if c["status"] != "ACTIVE":
            _raise_user_error(f"{ERROR_EXPECTED} campaign is not active")

        if int(gl.block.timestamp) < int(c["end_timestamp"]):
            _raise_user_error(f"{ERROR_EXPECTED} campaign end date not reached")

        if c["source"] == "nitter":
            url = c["post_url"].replace("twitter.com", "nitter.net")
        else:
            url = c["post_url"]

        def fetch_and_evaluate() -> str:
            response = gl.nondet.web.get(url)
            status = int(response.status)
            body = ""
            if response.body is not None:
                body = response.body.decode("utf-8")

            if status >= 400 and status < 500:
                _raise_user_error(f"{ERROR_EXTERNAL} source client error: {status}")
            if status >= 500:
                _raise_user_error(f"{ERROR_EXTERNAL} source server error: {status}")
            if len(body.strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} source response empty")

            prompt = f"""
You are a campaign KPI adjudicator.
Evaluate whether this sponsored post met agreed performance targets.
Return JSON only.

Targets:
- min_likes: {c['min_likes']}
- min_shares: {c['min_shares']}
- min_reach: {c['min_reach']}

Rules:
1) Extract likes shares reach from payload.
2) Determine if quality engagement meets the agreement.
3) kpi_met must be true only when all numeric thresholds are met or contextual clause language supports acceptance.

Return exactly:
{{
  "likes": int,
  "shares": int,
  "reach": int,
  "kpi_met": bool,
  "reason": "string"
}}

Payload:
{body[:12000]}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            likes = int(parsed.get("likes", 0))
            shares = int(parsed.get("shares", 0))
            reach = int(parsed.get("reach", 0))
            llm_met = bool(parsed.get("kpi_met", False))

            numeric_met = (
                likes >= int(c["min_likes"])
                and shares >= int(c["min_shares"])
                and reach >= int(c["min_reach"])
            )

            return json.dumps(
                {
                    "likes": likes,
                    "shares": shares,
                    "reach": reach,
                    "kpi_met": bool(llm_met or numeric_met),
                    "reason": str(parsed.get("reason", ""))[:500],
                }
            )

        principle = "Equivalent when kpi_met is identical and each metric differs by at most 25 percent."
        result_json = _run_prompt_consensus(fetch_and_evaluate, principle)
        result = json.loads(result_json)

        c["result"] = result
        c["kpi_met"] = bool(result.get("kpi_met", False))
        c["resolved_at"] = str(gl.block.timestamp)

        balances = json.loads(self.balances)
        if c["kpi_met"]:
            creator = str(c["creator_wallet"])
            balances[creator] = int(balances.get(creator, 0)) + int(c["payout_amount"])
            c["status"] = "SETTLED_PAID"
        else:
            sponsor = str(c["sponsor"])
            balances[sponsor] = int(balances.get(sponsor, 0)) + int(c["payout_amount"])
            c["status"] = "SETTLED_DENIED"

        campaigns[key] = c
        self.campaigns = json.dumps(campaigns)
        self.balances = json.dumps(balances)

        return c["status"]

    @gl.public.view
    def get_campaign(self, campaign_id: str) -> str:
        """Get campaign record.

        Parameters:
            campaign_id: Campaign id string.

        Returns:
            Campaign JSON string.
        """
        campaigns = json.loads(self.campaigns)
        key = str(campaign_id)
        if key not in campaigns:
            _raise_user_error(f"{ERROR_EXPECTED} campaign not found")
        return json.dumps(campaigns[key])

    @gl.public.view
    def get_all_campaigns(self) -> str:
        """Get all campaigns.

        Parameters:
            None.

        Returns:
            Campaign map JSON string.
        """
        return self.campaigns
