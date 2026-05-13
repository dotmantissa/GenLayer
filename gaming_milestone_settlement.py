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


class GamingMilestoneSettlement(gl.Contract):
    """Escrows and releases tournament prizes after platform milestone verification."""

    cases: str
    balances: str
    next_case_id: u256

    def __init__(self):
        """Initialize contract state.

        Parameters:
            None.

        Returns:
            None.
        """
        self.cases = "{}"
        self.balances = "{}"
        self.next_case_id = 1

    def _get_balance(self, wallet: str) -> int:
        data = json.loads(self.balances)
        return int(data.get(str(wallet).lower(), 0))

    def _set_balance(self, wallet: str, amount: int) -> None:
        data = json.loads(self.balances)
        data[str(wallet).lower()] = int(amount)
        self.balances = json.dumps(data)

    @gl.public.write
    def top_up_balance(self, amount: int) -> None:
        """Increase caller internal balance.

        Parameters:
            amount: Positive amount to add.

        Returns:
            None.
        """
        if amount <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} amount must be positive")
        sender = str(gl.message.sender_account).lower()
        self._set_balance(sender, self._get_balance(sender) + int(amount))

    @gl.public.view
    def balance_of(self, wallet: str) -> int:
        """Read internal balance.

        Parameters:
            wallet: Wallet address string.

        Returns:
            Integer balance.
        """
        return self._get_balance(str(wallet))

    @gl.public.write
    def create_case(
        self,
        platform: str,
        game_id: str,
        player_id: str,
        milestone_type: str,
        target_value: int,
        payout_amount: int,
        recipient_wallet: str,
    ) -> str:
        """Create a milestone verification case and escrow payout.

        Parameters:
            platform: steam xbox or playstation.
            game_id: Game title identifier.
            player_id: Platform player identifier.
            milestone_type: achievements ranking or leaderboard_score.
            target_value: Required threshold value.
            payout_amount: Amount paid when milestone is met.
            recipient_wallet: Beneficiary wallet address.

        Returns:
            Case id string.
        """
        p = str(platform).strip().lower()
        g = str(game_id).strip()
        player = str(player_id).strip()
        m = str(milestone_type).strip().lower()
        recipient = str(recipient_wallet).strip().lower()

        if p not in ["steam", "xbox", "playstation"]:
            _raise_user_error(f"{ERROR_EXPECTED} unsupported platform")
        if len(g) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid game_id")
        if len(player) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid player_id")
        if m not in ["achievements", "ranking", "leaderboard_score"]:
            _raise_user_error(f"{ERROR_EXPECTED} invalid milestone_type")
        if target_value <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} target_value must be positive")
        if payout_amount <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} payout_amount must be positive")
        if len(recipient) < 10:
            _raise_user_error(f"{ERROR_EXPECTED} invalid recipient_wallet")

        sponsor = str(gl.message.sender_account).lower()
        sponsor_balance = self._get_balance(sponsor)
        if sponsor_balance < payout_amount:
            _raise_user_error(f"{ERROR_EXPECTED} insufficient sponsor balance")
        self._set_balance(sponsor, sponsor_balance - int(payout_amount))

        case_id = str(self.next_case_id)
        self.next_case_id += 1

        cases = json.loads(self.cases)
        cases[case_id] = {
            "case_id": case_id,
            "sponsor": sponsor,
            "platform": p,
            "game_id": g,
            "player_id": player,
            "milestone_type": m,
            "target_value": int(target_value),
            "payout_amount": int(payout_amount),
            "recipient_wallet": recipient,
            "status": "PENDING",
            "milestone_met": False,
            "measured_value": 0,
            "consensus_sources": 0,
            "reason": "",
            "resolved_at": "",
        }
        self.cases = json.dumps(cases)
        return case_id

    @gl.public.write
    def resolve_case(self, case_id: str) -> str:
        """Resolve milestone outcome and settle escrow.

        Parameters:
            case_id: Case id string.

        Returns:
            Settlement status PAID or NOT_MET.
        """
        cases = json.loads(self.cases)
        key = str(case_id)
        if key not in cases:
            _raise_user_error(f"{ERROR_EXPECTED} case not found")
        c = cases[key]
        if c["status"] != "PENDING":
            _raise_user_error(f"{ERROR_EXPECTED} case already resolved")

        def fetch_and_verify() -> str:
            if c["platform"] == "steam":
                primary_url = f"https://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v1/?appid={c['game_id']}&steamid={c['player_id']}"
                backup_url = f"https://api.steampowered.com/ISteamUserStats/GetUserStatsForGame/v2/?appid={c['game_id']}&steamid={c['player_id']}"
            elif c["platform"] == "xbox":
                primary_url = f"https://profile.xboxlive.com/users/gt({c['player_id']})/stats?titleId={c['game_id']}"
                backup_url = f"https://achievements.xboxlive.com/users/xuid({c['player_id']})/achievements"
            else:
                primary_url = f"https://m.np.playstation.com/api/trophy/v1/users/{c['player_id']}/npCommunicationIds/{c['game_id']}/trophyGroups/all/trophies"
                backup_url = f"https://m.np.playstation.com/api/userProfile/v1/internal/users/{c['player_id']}/profile2"

            r1 = gl.nondet.web.get(primary_url)
            r2 = gl.nondet.web.get(backup_url)

            for name, res in [("primary", r1), ("backup", r2)]:
                status = int(res.status)
                if status >= 400 and status < 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} client error: {status}")
                if status >= 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} server error: {status}")

            b1 = r1.body.decode("utf-8") if r1.body is not None else ""
            b2 = r2.body.decode("utf-8") if r2.body is not None else ""
            if len((b1 + b2).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty platform payload")

            prompt = f"""
You are a gaming milestone adjudicator.
Return JSON only.

Case data:
- platform: {c['platform']}
- game_id: {c['game_id']}
- player_id: {c['player_id']}
- milestone_type: {c['milestone_type']}
- target_value: {c['target_value']}

Rules:
1) Parse both sources and extract a measured value for the requested milestone type.
2) milestone_met is true when measured value is greater than or equal to target value.
3) consensus_sources is number of sources that support the same measurement direction.

Return exactly:
{{
  "milestone_met": bool,
  "measured_value": int,
  "consensus_sources": int,
  "reason": "string"
}}

Inputs:
{json.dumps({"primary": b1[:7000], "backup": b2[:7000]})}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            measured = int(parsed.get("measured_value", 0))
            if measured < 0:
                _raise_user_error(f"{ERROR_EXTERNAL} invalid measured_value")

            met = bool(parsed.get("milestone_met", False))
            expected_met = measured >= int(c["target_value"])
            met = bool(met or expected_met)

            sources = int(parsed.get("consensus_sources", 0))
            sources = max(0, min(2, sources))
            if sources < 1:
                _raise_user_error(f"{ERROR_EXTERNAL} insufficient source consensus")

            return json.dumps(
                {
                    "milestone_met": met,
                    "measured_value": measured,
                    "consensus_sources": sources,
                    "reason": str(parsed.get("reason", ""))[:500],
                }
            )

        principle = "Equivalent when milestone_met matches and measured_value differs by at most 5 percent."
        result_json = _run_prompt_consensus(fetch_and_verify, principle)
        result = json.loads(result_json)

        c["milestone_met"] = bool(result.get("milestone_met", False))
        c["measured_value"] = int(result.get("measured_value", 0))
        c["consensus_sources"] = int(result.get("consensus_sources", 0))
        c["reason"] = str(result.get("reason", ""))
        c["resolved_at"] = str(gl.block.timestamp)

        if c["milestone_met"]:
            recipient = str(c["recipient_wallet"]).lower()
            self._set_balance(recipient, self._get_balance(recipient) + int(c["payout_amount"]))
            c["status"] = "PAID"
        else:
            sponsor = str(c["sponsor"]).lower()
            self._set_balance(sponsor, self._get_balance(sponsor) + int(c["payout_amount"]))
            c["status"] = "NOT_MET"

        cases[key] = c
        self.cases = json.dumps(cases)
        return c["status"]

    @gl.public.view
    def get_case(self, case_id: str) -> str:
        """Read one case by id.

        Parameters:
            case_id: Case id string.

        Returns:
            Case JSON string.
        """
        cases = json.loads(self.cases)
        key = str(case_id)
        if key not in cases:
            _raise_user_error(f"{ERROR_EXPECTED} case not found")
        return json.dumps(cases[key])

    @gl.public.view
    def get_all_cases(self) -> str:
        """Read all cases.

        Parameters:
            None.

        Returns:
            Cases map JSON string.
        """
        return self.cases
