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


class SportsResultConsensus(gl.Contract):
    """Settles betting outcomes using multi provider final score consensus."""

    matches: str
    next_match_id: u256

    def __init__(self):
        """Initialize state.

        Parameters:
            None.

        Returns:
            None.
        """
        self.matches = "{}"
        self.next_match_id = 1

    @gl.public.write
    def create_match_case(
        self,
        league_code: str,
        event_key: str,
        home_team: str,
        away_team: str,
        tolerance_points: int,
    ) -> str:
        """Create a score consensus case for one finished match.

        Parameters:
            league_code: League identifier such as nba nfl epl.
            event_key: Provider event id or slug.
            home_team: Home team name.
            away_team: Away team name.
            tolerance_points: Allowed provider difference in points.

        Returns:
            Case id string.
        """
        league = str(league_code).strip().lower()
        event = str(event_key).strip()
        home = str(home_team).strip()
        away = str(away_team).strip()

        if len(league) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid league_code")
        if len(event) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid event_key")
        if len(home) < 2 or len(away) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid team name")
        if tolerance_points < 0 or tolerance_points > 10:
            _raise_user_error(f"{ERROR_EXPECTED} tolerance_points out of range")

        cid = str(self.next_match_id)
        self.next_match_id += 1

        matches = json.loads(self.matches)
        matches[cid] = {
            "case_id": cid,
            "requester": str(gl.message.sender_account),
            "league_code": league,
            "event_key": event,
            "home_team": home,
            "away_team": away,
            "tolerance_points": int(tolerance_points),
            "status": "PENDING",
            "home_score": -1,
            "away_score": -1,
            "winner": "",
            "consensus_sources": 0,
            "reason": "",
            "resolved_at": "",
        }
        self.matches = json.dumps(matches)
        return cid

    @gl.public.write
    def resolve_match_case(self, case_id: str) -> str:
        """Resolve match result using ESPN and official style feed consensus.

        Parameters:
            case_id: Match case id.

        Returns:
            Winner team name or DRAW.
        """
        matches = json.loads(self.matches)
        key = str(case_id)
        if key not in matches:
            _raise_user_error(f"{ERROR_EXPECTED} case not found")

        m = matches[key]
        if m["status"] != "PENDING":
            _raise_user_error(f"{ERROR_EXPECTED} case already resolved")

        def fetch_and_consensus() -> str:
            espn_url = f"https://site.api.espn.com/apis/site/v2/sports/{m['league_code']}/scoreboard"
            official_url = f"https://api.sportsdata.io/v3/{m['league_code']}/scores/json/Games"
            backup_url = f"https://www.thesportsdb.com/api/v1/json/3/searchfilename.php?e={m['event_key']}"

            espn = gl.nondet.web.get(espn_url)
            official = gl.nondet.web.get(official_url)
            backup = gl.nondet.web.get(backup_url)

            for name, res in [("espn", espn), ("official", official), ("backup", backup)]:
                status = int(res.status)
                if status >= 400 and status < 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} client error: {status}")
                if status >= 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} server error: {status}")

            b1 = espn.body.decode("utf-8") if espn.body is not None else ""
            b2 = official.body.decode("utf-8") if official.body is not None else ""
            b3 = backup.body.decode("utf-8") if backup.body is not None else ""
            if len((b1 + b2 + b3).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty score payload")

            prompt = f"""
You are a sports result verifier.
Find final score and winner from three providers for one event.
Return JSON only.

Event key: {m['event_key']}
Home team: {m['home_team']}
Away team: {m['away_team']}
Allowed score tolerance: {m['tolerance_points']}

Rules:
1) Extract candidate final scores from each source.
2) Build consensus score if at least two sources align within tolerance.
3) consensus_sources is number of aligned sources.
4) winner is home team away team or DRAW.

Return exactly:
{{
  "home_score": int,
  "away_score": int,
  "winner": "HOME_or_AWAY_or_DRAW",
  "consensus_sources": int,
  "reason": "string"
}}

Inputs:
{json.dumps({"espn": b1[:5000], "official": b2[:5000], "backup": b3[:5000]})}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            hs = int(parsed.get("home_score", -1))
            as_ = int(parsed.get("away_score", -1))
            if hs < 0 or as_ < 0:
                _raise_user_error(f"{ERROR_EXTERNAL} invalid parsed score")

            winner = str(parsed.get("winner", "DRAW")).strip().upper()
            if winner not in ["HOME", "AWAY", "DRAW"]:
                winner = "DRAW"

            aligned = int(parsed.get("consensus_sources", 0))
            aligned = max(0, min(3, aligned))
            if aligned < 2:
                _raise_user_error(f"{ERROR_EXTERNAL} insufficient source consensus")

            reason = str(parsed.get("reason", ""))[:500]

            return json.dumps(
                {
                    "home_score": hs,
                    "away_score": as_,
                    "winner": winner,
                    "consensus_sources": aligned,
                    "reason": reason,
                }
            )

        principle = "Equivalent when winner matches and both scores differ by at most tolerance_points."
        out_json = _run_prompt_consensus(fetch_and_consensus, principle)
        out = json.loads(out_json)

        m["home_score"] = int(out.get("home_score", -1))
        m["away_score"] = int(out.get("away_score", -1))
        w = str(out.get("winner", "DRAW")).upper()
        m["consensus_sources"] = int(out.get("consensus_sources", 0))
        m["reason"] = str(out.get("reason", ""))
        m["status"] = "RESOLVED"
        m["resolved_at"] = str(gl.block.timestamp)

        if w == "HOME":
            m["winner"] = str(m["home_team"])
        elif w == "AWAY":
            m["winner"] = str(m["away_team"])
        else:
            m["winner"] = "DRAW"

        matches[key] = m
        self.matches = json.dumps(matches)
        return m["winner"]

    @gl.public.view
    def get_match_case(self, case_id: str) -> str:
        """Read one match case.

        Parameters:
            case_id: Case id.

        Returns:
            Case JSON string.
        """
        matches = json.loads(self.matches)
        key = str(case_id)
        if key not in matches:
            _raise_user_error(f"{ERROR_EXPECTED} case not found")
        return json.dumps(matches[key])

    @gl.public.view
    def get_all_match_cases(self) -> str:
        """Read all match cases.

        Parameters:
            None.

        Returns:
            Cases map JSON string.
        """
        return self.matches
