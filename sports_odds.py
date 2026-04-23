# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

class SportsOddsAggregator(gl.Contract):
    """
    Aggregates h2h odds from multiple bookmakers via The Odds API, computes a
    vig-removed consensus probability line, and runs parimutuel prediction
    markets settled by AI-verified game results.

    NOTE: odds_api_key is stored on-chain and visible to all validators.
          Use a dedicated free-tier key (500 req/month on the free plan).

    Probabilities are stored scaled x10000  (e.g. 55.43% -> 5543).
    Amounts are wei-denominated accounting units; no real ETH in the simulator.
    Payout: parimutuel — winners split the total pool pro-rata by stake.
    If no bets were placed on the winning outcome, all bettors are refunded.

    Emits (via print): MarketCreated, OddsUpdated, BetPlaced, MarketSettled, Claimed.
    """

    # event_id -> JSON market record
    markets: TreeMap[str, str]
    # event_id -> JSON consensus odds snapshot
    odds: TreeMap[str, str]
    # bet_id   -> JSON bet record
    bets: TreeMap[str, str]
    # event_id -> JSON list of bet_ids
    market_bets: TreeMap[str, str]
    # address  -> claimable wei balance
    balances: TreeMap[str, u256]
    # monotonic counter for collision-free bet IDs
    bet_counter: u256
    odds_api_key: str

    def __init__(self, odds_api_key: str):
        self.odds_api_key = str(odds_api_key).strip()
        self.bet_counter = u256(0)

    # ------------------------------------------------------------------ #
    #  MARKET MANAGEMENT                                                   #
    # ------------------------------------------------------------------ #

    @gl.public.write
    def create_market(
        self,
        event_id: str,
        sport: str,
        team_a: str,
        team_b: str,
        event_timestamp: str,
    ) -> None:
        """
        Register a new prediction market.

        event_id        — UUID from The Odds API
                          (discover with GET /v4/sports/{sport}/events?apiKey=...)
        sport           — e.g. 'basketball_nba', 'americanfootball_nfl', 'soccer_epl'
        team_a / team_b — canonical names exactly as returned by The Odds API
        event_timestamp — ISO-8601 string, e.g. '2025-03-15T20:00:00Z'
        """
        if event_id in self.markets:
            print(f"ERROR: Market {event_id} already exists.")
            return None

        self.markets[event_id] = json.dumps({
            "event_id":        str(event_id).strip(),
            "sport":           str(sport).strip(),
            "team_a":          str(team_a).strip(),
            "team_b":          str(team_b).strip(),
            "event_timestamp": str(event_timestamp).strip(),
            "status":          "OPEN",
            "pool_a":          0,
            "pool_b":          0,
            "pool_draw":       0,
            "result":          None,
        })
        self.market_bets[event_id] = json.dumps([])

        print(f"[MarketCreated] {event_id} | {sport} | {team_a} vs {team_b} @ {event_timestamp}")
        return None

    # ------------------------------------------------------------------ #
    #  ODDS AGGREGATION                                                    #
    # ------------------------------------------------------------------ #

    @gl.public.write
    def fetch_consensus_odds(self, event_id: str) -> None:
        """
        Queries The Odds API for h2h odds across US, UK, and EU bookmakers.
        Requires >=3 bookmakers. Each validator independently fetches and
        runs the conversion; prompt_comparative resolves consensus within
        a 1% (100 bp) tolerance on each probability.
        """
        if event_id not in self.markets:
            print(f"ERROR: Market {event_id} not found.")
            return None

        market = json.loads(self.markets[event_id])
        if market["status"] != "OPEN":
            print(f"ERROR: Market is '{market['status']}'; must be OPEN to update odds.")
            return None

        sport  = market["sport"]
        team_a = market["team_a"]
        team_b = market["team_b"]
        key    = self.odds_api_key

        # eventIds filter keeps the response small
        url = (
            f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
            f"?apiKey={key}&eventIds={event_id}"
            f"&regions=us,uk,eu&markets=h2h&oddsFormat=decimal"
        )

        def aggregate_nondet() -> str:
            try:
                raw    = gl.nondet.web.render(url, mode="text")
                events = json.loads(raw)
            except Exception as e:
                return json.dumps({"valid": False, "error": str(e)})

            if not events:
                return json.dumps({"valid": False, "error": "event not in API response"})

            task = f"""
You are a Sports Odds Analyst. Compute a consensus vig-adjusted probability line.

Game     : {team_a} vs {team_b}
Event ID : {event_id}
Raw data :
{json.dumps(events[0])[:4500]}

Steps — follow exactly:
1. Collect every bookmaker in the "bookmakers" array that has an "h2h" market.
2. For each bookmaker, read the decimal odds for "{team_a}", "{team_b}", and "Draw"
   (Draw is absent for sports that cannot tie — use 0.0 in that case).
3. Convert: implied_prob = 1 / decimal_odds
4. Average across all bookmakers:
     avg_a    = mean of all {team_a} implied probs
     avg_b    = mean of all {team_b} implied probs
     avg_draw = mean of all draw implied probs (0.0 if not present)
5. overround = avg_a + avg_b + avg_draw
6. Remove vig (normalise):
     true_a    = avg_a    / overround
     true_b    = avg_b    / overround
     true_draw = avg_draw / overround
7. Scale to int x10000: e.g. 0.5543 -> 5543
8. vig_pct = (overround - 1.0) x 100   (e.g. 5.2 means 5.2% vig)

Respond ONLY with raw JSON — no markdown fences:
{{
  "bookmaker_count" : <int>,
  "bookmakers_used" : [<str>, ...],
  "vig_pct"         : <float>,
  "team_a_prob"     : <int>,
  "team_b_prob"     : <int>,
  "draw_prob"       : <int>,
  "valid"           : true
}}
"""
            raw_llm = gl.nondet.exec_prompt(task)
            try:
                cleaned = raw_llm.replace("```json", "").replace("```", "").strip()
                parsed  = json.loads(cleaned)
                parsed["valid"] = True
                return json.dumps(parsed)
            except Exception as e:
                return json.dumps({"valid": False, "error": f"LLM parse failed: {e}"})

        # Allow ±100 bp (1%) per probability to tolerate minor float rounding
        criteria = """
Compare team_a_prob and team_b_prob (integers scaled x10000).
Return EQUAL if ALL conditions hold:
  - val_a.valid == true AND val_b.valid == true
  - abs(val_a.team_a_prob - val_b.team_a_prob) <= 100
  - abs(val_a.team_b_prob - val_b.team_b_prob) <= 100
Return DIFFERENT otherwise.
"""
        consensus_raw = gl.eq_principle.prompt_comparative(aggregate_nondet, criteria)

        try:
            r = json.loads(consensus_raw)
        except Exception as e:
            print(f"ERROR: Cannot parse consensus result: {e}")
            return None

        if not r.get("valid"):
            print(f"ERROR: Odds fetch failed — {r.get('error', 'unknown')}")
            return None

        n_books = int(r.get("bookmaker_count", 0))
        if n_books < 3:
            print(f"ERROR: Only {n_books} bookmaker(s) found; need >=3 for a valid line.")
            return None

        self.odds[event_id] = json.dumps({
            "team_a_prob":     int(r["team_a_prob"]),
            "team_b_prob":     int(r["team_b_prob"]),
            "draw_prob":       int(r.get("draw_prob", 0)),
            "vig_pct":         float(r.get("vig_pct", 0.0)),
            "bookmaker_count": n_books,
            "bookmakers_used": r.get("bookmakers_used", []),
        })

        print(
            f"[OddsUpdated] {event_id} | "
            f"{team_a} {int(r['team_a_prob']) / 100:.2f}% | "
            f"{team_b} {int(r['team_b_prob']) / 100:.2f}% | "
            f"draw {int(r.get('draw_prob', 0)) / 100:.2f}% | "
            f"vig {float(r.get('vig_pct', 0)):.2f}% | "
            f"{n_books} bookmakers: {r.get('bookmakers_used', [])}"
        )
        return None

    # ------------------------------------------------------------------ #
    #  BETTING                                                             #
    # ------------------------------------------------------------------ #

    @gl.public.write
    def bet(self, event_id: str, outcome: int, amount_wei: int, bettor: str) -> None:
        """
        Place a wager on an open market.

        outcome   : 0 = team_a wins | 1 = team_b wins | 2 = draw
        amount_wei: stake in wei-scale accounting units
        bettor    : caller address

        The consensus odds at call time are snapshotted into the bet record
        so bettors can verify the line they accepted.
        """
        if event_id not in self.markets:
            print(f"ERROR: Market {event_id} not found.")
            return None

        market = json.loads(self.markets[event_id])
        if market["status"] != "OPEN":
            print(f"ERROR: Market is '{market['status']}'; betting is closed.")
            return None

        if outcome not in (0, 1, 2):
            print("ERROR: outcome must be 0 (team_a), 1 (team_b), or 2 (draw).")
            return None

        amount = int(amount_wei)
        if amount <= 0:
            print("ERROR: amount_wei must be > 0.")
            return None

        odds_snap = json.loads(self.odds[event_id]) if event_id in self.odds else {}

        bet_id = f"{event_id}:{str(bettor).strip()}:{int(self.bet_counter)}"
        self.bet_counter = u256(int(self.bet_counter) + 1)

        outcome_map = {0: market["team_a"], 1: market["team_b"], 2: "draw"}
        self.bets[bet_id] = json.dumps({
            "bet_id":             bet_id,
            "event_id":           event_id,
            "bettor":             str(bettor).strip(),
            "outcome":            outcome,
            "outcome_name":       outcome_map[outcome],
            "amount_wei":         amount,
            "team_a_prob_at_bet": odds_snap.get("team_a_prob", 0),
            "team_b_prob_at_bet": odds_snap.get("team_b_prob", 0),
            "draw_prob_at_bet":   odds_snap.get("draw_prob",   0),
        })

        if outcome == 0:
            market["pool_a"]    += amount
        elif outcome == 1:
            market["pool_b"]    += amount
        else:
            market["pool_draw"] += amount
        self.markets[event_id] = json.dumps(market)

        bids = json.loads(self.market_bets[event_id]) if event_id in self.market_bets else []
        bids.append(bet_id)
        self.market_bets[event_id] = json.dumps(bids)

        print(
            f"[BetPlaced] {bet_id} | "
            f"bettor={bettor} outcome={outcome_map[outcome]} amount={amount}"
        )
        return None

    # ------------------------------------------------------------------ #
    #  SETTLEMENT                                                          #
    # ------------------------------------------------------------------ #

    @gl.public.write
    def settle(self, event_id: str) -> None:
        """
        Fetches the final score from The Odds API scores endpoint.
        Each validator independently calls the API and uses an LLM to
        determine the winner; prompt_comparative enforces exact agreement
        on winner_outcome before any funds move.

        Winnings: parimutuel — total pool split pro-rata among correct bettors.
        Fallback: if nobody backed the winning outcome, all stakes are refunded.
        """
        if event_id not in self.markets:
            print(f"ERROR: Market {event_id} not found.")
            return None

        market = json.loads(self.markets[event_id])
        if market["status"] != "OPEN":
            print(f"ERROR: Market is '{market['status']}'; cannot settle.")
            return None

        sport  = market["sport"]
        team_a = market["team_a"]
        team_b = market["team_b"]
        key    = self.odds_api_key

        # daysFrom=3 covers games completed in the last 3 days
        url_scores = (
            f"https://api.the-odds-api.com/v4/sports/{sport}/scores"
            f"?apiKey={key}&daysFrom=3&eventIds={event_id}"
        )

        def get_result_nondet() -> str:
            try:
                raw    = gl.nondet.web.render(url_scores, mode="text")
                scores = json.loads(raw)
            except Exception as e:
                return json.dumps({"valid": False, "error": str(e)})

            if not scores:
                return json.dumps({"valid": False, "reason": "event_not_in_scores"})

            task = f"""
You are a Sports Data Analyst. Determine the final result of this game.

Game     : {team_a} (outcome 0) vs {team_b} (outcome 1)
Event ID : {event_id}

Scores data:
{json.dumps(scores)[:3500]}

Instructions:
1. Find the entry whose "id" equals "{event_id}".
   If absent, match by both team names "{team_a}" and "{team_b}".
2. Check "completed" is true. If not, return: {{"valid": false, "reason": "not_completed"}}
3. Read numeric scores from the "scores" array.
4. Determine winner_outcome:
     {team_a} score >  {team_b} score  ->  winner_outcome = 0
     {team_b} score >  {team_a} score  ->  winner_outcome = 1
     scores equal                       ->  winner_outcome = 2  (draw)

Respond ONLY with raw JSON — no markdown fences:
{{
  "winner_outcome" : <int>,
  "team_a_score"   : <int>,
  "team_b_score"   : <int>,
  "completed"      : true,
  "valid"          : true
}}
"""
            raw_llm = gl.nondet.exec_prompt(task)
            try:
                cleaned = raw_llm.replace("```json", "").replace("```", "").strip()
                parsed  = json.loads(cleaned)
                parsed["valid"] = bool(parsed.get("valid", False))
                return json.dumps(parsed)
            except:
                return json.dumps({"valid": False, "error": "parse_failed"})

        # Exact match on winner_outcome — funds cannot move on a split decision
        criteria = """
Compare winner_outcome integers.
Return EQUAL if:
  - val_a.valid == true AND val_b.valid == true
  - val_a.winner_outcome == val_b.winner_outcome
Return DIFFERENT otherwise.
"""
        consensus_raw = gl.eq_principle.prompt_comparative(get_result_nondet, criteria)

        try:
            r = json.loads(consensus_raw)
        except Exception as e:
            print(f"ERROR: Cannot parse settlement consensus: {e}")
            return None

        if not r.get("valid"):
            reason = r.get("reason", r.get("error", "unknown"))
            print(f"ERROR: Settlement blocked — {reason}. Game may not be completed yet.")
            return None

        winner_outcome = int(r["winner_outcome"])
        outcome_names  = {0: team_a, 1: team_b, 2: "draw"}

        pool_a    = int(market.get("pool_a",    0))
        pool_b    = int(market.get("pool_b",    0))
        pool_draw = int(market.get("pool_draw", 0))
        total_pool = pool_a + pool_b + pool_draw

        if winner_outcome == 0:
            winning_pool = pool_a
        elif winner_outcome == 1:
            winning_pool = pool_b
        else:
            winning_pool = pool_draw

        bids = json.loads(self.market_bets[event_id]) if event_id in self.market_bets else []
        winners_paid = 0

        if winning_pool > 0 and total_pool > 0:
            for bid in bids:
                if bid not in self.bets:
                    continue
                bd = json.loads(self.bets[bid])
                if int(bd["outcome"]) != winner_outcome:
                    continue
                bettor = bd["bettor"]
                stake  = int(bd["amount_wei"])
                # Parimutuel: stake / winning_pool * total_pool
                payout = (stake * total_pool) // winning_pool
                prev   = int(self.balances[bettor]) if bettor in self.balances else 0
                self.balances[bettor] = u256(prev + payout)
                winners_paid += 1
        else:
            # Nobody backed the winner — refund everyone
            for bid in bids:
                if bid not in self.bets:
                    continue
                bd     = json.loads(self.bets[bid])
                bettor = bd["bettor"]
                stake  = int(bd["amount_wei"])
                prev   = int(self.balances[bettor]) if bettor in self.balances else 0
                self.balances[bettor] = u256(prev + stake)

        market["status"] = "SETTLED"
        market["result"] = {
            "winner_outcome": winner_outcome,
            "winner_name":    outcome_names.get(winner_outcome, "unknown"),
            "team_a_score":   r.get("team_a_score", 0),
            "team_b_score":   r.get("team_b_score", 0),
            "total_pool":     total_pool,
            "winning_pool":   winning_pool,
            "winners_paid":   winners_paid,
        }
        self.markets[event_id] = json.dumps(market)

        print(
            f"[MarketSettled] {event_id} | "
            f"winner={outcome_names.get(winner_outcome)} "
            f"({r.get('team_a_score','?')}-{r.get('team_b_score','?')}) | "
            f"pool={total_pool} | paid={winners_paid} winner(s)"
        )
        return None

    @gl.public.write
    def claim(self, address: str) -> None:
        """Zeroes out and logs the claimable balance for address."""
        if address not in self.balances:
            print(f"No balance for {address}.")
            return None

        amount = int(self.balances[address])
        if amount == 0:
            print(f"Zero balance for {address}.")
            return None

        self.balances[address] = u256(0)
        print(f"[Claimed] {address} -> {amount} wei")
        return None

    # ------------------------------------------------------------------ #
    #  VIEWS                                                               #
    # ------------------------------------------------------------------ #

    @gl.public.view
    def get_market(self, event_id: str) -> str:
        return self.markets[event_id] if event_id in self.markets else json.dumps({"error": "not found"})

    @gl.public.view
    def get_odds(self, event_id: str) -> str:
        return self.odds[event_id] if event_id in self.odds else json.dumps({"error": "no odds yet — call fetch_consensus_odds first"})

    @gl.public.view
    def get_bet(self, bet_id: str) -> str:
        return self.bets[bet_id] if bet_id in self.bets else json.dumps({"error": "not found"})

    @gl.public.view
    def get_balance(self, address: str) -> int:
        return int(self.balances[address]) if address in self.balances else 0

    @gl.public.view
    def list_bets_for_event(self, event_id: str) -> str:
        return self.market_bets[event_id] if event_id in self.market_bets else json.dumps([])
