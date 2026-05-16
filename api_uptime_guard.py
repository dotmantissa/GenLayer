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


class APIUptimeGuard(gl.Contract):
    """Monitors API endpoint health and signals oracle pause requirements."""

    owner: Address
    endpoints: str
    dependencies: str
    health_checks: str
    next_check_id: u256

    def __init__(self):
        """Initialize storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.owner = Address(_sender())
        self.endpoints = "{}"
        self.dependencies = "{}"
        self.health_checks = "{}"
        self.next_check_id = 1

    def _require_owner(self) -> None:
        if _sender().strip().lower() != str(self.owner).strip().lower():
            _raise_user_error(f"{ERROR_EXPECTED} only owner")

    def _norm(self, value: str) -> str:
        return str(value).strip().lower()

    def _health_bucket(self, status: str) -> int:
        state = self._norm(status)
        if state == "healthy":
            return 2
        if state == "degraded":
            return 1
        return 0

    def _fetch_endpoint(self, url: str, timeout_ms: int) -> dict:
        response = gl.nondet.web.get(url)
        http_status = int(response.status)
        body_text = response.body.decode("utf-8") if response.body is not None else ""

        if http_status >= 500:
            _raise_user_error(f"{ERROR_TRANSIENT} upstream server error {http_status}")

        if http_status >= 400 and http_status < 500:
            return {
                "http_status": http_status,
                "body": body_text,
                "timed_out": False,
                "transport_ok": True,
                "timeout_ms": int(timeout_ms),
            }

        if len(body_text.strip()) == 0:
            _raise_user_error(f"{ERROR_EXTERNAL} empty response body")

        return {
            "http_status": http_status,
            "body": body_text,
            "timed_out": False,
            "transport_ok": True,
            "timeout_ms": int(timeout_ms),
        }

    def _assess_validity(self, endpoint_id: str, payload: dict, required_keys_csv: str) -> dict:
        prompt = f"""
You are validating an API health check result.
Return strict JSON with keys:
- health_status: one of healthy, degraded, offline
- valid_data: boolean
- reason: short string

Endpoint id: {endpoint_id}
Required keys csv: {required_keys_csv}
HTTP status: {payload.get('http_status')}
Body sample:\n{str(payload.get('body', ''))[:1500]}
"""
        llm = gl.nondet.exec_prompt(prompt, response_format="json")
        if not isinstance(llm, dict):
            _raise_user_error(f"{ERROR_LLM} invalid llm shape")

        state = self._norm(llm.get("health_status", ""))
        if state not in {"healthy", "degraded", "offline"}:
            _raise_user_error(f"{ERROR_LLM} invalid health_status")

        valid_data = bool(llm.get("valid_data", False))
        reason = str(llm.get("reason", "")).strip()
        if len(reason) == 0:
            reason = "no reason provided"

        # Enforce deterministic downgrade on hard HTTP errors.
        status_code = int(payload.get("http_status", 0))
        if status_code >= 400 and state == "healthy":
            state = "degraded"

        return {
            "health_status": state,
            "valid_data": valid_data,
            "reason": reason,
        }

    @gl.public.write
    def register_endpoint(self, endpoint_id: str, url: str, required_keys_csv: str, timeout_ms: int) -> None:
        """Register an endpoint for periodic uptime and data-validity checks.

        Parameters:
            endpoint_id: Local endpoint identifier.
            url: Endpoint URL.
            required_keys_csv: Required JSON keys as comma-separated list.
            timeout_ms: Timeout target in milliseconds.

        Returns:
            None.
        """
        self._require_owner()
        eid = self._norm(endpoint_id)
        endpoint_url = str(url).strip()
        req = str(required_keys_csv).strip()
        timeout_value = int(timeout_ms)

        if len(eid) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid endpoint_id")
        if not (endpoint_url.startswith("https://") or endpoint_url.startswith("http://")):
            _raise_user_error(f"{ERROR_EXPECTED} invalid url")
        if len(req) == 0:
            _raise_user_error(f"{ERROR_EXPECTED} invalid required_keys_csv")
        if timeout_value < 100 or timeout_value > 60000:
            _raise_user_error(f"{ERROR_EXPECTED} invalid timeout_ms")

        endpoints = json.loads(self.endpoints)
        endpoints[eid] = {
            "endpoint_id": eid,
            "url": endpoint_url,
            "required_keys_csv": req,
            "timeout_ms": timeout_value,
            "enabled": True,
            "updated_at": str(gl.block.timestamp),
        }
        self.endpoints = json.dumps(endpoints)

    @gl.public.write
    def set_endpoint_enabled(self, endpoint_id: str, enabled: bool) -> None:
        """Enable or disable active monitoring for an endpoint.

        Parameters:
            endpoint_id: Local endpoint identifier.
            enabled: Monitoring state.

        Returns:
            None.
        """
        self._require_owner()
        eid = self._norm(endpoint_id)
        endpoints = json.loads(self.endpoints)
        if eid not in endpoints:
            _raise_user_error(f"{ERROR_EXPECTED} endpoint not found")
        endpoints[eid]["enabled"] = bool(enabled)
        endpoints[eid]["updated_at"] = str(gl.block.timestamp)
        self.endpoints = json.dumps(endpoints)

    @gl.public.write
    def link_dependency(self, endpoint_id: str, dependency_contract_id: str) -> None:
        """Associate an endpoint with a dependent oracle contract identifier.

        Parameters:
            endpoint_id: Local endpoint identifier.
            dependency_contract_id: Dependent oracle contract id or address.

        Returns:
            None.
        """
        self._require_owner()
        eid = self._norm(endpoint_id)
        dep = str(dependency_contract_id).strip()
        if len(dep) < 4:
            _raise_user_error(f"{ERROR_EXPECTED} invalid dependency_contract_id")

        endpoints = json.loads(self.endpoints)
        if eid not in endpoints:
            _raise_user_error(f"{ERROR_EXPECTED} endpoint not found")

        dependencies = json.loads(self.dependencies)
        dependencies[eid] = {
            "endpoint_id": eid,
            "dependency_contract_id": dep,
            "updated_at": str(gl.block.timestamp),
        }
        self.dependencies = json.dumps(dependencies)

    @gl.public.write
    def run_health_check(self, endpoint_id: str) -> str:
        """Run a live health check and persist pause recommendation.

        Parameters:
            endpoint_id: Local endpoint identifier.

        Returns:
            Health check id string.
        """
        eid = self._norm(endpoint_id)
        endpoints = json.loads(self.endpoints)
        if eid not in endpoints:
            _raise_user_error(f"{ERROR_EXPECTED} endpoint not found")

        endpoint = endpoints[eid]
        if not bool(endpoint.get("enabled", False)):
            _raise_user_error(f"{ERROR_EXPECTED} endpoint disabled")

        dependency = json.loads(self.dependencies).get(eid, {})

        def leader_fn():
            payload = self._fetch_endpoint(str(endpoint.get("url", "")), int(endpoint.get("timeout_ms", 2000)))
            assessed = self._assess_validity(eid, payload, str(endpoint.get("required_keys_csv", "")))
            status_text = str(assessed.get("health_status", "offline"))
            bucket = self._health_bucket(status_text)
            pause = bucket < 2 or not bool(assessed.get("valid_data", False))
            return {
                "endpoint_id": eid,
                "health_status": status_text,
                "valid_data": bool(assessed.get("valid_data", False)),
                "reason": str(assessed.get("reason", "")),
                "http_status": int(payload.get("http_status", 0)),
                "bucket": bucket,
                "pause_dependent_oracles": pause,
                "dependency_contract_id": str(dependency.get("dependency_contract_id", "")),
            }

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
            if bool(validator_out.get("pause_dependent_oracles", False)) != bool(leader_out.get("pause_dependent_oracles", False)):
                return False
            return int(validator_out.get("bucket", -1)) == int(leader_out.get("bucket", -2))

        out = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        check_id = str(self.next_check_id)
        self.next_check_id += 1

        checks = json.loads(self.health_checks)
        checks[check_id] = {
            "check_id": check_id,
            "endpoint_id": out["endpoint_id"],
            "requester": _sender(),
            "health_status": out["health_status"],
            "valid_data": bool(out["valid_data"]),
            "reason": out["reason"],
            "http_status": int(out["http_status"]),
            "pause_dependent_oracles": bool(out["pause_dependent_oracles"]),
            "dependency_contract_id": out["dependency_contract_id"],
            "created_at": str(gl.block.timestamp),
        }
        self.health_checks = json.dumps(checks)
        return check_id

    @gl.public.view
    def get_endpoint(self, endpoint_id: str) -> str:
        """Read endpoint configuration.

        Parameters:
            endpoint_id: Local endpoint identifier.

        Returns:
            Endpoint JSON string.
        """
        eid = self._norm(endpoint_id)
        endpoints = json.loads(self.endpoints)
        if eid not in endpoints:
            _raise_user_error(f"{ERROR_EXPECTED} endpoint not found")
        return json.dumps(endpoints[eid])

    @gl.public.view
    def get_dependency(self, endpoint_id: str) -> str:
        """Read dependency mapping for an endpoint.

        Parameters:
            endpoint_id: Local endpoint identifier.

        Returns:
            Dependency JSON string.
        """
        eid = self._norm(endpoint_id)
        dependencies = json.loads(self.dependencies)
        if eid not in dependencies:
            _raise_user_error(f"{ERROR_EXPECTED} dependency not found")
        return json.dumps(dependencies[eid])

    @gl.public.view
    def get_health_check(self, check_id: str) -> str:
        """Read one health check record.

        Parameters:
            check_id: Check identifier.

        Returns:
            Health check JSON string.
        """
        key = self._norm(check_id)
        checks = json.loads(self.health_checks)
        if key not in checks:
            _raise_user_error(f"{ERROR_EXPECTED} check not found")
        return json.dumps(checks[key])

    @gl.public.view
    def get_all_health_checks(self) -> str:
        """Read all stored health checks.

        Parameters:
            None.

        Returns:
            JSON map of health checks.
        """
        return self.health_checks
