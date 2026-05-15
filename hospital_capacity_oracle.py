# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM = "[LLM_ERROR]"


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


class HospitalCapacityOracle(gl.Contract):
    """Parses hospital occupancy and ICU capacity from public dashboards."""

    owner: Address
    occupancy_alert_bps: u256
    icu_alert_bps: u256
    reports: str
    next_report_id: u256

    def __init__(self, occupancy_alert_bps: int, icu_alert_bps: int):
        """Initialize alert thresholds.

        Parameters:
            occupancy_alert_bps: Alert threshold for bed occupancy in basis points.
            icu_alert_bps: Alert threshold for ICU occupancy in basis points.

        Returns:
            None.
        """
        if occupancy_alert_bps < 1 or occupancy_alert_bps > 10000:
            _raise_user_error(f"{ERROR_EXPECTED} occupancy_alert_bps out of range")
        if icu_alert_bps < 1 or icu_alert_bps > 10000:
            _raise_user_error(f"{ERROR_EXPECTED} icu_alert_bps out of range")

        self.owner = Address(_sender())
        self.occupancy_alert_bps = int(occupancy_alert_bps)
        self.icu_alert_bps = int(icu_alert_bps)
        self.reports = "{}"
        self.next_report_id = 1

    def _require_owner(self) -> None:
        if _sender().strip().lower() != str(self.owner).strip().lower():
            _raise_user_error(f"{ERROR_EXPECTED} only owner")

    def _fetch_dashboard(self, url: str) -> str:
        response = gl.nondet.web.get(url)
        status = int(response.status)
        if status >= 400 and status < 500:
            _raise_user_error(f"{ERROR_EXTERNAL} dashboard client error {status}")
        if status >= 500:
            _raise_user_error(f"{ERROR_TRANSIENT} dashboard server error {status}")
        text = response.body.decode("utf-8") if response.body is not None else ""
        if len(text.strip()) < 20:
            _raise_user_error(f"{ERROR_EXTERNAL} dashboard text too short")
        return text

    def _parse_metrics(self, page_text: str, state_code: str) -> dict:
        prompt = f"""
Extract hospital capacity metrics from this state dashboard text.
Return JSON with keys:
- bed_occupied (integer)
- bed_total (integer)
- icu_occupied (integer)
- icu_total (integer)
- source_note (string)

State: {state_code}
Dashboard text:
{page_text}
"""
        result = gl.nondet.exec_prompt(prompt, response_format="json")
        if not isinstance(result, dict):
            _raise_user_error(f"{ERROR_LLM} non-dict response")

        def parse_int(key: str) -> int:
            raw = result.get(key, 0)
            try:
                return int(round(float(str(raw).strip())))
            except Exception:
                _raise_user_error(f"{ERROR_LLM} invalid {key}")
            return 0

        bed_occupied = parse_int("bed_occupied")
        bed_total = parse_int("bed_total")
        icu_occupied = parse_int("icu_occupied")
        icu_total = parse_int("icu_total")

        if bed_total <= 0 or icu_total <= 0:
            _raise_user_error(f"{ERROR_LLM} invalid totals")
        if bed_occupied < 0 or bed_occupied > bed_total:
            _raise_user_error(f"{ERROR_LLM} invalid bed occupancy")
        if icu_occupied < 0 or icu_occupied > icu_total:
            _raise_user_error(f"{ERROR_LLM} invalid icu occupancy")

        bed_bps = int((int(bed_occupied) * 10000) / int(bed_total))
        icu_bps = int((int(icu_occupied) * 10000) / int(icu_total))

        return {
            "bed_occupied": bed_occupied,
            "bed_total": bed_total,
            "icu_occupied": icu_occupied,
            "icu_total": icu_total,
            "bed_occupancy_bps": bed_bps,
            "icu_occupancy_bps": icu_bps,
            "source_note": str(result.get("source_note", "")).strip(),
        }

    def _risk_bucket(self, bed_bps: int, icu_bps: int) -> int:
        score = 0
        if bed_bps >= int(self.occupancy_alert_bps):
            score += 1
        if icu_bps >= int(self.icu_alert_bps):
            score += 1
        return score

    @gl.public.write
    def set_thresholds(self, occupancy_alert_bps: int, icu_alert_bps: int) -> None:
        """Update alert thresholds.

        Parameters:
            occupancy_alert_bps: Bed occupancy threshold in basis points.
            icu_alert_bps: ICU occupancy threshold in basis points.

        Returns:
            None.
        """
        self._require_owner()
        if occupancy_alert_bps < 1 or occupancy_alert_bps > 10000:
            _raise_user_error(f"{ERROR_EXPECTED} occupancy_alert_bps out of range")
        if icu_alert_bps < 1 or icu_alert_bps > 10000:
            _raise_user_error(f"{ERROR_EXPECTED} icu_alert_bps out of range")
        self.occupancy_alert_bps = int(occupancy_alert_bps)
        self.icu_alert_bps = int(icu_alert_bps)

    @gl.public.write
    def capture_capacity(self, state_code: str, dashboard_url: str) -> str:
        """Capture and normalize state hospital capacity metrics.

        Parameters:
            state_code: Two letter state code.
            dashboard_url: Public dashboard URL.

        Returns:
            Report id string.
        """
        state = str(state_code).strip().upper()
        url = str(dashboard_url).strip()
        if len(state) != 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid state_code")
        if not (url.startswith("https://") or url.startswith("http://")):
            _raise_user_error(f"{ERROR_EXPECTED} invalid dashboard_url")

        def leader_fn():
            page_text = self._fetch_dashboard(url)
            metrics = self._parse_metrics(page_text, state)
            bucket = self._risk_bucket(int(metrics["bed_occupancy_bps"]), int(metrics["icu_occupancy_bps"]))
            return {"state": state, "url": url, "metrics": metrics, "bucket": bucket}

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                try:
                    leader_fn()
                    return False
                except Exception as e:
                    leader_msg = leaders_res.message if hasattr(leaders_res, "message") else ""
                    validator_msg = str(e)
                    if validator_msg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
                        return True
                    return False

            validator_out = leader_fn()
            leader_out = leaders_res.calldata
            return int(validator_out.get("bucket", -1)) == int(leader_out.get("bucket", -2))

        out = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        rid = str(self.next_report_id)
        self.next_report_id += 1
        metrics = out["metrics"]
        bucket = int(out["bucket"])

        reports = json.loads(self.reports)
        reports[rid] = {
            "report_id": rid,
            "requester": _sender(),
            "state": out["state"],
            "dashboard_url": out["url"],
            "metrics": metrics,
            "risk_bucket": bucket,
            "bed_alert": bucket >= 1 and int(metrics["bed_occupancy_bps"]) >= int(self.occupancy_alert_bps),
            "icu_alert": bucket >= 1 and int(metrics["icu_occupancy_bps"]) >= int(self.icu_alert_bps),
            "created_at": str(gl.block.timestamp),
        }
        self.reports = json.dumps(reports)
        return rid

    @gl.public.view
    def get_report(self, report_id: str) -> str:
        """Read one captured capacity report.

        Parameters:
            report_id: Report identifier.

        Returns:
            Report JSON string.
        """
        key = str(report_id).strip()
        reports = json.loads(self.reports)
        if key not in reports:
            _raise_user_error(f"{ERROR_EXPECTED} report not found")
        return json.dumps(reports[key])

    @gl.public.view
    def get_all_reports(self) -> str:
        """Read all captured reports.

        Parameters:
            None.

        Returns:
            JSON map of reports.
        """
        return self.reports

    @gl.public.view
    def get_thresholds(self) -> str:
        """Read alert thresholds.

        Parameters:
            None.

        Returns:
            Threshold JSON string.
        """
        return json.dumps(
            {
                "occupancy_alert_bps": int(self.occupancy_alert_bps),
                "icu_alert_bps": int(self.icu_alert_bps),
            }
        )
