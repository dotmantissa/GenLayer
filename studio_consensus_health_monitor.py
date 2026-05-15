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


class StudioConsensusHealthMonitor(gl.Contract):
    """Tracks per-contract consensus health snapshots and emits instability alerts."""

    owner: Address
    reporters: TreeMap[str, bool]
    known_contracts: DynArray[str]
    contract_meta: TreeMap[str, str]
    snapshots: TreeMap[str, str]
    thresholds: str

    def __init__(self):
        """Initialize monitor state.

        Parameters:
            None.

        Returns:
            None.
        """
        self.owner = Address(_sender())
        self.thresholds = json.dumps(
            {
                "consensus_failure_rate_bps": 700,
                "divergence_rate_bps": 1000,
                "revert_rate_bps": 1500,
                "spike_delta_bps": 300,
                "min_samples": 5,
            }
        )

    def _require_owner(self) -> None:
        if _sender().strip().lower() != str(self.owner).strip().lower():
            _raise_user_error(f"{ERROR_EXPECTED} only owner")

    def _require_reporter(self) -> None:
        sender = _sender().strip().lower()
        owner = str(self.owner).strip().lower()
        if sender != owner and not bool(self.reporters.get(sender, False)):
            _raise_user_error(f"{ERROR_EXPECTED} only reporter")

    def _clean_contract_id(self, contract_id: str) -> str:
        cleaned = str(contract_id).strip().lower()
        if len(cleaned) < 4:
            _raise_user_error(f"{ERROR_EXPECTED} invalid contract_id")
        return cleaned

    def _read_meta(self, contract_id: str) -> dict:
        if contract_id not in self.contract_meta:
            _raise_user_error(f"{ERROR_EXPECTED} contract not registered")
        return json.loads(self.contract_meta[contract_id])

    def _read_snapshots(self, contract_id: str) -> list:
        raw = self.snapshots.get(contract_id, "[]")
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            _raise_user_error(f"{ERROR_EXPECTED} snapshots corrupted")
        return parsed

    @gl.public.write
    def add_reporter(self, account: str) -> None:
        """Grant telemetry submission permission.

        Parameters:
            account: Reporter account address string.

        Returns:
            None.
        """
        self._require_owner()
        key = str(account).strip().lower()
        if len(key) < 4:
            _raise_user_error(f"{ERROR_EXPECTED} invalid account")
        self.reporters[key] = True

    @gl.public.write
    def remove_reporter(self, account: str) -> None:
        """Revoke telemetry submission permission.

        Parameters:
            account: Reporter account address string.

        Returns:
            None.
        """
        self._require_owner()
        key = str(account).strip().lower()
        if len(key) < 4:
            _raise_user_error(f"{ERROR_EXPECTED} invalid account")
        self.reporters[key] = False

    @gl.public.write
    def register_contract(self, contract_id: str, equivalence_principle: str) -> None:
        """Register a deployed contract for monitoring.

        Parameters:
            contract_id: Unique deployed contract identifier.
            equivalence_principle: Human readable equivalence strategy label.

        Returns:
            None.
        """
        self._require_reporter()
        cid = self._clean_contract_id(contract_id)
        principle = str(equivalence_principle).strip()
        if len(principle) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid equivalence_principle")
        if cid in self.contract_meta:
            _raise_user_error(f"{ERROR_EXPECTED} contract already registered")

        self.known_contracts.append(cid)
        self.contract_meta[cid] = json.dumps(
            {
                "contract_id": cid,
                "equivalence_principle": principle,
                "latest_alerts": [],
                "last_snapshot_index": -1,
                "last_updated_at": str(gl.block.timestamp),
            }
        )
        self.snapshots[cid] = "[]"

    @gl.public.write
    def update_thresholds(
        self,
        consensus_failure_rate_bps: int,
        divergence_rate_bps: int,
        revert_rate_bps: int,
        spike_delta_bps: int,
        min_samples: int,
    ) -> None:
        """Update global alert threshold configuration.

        Parameters:
            consensus_failure_rate_bps: Failure rate alert trigger in basis points.
            divergence_rate_bps: Divergence rate alert trigger in basis points.
            revert_rate_bps: Revert rate alert trigger in basis points.
            spike_delta_bps: Sudden increase trigger in basis points.
            min_samples: Minimum tx sample size before rate alerts can fire.

        Returns:
            None.
        """
        self._require_owner()
        for value in [
            consensus_failure_rate_bps,
            divergence_rate_bps,
            revert_rate_bps,
            spike_delta_bps,
        ]:
            if value < 0 or value > 10000:
                _raise_user_error(f"{ERROR_EXPECTED} invalid threshold")
        if min_samples < 1:
            _raise_user_error(f"{ERROR_EXPECTED} invalid min_samples")

        self.thresholds = json.dumps(
            {
                "consensus_failure_rate_bps": int(consensus_failure_rate_bps),
                "divergence_rate_bps": int(divergence_rate_bps),
                "revert_rate_bps": int(revert_rate_bps),
                "spike_delta_bps": int(spike_delta_bps),
                "min_samples": int(min_samples),
            }
        )

    @gl.public.write
    def submit_snapshot(
        self,
        contract_id: str,
        total_transactions: int,
        consensus_failures: int,
        divergence_events: int,
        revert_count: int,
        window_minutes: int,
    ) -> str:
        """Submit monitoring telemetry and compute active alerts.

        Parameters:
            contract_id: Registered contract identifier.
            total_transactions: Number of observed transactions in the sample window.
            consensus_failures: Number of consensus failures in the same window.
            divergence_events: Number of validator divergence events in the same window.
            revert_count: Number of reverts in the same window.
            window_minutes: Window length used for this snapshot.

        Returns:
            JSON string containing snapshot index and active alerts.
        """
        self._require_reporter()
        cid = self._clean_contract_id(contract_id)
        meta = self._read_meta(cid)
        settings = json.loads(self.thresholds)

        if total_transactions < 0:
            _raise_user_error(f"{ERROR_EXPECTED} invalid total_transactions")
        if window_minutes < 1:
            _raise_user_error(f"{ERROR_EXPECTED} invalid window_minutes")
        for metric in [consensus_failures, divergence_events, revert_count]:
            if metric < 0:
                _raise_user_error(f"{ERROR_EXPECTED} invalid metric")
            if metric > total_transactions:
                _raise_user_error(f"{ERROR_EXPECTED} metric exceeds total")

        snapshots = self._read_snapshots(cid)
        previous = snapshots[-1] if len(snapshots) > 0 else None

        fail_rate = 0
        divergence_rate = 0
        revert_rate = 0
        if total_transactions > 0:
            fail_rate = int((int(consensus_failures) * 10000) / int(total_transactions))
            divergence_rate = int((int(divergence_events) * 10000) / int(total_transactions))
            revert_rate = int((int(revert_count) * 10000) / int(total_transactions))

        alerts = []
        if total_transactions >= int(settings["min_samples"]):
            if fail_rate >= int(settings["consensus_failure_rate_bps"]):
                alerts.append("consensus_failure_rate_high")
            if divergence_rate >= int(settings["divergence_rate_bps"]):
                alerts.append("validator_divergence_high")
            if revert_rate >= int(settings["revert_rate_bps"]):
                alerts.append("revert_rate_high")

        if previous is not None:
            prev_fail = int(previous.get("consensus_failure_rate_bps", 0))
            prev_div = int(previous.get("divergence_rate_bps", 0))
            if fail_rate - prev_fail >= int(settings["spike_delta_bps"]):
                alerts.append("consensus_failure_spike")
            if divergence_rate - prev_div >= int(settings["spike_delta_bps"]):
                alerts.append("validator_divergence_spike")

        snapshot = {
            "index": len(snapshots),
            "contract_id": cid,
            "submitted_by": _sender(),
            "window_minutes": int(window_minutes),
            "total_transactions": int(total_transactions),
            "consensus_failures": int(consensus_failures),
            "divergence_events": int(divergence_events),
            "revert_count": int(revert_count),
            "consensus_failure_rate_bps": int(fail_rate),
            "divergence_rate_bps": int(divergence_rate),
            "revert_rate_bps": int(revert_rate),
            "alerts": alerts,
            "timestamp": str(gl.block.timestamp),
        }

        snapshots.append(snapshot)
        self.snapshots[cid] = json.dumps(snapshots)

        meta["latest_alerts"] = alerts
        meta["last_snapshot_index"] = len(snapshots) - 1
        meta["last_updated_at"] = str(gl.block.timestamp)
        self.contract_meta[cid] = json.dumps(meta)

        return json.dumps(
            {
                "contract_id": cid,
                "snapshot_index": len(snapshots) - 1,
                "alerts": alerts,
            }
        )

    @gl.public.view
    def get_contract_status(self, contract_id: str) -> str:
        """Read latest monitoring status for one contract.

        Parameters:
            contract_id: Registered contract identifier.

        Returns:
            JSON status object string.
        """
        cid = self._clean_contract_id(contract_id)
        meta = self._read_meta(cid)
        snapshots = self._read_snapshots(cid)

        last_snapshot = None
        if len(snapshots) > 0:
            last_snapshot = snapshots[-1]

        status = {
            "contract_id": cid,
            "equivalence_principle": meta.get("equivalence_principle", ""),
            "latest_alerts": meta.get("latest_alerts", []),
            "last_snapshot": last_snapshot,
        }
        return json.dumps(status)

    @gl.public.view
    def get_snapshot(self, contract_id: str, index: int) -> str:
        """Read a specific historical snapshot.

        Parameters:
            contract_id: Registered contract identifier.
            index: Snapshot index in insertion order.

        Returns:
            JSON snapshot object string.
        """
        cid = self._clean_contract_id(contract_id)
        snapshots = self._read_snapshots(cid)
        if index < 0 or index >= len(snapshots):
            _raise_user_error(f"{ERROR_EXPECTED} snapshot index out of range")
        return json.dumps(snapshots[index])

    @gl.public.view
    def get_alerts(self, contract_id: str) -> str:
        """Read latest active alert labels for a contract.

        Parameters:
            contract_id: Registered contract identifier.

        Returns:
            JSON array string containing latest alert labels.
        """
        cid = self._clean_contract_id(contract_id)
        meta = self._read_meta(cid)
        return json.dumps(meta.get("latest_alerts", []))

    @gl.public.view
    def list_contracts(self) -> str:
        """List all registered contract identifiers.

        Parameters:
            None.

        Returns:
            JSON array string of contract identifiers.
        """
        return json.dumps(list(self.known_contracts))

    @gl.public.view
    def is_reporter(self, account: str) -> bool:
        """Check if an account can submit snapshots.

        Parameters:
            account: Account address string.

        Returns:
            True if account has reporter permission.
        """
        key = str(account).strip().lower()
        if key == str(self.owner).strip().lower():
            return True
        return bool(self.reporters.get(key, False))

    @gl.public.view
    def get_thresholds(self) -> str:
        """Read current threshold configuration.

        Parameters:
            None.

        Returns:
            JSON thresholds object string.
        """
        return self.thresholds
