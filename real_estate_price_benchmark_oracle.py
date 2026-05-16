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


class RealEstatePriceBenchmarkOracle(gl.Contract):
    """Publishes on chain real estate benchmark values from public sources."""

    owner: Address
    configs: str
    benchmarks: str
    next_benchmark_id: u256

    def __init__(self):
        """Initialize storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.owner = Address(_sender())
        self.configs = "{}"
        self.benchmarks = "{}"
        self.next_benchmark_id = 1

    def _require_owner(self) -> None:
        if _sender().strip().lower() != str(self.owner).strip().lower():
            _raise_user_error(f"{ERROR_EXPECTED} only owner")

    def _norm(self, value: str) -> str:
        return str(value).strip().lower()

    def _fetch_page(self, url: str) -> str:
        res = gl.nondet.web.get(url)
        status = int(res.status)
        body = res.body.decode("utf-8") if res.body is not None else ""
        if status >= 500:
            _raise_user_error(f"{ERROR_TRANSIENT} source server error {status}")
        if status >= 400:
            _raise_user_error(f"{ERROR_EXTERNAL} source client error {status}")
        if len(body.strip()) < 20:
            _raise_user_error(f"{ERROR_EXTERNAL} source body too short")
        return body

    def _parse_price(self, source: str, region: str, html: str) -> dict:
        prompt = f"""
Extract real estate index value from source page text.
Return strict JSON with keys:
- price_usd: number
- metric_name: short string
- jurisdiction: short string
- confidence: integer 0..100

Source: {source}
Region: {region}
Page sample:\n{html[:12000]}
"""
        out = gl.nondet.exec_prompt(prompt, response_format="json")
        if not isinstance(out, dict):
            _raise_user_error(f"{ERROR_LLM} invalid parser response")

        try:
            price_usd = float(out.get("price_usd", -1))
            confidence = int(out.get("confidence", -1))
        except Exception:
            _raise_user_error(f"{ERROR_LLM} invalid numeric fields")

        metric_name = str(out.get("metric_name", "")).strip()
        jurisdiction = str(out.get("jurisdiction", "")).strip()

        if price_usd <= 0:
            _raise_user_error(f"{ERROR_LLM} invalid price_usd")
        if confidence < 0 or confidence > 100:
            _raise_user_error(f"{ERROR_LLM} invalid confidence")
        if len(metric_name) < 2:
            _raise_user_error(f"{ERROR_LLM} invalid metric_name")
        if len(jurisdiction) < 2:
            _raise_user_error(f"{ERROR_LLM} invalid jurisdiction")

        return {
            "price_usd": price_usd,
            "metric_name": metric_name,
            "jurisdiction": jurisdiction,
            "confidence": confidence,
        }

    @gl.public.write
    def register_region(self, region_id: str, source: str, region_query: str, source_url: str, max_deviation_bps: int) -> None:
        """Register a region benchmark configuration.

        Parameters:
            region_id: Local region key.
            source: zillow or redfin.
            region_query: ZIP code or city text.
            source_url: Public page URL to parse.
            max_deviation_bps: Allowed deviation threshold.

        Returns:
            None.
        """
        self._require_owner()
        rid = self._norm(region_id)
        src = self._norm(source)
        rq = str(region_query).strip()
        su = str(source_url).strip()

        if len(rid) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid region_id")
        if src not in {"zillow", "redfin"}:
            _raise_user_error(f"{ERROR_EXPECTED} invalid source")
        if len(rq) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid region_query")
        if not (su.startswith("https://") or su.startswith("http://")):
            _raise_user_error(f"{ERROR_EXPECTED} invalid source_url")
        if max_deviation_bps < 100 or max_deviation_bps > 50000:
            _raise_user_error(f"{ERROR_EXPECTED} invalid max_deviation_bps")

        cfg = json.loads(self.configs)
        cfg[rid] = {
            "region_id": rid,
            "source": src,
            "region_query": rq,
            "source_url": su,
            "max_deviation_bps": int(max_deviation_bps),
            "updated_at": str(gl.block.timestamp),
        }
        self.configs = json.dumps(cfg)

    @gl.public.write
    def capture_benchmark(self, region_id: str) -> str:
        """Capture one benchmark observation.

        Parameters:
            region_id: Local region key.

        Returns:
            Benchmark id string.
        """
        rid = self._norm(region_id)
        cfg = json.loads(self.configs)
        if rid not in cfg:
            _raise_user_error(f"{ERROR_EXPECTED} region not found")
        c = cfg[rid]

        def leader_fn():
            html = self._fetch_page(c["source_url"])
            parsed = self._parse_price(c["source"], c["region_query"], html)
            return {
                "price_usd": float(parsed["price_usd"]),
                "metric_name": parsed["metric_name"],
                "jurisdiction": parsed["jurisdiction"],
                "confidence": int(parsed["confidence"]),
                "bucket": int(float(parsed["price_usd"]) / 1000),
            }

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                try:
                    leader_fn()
                    return False
                except Exception as e:
                    lm = leaders_res.message if hasattr(leaders_res, "message") else ""
                    vm = str(e)
                    if vm.startswith(ERROR_TRANSIENT) and lm.startswith(ERROR_TRANSIENT):
                        return True
                    return False

            vo = leader_fn()
            lo = leaders_res.calldata
            if str(vo.get("metric_name", "")).strip().lower() != str(lo.get("metric_name", "")).strip().lower():
                return False
            return int(vo.get("bucket", -1)) == int(lo.get("bucket", -2))

        out = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        bid = str(self.next_benchmark_id)
        self.next_benchmark_id += 1
        data = json.loads(self.benchmarks)
        data[bid] = {
            "benchmark_id": bid,
            "region_id": rid,
            "source": c["source"],
            "region_query": c["region_query"],
            "metric_name": out["metric_name"],
            "jurisdiction": out["jurisdiction"],
            "price_usd": float(out["price_usd"]),
            "confidence": int(out["confidence"]),
            "created_at": str(gl.block.timestamp),
            "requester": _sender(),
        }
        self.benchmarks = json.dumps(data)
        return bid

    @gl.public.view
    def get_region(self, region_id: str) -> str:
        """Read one region configuration.

        Parameters:
            region_id: Local region key.

        Returns:
            Region config JSON string.
        """
        rid = self._norm(region_id)
        cfg = json.loads(self.configs)
        if rid not in cfg:
            _raise_user_error(f"{ERROR_EXPECTED} region not found")
        return json.dumps(cfg[rid])

    @gl.public.view
    def get_benchmark(self, benchmark_id: str) -> str:
        """Read one benchmark observation.

        Parameters:
            benchmark_id: Benchmark id.

        Returns:
            Benchmark JSON string.
        """
        bid = self._norm(benchmark_id)
        data = json.loads(self.benchmarks)
        if bid not in data:
            _raise_user_error(f"{ERROR_EXPECTED} benchmark not found")
        return json.dumps(data[bid])

    @gl.public.view
    def get_all_benchmarks(self) -> str:
        """Read all benchmark observations.

        Parameters:
            None.

        Returns:
            JSON map of benchmarks.
        """
        return self.benchmarks
