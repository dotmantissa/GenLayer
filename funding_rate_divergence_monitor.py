# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"


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


class FundingRateDivergenceMonitor(gl.Contract):
    """Fetches funding rates across venues and flags material divergence."""

    owner: Address
    materiality_bps: u256
    min_venues: u256
    reports: str
    next_report_id: u256

    def __init__(self, materiality_bps: int, min_venues: int):
        """Initialize monitor settings.

        Parameters:
            materiality_bps: Divergence threshold in basis points.
            min_venues: Minimum venues required for a valid comparison.

        Returns:
            None.
        """
        if materiality_bps < 1 or materiality_bps > 5000:
            _raise_user_error(f"{ERROR_EXPECTED} materiality_bps out of range")
        if min_venues < 2 or min_venues > 3:
            _raise_user_error(f"{ERROR_EXPECTED} min_venues out of range")

        self.owner = Address(_sender())
        self.materiality_bps = int(materiality_bps)
        self.min_venues = int(min_venues)
        self.reports = "{}"
        self.next_report_id = 1

    def _require_owner(self) -> None:
        if _sender().strip().lower() != str(self.owner).strip().lower():
            _raise_user_error(f"{ERROR_EXPECTED} only owner")

    def _fetch_json(self, url: str):
        response = gl.nondet.web.get(url)
        status = int(response.status)
        if status >= 400 and status < 500:
            _raise_user_error(f"{ERROR_EXTERNAL} API client error {status}")
        if status >= 500:
            _raise_user_error(f"{ERROR_TRANSIENT} API server error {status}")

        try:
            body = response.body.decode("utf-8") if response.body is not None else "{}"
            return json.loads(body)
        except Exception:
            _raise_user_error(f"{ERROR_EXTERNAL} API invalid json")

    def _okx_symbol(self, symbol: str) -> str:
        upper = str(symbol).strip().upper()
        if upper.endswith("USDT"):
            base = upper[:-4]
            return base + "-USDT-SWAP"
        _raise_user_error(f"{ERROR_EXPECTED} unsupported symbol")
        return ""

    def _parse_rate(self, value) -> float:
        try:
            return float(str(value).strip())
        except Exception:
            _raise_user_error(f"{ERROR_EXTERNAL} invalid funding rate")
        return 0.0

    def _bucketize_bps(self, bps: int) -> int:
        if bps < 25:
            return 0
        if bps < 50:
            return 1
        if bps < 100:
            return 2
        if bps < 200:
            return 3
        return 4

    def _collect_rates(self, symbol: str) -> dict:
        upper_symbol = str(symbol).strip().upper()
        if len(upper_symbol) < 6:
            _raise_user_error(f"{ERROR_EXPECTED} invalid symbol")
        okx_inst = self._okx_symbol(upper_symbol)

        venues = {}

        b_url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={upper_symbol}&limit=1"
        b_data = self._fetch_json(b_url)
        b_list = b_data if isinstance(b_data, list) else []
        if len(b_list) > 0 and isinstance(b_list[0], dict) and "fundingRate" in b_list[0]:
            venues["binance"] = self._parse_rate(b_list[0].get("fundingRate"))

        by_url = f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={upper_symbol}&limit=1"
        by_data = self._fetch_json(by_url)
        by_rate = None
        if "result" in by_data and isinstance(by_data.get("result"), dict):
            by_list = by_data["result"].get("list", [])
            if isinstance(by_list, list) and len(by_list) > 0 and isinstance(by_list[0], dict):
                by_rate = by_list[0].get("fundingRate")
        if by_rate is not None:
            venues["bybit"] = self._parse_rate(by_rate)

        o_url = f"https://www.okx.com/api/v5/public/funding-rate?instId={okx_inst}"
        o_data = self._fetch_json(o_url)
        o_rate = None
        o_list = o_data.get("data", []) if isinstance(o_data, dict) else []
        if isinstance(o_list, list) and len(o_list) > 0 and isinstance(o_list[0], dict):
            o_rate = o_list[0].get("fundingRate")
        if o_rate is not None:
            venues["okx"] = self._parse_rate(o_rate)

        if len(venues) < int(self.min_venues):
            _raise_user_error(f"{ERROR_EXTERNAL} insufficient venue data")

        rates = list(venues.values())
        max_rate = max(rates)
        min_rate = min(rates)
        spread_bps = int(round((max_rate - min_rate) * 10000.0))
        alert = spread_bps >= int(self.materiality_bps)

        return {
            "symbol": upper_symbol,
            "venues": venues,
            "venue_count": len(venues),
            "spread_bps": spread_bps,
            "alert": alert,
            "bucket": self._bucketize_bps(spread_bps),
        }

    @gl.public.write
    def set_thresholds(self, materiality_bps: int, min_venues: int) -> None:
        """Update divergence thresholds.

        Parameters:
            materiality_bps: Divergence threshold in basis points.
            min_venues: Minimum venues required for comparison.

        Returns:
            None.
        """
        self._require_owner()
        if materiality_bps < 1 or materiality_bps > 5000:
            _raise_user_error(f"{ERROR_EXPECTED} materiality_bps out of range")
        if min_venues < 2 or min_venues > 3:
            _raise_user_error(f"{ERROR_EXPECTED} min_venues out of range")
        self.materiality_bps = int(materiality_bps)
        self.min_venues = int(min_venues)

    @gl.public.write
    def check_divergence(self, symbol: str) -> str:
        """Fetch rates and produce consensus safe divergence report.

        Parameters:
            symbol: Perpetual market symbol, for example BTCUSDT.

        Returns:
            Report id string.
        """

        def leader_fn():
            return self._collect_rates(symbol)

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                try:
                    self._collect_rates(symbol)
                    return False
                except Exception as e:
                    leader_message = leaders_res.message if hasattr(leaders_res, "message") else ""
                    validator_message = str(e)
                    if validator_message.startswith(ERROR_TRANSIENT) and leader_message.startswith(ERROR_TRANSIENT):
                        return True
                    return False

            validator_data = self._collect_rates(symbol)
            leader_data = leaders_res.calldata

            if bool(leader_data.get("alert", False)) != bool(validator_data.get("alert", False)):
                return False
            if int(leader_data.get("bucket", -1)) != int(validator_data.get("bucket", -2)):
                return False
            return True

        outcome = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        rid = str(self.next_report_id)
        self.next_report_id += 1

        reports = json.loads(self.reports)
        reports[rid] = {
            "report_id": rid,
            "requester": _sender(),
            "symbol": outcome["symbol"],
            "venues": outcome["venues"],
            "venue_count": int(outcome["venue_count"]),
            "spread_bps": int(outcome["spread_bps"]),
            "materiality_bps": int(self.materiality_bps),
            "alert": bool(outcome["alert"]),
            "bucket": int(outcome["bucket"]),
            "created_at": str(gl.block.timestamp),
        }
        self.reports = json.dumps(reports)
        return rid

    @gl.public.view
    def get_report(self, report_id: str) -> str:
        """Read one divergence report.

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
        """Read all divergence reports.

        Parameters:
            None.

        Returns:
            JSON map of reports.
        """
        return self.reports

    @gl.public.view
    def get_thresholds(self) -> str:
        """Read current threshold settings.

        Parameters:
            None.

        Returns:
            Threshold JSON string.
        """
        return json.dumps(
            {
                "materiality_bps": int(self.materiality_bps),
                "min_venues": int(self.min_venues),
            }
        )
