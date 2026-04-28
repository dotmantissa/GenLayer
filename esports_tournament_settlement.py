# v0.1.0
# { "Depends": "py-genlayer:test" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"
ERROR_TRANSIENT = "[TRANSIENT]"

DISPUTE_WINDOW_SECONDS = 172800


@allow_storage
@dataclass
class Tournament:
    organizer: Address
    game_title: str
    tournament_id: str
    bracket_json: str
    teams_json: str
    prize_pool_wei: u256
    winner_wallet: str
    complete: bool


@allow_storage
@dataclass
class Match:
    tournament_id: str
    match_id: str
    team_a: str
    team_b: str
    winner: str
    resolved: bool
    status: str  # OPEN | DISPUTED | RESOLVED
    dispute_deadline: u256
    forfeit: bool


class EsportsTournamentSettlement(gl.Contract):
    tournaments: TreeMap[str, Tournament]
    matches: TreeMap[str, Match]                    # key: tid|mid
    balances: TreeMap[str, u256]
    yes_pool: TreeMap[str, u256]                    # key: tid|mid|team
    user_bets: TreeMap[str, u256]                   # key: tid|mid|user|team

    def __init__(self):
        pass

    def _mkey(self, tid: str, mid: str) -> str:
        return f"{tid}|{mid}"

    def _bet_pool_key(self, tid: str, mid: str, team: str) -> str:
        return f"{tid}|{mid}|{team}"

    def _user_bet_key(self, tid: str, mid: str, user: str, team: str) -> str:
        return f"{tid}|{mid}|{str(user).lower()}|{team}"

    def _now(self) -> int:
        return int(gl.block.timestamp)

    def _balance_of(self, wallet: str) -> int:
        w = str(wallet).lower()
        return int(self.balances[w]) if w in self.balances else 0

    def _set_balance(self, wallet: str, amount: int) -> None:
        self.balances[str(wallet).lower()] = u256(amount)

    def _team_wallet(self, t: Tournament, team_name: str) -> str:
        teams = json.loads(t.teams_json)
        for row in teams:
            if str(row.get("name", "")).strip() == str(team_name).strip():
                return str(row.get("wallet_address", "")).lower()
        return ""

    def _advance_bracket(self, t: Tournament, winner: str) -> None:
        bracket = json.loads(t.bracket_json)
        if not isinstance(bracket, list):
            bracket = []
        remaining = [x for x in bracket if str(x).strip() != str(winner).strip()]
        t.bracket_json = json.dumps(remaining)
        if len(remaining) == 0:
            t.complete = True
            t.winner_wallet = self._team_wallet(t, winner)

    def _fetch_match_result(self, game_title: str, tid: str, mid: str) -> dict:
        game = str(game_title).lower().strip()
        if game in {"cs2", "counter-strike 2", "counter strike 2"}:
            primary_url = f"https://open.faceit.com/data/v4/matches/{mid}"
        elif game in {"overwatch", "ow2", "overwatch 2"}:
            primary_url = f"https://api.battlefy.com/matches/{mid}"
        else:
            primary_url = f"https://api.pandascore.co/matches/{mid}"

        secondary_url = f"https://api.pandascore.co/tournaments/{tid}/matches/{mid}"

        res1 = gl.nondet.web.get(primary_url)
        res2 = gl.nondet.web.get(secondary_url)

        if res1.status >= 400 or res2.status >= 400:
            raise gl.vm.UserError(f"{ERROR_TRANSIENT} could not fetch result sources")

        d1 = json.loads(res1.body.decode("utf-8"))
        d2 = json.loads(res2.body.decode("utf-8"))

        # Normalize both sources to winner+forfeit using AI.
        prompt = f"""Given two esports match payloads, return strict JSON:
- winner: team name string
- forfeit: boolean
- confidence: float 0..1
- agree: boolean
Input1:\n{json.dumps(d1)[:7000]}\nInput2:\n{json.dumps(d2)[:7000]}
"""
        out = gl.nondet.exec_prompt(
            prompt,
            response_format={
                "type": "object",
                "properties": {
                    "winner": {"type": "string"},
                    "forfeit": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "agree": {"type": "boolean"},
                },
            },
        )

        return {
            "winner": str(out.get("winner", "")).strip(),
            "forfeit": bool(out.get("forfeit", False)),
            "confidence": float(out.get("confidence", 0.0)),
            "agree": bool(out.get("agree", False)),
        }

    @gl.public.write
    def top_up_balance(self, amount_wei: int) -> None:
        if amount_wei <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} amount_wei must be positive")
        s = str(gl.message.sender_account).lower()
        self._set_balance(s, self._balance_of(s) + int(amount_wei))

    @gl.public.write
    def create_tournament(
        self,
        game_title: str,
        tournament_id: str,
        prize_pool_wei: int,
        teams_json: str,
        bracket_json: str,
    ) -> None:
        tid = str(tournament_id).strip()
        if not tid:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} tournament_id required")
        if tid in self.tournaments:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} tournament exists")
        if prize_pool_wei <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} prize_pool_wei must be positive")

        organizer = str(gl.message.sender_account).lower()
        bal = self._balance_of(organizer)
        if bal < int(prize_pool_wei):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} insufficient organizer balance")
        self._set_balance(organizer, bal - int(prize_pool_wei))

        # Validate JSON inputs.
        teams = json.loads(teams_json)
        bracket = json.loads(bracket_json)
        if not isinstance(teams, list) or len(teams) < 2:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} at least 2 teams required")
        if not isinstance(bracket, list):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} bracket must be list")

        self.tournaments[tid] = Tournament(
            organizer=gl.message.sender_account,
            game_title=str(game_title).strip(),
            tournament_id=tid,
            bracket_json=json.dumps(bracket),
            teams_json=json.dumps(teams),
            prize_pool_wei=u256(int(prize_pool_wei)),
            winner_wallet="",
            complete=False,
        )

    @gl.public.write
    def create_match(self, tournament_id: str, match_id: str, team_a: str, team_b: str) -> None:
        tid = str(tournament_id).strip()
        mid = str(match_id).strip()
        if tid not in self.tournaments:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} tournament not found")
        key = self._mkey(tid, mid)
        if key in self.matches:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} match exists")

        self.matches[key] = Match(
            tournament_id=tid,
            match_id=mid,
            team_a=str(team_a).strip(),
            team_b=str(team_b).strip(),
            winner="",
            resolved=False,
            status="OPEN",
            dispute_deadline=u256(0),
            forfeit=False,
        )
        print(f"[MatchCreated] tournament_id={tid} match_id={mid} team_a={team_a} team_b={team_b}")

    @gl.public.write
    def bet_match(self, tournament_id: str, match_id: str, team_pick: str, amount_wei: int) -> None:
        key = self._mkey(tournament_id, match_id)
        if key not in self.matches:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} match not found")
        m = self.matches[key]
        if m.status != "OPEN":
            raise gl.vm.UserError(f"{ERROR_EXPECTED} betting closed")
        pick = str(team_pick).strip()
        if pick not in {m.team_a, m.team_b}:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} invalid team_pick")
        if amount_wei <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} amount_wei must be positive")

        user = str(gl.message.sender_account).lower()
        bal = self._balance_of(user)
        if bal < int(amount_wei):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} insufficient balance")
        self._set_balance(user, bal - int(amount_wei))

        pkey = self._bet_pool_key(tournament_id, match_id, pick)
        pool_prev = int(self.yes_pool[pkey]) if pkey in self.yes_pool else 0
        self.yes_pool[pkey] = u256(pool_prev + int(amount_wei))

        ukey = self._user_bet_key(tournament_id, match_id, user, pick)
        prev = int(self.user_bets[ukey]) if ukey in self.user_bets else 0
        self.user_bets[ukey] = u256(prev + int(amount_wei))

    @gl.public.write
    def resolve_match(self, tournament_id: str, match_id: str) -> str:
        tid = str(tournament_id).strip()
        key = self._mkey(tid, match_id)
        if tid not in self.tournaments:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} tournament not found")
        if key not in self.matches:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} match not found")

        t = self.tournaments[tid]
        m = self.matches[key]
        if m.status == "RESOLVED":
            raise gl.vm.UserError(f"{ERROR_EXPECTED} match already resolved")

        data = self._fetch_match_result(t.game_title, tid, m.match_id)
        print(
            f"[ResultFetched] tournament_id={tid} match_id={m.match_id} "
            f"winner={data['winner']} agree={data['agree']} confidence={data['confidence']}"
        )

        if not data["agree"]:
            m.status = "DISPUTED"
            m.dispute_deadline = u256(self._now() + DISPUTE_WINDOW_SECONDS)
            self.matches[key] = m
            return "DISPUTED"

        m.winner = data["winner"]
        m.forfeit = bool(data["forfeit"])
        m.resolved = True
        m.status = "RESOLVED"
        self.matches[key] = m

        # Bracket update and tournament completion.
        self._advance_bracket(t, m.winner)
        self.tournaments[tid] = t

        print(
            f"[MatchResolved] tournament_id={tid} match_id={m.match_id} winner={m.winner} forfeit={m.forfeit}"
        )

        if t.complete:
            w = str(t.winner_wallet).lower()
            if not w:
                raise gl.vm.UserError(f"{ERROR_EXPECTED} winner wallet not found")
            payout = int(t.prize_pool_wei)
            self._set_balance(w, self._balance_of(w) + payout)
            print(f"[PrizePaid] tournament_id={tid} winner_wallet={w} amount_wei={payout}")

        return "RESOLVED"

    @gl.public.write
    def challenge_result(self, tournament_id: str, match_id: str, screenshot_evidence: str) -> bool:
        key = self._mkey(tournament_id, match_id)
        if key not in self.matches:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} match not found")
        m = self.matches[key]
        if m.status != "DISPUTED":
            raise gl.vm.UserError(f"{ERROR_EXPECTED} match not in dispute")
        if self._now() > int(m.dispute_deadline):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} dispute window closed")

        prompt = f"""Analyze this screenshot OCR text and determine if it proves a different winner.
Return strict JSON:
- overrides: boolean
- winner: team name string
Evidence:\n{str(screenshot_evidence)[:12000]}
"""
        out = gl.nondet.exec_prompt(
            prompt,
            response_format={
                "type": "object",
                "properties": {
                    "overrides": {"type": "boolean"},
                    "winner": {"type": "string"},
                },
            },
        )

        if bool(out.get("overrides", False)):
            m.winner = str(out.get("winner", "")).strip()
            m.forfeit = False
            m.status = "RESOLVED"
            m.resolved = True
            self.matches[key] = m

            t = self.tournaments[str(tournament_id).strip()]
            self._advance_bracket(t, m.winner)
            self.tournaments[str(tournament_id).strip()] = t

            return True
        return False

    @gl.public.write
    def claim_match_bet_payout(self, tournament_id: str, match_id: str) -> int:
        key = self._mkey(tournament_id, match_id)
        if key not in self.matches:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} match not found")
        m = self.matches[key]
        if m.status != "RESOLVED":
            raise gl.vm.UserError(f"{ERROR_EXPECTED} match not resolved")

        user = str(gl.message.sender_account).lower()
        winner = m.winner
        if winner not in {m.team_a, m.team_b}:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} invalid winner")

        win_pool_key = self._bet_pool_key(tournament_id, match_id, winner)
        lose = m.team_b if winner == m.team_a else m.team_a
        lose_pool_key = self._bet_pool_key(tournament_id, match_id, lose)

        user_key = self._user_bet_key(tournament_id, match_id, user, winner)
        user_stake = int(self.user_bets[user_key]) if user_key in self.user_bets else 0
        if user_stake <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} no winning bet")

        win_pool = int(self.yes_pool[win_pool_key]) if win_pool_key in self.yes_pool else 0
        lose_pool = int(self.yes_pool[lose_pool_key]) if lose_pool_key in self.yes_pool else 0

        # Forfeit gives partial payout: winners get own stake + 50% of losing pool share.
        if m.forfeit:
            payout_pool = win_pool + (lose_pool // 2)
        else:
            payout_pool = win_pool + lose_pool

        payout = payout_pool * user_stake // win_pool if win_pool > 0 else 0
        if payout <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} zero payout")

        self.user_bets[user_key] = u256(0)
        self._set_balance(user, self._balance_of(user) + payout)
        return payout

    @gl.public.view
    def get_tournament(self, tournament_id: str) -> str:
        tid = str(tournament_id).strip()
        if tid not in self.tournaments:
            return json.dumps({"error": "not found"})
        t = self.tournaments[tid]
        return json.dumps(
            {
                "tournament_id": t.tournament_id,
                "game_title": t.game_title,
                "prize_pool_wei": int(t.prize_pool_wei),
                "winner_wallet": t.winner_wallet,
                "complete": bool(t.complete),
                "bracket": json.loads(t.bracket_json),
            }
        )
