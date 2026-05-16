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


class ProviderFallbackRouter(gl.Contract):
    """Routes data requests across ordered equivalent API providers."""

    owner: Address
    provider_sets: str
    reads: str
    next_read_id: u256

    def __init__(self):
        """Initialize storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.owner = Address(_sender())
        self.provider_sets = "{}"
        self.reads = "{}"
        self.next_read_id = 1

    def _require_owner(self) -> None:
        if _sender().strip().lower() != str(self.owner).strip().lower():
            _raise_user_error(f"{ERROR_EXPECTED} only owner")

    def _norm(self, value: str) -> str:
        return str(value).strip().lower()

    def _fetch_provider(self, base_url: str, query: str) -> dict:
        url = f"{base_url}?q={query}"
        res = gl.nondet.web.get(url)
        status = int(res.status)
        body = res.body.decode("utf-8") if res.body is not None else ""

        if status >= 500:
            _raise_user_error(f"{ERROR_TRANSIENT} provider server error {status}")
        if status >= 400:
            return {
                "ok": False,
                "status": status,
                "body": body,
                "value": 0.0,
                "reason": f"http {status}",
            }
        if len(body.strip()) == 0:
            return {
                "ok": False,
                "status": status,
                "body": body,
                "value": 0.0,
                "reason": "empty body",
            }

        prompt = f"""
You validate provider payload quality.
Return JSON with keys:
- valid_data: boolean
- numeric_value: number
- reason: short string

Payload:\n{body[:1200]}
"""
        llm = gl.nondet.exec_prompt(prompt, response_format="json")
        if not isinstance(llm, dict):
            _raise_user_error(f"{ERROR_LLM} invalid llm response")

        valid_data = bool(llm.get("valid_data", False))
        reason = str(llm.get("reason", "")).strip()
        try:
            numeric_value = float(llm.get("numeric_value", 0.0))
        except Exception:
            _raise_user_error(f"{ERROR_LLM} invalid numeric_value")

        if numeric_value < 0:
            valid_data = False

        return {
            "ok": bool(valid_data),
            "status": status,
            "body": body,
            "value": numeric_value,
            "reason": reason if len(reason) > 0 else "no reason",
        }

    def _select_with_fallback(self, providers: list, query: str, tolerance_bps: int) -> dict:
        attempts = []
        last_ok_value = None

        for idx in range(len(providers)):
            provider = providers[idx]
            purl = str(provider.get("url", "")).strip()
            pname = str(provider.get("name", "")).strip()
            if len(purl) == 0 or len(pname) == 0:
                continue

            res = self._fetch_provider(purl, query)
            anomalous = False
            if res["ok"] and last_ok_value is not None:
                delta = abs(float(res["value"]) - float(last_ok_value))
                base = max(abs(float(last_ok_value)), 0.0001)
                bps = int((delta / base) * 10000)
                if bps > int(tolerance_bps):
                    anomalous = True

            attempts.append(
                {
                    "provider": pname,
                    "url": purl,
                    "status": int(res["status"]),
                    "valid_data": bool(res["ok"]),
                    "value": float(res["value"]),
                    "reason": str(res["reason"]),
                    "anomalous": anomalous,
                }
            )

            if res["ok"] and not anomalous:
                return {
                    "selected_provider": pname,
                    "selected_url": purl,
                    "selected_value": float(res["value"]),
                    "fallback_used": idx > 0,
                    "attempts": attempts,
                }

            if res["ok"]:
                last_ok_value = float(res["value"])

        _raise_user_error(f"{ERROR_EXTERNAL} no valid provider response")
        return {}

    @gl.public.write
    def register_provider_set(self, data_type: str, providers_json: str, tolerance_bps: int) -> None:
        """Register ordered equivalent providers for a data type.

        Parameters:
            data_type: Logical key such as weather or price.
            providers_json: JSON array of objects with name and url.
            tolerance_bps: Anomaly threshold in basis points.

        Returns:
            None.
        """
        self._require_owner()
        dtype = self._norm(data_type)
        if len(dtype) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid data_type")
        if tolerance_bps < 1 or tolerance_bps > 10000:
            _raise_user_error(f"{ERROR_EXPECTED} invalid tolerance_bps")

        try:
            providers = json.loads(str(providers_json))
        except Exception:
            _raise_user_error(f"{ERROR_EXPECTED} invalid providers_json")

        if not isinstance(providers, list) or len(providers) < 1:
            _raise_user_error(f"{ERROR_EXPECTED} providers list required")

        cleaned = []
        for p in providers:
            if not isinstance(p, dict):
                _raise_user_error(f"{ERROR_EXPECTED} invalid provider entry")
            name = str(p.get("name", "")).strip()
            url = str(p.get("url", "")).strip()
            if len(name) < 2:
                _raise_user_error(f"{ERROR_EXPECTED} invalid provider name")
            if not (url.startswith("https://") or url.startswith("http://")):
                _raise_user_error(f"{ERROR_EXPECTED} invalid provider url")
            cleaned.append({"name": name, "url": url})

        psets = json.loads(self.provider_sets)
        psets[dtype] = {
            "data_type": dtype,
            "providers": cleaned,
            "tolerance_bps": int(tolerance_bps),
            "updated_at": str(gl.block.timestamp),
        }
        self.provider_sets = json.dumps(psets)

    @gl.public.write
    def set_provider_order(self, data_type: str, providers_json: str) -> None:
        """Replace provider order for an existing data type.

        Parameters:
            data_type: Logical key.
            providers_json: JSON array of provider objects.

        Returns:
            None.
        """
        self._require_owner()
        dtype = self._norm(data_type)
        psets = json.loads(self.provider_sets)
        if dtype not in psets:
            _raise_user_error(f"{ERROR_EXPECTED} provider set not found")

        try:
            providers = json.loads(str(providers_json))
        except Exception:
            _raise_user_error(f"{ERROR_EXPECTED} invalid providers_json")

        if not isinstance(providers, list) or len(providers) < 1:
            _raise_user_error(f"{ERROR_EXPECTED} providers list required")

        cleaned = []
        for p in providers:
            if not isinstance(p, dict):
                _raise_user_error(f"{ERROR_EXPECTED} invalid provider entry")
            name = str(p.get("name", "")).strip()
            url = str(p.get("url", "")).strip()
            if len(name) < 2:
                _raise_user_error(f"{ERROR_EXPECTED} invalid provider name")
            if not (url.startswith("https://") or url.startswith("http://")):
                _raise_user_error(f"{ERROR_EXPECTED} invalid provider url")
            cleaned.append({"name": name, "url": url})

        psets[dtype]["providers"] = cleaned
        psets[dtype]["updated_at"] = str(gl.block.timestamp)
        self.provider_sets = json.dumps(psets)

    @gl.public.write
    def read_with_fallback(self, data_type: str, query: str) -> str:
        """Fetch data from providers with automatic fallback on failure or anomaly.

        Parameters:
            data_type: Logical key.
            query: Query term passed to providers.

        Returns:
            Read id string.
        """
        dtype = self._norm(data_type)
        q = str(query).strip()
        if len(q) < 1:
            _raise_user_error(f"{ERROR_EXPECTED} invalid query")

        psets = json.loads(self.provider_sets)
        if dtype not in psets:
            _raise_user_error(f"{ERROR_EXPECTED} provider set not found")

        entry = psets[dtype]
        providers = entry.get("providers", [])
        tolerance = int(entry.get("tolerance_bps", 1000))

        def leader_fn():
            out = self._select_with_fallback(providers, q, tolerance)
            bucket = 1 if bool(out.get("fallback_used", False)) else 0
            out["bucket"] = bucket
            return out

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
            if str(validator_out.get("selected_provider", "")) != str(leader_out.get("selected_provider", "")):
                return False
            return int(validator_out.get("bucket", -1)) == int(leader_out.get("bucket", -2))

        out = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        rid = str(self.next_read_id)
        self.next_read_id += 1
        reads = json.loads(self.reads)
        reads[rid] = {
            "read_id": rid,
            "data_type": dtype,
            "query": q,
            "selected_provider": out["selected_provider"],
            "selected_url": out["selected_url"],
            "selected_value": float(out["selected_value"]),
            "fallback_used": bool(out["fallback_used"]),
            "attempts": out["attempts"],
            "created_at": str(gl.block.timestamp),
        }
        self.reads = json.dumps(reads)
        return rid

    @gl.public.view
    def get_provider_set(self, data_type: str) -> str:
        """Read provider set configuration.

        Parameters:
            data_type: Logical key.

        Returns:
            Provider set JSON string.
        """
        dtype = self._norm(data_type)
        psets = json.loads(self.provider_sets)
        if dtype not in psets:
            _raise_user_error(f"{ERROR_EXPECTED} provider set not found")
        return json.dumps(psets[dtype])

    @gl.public.view
    def get_read(self, read_id: str) -> str:
        """Read one fallback execution record.

        Parameters:
            read_id: Read identifier.

        Returns:
            Read JSON string.
        """
        rid = self._norm(read_id)
        reads = json.loads(self.reads)
        if rid not in reads:
            _raise_user_error(f"{ERROR_EXPECTED} read not found")
        return json.dumps(reads[rid])

    @gl.public.view
    def get_all_reads(self) -> str:
        """Read all fallback execution records.

        Parameters:
            None.

        Returns:
            JSON map of reads.
        """
        return self.reads
