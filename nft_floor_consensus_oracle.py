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


class NFTFloorConsensusOracle(gl.Contract):
    """Builds consensus floor prices from OpenSea, Blur, and Magic Eden."""

    owner: Address
    collections: str
    reports: str
    next_report_id: u256

    def __init__(self):
        """Initialize storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.owner = Address(_sender())
        self.collections = "{}"
        self.reports = "{}"
        self.next_report_id = 1

    def _require_owner(self) -> None:
        if _sender().strip().lower() != str(self.owner).strip().lower():
            _raise_user_error(f"{ERROR_EXPECTED} only owner")

    def _norm(self, value: str) -> str:
        return str(value).strip().lower()

    def _fetch_market(self, url: str) -> str:
        res = gl.nondet.web.get(url)
        status = int(res.status)
        body = res.body.decode("utf-8") if res.body is not None else ""
        if status >= 500:
            _raise_user_error(f"{ERROR_TRANSIENT} market server error {status}")
        if status >= 400:
            _raise_user_error(f"{ERROR_EXTERNAL} market client error {status}")
        if len(body.strip()) < 2:
            _raise_user_error(f"{ERROR_EXTERNAL} market body too short")
        return body

    def _parse_floor(self, market: str, collection_slug: str, payload: str) -> dict:
        prompt = f"""
Extract floor price for NFT collection from marketplace payload.
Return strict JSON with keys:
- floor_eth: number
- confidence: integer 0..100
- wash_risk: one of low, medium, high
- rationale: short string

Market: {market}
Collection: {collection_slug}
Payload:\n{payload[:12000]}
"""
        out = gl.nondet.exec_prompt(prompt, response_format="json")
        if not isinstance(out, dict):
            _raise_user_error(f"{ERROR_LLM} invalid parser response")

        try:
            floor_eth = float(out.get("floor_eth", -1))
            confidence = int(out.get("confidence", -1))
        except Exception:
            _raise_user_error(f"{ERROR_LLM} invalid numeric fields")

        wash_risk = self._norm(out.get("wash_risk", ""))
        if wash_risk not in {"low", "medium", "high"}:
            _raise_user_error(f"{ERROR_LLM} invalid wash_risk")
        if floor_eth <= 0:
            _raise_user_error(f"{ERROR_LLM} invalid floor_eth")
        if confidence < 0 or confidence > 100:
            _raise_user_error(f"{ERROR_LLM} invalid confidence")

        return {
            "market": market,
            "floor_eth": floor_eth,
            "confidence": confidence,
            "wash_risk": wash_risk,
            "rationale": str(out.get("rationale", "")).strip(),
        }

    def _risk_mult(self, wash_risk: str) -> float:
        if wash_risk == "high":
            return 0.4
        if wash_risk == "medium":
            return 0.7
        return 1.0

    @gl.public.write
    def register_collection(self, collection_id: str, opensea_url: str, blur_url: str, magiceden_url: str, max_spread_bps: int) -> None:
        """Register marketplace endpoints for one collection.

        Parameters:
            collection_id: Local collection key.
            opensea_url: OpenSea public endpoint URL.
            blur_url: Blur public endpoint URL.
            magiceden_url: Magic Eden public endpoint URL.
            max_spread_bps: Max acceptable spread threshold.

        Returns:
            None.
        """
        self._require_owner()
        cid = self._norm(collection_id)
        os_url = str(opensea_url).strip()
        bl_url = str(blur_url).strip()
        me_url = str(magiceden_url).strip()
        if len(cid) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid collection_id")
        if not os_url.startswith("http") or not bl_url.startswith("http") or not me_url.startswith("http"):
            _raise_user_error(f"{ERROR_EXPECTED} invalid market url")
        if max_spread_bps < 100 or max_spread_bps > 50000:
            _raise_user_error(f"{ERROR_EXPECTED} invalid max_spread_bps")

        cols = json.loads(self.collections)
        cols[cid] = {
            "collection_id": cid,
            "opensea_url": os_url,
            "blur_url": bl_url,
            "magiceden_url": me_url,
            "max_spread_bps": int(max_spread_bps),
            "updated_at": str(gl.block.timestamp),
        }
        self.collections = json.dumps(cols)

    @gl.public.write
    def compute_consensus_floor(self, collection_id: str) -> str:
        """Compute consensus floor price using multi-market comparative logic.

        Parameters:
            collection_id: Local collection key.

        Returns:
            Report id string.
        """
        cid = self._norm(collection_id)
        cols = json.loads(self.collections)
        if cid not in cols:
            _raise_user_error(f"{ERROR_EXPECTED} collection not found")
        c = cols[cid]

        def leader_fn():
            os_payload = self._fetch_market(c["opensea_url"])
            bl_payload = self._fetch_market(c["blur_url"])
            me_payload = self._fetch_market(c["magiceden_url"])

            os_floor = self._parse_floor("opensea", cid, os_payload)
            bl_floor = self._parse_floor("blur", cid, bl_payload)
            me_floor = self._parse_floor("magiceden", cid, me_payload)

            entries = [os_floor, bl_floor, me_floor]
            floors = [float(e["floor_eth"]) for e in entries]
            min_floor = min(floors)
            max_floor = max(floors)
            spread_bps = int(((max_floor - min_floor) * 10000) / max(min_floor, 0.0000001))

            weighted_sum = 0.0
            weight_total = 0.0
            for e in entries:
                w = float(e["confidence"]) * self._risk_mult(str(e["wash_risk"]))
                weighted_sum += float(e["floor_eth"]) * w
                weight_total += w

            if weight_total <= 0:
                _raise_user_error(f"{ERROR_LLM} zero aggregate confidence")

            consensus = weighted_sum / weight_total
            stale = spread_bps > int(c["max_spread_bps"])
            return {
                "entries": entries,
                "consensus_floor_eth": consensus,
                "spread_bps": spread_bps,
                "stale": stale,
                "bucket": int(consensus * 1000),
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

            vo = leader_fn()
            lo = leaders_res.calldata
            if bool(vo.get("stale", False)) != bool(lo.get("stale", False)):
                return False
            return int(vo.get("bucket", -1)) == int(lo.get("bucket", -2))

        out = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        rid = str(self.next_report_id)
        self.next_report_id += 1
        reports = json.loads(self.reports)
        reports[rid] = {
            "report_id": rid,
            "collection_id": cid,
            "markets": out["entries"],
            "consensus_floor_eth": float(out["consensus_floor_eth"]),
            "spread_bps": int(out["spread_bps"]),
            "stale": bool(out["stale"]),
            "created_at": str(gl.block.timestamp),
            "requester": _sender(),
        }
        self.reports = json.dumps(reports)
        return rid

    @gl.public.view
    def get_collection(self, collection_id: str) -> str:
        """Read one collection config.

        Parameters:
            collection_id: Local collection key.

        Returns:
            Collection JSON string.
        """
        cid = self._norm(collection_id)
        cols = json.loads(self.collections)
        if cid not in cols:
            _raise_user_error(f"{ERROR_EXPECTED} collection not found")
        return json.dumps(cols[cid])

    @gl.public.view
    def get_report(self, report_id: str) -> str:
        """Read one floor consensus report.

        Parameters:
            report_id: Report id.

        Returns:
            Report JSON string.
        """
        rid = self._norm(report_id)
        reports = json.loads(self.reports)
        if rid not in reports:
            _raise_user_error(f"{ERROR_EXPECTED} report not found")
        return json.dumps(reports[rid])

    @gl.public.view
    def get_all_reports(self) -> str:
        """Read all floor consensus reports.

        Parameters:
            None.

        Returns:
            JSON map of reports.
        """
        return self.reports
