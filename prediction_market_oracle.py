# v0.1.0
# { "Depends": "py-genlayer:test" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"
ERROR_TRANSIENT = "[TRANSIENT]"

RESOLUTION_BUFFER_SECONDS = 3600
DISPUTE_WINDOW_SECONDS = 172800
MIN_CONFIDENCE = 0.90


@allow_storage
@dataclass
class Market:
    market_id: str
    creator: Address
    question: str
    resolution_source_url: str
    resolution_criteria: str
    closing_timestamp: u256
    yes_reserve: u256
    no_reserve: u256
    total_liquidity: u256
    fee_bps: u256
    pool_collateral_wei: u256
    yes_shares_outstanding: u256
    no_shares_outstanding: u256
    resolved: bool
    outcome_yes: bool
    status: str  # OPEN | DISPUTE | RESOLVED
    dispute_deadline: u256
    confidence_bps: u256


class PredictionMarketOracle(gl.Contract):
    markets: TreeMap[str, Market]
    market_ids: DynArray[str]
    balances: TreeMap[str, u256]
    yes_shares: TreeMap[str, u256]  # key: market|wallet
    no_shares: TreeMap[str, u256]   # key: market|wallet

    def __init__(self):
        pass

    def _now(self) -> int:
        return int(gl.block.timestamp)

    def _mkey(self, market_id: str, wallet: str) -> str:
        return f"{market_id}|{str(wallet).lower()}"

    def _balance_of(self, wallet: str) -> int:
        w = str(wallet).lower()
        return int(self.balances[w]) if w in self.balances else 0

    def _set_balance(self, wallet: str, amount: int) -> None:
        self.balances[str(wallet).lower()] = u256(amount)

    def _get_price_yes(self, m: Market) -> float:
        total = int(m.yes_reserve) + int(m.no_reserve)
        if total == 0:
            return 0.5
        return int(m.no_reserve) / total

    def _safe_resolution_passes(self, m: Market) -> list:
        passes = []
        for idx in range(3):
            url = f"{m.resolution_source_url}{'&' if '?' in m.resolution_source_url else '?'}_safe_pass={idx}"
            res = gl.nondet.web.get(url)
            if res.status >= 400:
                raise gl.vm.UserError(f"{ERROR_TRANSIENT} source fetch failed on pass {idx}")

            body = res.body.decode("utf-8")
            prompt = f"""You are an adjudicator for prediction markets.
Given the page content and criteria, decide binary outcome.
Return JSON only with fields:
- outcome: YES or NO
- confidence: float 0..1
- rationale: short text

Question: {m.question}
Resolution criteria: {m.resolution_criteria}
Source content (truncated):
{body[:9000]}
"""
            ai = gl.nondet.exec_prompt(
                prompt,
                response_format={
                    "type": "object",
                    "properties": {
                        "outcome": {"type": "string"},
                        "confidence": {"type": "number"},
                        "rationale": {"type": "string"},
                    },
                },
            )
            outcome = str(ai.get("outcome", "NO")).upper().strip()
            conf = float(ai.get("confidence", 0.0))
            conf = max(0.0, min(1.0, conf))
            passes.append({"outcome": outcome, "confidence": conf})
        return passes

    def _resolve_from_passes(self, passes: list) -> dict:
        yes_votes = sum(1 for p in passes if p["outcome"] == "YES")
        no_votes = sum(1 for p in passes if p["outcome"] == "NO")
        avg_conf = sum(float(p["confidence"]) for p in passes) / len(passes)
        outcome_yes = yes_votes >= no_votes
        return {"outcome_yes": outcome_yes, "avg_conf": avg_conf}

    @gl.public.write
    def top_up_balance(self, amount_wei: int) -> None:
        if amount_wei <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} amount_wei must be positive")
        sender = str(gl.message.sender_account).lower()
        self._set_balance(sender, self._balance_of(sender) + int(amount_wei))
        print(f"[BalanceToppedUp] wallet={sender} amount_wei={amount_wei}")

    @gl.public.write
    def create_market(
        self,
        question: str,
        resolution_source_url: str,
        resolution_criteria: str,
        closing_timestamp: int,
        liquidity_deposit_wei: int,
        fee_bps: int = 200,
    ) -> str:
        q = str(question).strip()
        src = str(resolution_source_url).strip()
        crit = str(resolution_criteria).strip()
        if not q or not src or not crit:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} question, source, and criteria are required")
        if closing_timestamp <= self._now():
            raise gl.vm.UserError(f"{ERROR_EXPECTED} closing_timestamp must be in the future")
        if liquidity_deposit_wei <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} liquidity_deposit_wei must be positive")
        if fee_bps < 0 or fee_bps > 1000:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} fee_bps out of range")

        creator = str(gl.message.sender_account).lower()
        bal = self._balance_of(creator)
        if bal < int(liquidity_deposit_wei):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} insufficient creator balance")
        self._set_balance(creator, bal - int(liquidity_deposit_wei))

        market_id = str(len(self.market_ids))
        half = int(liquidity_deposit_wei) // 2
        m = Market(
            market_id=market_id,
            creator=gl.message.sender_account,
            question=q,
            resolution_source_url=src,
            resolution_criteria=crit,
            closing_timestamp=u256(int(closing_timestamp)),
            yes_reserve=u256(half),
            no_reserve=u256(int(liquidity_deposit_wei) - half),
            total_liquidity=u256(int(liquidity_deposit_wei)),
            fee_bps=u256(int(fee_bps)),
            pool_collateral_wei=u256(int(liquidity_deposit_wei)),
            yes_shares_outstanding=u256(0),
            no_shares_outstanding=u256(0),
            resolved=False,
            outcome_yes=False,
            status="OPEN",
            dispute_deadline=u256(0),
            confidence_bps=u256(0),
        )
        self.markets[market_id] = m
        self.market_ids.append(market_id)

        print(
            f"[MarketCreated] market_id={market_id} creator={creator} "
            f"closing_timestamp={closing_timestamp} liquidity_wei={liquidity_deposit_wei}"
        )
        return market_id

    @gl.public.write
    def buy_shares(self, market_id: str, buy_yes: bool, amount_wei: int) -> int:
        if market_id not in self.markets:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} market not found")
        if amount_wei <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} amount_wei must be positive")

        m = self.markets[market_id]
        if m.status != "OPEN":
            raise gl.vm.UserError(f"{ERROR_EXPECTED} market is not open")
        if self._now() >= int(m.closing_timestamp):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} market already closed")

        buyer = str(gl.message.sender_account).lower()
        bal = self._balance_of(buyer)
        if bal < int(amount_wei):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} insufficient balance")

        fee = int(amount_wei) * int(m.fee_bps) // 10000
        net = int(amount_wei) - fee
        if net <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} trade too small after fee")

        self._set_balance(buyer, bal - int(amount_wei))

        # Constant product AMM with share mint based on reserve movement.
        if buy_yes:
            x = int(m.yes_reserve)
            y = int(m.no_reserve)
            k = x * y
            new_x = x + net
            new_y = k // new_x if new_x > 0 else y
            shares = y - new_y
            if shares <= 0:
                raise gl.vm.UserError(f"{ERROR_EXPECTED} trade produced zero shares")

            m.yes_reserve = u256(new_x)
            m.no_reserve = u256(new_y)
            m.yes_shares_outstanding = u256(int(m.yes_shares_outstanding) + shares)
            key = self._mkey(market_id, buyer)
            prev = int(self.yes_shares[key]) if key in self.yes_shares else 0
            self.yes_shares[key] = u256(prev + shares)
        else:
            x = int(m.no_reserve)
            y = int(m.yes_reserve)
            k = x * y
            new_x = x + net
            new_y = k // new_x if new_x > 0 else y
            shares = y - new_y
            if shares <= 0:
                raise gl.vm.UserError(f"{ERROR_EXPECTED} trade produced zero shares")

            m.no_reserve = u256(new_x)
            m.yes_reserve = u256(new_y)
            m.no_shares_outstanding = u256(int(m.no_shares_outstanding) + shares)
            key = self._mkey(market_id, buyer)
            prev = int(self.no_shares[key]) if key in self.no_shares else 0
            self.no_shares[key] = u256(prev + shares)

        m.pool_collateral_wei = u256(int(m.pool_collateral_wei) + net + fee)
        self.markets[market_id] = m

        print(
            f"[SharesBought] market_id={market_id} buyer={buyer} side={'YES' if buy_yes else 'NO'} "
            f"amount_wei={amount_wei} fee_wei={fee} shares={shares}"
        )
        return shares

    @gl.public.write
    def resolve_market(self, market_id: str) -> str:
        if market_id not in self.markets:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} market not found")
        m = self.markets[market_id]
        if m.status == "RESOLVED":
            raise gl.vm.UserError(f"{ERROR_EXPECTED} market already resolved")
        if self._now() < int(m.closing_timestamp) + RESOLUTION_BUFFER_SECONDS:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} resolution buffer not reached")

        passes = self._safe_resolution_passes(m)
        result = self._resolve_from_passes(passes)

        avg_conf = float(result["avg_conf"])
        m.confidence_bps = u256(int(round(avg_conf * 10000)))

        if avg_conf < MIN_CONFIDENCE:
            m.status = "DISPUTE"
            m.dispute_deadline = u256(self._now() + DISPUTE_WINDOW_SECONDS)
            self.markets[market_id] = m
            print(
                f"[DisputeOpened] market_id={market_id} confidence={avg_conf} "
                f"deadline={int(m.dispute_deadline)}"
            )
            return "DISPUTE"

        m.status = "RESOLVED"
        m.resolved = True
        m.outcome_yes = bool(result["outcome_yes"])
        self.markets[market_id] = m
        print(
            f"[MarketResolved] market_id={market_id} outcome={'YES' if m.outcome_yes else 'NO'} "
            f"confidence={avg_conf}"
        )
        return "RESOLVED"

    @gl.public.write
    def claim_payout(self, market_id: str) -> int:
        if market_id not in self.markets:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} market not found")
        m = self.markets[market_id]
        if m.status != "RESOLVED":
            raise gl.vm.UserError(f"{ERROR_EXPECTED} market not resolved")

        claimer = str(gl.message.sender_account).lower()
        key = self._mkey(market_id, claimer)

        if m.outcome_yes:
            user_shares = int(self.yes_shares[key]) if key in self.yes_shares else 0
            total_win = int(m.yes_shares_outstanding)
            if key in self.yes_shares:
                self.yes_shares[key] = u256(0)
        else:
            user_shares = int(self.no_shares[key]) if key in self.no_shares else 0
            total_win = int(m.no_shares_outstanding)
            if key in self.no_shares:
                self.no_shares[key] = u256(0)

        if user_shares <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} no winning shares")
        if total_win <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} invalid winner pool")

        payout = int(m.pool_collateral_wei) * user_shares // total_win
        self._set_balance(claimer, self._balance_of(claimer) + payout)
        return payout

    @gl.public.view
    def get_market(self, market_id: str) -> str:
        if market_id not in self.markets:
            return json.dumps({"error": "not found"})
        m = self.markets[market_id]
        return json.dumps(
            {
                "market_id": m.market_id,
                "question": m.question,
                "status": m.status,
                "closing_timestamp": int(m.closing_timestamp),
                "yes_reserve": int(m.yes_reserve),
                "no_reserve": int(m.no_reserve),
                "pool_collateral_wei": int(m.pool_collateral_wei),
                "confidence": int(m.confidence_bps) / 10000.0,
                "outcome": "YES" if m.resolved and m.outcome_yes else ("NO" if m.resolved else None),
                "dispute_deadline": int(m.dispute_deadline),
            }
        )

    @gl.public.view
    def get_price_yes(self, market_id: str) -> float:
        if market_id not in self.markets:
            return 0.0
        return self._get_price_yes(self.markets[market_id])

    @gl.public.view
    def get_balance(self, wallet: str) -> int:
        return self._balance_of(wallet)
