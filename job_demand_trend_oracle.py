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


class JobDemandTrendOracle(gl.Contract):
    """Tracks quarter over quarter job listing demand changes."""

    owner: Address
    configs: str
    snapshots: str
    analyses: str
    next_snapshot_id: u256
    next_analysis_id: u256

    def __init__(self):
        """Initialize storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.owner = Address(_sender())
        self.configs = "{}"
        self.snapshots = "{}"
        self.analyses = "{}"
        self.next_snapshot_id = 1
        self.next_analysis_id = 1

    def _require_owner(self) -> None:
        if _sender().strip().lower() != str(self.owner).strip().lower():
            _raise_user_error(f"{ERROR_EXPECTED} only owner")

    def _norm(self, v: str) -> str:
        return str(v).strip().lower()

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

    def _extract_count(self, role: str, skill: str, source: str, html: str) -> int:
        prompt = f"""
Extract active job listing count from this search page.
Return JSON only with keys:
- count: integer
- confidence: integer 0 to 100
- rationale: short string

Role: {role}
Skill: {skill}
Source: {source}
HTML sample:\n{html[:12000]}
"""
        out = gl.nondet.exec_prompt(prompt, response_format="json")
        if not isinstance(out, dict):
            _raise_user_error(f"{ERROR_LLM} invalid extraction response")

        try:
            count = int(out.get("count", -1))
            confidence = int(out.get("confidence", -1))
        except Exception:
            _raise_user_error(f"{ERROR_LLM} invalid numeric fields")

        if count < 0:
            _raise_user_error(f"{ERROR_LLM} invalid count")
        if confidence < 0 or confidence > 100:
            _raise_user_error(f"{ERROR_LLM} invalid confidence")
        return count

    @gl.public.write
    def register_target(self, target_id: str, role: str, skill: str, source: str, min_change_bps: int) -> None:
        """Register a job demand monitoring target.

        Parameters:
            target_id: Local identifier.
            role: Role phrase to search.
            skill: Skill phrase to search.
            source: Source name indeed or linkedin.
            min_change_bps: Required quarter change threshold in bps.

        Returns:
            None.
        """
        self._require_owner()
        tid = self._norm(target_id)
        role_clean = str(role).strip()
        skill_clean = str(skill).strip()
        src = self._norm(source)
        if len(tid) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid target_id")
        if len(role_clean) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid role")
        if len(skill_clean) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid skill")
        if src not in {"indeed", "linkedin"}:
            _raise_user_error(f"{ERROR_EXPECTED} invalid source")
        if min_change_bps < 1 or min_change_bps > 50000:
            _raise_user_error(f"{ERROR_EXPECTED} invalid min_change_bps")

        cfg = json.loads(self.configs)
        cfg[tid] = {
            "target_id": tid,
            "role": role_clean,
            "skill": skill_clean,
            "source": src,
            "min_change_bps": int(min_change_bps),
            "updated_at": str(gl.block.timestamp),
        }
        self.configs = json.dumps(cfg)

    @gl.public.write
    def capture_snapshot(self, target_id: str, quarter_label: str, search_url: str) -> str:
        """Capture one quarter snapshot of listing count.

        Parameters:
            target_id: Registered target id.
            quarter_label: Quarter label like 2026Q2.
            search_url: Public search page URL.

        Returns:
            Snapshot id string.
        """
        tid = self._norm(target_id)
        qlabel = str(quarter_label).strip().upper()
        url = str(search_url).strip()
        if len(qlabel) < 4:
            _raise_user_error(f"{ERROR_EXPECTED} invalid quarter_label")
        if not (url.startswith("https://") or url.startswith("http://")):
            _raise_user_error(f"{ERROR_EXPECTED} invalid search_url")

        cfg = json.loads(self.configs)
        if tid not in cfg:
            _raise_user_error(f"{ERROR_EXPECTED} target not found")
        c = cfg[tid]

        def leader_fn():
            html = self._fetch_page(url)
            count = self._extract_count(c["role"], c["skill"], c["source"], html)
            return {"count": int(count), "bucket": int(count / 10)}

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
            return int(vo.get("bucket", -1)) == int(lo.get("bucket", -2))

        out = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        sid = str(self.next_snapshot_id)
        self.next_snapshot_id += 1
        snaps = json.loads(self.snapshots)
        snaps[sid] = {
            "snapshot_id": sid,
            "target_id": tid,
            "quarter_label": qlabel,
            "search_url": url,
            "listing_count": int(out["count"]),
            "captured_by": _sender(),
            "created_at": str(gl.block.timestamp),
        }
        self.snapshots = json.dumps(snaps)
        return sid

    @gl.public.write
    def analyze_quarter_change(self, target_id: str, previous_snapshot_id: str, current_snapshot_id: str) -> str:
        """Analyze quarter change and decide unlock signal.

        Parameters:
            target_id: Registered target id.
            previous_snapshot_id: Prior quarter snapshot id.
            current_snapshot_id: Current quarter snapshot id.

        Returns:
            Analysis id string.
        """
        tid = self._norm(target_id)
        cfg = json.loads(self.configs)
        if tid not in cfg:
            _raise_user_error(f"{ERROR_EXPECTED} target not found")
        snaps = json.loads(self.snapshots)
        prev_id = self._norm(previous_snapshot_id)
        curr_id = self._norm(current_snapshot_id)
        if prev_id not in snaps or curr_id not in snaps:
            _raise_user_error(f"{ERROR_EXPECTED} snapshot not found")

        prev = snaps[prev_id]
        curr = snaps[curr_id]
        if self._norm(prev.get("target_id", "")) != tid or self._norm(curr.get("target_id", "")) != tid:
            _raise_user_error(f"{ERROR_EXPECTED} snapshot target mismatch")

        prev_count = int(prev.get("listing_count", 0))
        curr_count = int(curr.get("listing_count", 0))
        if prev_count <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} invalid previous count")

        change_bps = int(((curr_count - prev_count) * 10000) / prev_count)
        threshold = int(cfg[tid]["min_change_bps"])
        direction = "flat"
        if change_bps > 0:
            direction = "growth"
        elif change_bps < 0:
            direction = "shrink"

        materially_grew = change_bps >= threshold
        materially_shrank = change_bps <= -threshold
        unlock = materially_grew

        aid = str(self.next_analysis_id)
        self.next_analysis_id += 1
        analyses = json.loads(self.analyses)
        analyses[aid] = {
            "analysis_id": aid,
            "target_id": tid,
            "previous_snapshot_id": prev_id,
            "current_snapshot_id": curr_id,
            "previous_count": prev_count,
            "current_count": curr_count,
            "change_bps": change_bps,
            "direction": direction,
            "materially_grew": materially_grew,
            "materially_shrank": materially_shrank,
            "unlock_education_investment": unlock,
            "created_at": str(gl.block.timestamp),
        }
        self.analyses = json.dumps(analyses)
        return aid

    @gl.public.view
    def get_target(self, target_id: str) -> str:
        """Read target configuration.

        Parameters:
            target_id: Target id.

        Returns:
            Target JSON string.
        """
        tid = self._norm(target_id)
        cfg = json.loads(self.configs)
        if tid not in cfg:
            _raise_user_error(f"{ERROR_EXPECTED} target not found")
        return json.dumps(cfg[tid])

    @gl.public.view
    def get_snapshot(self, snapshot_id: str) -> str:
        """Read one snapshot.

        Parameters:
            snapshot_id: Snapshot id.

        Returns:
            Snapshot JSON string.
        """
        sid = self._norm(snapshot_id)
        snaps = json.loads(self.snapshots)
        if sid not in snaps:
            _raise_user_error(f"{ERROR_EXPECTED} snapshot not found")
        return json.dumps(snaps[sid])

    @gl.public.view
    def get_analysis(self, analysis_id: str) -> str:
        """Read one analysis.

        Parameters:
            analysis_id: Analysis id.

        Returns:
            Analysis JSON string.
        """
        aid = self._norm(analysis_id)
        analyses = json.loads(self.analyses)
        if aid not in analyses:
            _raise_user_error(f"{ERROR_EXPECTED} analysis not found")
        return json.dumps(analyses[aid])

    @gl.public.view
    def get_all_analyses(self) -> str:
        """Read all analyses.

        Parameters:
            None.

        Returns:
            JSON map.
        """
        return self.analyses
