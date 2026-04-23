# v0.1.0
# { "Depends": "py-genlayer:test" }

from genlayer import *
import json
import hashlib

ERROR_EXPECTED  = "[EXPECTED]"
ERROR_EXTERNAL  = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM       = "[LLM_ERROR]"


@allow_storage
@dataclass
class MigrationPlan:
    plan_id: str
    old_address: str
    new_code_hash: str       # first 16 chars of SHA-256 of new contract code
    steps: str               # JSON list of step dicts
    breaking_changes: str    # JSON list of plain-English descriptions
    complexity_score: u256   # 0–100 scale
    step_count: u256
    state_snapshot: str      # JSON of old contract state (truncated to 1 KB)
    status: str              # PLANNED | EXECUTING | COMPLETE | FAILED
    created_at: u256


class StateMigrator(gl.Contract):
    node_url: str
    counter: u256
    plans: TreeMap[str, MigrationPlan]
    plan_order: DynArray[str]
    logs: TreeMap[str, str]   # plan_id -> JSON list of step log dicts

    def __init__(self, node_url: str = ""):
        self.node_url = str(node_url).strip()
        self.counter  = u256(0)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _next_id(self, prefix: str) -> str:
        n = int(self.counter) + 1
        self.counter = u256(n)
        return f"{prefix}-{n}"

    def _fetch_state(self, node_url: str, address: str) -> dict:
        url = f"{node_url.rstrip('/')}/contract/state?address={address}"
        try:
            res = gl.nondet.web.get(url)
            if res.status == 404:
                raise gl.vm.UserError(
                    f"{ERROR_EXTERNAL} Contract {address[:12]}... not found on node"
                )
            if res.status >= 500:
                raise gl.vm.UserError(f"{ERROR_TRANSIENT} Node unavailable ({res.status})")
            if res.status >= 400:
                raise gl.vm.UserError(f"{ERROR_EXTERNAL} Node returned {res.status}")
            return json.loads(res.body.decode("utf-8"))
        except gl.vm.UserError:
            raise
        except Exception as e:
            raise gl.vm.UserError(f"{ERROR_TRANSIENT} Failed to fetch contract state: {e}")

    def _append_log(self, plan_id: str, entry: dict) -> None:
        existing = json.loads(self.logs[plan_id]) if plan_id in self.logs else []
        existing.append(entry)
        self.logs[plan_id] = json.dumps(existing)

    # ── Write methods ─────────────────────────────────────────────────────────

    @gl.public.write
    def plan_migration(self, old_contract_address: str, new_contract_code: str) -> str:
        """
        Fetch the old contract's on-chain state snapshot, then use AI to compare
        it against the new contract code. Produces a step-by-step migration plan,
        flags breaking changes that could cause data loss, and scores complexity.
        Emits: [MigrationPlanned], [BreakingChangesDetected]
        Returns plan_id.
        """
        addr = str(old_contract_address).strip()
        code = str(new_contract_code).strip()
        if not addr:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} old_contract_address cannot be empty")
        if not code:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} new_contract_code cannot be empty")

        plan_id   = self._next_id("plan")
        node_url  = self.node_url
        code_hash = hashlib.sha256(code.encode()).hexdigest()[:16]

        def analyse() -> dict:
            # Fetch old state — graceful: proceed with empty snapshot if unreachable
            state_raw   = {}
            fetch_error = ""
            try:
                state_raw = self._fetch_state(node_url, addr)
            except gl.vm.UserError as e:
                fetch_error = getattr(e, "message", str(e))
            except Exception as e:
                fetch_error = str(e)

            old_fields = json.dumps(
                state_raw.get("fields", state_raw), indent=2
            )[:2000]

            prompt = f"""You are a GenLayer smart contract migration expert.

OLD CONTRACT state (address: {addr}):
{old_fields or "[unavailable: " + fetch_error[:100] + "]"}

NEW CONTRACT code to migrate to:
{code[:3000]}

Compare the storage layout between old and new. For every field:
  same name + same type  →  "copy" step (LOW risk)
  new field not in old   →  "set_default" step (LOW risk, use zero/empty default)
  type changed           →  "transform" step (MEDIUM–HIGH risk, data loss possible)
  field removed          →  breaking_change entry (HIGH risk, data lost permanently)

Also flag any dataclass field reordering (storage layout corruption) or signatory
list changes (access control break) as breaking changes.

Respond ONLY with valid JSON (no markdown):
{{
  "steps": [
    {{
      "index": 1,
      "step_type": "copy|set_default|transform|skip",
      "description": "plain-English description of this step",
      "old_field": "field_name or null",
      "new_field": "field_name",
      "transform": "none or description of data transformation",
      "setter_fn": "setter function name on new contract or null",
      "risk": "LOW|MEDIUM|HIGH"
    }}
  ],
  "breaking_changes": [
    "plain-English description of each breaking change"
  ],
  "complexity_score": 0-100,
  "summary": "one-paragraph migration overview"
}}"""

            raw = gl.nondet.exec_prompt(prompt, response_format="json")
            if not isinstance(raw, dict):
                raise gl.vm.UserError(f"{ERROR_LLM} Expected dict from LLM")

            steps = raw.get("steps", [])
            if not isinstance(steps, list):
                raise gl.vm.UserError(f"{ERROR_LLM} steps must be a list")

            bc = raw.get("breaking_changes", [])
            if not isinstance(bc, list):
                bc = []

            complexity = max(0, min(100, int(raw.get("complexity_score", 50))))

            return {
                "state_snapshot":   old_fields,
                "steps":            steps,
                "breaking_changes": bc,
                "complexity_score": complexity,
                "summary":          str(raw.get("summary", "")),
            }

        def validator(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                leader_msg = getattr(leaders_res, "message", "")
                try:
                    analyse()
                    return False
                except gl.vm.UserError as e:
                    vmsg = getattr(e, "message", str(e))
                    if vmsg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
                        return True
                    return vmsg == leader_msg
                except Exception:
                    return False
            try:
                val = analyse()
            except Exception:
                return False
            ld = leaders_res.calldata
            # Same code always produces the same number of migration steps
            return len(ld.get("steps", [])) == len(val.get("steps", []))

        result = gl.vm.run_nondet_unsafe(analyse, validator)
        now    = int(gl.block.timestamp)

        self.plans[plan_id] = MigrationPlan(
            plan_id=plan_id,
            old_address=addr,
            new_code_hash=code_hash,
            steps=json.dumps(result["steps"]),
            breaking_changes=json.dumps(result["breaking_changes"]),
            complexity_score=u256(result["complexity_score"]),
            step_count=u256(len(result["steps"])),
            state_snapshot=result["state_snapshot"][:1000],
            status="PLANNED",
            created_at=u256(now),
        )
        self.plan_order.append(plan_id)
        self.logs[plan_id] = "[]"

        bc_count = len(result["breaking_changes"])
        print(
            f"[MigrationPlanned] id={plan_id} addr={addr[:12]} "
            f"steps={len(result['steps'])} breaking={bc_count} "
            f"complexity={result['complexity_score']}"
        )
        if bc_count > 0:
            print(
                f"[BreakingChangesDetected] id={plan_id} count={bc_count}"
            )
        return plan_id

    @gl.public.write
    def execute_migration(
        self,
        plan_id: str,
        new_contract_address: str,
        dry_run: bool,
    ) -> str:
        """
        Execute or simulate each step of a migration plan against the new contract.
        dry_run=True logs what would happen without making any RPC calls.
        dry_run=False calls each step's setter on the new contract via the node.
        A state snapshot taken during plan_migration is preserved for rollback reference.
        Emits: [StepExecuted], [MigrationComplete], [MigrationFailed]
        Returns JSON execution summary.
        """
        pid = str(plan_id).strip()
        if pid not in self.plans:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Plan {pid} not found")
        plan = self.plans[pid]
        if plan.status not in ("PLANNED", "FAILED"):
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Plan status is {plan.status} — only PLANNED or FAILED plans can run"
            )
        if not dry_run and not self.node_url:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} node_url not configured — set it at deploy time to run live migrations"
            )

        new_addr = str(new_contract_address).strip()
        steps    = json.loads(plan.steps)
        node_url = self.node_url

        def execute_steps() -> dict:
            results     = []
            all_success = True

            for step in steps:
                idx    = step.get("index", 0)
                s_type = step.get("step_type", "skip")
                desc   = step.get("description", "")
                setter = step.get("setter_fn")
                risk   = step.get("risk", "LOW")

                entry = {
                    "index":       idx,
                    "description": desc,
                    "step_type":   s_type,
                    "risk":        risk,
                    "success":     False,
                    "message":     "",
                    "dry_run":     dry_run,
                }

                if s_type == "skip" or not setter:
                    entry["success"] = True
                    entry["message"] = "Skipped — no setter function required"

                elif dry_run:
                    entry["success"] = True
                    entry["message"] = (
                        f"[DRY RUN] Would call {setter}() on {new_addr[:12]}..."
                    )

                else:
                    # Real execution via node RPC
                    url = (
                        f"{node_url.rstrip('/')}/contract/call"
                        f"?address={new_addr}&function={setter}"
                    )
                    try:
                        res = gl.nondet.web.get(url)
                        if res.status >= 500:
                            raise gl.vm.UserError(
                                f"{ERROR_TRANSIENT} Node unavailable ({res.status})"
                            )
                        if res.status >= 400:
                            body = json.loads(res.body.decode("utf-8"))
                            err  = body.get("error", f"HTTP {res.status}")
                            entry["success"] = False
                            entry["message"] = f"Step failed: {err}"
                            all_success      = False
                        else:
                            entry["success"] = True
                            entry["message"] = f"Called {setter}() — OK"
                    except gl.vm.UserError:
                        raise
                    except Exception as e:
                        entry["success"] = False
                        entry["message"] = f"Network error: {e}"
                        all_success      = False

                results.append(entry)

            return {"results": results, "all_success": all_success}

        def validator(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                leader_msg = getattr(leaders_res, "message", "")
                try:
                    execute_steps()
                    return False
                except gl.vm.UserError as e:
                    vmsg = getattr(e, "message", str(e))
                    if vmsg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
                        return True
                    return vmsg == leader_msg
                except Exception:
                    return False

            ld = leaders_res.calldata

            if dry_run:
                # Dry run is deterministic — re-run and compare step-by-step
                try:
                    val = execute_steps()
                except Exception:
                    return False
                ld_flags  = [r["success"] for r in ld.get("results", [])]
                val_flags = [r["success"] for r in val.get("results", [])]
                return ld_flags == val_flags
            else:
                # Live execution: state already changed — verify structure only
                # (re-executing would double-apply each step)
                return len(ld.get("results", [])) == len(steps)

        result = gl.vm.run_nondet_unsafe(execute_steps, validator)
        now    = int(gl.block.timestamp)

        # Record each step and emit events
        for r in result["results"]:
            self._append_log(pid, {
                "index":       r["index"],
                "description": r["description"],
                "success":     r["success"],
                "message":     r["message"],
                "dry_run":     r["dry_run"],
                "logged_at":   now,
            })
            print(
                f"[StepExecuted] plan={pid} step={r['index']} "
                f"success={r['success']} dry_run={dry_run}"
            )

        # Update status — dry-run leaves plan as PLANNED
        plan = self.plans[pid]
        if not dry_run:
            if result["all_success"]:
                plan.status = "COMPLETE"
                print(f"[MigrationComplete] plan={pid} steps={len(result['results'])}")
            else:
                plan.status = "FAILED"
                print(f"[MigrationFailed] plan={pid} — check execution log for details")
        self.plans[pid] = plan

        return json.dumps({
            "plan_id":     pid,
            "dry_run":     dry_run,
            "all_success": result["all_success"],
            "steps_run":   len(result["results"]),
            "snapshot_available": True,
        })

    # ── View methods ──────────────────────────────────────────────────────────

    @gl.public.view
    def get_plan(self, plan_id: str) -> str:
        pid = str(plan_id).strip()
        if pid not in self.plans:
            return json.dumps({"error": "not found"})
        p = self.plans[pid]
        return json.dumps({
            "plan_id":          p.plan_id,
            "old_address":      p.old_address,
            "new_code_hash":    p.new_code_hash,
            "steps":            json.loads(p.steps),
            "breaking_changes": json.loads(p.breaking_changes),
            "complexity_score": int(p.complexity_score),
            "step_count":       int(p.step_count),
            "state_snapshot":   p.state_snapshot,
            "status":           p.status,
            "created_at":       int(p.created_at),
        })

    @gl.public.view
    def get_execution_log(self, plan_id: str) -> str:
        pid = str(plan_id).strip()
        if pid not in self.logs:
            return json.dumps([])
        return self.logs[pid]

    @gl.public.view
    def list_plans(self) -> str:
        result = []
        for i in range(len(self.plan_order)):
            pid = self.plan_order[i]
            if pid in self.plans:
                p = self.plans[pid]
                result.append({
                    "plan_id":          p.plan_id,
                    "old_address":      p.old_address,
                    "status":           p.status,
                    "step_count":       int(p.step_count),
                    "complexity_score": int(p.complexity_score),
                    "breaking_changes": len(json.loads(p.breaking_changes)),
                    "created_at":       int(p.created_at),
                })
        return json.dumps(result)
