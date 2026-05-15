# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"


def _raise_user_error(message: str) -> None:
    err_cls = getattr(gl, "UserError", Exception)
    raise err_cls(message)


def _sender() -> str:
    sender_address = getattr(gl.message, "sender_address", None)
    if sender_address is not None:
        return str(sender_address)
    sender_account = getattr(gl.message, "sender_account", None)
    if sender_account is not None:
        return str(sender_account)
    return ""


class TemporalWebReplayValidator(gl.Contract):
    """Replays historical web responses to measure temporal robustness drift."""

    reports: str
    next_report_id: u256
    scenarios: str
    next_scenario_id: u256

    def __init__(self):
        """Initialize replay validator state.

        Parameters:
            None.

        Returns:
            None.
        """
        self.reports = "{}"
        self.next_report_id = 1
        self.scenarios = "{}"
        self.next_scenario_id = 1

    @gl.public.write
    def create_scenario(
        self,
        name: str,
        verdict_json_key: str,
        baseline_expected: str,
    ) -> str:
        """Create a temporal replay scenario.

        Parameters:
            name: Human readable scenario name.
            verdict_json_key: JSON key extracted from each historical response.
            baseline_expected: Expected stable verdict string.

        Returns:
            Scenario id string.
        """
        clean_name = str(name).strip()
        verdict_key = str(verdict_json_key).strip()
        baseline = str(baseline_expected).strip().lower()

        if len(clean_name) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid name")
        if len(verdict_key) < 1:
            _raise_user_error(f"{ERROR_EXPECTED} invalid verdict_json_key")
        if len(baseline) < 1:
            _raise_user_error(f"{ERROR_EXPECTED} invalid baseline_expected")

        sid = str(self.next_scenario_id)
        self.next_scenario_id += 1

        scenarios = json.loads(self.scenarios)
        scenarios[sid] = {
            "scenario_id": sid,
            "name": clean_name,
            "verdict_json_key": verdict_key,
            "baseline_expected": baseline,
            "points": [],
            "created_by": _sender(),
            "created_at": str(gl.block.timestamp),
        }
        self.scenarios = json.dumps(scenarios)
        return sid

    @gl.public.write
    def add_historical_point(
        self,
        scenario_id: str,
        label: str,
        web_response_json: str,
    ) -> None:
        """Attach a historical web response snapshot to a scenario.

        Parameters:
            scenario_id: Existing scenario identifier.
            label: Time or source label for this replay point.
            web_response_json: JSON object string of historical response.

        Returns:
            None.
        """
        sid = str(scenario_id).strip()
        clean_label = str(label).strip()
        if len(sid) < 1:
            _raise_user_error(f"{ERROR_EXPECTED} invalid scenario_id")
        if len(clean_label) < 1:
            _raise_user_error(f"{ERROR_EXPECTED} invalid label")

        try:
            payload = json.loads(web_response_json)
        except Exception:
            _raise_user_error(f"{ERROR_EXPECTED} invalid web_response_json")
        if not isinstance(payload, dict):
            _raise_user_error(f"{ERROR_EXPECTED} web_response_json must be object")

        scenarios = json.loads(self.scenarios)
        if sid not in scenarios:
            _raise_user_error(f"{ERROR_EXPECTED} scenario not found")

        points = scenarios[sid].get("points", [])
        points.append(
            {
                "label": clean_label,
                "response": payload,
                "added_at": str(gl.block.timestamp),
            }
        )
        scenarios[sid]["points"] = points
        self.scenarios = json.dumps(scenarios)

    @gl.public.write
    def run_replay(self, scenario_id: str) -> str:
        """Replay all historical points and measure verdict drift.

        Parameters:
            scenario_id: Existing scenario identifier.

        Returns:
            Report id string.
        """
        sid = str(scenario_id).strip()
        scenarios = json.loads(self.scenarios)
        if sid not in scenarios:
            _raise_user_error(f"{ERROR_EXPECTED} scenario not found")

        scenario = scenarios[sid]
        verdict_key = str(scenario.get("verdict_json_key", ""))
        baseline = str(scenario.get("baseline_expected", "")).strip().lower()
        points = scenario.get("points", [])

        if len(points) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} insufficient historical points")

        outcomes = []
        mismatch_count = 0
        missing_key_count = 0
        verdict_set = set()

        for i in range(len(points)):
            p = points[i]
            label = str(p.get("label", f"point_{i}"))
            response = p.get("response", {})
            if not isinstance(response, dict):
                _raise_user_error(f"{ERROR_EXPECTED} corrupted scenario point")

            if verdict_key not in response:
                missing_key_count += 1
                outcomes.append(
                    {
                        "label": label,
                        "status": "missing_verdict_key",
                        "verdict": "",
                        "matches_baseline": False,
                    }
                )
                continue

            verdict = str(response.get(verdict_key, "")).strip().lower()
            verdict_set.add(verdict)
            matches = verdict == baseline
            if not matches:
                mismatch_count += 1

            outcomes.append(
                {
                    "label": label,
                    "status": "ok",
                    "verdict": verdict,
                    "matches_baseline": bool(matches),
                }
            )

        valid_points = len(points) - missing_key_count
        drift_rate_bps = 0
        if valid_points > 0:
            drift_rate_bps = int((int(mismatch_count) * 10000) / int(valid_points))

        distinct_verdict_count = len(verdict_set)
        temporal_brittle = False
        if drift_rate_bps >= 2500 or distinct_verdict_count >= 3 or missing_key_count > 0:
            temporal_brittle = True

        rid = str(self.next_report_id)
        self.next_report_id += 1

        report = {
            "report_id": rid,
            "scenario_id": sid,
            "scenario_name": str(scenario.get("name", "")),
            "requested_by": _sender(),
            "baseline_expected": baseline,
            "total_points": len(points),
            "valid_points": valid_points,
            "mismatch_count": mismatch_count,
            "missing_key_count": missing_key_count,
            "distinct_verdict_count": distinct_verdict_count,
            "drift_rate_bps": drift_rate_bps,
            "temporal_brittle": bool(temporal_brittle),
            "outcomes": outcomes,
            "created_at": str(gl.block.timestamp),
        }

        reports = json.loads(self.reports)
        reports[rid] = report
        self.reports = json.dumps(reports)
        return rid

    @gl.public.view
    def get_report(self, report_id: str) -> str:
        """Read one replay report.

        Parameters:
            report_id: Report identifier.

        Returns:
            JSON report string.
        """
        key = str(report_id).strip()
        reports = json.loads(self.reports)
        if key not in reports:
            _raise_user_error(f"{ERROR_EXPECTED} report not found")
        return json.dumps(reports[key])

    @gl.public.view
    def get_all_reports(self) -> str:
        """Read all replay reports.

        Parameters:
            None.

        Returns:
            JSON object string mapping report ids to reports.
        """
        return self.reports

    @gl.public.view
    def get_scenario(self, scenario_id: str) -> str:
        """Read one scenario including historical points.

        Parameters:
            scenario_id: Scenario identifier.

        Returns:
            JSON scenario string.
        """
        sid = str(scenario_id).strip()
        scenarios = json.loads(self.scenarios)
        if sid not in scenarios:
            _raise_user_error(f"{ERROR_EXPECTED} scenario not found")
        return json.dumps(scenarios[sid])
