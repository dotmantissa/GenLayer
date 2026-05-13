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


class CustomsTradeEscrow(gl.Contract):
    """Releases trade escrow when customs cleared and delivered shipment is verified."""

    trades: str
    balances: str
    next_trade_id: u256

    def __init__(self):
        """Initialize storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.trades = "{}"
        self.balances = "{}"
        self.next_trade_id = 1

    @gl.public.write
    def top_up_balance(self, amount: int) -> None:
        """Credit sender internal balance.

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
        """Read account internal balance.

        Parameters:
            account: Address string.

        Returns:
            Integer balance.
        """
        balances = json.loads(self.balances)
        return int(balances.get(str(account), 0))

    @gl.public.write
    def create_trade(
        self,
        seller_wallet: str,
        buyer_wallet: str,
        carrier: str,
        tracking_number: str,
        escrow_amount: int,
    ) -> str:
        """Create escrow trade and lock buyer funds.

        Parameters:
            seller_wallet: Seller payout address.
            buyer_wallet: Buyer address.
            carrier: Carrier name dhl ups or fedex.
            tracking_number: Shipment tracking id.
            escrow_amount: Escrow amount to lock.

        Returns:
            Trade id string.
        """
        creator = str(gl.message.sender_account)
        seller = str(seller_wallet).strip()
        buyer = str(buyer_wallet).strip()
        carr = str(carrier).strip().lower()
        tracking = str(tracking_number).strip().upper()

        if len(seller) < 10 or len(buyer) < 10:
            _raise_user_error(f"{ERROR_EXPECTED} invalid participant wallet")
        if carr not in ["dhl", "ups", "fedex"]:
            _raise_user_error(f"{ERROR_EXPECTED} unsupported carrier")
        if len(tracking) < 5:
            _raise_user_error(f"{ERROR_EXPECTED} invalid tracking number")
        if escrow_amount <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} escrow amount must be positive")
        if creator != buyer:
            _raise_user_error(f"{ERROR_EXPECTED} trade creator must be buyer")

        balances = json.loads(self.balances)
        if int(balances.get(buyer, 0)) < int(escrow_amount):
            _raise_user_error(f"{ERROR_EXPECTED} insufficient buyer balance")

        balances[buyer] = int(balances.get(buyer, 0)) - int(escrow_amount)
        self.balances = json.dumps(balances)

        trade_id = str(self.next_trade_id)
        self.next_trade_id += 1

        trades = json.loads(self.trades)
        trades[trade_id] = {
            "trade_id": trade_id,
            "seller_wallet": seller,
            "buyer_wallet": buyer,
            "carrier": carr,
            "tracking_number": tracking,
            "escrow_amount": int(escrow_amount),
            "status": "ACTIVE",
            "customs_cleared": False,
            "delivered": False,
            "resolution": "",
            "reason": "",
            "resolved_at": "",
        }
        self.trades = json.dumps(trades)
        return trade_id

    @gl.public.write
    def settle_trade(self, trade_id: str) -> str:
        """Settle trade escrow from carrier tracking evidence.

        Parameters:
            trade_id: Trade id string.

        Returns:
            Settlement status string.
        """
        trades = json.loads(self.trades)
        key = str(trade_id)
        if key not in trades:
            _raise_user_error(f"{ERROR_EXPECTED} trade not found")

        t = trades[key]
        if t["status"] != "ACTIVE":
            _raise_user_error(f"{ERROR_EXPECTED} trade is not active")

        if t["carrier"] == "dhl":
            url = f"https://api-eu.dhl.com/track/shipments?trackingNumber={t['tracking_number']}"
        elif t["carrier"] == "ups":
            url = f"https://onlinetools.ups.com/track/v1/details/{t['tracking_number']}"
        else:
            url = f"https://apis.fedex.com/track/v1/trackingnumbers/{t['tracking_number']}"

        def fetch_and_parse() -> str:
            resp = gl.nondet.web.get(url)

            if int(resp.status) >= 400 and int(resp.status) < 500:
                _raise_user_error(f"{ERROR_EXTERNAL} carrier client error: {resp.status}")
            if int(resp.status) >= 500:
                _raise_user_error(f"{ERROR_EXTERNAL} carrier server error: {resp.status}")

            body = resp.body.decode("utf-8") if resp.body is not None else ""
            if len(body.strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty carrier payload")

            prompt = f"""
You are a shipment status adjudicator.
Interpret carrier specific tracking statuses for customs and delivery.
Return JSON only.

Rules:
1) customs_cleared true only if customs release or clearance is explicit.
2) delivered true only if final delivered status is explicit.
3) resolve_to should be seller when both customs_cleared and delivered are true.
4) resolve_to should be buyer otherwise.

Return exactly:
{{
  "customs_cleared": bool,
  "delivered": bool,
  "resolve_to": "seller_or_buyer",
  "reason": "string"
}}

Carrier: {t['carrier']}
Tracking: {t['tracking_number']}
Payload:
{body[:12000]}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            customs = bool(parsed.get("customs_cleared", False))
            delivered = bool(parsed.get("delivered", False))
            resolve_to = str(parsed.get("resolve_to", "buyer")).strip().lower()
            if resolve_to not in ["seller", "buyer"]:
                resolve_to = "buyer"

            if customs and delivered:
                resolve_to = "seller"
            else:
                resolve_to = "buyer"

            return json.dumps(
                {
                    "customs_cleared": customs,
                    "delivered": delivered,
                    "resolve_to": resolve_to,
                    "reason": str(parsed.get("reason", ""))[:500],
                }
            )

        principle = "Equivalent when customs and delivered booleans match exactly and resolve_to matches."
        result_json = _run_prompt_consensus(fetch_and_parse, principle)
        result = json.loads(result_json)

        t["customs_cleared"] = bool(result.get("customs_cleared", False))
        t["delivered"] = bool(result.get("delivered", False))
        t["resolution"] = str(result.get("resolve_to", "buyer"))
        t["reason"] = str(result.get("reason", ""))
        t["resolved_at"] = str(gl.block.timestamp)

        balances = json.loads(self.balances)
        if t["resolution"] == "seller":
            balances[str(t["seller_wallet"])] = int(balances.get(str(t["seller_wallet"]), 0)) + int(t["escrow_amount"])
            t["status"] = "SETTLED_SELLER"
        else:
            balances[str(t["buyer_wallet"])] = int(balances.get(str(t["buyer_wallet"]), 0)) + int(t["escrow_amount"])
            t["status"] = "SETTLED_BUYER"

        trades[key] = t
        self.trades = json.dumps(trades)
        self.balances = json.dumps(balances)

        return t["status"]

    @gl.public.view
    def get_trade(self, trade_id: str) -> str:
        """Read one trade.

        Parameters:
            trade_id: Trade id string.

        Returns:
            Trade JSON string.
        """
        trades = json.loads(self.trades)
        key = str(trade_id)
        if key not in trades:
            _raise_user_error(f"{ERROR_EXPECTED} trade not found")
        return json.dumps(trades[key])

    @gl.public.view
    def get_all_trades(self) -> str:
        """Read all trades.

        Parameters:
            None.

        Returns:
            Trades map JSON string.
        """
        return self.trades
