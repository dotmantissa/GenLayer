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


class DaoGovernanceHealth(gl.Contract):
    """Assesses DAO governance participation quality and coalition capture risk."""

    analyses: str
    next_analysis_id: u256
    health_by_dao: str

    def __init__(self):
        """Initialize contract state.

        Parameters:
            None.

        Returns:
            None.
        """
        self.analyses = "{}"
        self.next_analysis_id = 1
        self.health_by_dao = "{}"

    @gl.public.write
    def create_analysis(
        self,
        dao_slug: str,
        chain: str,
        lookback_days: int,
        min_unique_voters: int,
        max_top_delegate_share_pct: int,
    ) -> str:
        """Create a governance participation health analysis request.

        Parameters:
            dao_slug: DAO identifier slug.
            chain: Network name string.
            lookback_days: Number of days to evaluate.
            min_unique_voters: Minimum unique voter breadth threshold.
            max_top_delegate_share_pct: Maximum acceptable top delegate vote share percent.

        Returns:
            Analysis id string.
        """
        slug = str(dao_slug).strip().lower()
        chain_val = str(chain).strip().lower()

        if len(slug) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid dao slug")
        if len(chain_val) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid chain")
        if lookback_days <= 0 or lookback_days > 365:
            _raise_user_error(f"{ERROR_EXPECTED} lookback_days out of range")
        if min_unique_voters <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} min_unique_voters must be positive")
        if max_top_delegate_share_pct < 1 or max_top_delegate_share_pct > 100:
            _raise_user_error(f"{ERROR_EXPECTED} max_top_delegate_share_pct out of range")

        analysis_id = str(self.next_analysis_id)
        self.next_analysis_id += 1

        analyses = json.loads(self.analyses)
        analyses[analysis_id] = {
            "analysis_id": analysis_id,
            "creator": str(gl.message.sender_account),
            "dao_slug": slug,
            "chain": chain_val,
            "lookback_days": int(lookback_days),
            "min_unique_voters": int(min_unique_voters),
            "max_top_delegate_share_pct": int(max_top_delegate_share_pct),
            "status": "PENDING",
            "health": "",
            "unique_voters": 0,
            "top_delegate_share_pct": 0,
            "coalition_capture_risk": "",
            "reason": "",
            "resolved_at": "",
        }
        self.analyses = json.dumps(analyses)
        return analysis_id

    @gl.public.write
    def resolve_analysis(self, analysis_id: str) -> str:
        """Resolve governance health analysis using Tally Snapshot and LLM synthesis.

        Parameters:
            analysis_id: Analysis id string.

        Returns:
            Health label string.
        """
        analyses = json.loads(self.analyses)
        key = str(analysis_id)
        if key not in analyses:
            _raise_user_error(f"{ERROR_EXPECTED} analysis not found")

        a = analyses[key]
        if a["status"] != "PENDING":
            _raise_user_error(f"{ERROR_EXPECTED} analysis already resolved")

        tally_url = f"https://api.tally.xyz/query/{a['dao_slug']}?chain={a['chain']}&days={a['lookback_days']}"
        snapshot_url = f"https://hub.snapshot.org/graphql?dao={a['dao_slug']}&days={a['lookback_days']}"

        def fetch_and_score() -> str:
            t = gl.nondet.web.get(tally_url)
            s = gl.nondet.web.get(snapshot_url)

            if int(t.status) >= 400 and int(t.status) < 500:
                _raise_user_error(f"{ERROR_EXTERNAL} tally client error: {t.status}")
            if int(t.status) >= 500:
                _raise_user_error(f"{ERROR_EXTERNAL} tally server error: {t.status}")
            if int(s.status) >= 400 and int(s.status) < 500:
                _raise_user_error(f"{ERROR_EXTERNAL} snapshot client error: {s.status}")
            if int(s.status) >= 500:
                _raise_user_error(f"{ERROR_EXTERNAL} snapshot server error: {s.status}")

            t_body = t.body.decode("utf-8") if t.body is not None else ""
            s_body = s.body.decode("utf-8") if s.body is not None else ""
            if len((t_body + s_body).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty governance payload")

            prompt = f"""
You are a governance health analyst.
Assess whether participation is broad or captured by a small coalition.
Return JSON only.

Thresholds:
- min_unique_voters: {a['min_unique_voters']}
- max_top_delegate_share_pct: {a['max_top_delegate_share_pct']}

Rules:
1) Extract unique_voters and top_delegate_share_pct from data.
2) Evaluate coalition capture risk from voting behavior concentration.
3) health must be one of HEALTHY WATCHLIST CAPTURED.
4) If either threshold fails strongly then health should degrade.

Return exactly:
{{
  "health": "HEALTHY_or_WATCHLIST_or_CAPTURED",
  "unique_voters": int,
  "top_delegate_share_pct": int,
  "coalition_capture_risk": "LOW_or_MEDIUM_or_HIGH",
  "reason": "string"
}}

Payload:
{json.dumps({"tally": t_body[:5000], "snapshot": s_body[:5000]})}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            unique_voters = int(parsed.get("unique_voters", 0))
            top_share = int(parsed.get("top_delegate_share_pct", 100))

            health = str(parsed.get("health", "WATCHLIST")).strip().upper()
            if health not in ["HEALTHY", "WATCHLIST", "CAPTURED"]:
                health = "WATCHLIST"

            if unique_voters < int(a["min_unique_voters"]) and top_share > int(a["max_top_delegate_share_pct"]):
                health = "CAPTURED"
            elif unique_voters >= int(a["min_unique_voters"]) and top_share <= int(a["max_top_delegate_share_pct"]):
                health = "HEALTHY"

            risk = str(parsed.get("coalition_capture_risk", "MEDIUM")).strip().upper()
            if risk not in ["LOW", "MEDIUM", "HIGH"]:
                risk = "MEDIUM"

            return json.dumps(
                {
                    "health": health,
                    "unique_voters": unique_voters,
                    "top_delegate_share_pct": top_share,
                    "coalition_capture_risk": risk,
                    "reason": str(parsed.get("reason", ""))[:500],
                }
            )

        principle = "Equivalent when health matches and metric values differ by at most 20 percent."
        result_json = _run_prompt_consensus(fetch_and_score, principle)
        result = json.loads(result_json)

        a["health"] = str(result.get("health", "WATCHLIST"))
        a["unique_voters"] = int(result.get("unique_voters", 0))
        a["top_delegate_share_pct"] = int(result.get("top_delegate_share_pct", 100))
        a["coalition_capture_risk"] = str(result.get("coalition_capture_risk", "MEDIUM"))
        a["reason"] = str(result.get("reason", ""))
        a["resolved_at"] = str(gl.block.timestamp)
        a["status"] = "RESOLVED"

        analyses[key] = a
        self.analyses = json.dumps(analyses)

        latest = json.loads(self.health_by_dao)
        latest[str(a["dao_slug"]).lower()] = a["health"]
        self.health_by_dao = json.dumps(latest)

        return a["health"]

    @gl.public.view
    def get_analysis(self, analysis_id: str) -> str:
        """Read one analysis.

        Parameters:
            analysis_id: Analysis id string.

        Returns:
            Analysis JSON string.
        """
        analyses = json.loads(self.analyses)
        key = str(analysis_id)
        if key not in analyses:
            _raise_user_error(f"{ERROR_EXPECTED} analysis not found")
        return json.dumps(analyses[key])

    @gl.public.view
    def get_all_analyses(self) -> str:
        """Read all analyses.

        Parameters:
            None.

        Returns:
            Analyses JSON map.
        """
        return self.analyses

    @gl.public.view
    def get_latest_health(self, dao_slug: str) -> str:
        """Read latest health label for a dao.

        Parameters:
            dao_slug: DAO slug string.

        Returns:
            HEALTHY WATCHLIST CAPTURED or UNKNOWN.
        """
        latest = json.loads(self.health_by_dao)
        return str(latest.get(str(dao_slug).strip().lower(), "UNKNOWN"))
