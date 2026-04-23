# v0.1.0
# { "Depends": "py-genlayer:test" }

from genlayer import *
import json

ERROR_EXPECTED  = "[EXPECTED]"
ERROR_EXTERNAL  = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM       = "[LLM_ERROR]"


@allow_storage
@dataclass
class Trace:
    trace_id: str
    target_address: str
    function_name: str
    args_json: str
    value_wei: u256
    success: str         # "true" | "false"
    result_json: str     # raw response or error message (truncated)
    explanation: str     # AI step-by-step explanation
    patterns: str        # JSON list[str] of identified failure patterns
    recorded_at: u256


@allow_storage
@dataclass
class Analysis:
    analysis_id: str
    kind: str            # "revert" | "optimize"
    context: str         # function name or code snippet header
    error_message: str
    diagnosis: str       # AI explanation
    suggestions: str     # JSON list
    analyzed_at: u256


@allow_storage
@dataclass
class Scenario:
    scenario_id: str
    contract_address: str
    description: str
    test_cases: str      # JSON list of generated test case dicts
    generated_at: u256


class ContractDebugger(gl.Contract):
    node_url: str
    counter: u256
    traces: TreeMap[str, Trace]
    trace_order: DynArray[str]
    analyses: TreeMap[str, Analysis]
    analysis_order: DynArray[str]
    scenarios: TreeMap[str, Scenario]
    scenario_order: DynArray[str]

    def __init__(self, node_url: str = ""):
        self.node_url = str(node_url).strip()
        self.counter  = u256(0)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _next_id(self, prefix: str) -> str:
        n = int(self.counter) + 1
        self.counter = u256(n)
        return f"{prefix}-{n}"

    def _rpc_call(self, node_url: str, target: str, fn: str, args: list) -> dict:
        url = (
            f"{node_url.rstrip('/')}/contract/call"
            f"?address={target}&function={fn}"
        )
        try:
            res = gl.nondet.web.get(url)
            if res.status >= 500:
                raise gl.vm.UserError(f"{ERROR_TRANSIENT} Node unavailable ({res.status})")
            if res.status >= 400:
                raise gl.vm.UserError(f"{ERROR_EXTERNAL} Node returned {res.status}")
            return json.loads(res.body.decode("utf-8"))
        except gl.vm.UserError:
            raise
        except Exception as e:
            raise gl.vm.UserError(f"{ERROR_TRANSIENT} RPC error: {e}")

    # ── Write methods ─────────────────────────────────────────────────────────

    @gl.public.write
    def trace_call(
        self,
        target_address: str,
        function_name: str,
        args_json: str,
        value_wei: int,
    ) -> str:
        """
        Calls a target contract function via the configured GenLayer node, then
        uses AI to produce a step-by-step plain-English trace of what happened
        and flags common failure patterns (bad permissions, stale data, etc.).
        Returns trace_id.
        Emits: [TraceRecorded]
        """
        if not self.node_url:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} node_url not configured on this debugger")

        trace_id = self._next_id("trace")
        addr     = str(target_address).strip()
        fn       = str(function_name).strip()
        args     = json.loads(args_json) if args_json.strip() not in ("", "[]") else []
        node_url = self.node_url

        def run() -> dict:
            # 1 — attempt the RPC call
            success   = False
            raw_resp  = {}
            error_msg = ""
            try:
                raw_resp = self._rpc_call(node_url, addr, fn, args)
                success  = "error" not in raw_resp and raw_resp.get("ok", True)
                if not success:
                    error_msg = str(raw_resp.get("error", raw_resp))
            except gl.vm.UserError as e:
                error_msg = getattr(e, "message", str(e))

            result_str = json.dumps(raw_resp) if success else error_msg

            # 2 — AI step-by-step explanation
            prompt = f"""You are an expert GenLayer smart contract debugger.

Call details:
  Target  : {addr}
  Function: {fn}
  Args    : {json.dumps(args)}
  Wei     : {value_wei}
  Outcome : {"SUCCESS" if success else "FAILURE"}
  Response: {result_str[:800]}

Produce a step-by-step plain-English explanation of what this call did.
Then identify which of these failure patterns apply (list only what matches):
  insufficient_balance, wrong_permissions, stale_oracle_data, already_exists,
  not_found, expired, invalid_input, contract_paused, reentrancy_guard

Respond ONLY with valid JSON (no markdown):
{{
  "steps": ["step 1", "step 2", ...],
  "summary": "one sentence",
  "patterns": ["pattern_name"],
  "root_cause": "plain English — why it succeeded or failed"
}}"""

            raw = gl.nondet.exec_prompt(prompt, response_format="json")
            if not isinstance(raw, dict):
                raw = {}

            return {
                "success":    success,
                "result_str": result_str[:400],
                "steps":      raw.get("steps", []),
                "summary":    str(raw.get("summary", "")),
                "patterns":   raw.get("patterns", []),
                "root_cause": str(raw.get("root_cause", "")),
            }

        def validator(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                leader_msg = getattr(leaders_res, "message", "")
                try:
                    run()
                    return False
                except gl.vm.UserError as e:
                    vmsg = getattr(e, "message", str(e))
                    if vmsg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
                        return True
                    return vmsg == leader_msg
                except Exception:
                    return False
            try:
                val = run()
            except Exception:
                return False
            ld = leaders_res.calldata
            # Must agree on success/failure — AI explanation may legitimately differ
            return ld.get("success") == val.get("success")

        result = gl.vm.run_nondet_unsafe(run, validator)
        now    = int(gl.block.timestamp)

        explanation = result["root_cause"]
        if result["steps"]:
            explanation += "\n\nSteps:\n" + "\n".join(
                f"  {i+1}. {s}" for i, s in enumerate(result["steps"])
            )

        self.traces[trace_id] = Trace(
            trace_id=trace_id,
            target_address=addr,
            function_name=fn,
            args_json=args_json[:200],
            value_wei=u256(max(0, int(value_wei))),
            success="true" if result["success"] else "false",
            result_json=result["result_str"],
            explanation=explanation,
            patterns=json.dumps(result["patterns"]),
            recorded_at=u256(now),
        )
        self.trace_order.append(trace_id)
        print(
            f"[TraceRecorded] id={trace_id} fn={fn} "
            f"success={result['success']} patterns={result['patterns']}"
        )
        return trace_id

    @gl.public.write
    def analyze_revert(
        self,
        error_message: str,
        function_name: str,
        args_json: str,
        contract_context: str,
    ) -> str:
        """
        Submit a revert error and get a structured diagnosis: what went wrong,
        which failure pattern it matches, and concrete fix suggestions.
        Returns analysis_id.
        Emits: [RevertAnalyzed]
        """
        err = str(error_message).strip()
        if not err:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} error_message cannot be empty")

        analysis_id = self._next_id("analysis")
        fn          = str(function_name).strip()
        ctx         = str(contract_context).strip()

        def run() -> dict:
            prompt = f"""You are a GenLayer smart contract expert debugging a transaction revert.

Failed call:
  Function : {fn}
  Args     : {args_json[:300]}
  Error    : {err[:400]}
  Context  : {ctx[:300] or "not provided"}

GenLayer error prefixes to recognise:
  [EXPECTED]  business logic rejection (access control, state validation, duplicate)
  [EXTERNAL]  external API returned an error code
  [TRANSIENT] network or infrastructure failure

Common revert patterns:
  wrong_permissions    "Only owner/admin/signatory" — caller not authorised
  duplicate_entry      "already registered/exists" — re-creation attempt
  not_found            "not found/registered" — missing resource
  wrong_status         "not OPEN/PENDING" — state machine violation
  expired              "expired/past deadline" — time-lock violated
  insufficient_balance "balance/funds" — not enough value
  invalid_input        empty string, out-of-range, bad format
  stale_oracle_data    cached/oracle value too old

Diagnose this revert precisely and suggest actionable fixes.

Respond ONLY with valid JSON (no markdown):
{{
  "pattern": "pattern_name",
  "diagnosis": "exact plain-English explanation of why it reverted",
  "suggestions": ["fix 1", "fix 2", ...],
  "severity": "LOW|MEDIUM|HIGH"
}}"""

            raw = gl.nondet.exec_prompt(prompt, response_format="json")
            if not isinstance(raw, dict):
                raise gl.vm.UserError(f"{ERROR_LLM} Expected dict from LLM")
            return {
                "pattern":     str(raw.get("pattern", "unknown")),
                "diagnosis":   str(raw.get("diagnosis", "")),
                "suggestions": raw.get("suggestions", []),
                "severity":    str(raw.get("severity", "MEDIUM")),
            }

        def validator(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                leader_msg = getattr(leaders_res, "message", "")
                try:
                    run()
                    return False
                except gl.vm.UserError as e:
                    vmsg = getattr(e, "message", str(e))
                    return vmsg == leader_msg if vmsg.startswith(ERROR_LLM) else False
                except Exception:
                    return False
            try:
                val = run()
            except Exception:
                return False
            ld = leaders_res.calldata
            # Agree if both identify the same failure pattern
            return ld.get("pattern") == val.get("pattern")

        result = gl.vm.run_nondet_unsafe(run, validator)
        now    = int(gl.block.timestamp)

        self.analyses[analysis_id] = Analysis(
            analysis_id=analysis_id,
            kind="revert",
            context=fn,
            error_message=err[:200],
            diagnosis=result["diagnosis"],
            suggestions=json.dumps(result["suggestions"]),
            analyzed_at=u256(now),
        )
        self.analysis_order.append(analysis_id)
        print(
            f"[RevertAnalyzed] id={analysis_id} pattern={result['pattern']} "
            f"severity={result['severity']}"
        )
        return analysis_id

    @gl.public.write
    def optimize_contract(self, contract_code: str) -> str:
        """
        AI reviews GenLayer contract code for inefficiencies specific to the
        GenVM model: nondet overuse, validator logic gaps, wrong storage types,
        missing error classification, float/money mistakes.
        Returns analysis_id.
        Emits: [OptimizationAnalyzed]
        """
        code = str(contract_code).strip()
        if not code:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} contract_code cannot be empty")

        analysis_id = self._next_id("analysis")

        def run() -> dict:
            prompt = f"""You are a GenLayer intelligent contract optimization expert.

Analyze this contract code for inefficiencies:

{code[:4000]}

Check for these GenLayer-specific issues:
  1. nondet_overuse       — web/LLM calls where deterministic alternatives exist
  2. validator_gap        — validator doesn't handle all [EXPECTED]/[EXTERNAL]/[TRANSIENT] paths
  3. wrong_storage_type   — Python dict/list instead of TreeMap/DynArray
  4. wrong_eq_principle   — strict_eq used for non-deterministic calls
  5. llm_fragility        — missing type checks, no key aliasing, no fallback parsing
  6. missing_error_prefix — errors raised without [EXPECTED]/[EXTERNAL]/[TRANSIENT] prefix
  7. redundant_fetch      — same external data fetched multiple times in one call
  8. float_money          — native float used for currency (use u256 atto-scale instead)
  9. storage_layout       — fields inserted mid-dataclass (must append at end)

Respond ONLY with valid JSON (no markdown):
{{
  "issues": [
    {{
      "priority": "HIGH|MEDIUM|LOW",
      "category": "category from list above",
      "location": "function or field name",
      "description": "what the issue is",
      "fix": "concrete fix"
    }}
  ],
  "summary": "one-paragraph overall assessment"
}}"""

            raw = gl.nondet.exec_prompt(prompt, response_format="json")
            if not isinstance(raw, dict):
                raise gl.vm.UserError(f"{ERROR_LLM} Expected dict from LLM")
            return {
                "issues":  raw.get("issues", []),
                "summary": str(raw.get("summary", "")),
            }

        def validator(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                leader_msg = getattr(leaders_res, "message", "")
                try:
                    run()
                    return False
                except gl.vm.UserError as e:
                    vmsg = getattr(e, "message", str(e))
                    return vmsg == leader_msg
                except Exception:
                    return False
            try:
                val = run()
            except Exception:
                return False
            ld = leaders_res.calldata
            # Agree if both found issues (or both found none) — LLM details vary
            leader_found  = len(ld.get("issues", [])) > 0
            val_found     = len(val.get("issues", [])) > 0
            return leader_found == val_found

        result = gl.vm.run_nondet_unsafe(run, validator)
        now    = int(gl.block.timestamp)

        self.analyses[analysis_id] = Analysis(
            analysis_id=analysis_id,
            kind="optimize",
            context=code[:80],
            error_message="",
            diagnosis=result["summary"],
            suggestions=json.dumps(result["issues"]),
            analyzed_at=u256(now),
        )
        self.analysis_order.append(analysis_id)
        print(
            f"[OptimizationAnalyzed] id={analysis_id} "
            f"issues={len(result['issues'])}"
        )
        return analysis_id

    @gl.public.write
    def generate_tests(
        self,
        contract_address: str,
        abi_json: str,
        scenario_description: str,
    ) -> str:
        """
        Describe a test scenario in plain English and get back a structured list
        of test cases: happy paths, edge cases, and expected reverts — ready to
        drop into a direct-mode test file.
        Returns scenario_id.
        Emits: [ScenarioGenerated]
        """
        desc = str(scenario_description).strip()
        if not desc:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} scenario_description cannot be empty")

        scenario_id = self._next_id("scenario")
        addr        = str(contract_address).strip()

        def run() -> dict:
            prompt = f"""You are a GenLayer smart contract testing expert.

Contract : {addr}
ABI      : {abi_json[:800] or "not provided"}
Scenario : "{desc}"

Generate test cases for this scenario covering:
  happy_path      — the primary success flow
  edge_case       — boundary values, empty inputs, large numbers, multi-party interactions
  expected_revert — unauthorised callers, wrong state, duplicate entries, expired windows

For each test case:
  name      : short snake_case name
  type      : happy_path | edge_case | expected_revert
  setup     : what storage state must exist before calling
  call      : {{"function": "name", "args": [...], "caller": "owner|user|stranger|signatory"}}
  expected  : return value, emitted event, or revert substring
  rationale : one sentence on why this case matters

Respond ONLY with valid JSON (no markdown):
{{
  "test_cases": [
    {{
      "name": "...",
      "type": "...",
      "setup": "...",
      "call": {{}},
      "expected": "...",
      "rationale": "..."
    }}
  ],
  "coverage_notes": "what is covered and any notable gaps"
}}"""

            raw = gl.nondet.exec_prompt(prompt, response_format="json")
            if not isinstance(raw, dict):
                raise gl.vm.UserError(f"{ERROR_LLM} Expected dict from LLM")
            cases = raw.get("test_cases", [])
            if not isinstance(cases, list):
                raise gl.vm.UserError(f"{ERROR_LLM} test_cases must be a list")
            return {
                "test_cases":     cases,
                "coverage_notes": str(raw.get("coverage_notes", "")),
            }

        def validator(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                leader_msg = getattr(leaders_res, "message", "")
                try:
                    run()
                    return False
                except gl.vm.UserError as e:
                    vmsg = getattr(e, "message", str(e))
                    return vmsg == leader_msg
                except Exception:
                    return False
            try:
                val = run()
            except Exception:
                return False
            ld = leaders_res.calldata
            # Agree if both generated at least one test case
            return (len(ld.get("test_cases", [])) > 0) == (len(val.get("test_cases", [])) > 0)

        result      = gl.vm.run_nondet_unsafe(run, validator)
        now         = int(gl.block.timestamp)

        self.scenarios[scenario_id] = Scenario(
            scenario_id=scenario_id,
            contract_address=addr,
            description=desc[:300],
            test_cases=json.dumps(result["test_cases"]),
            generated_at=u256(now),
        )
        self.scenario_order.append(scenario_id)
        print(
            f"[ScenarioGenerated] id={scenario_id} addr={addr[:12]} "
            f"cases={len(result['test_cases'])}"
        )
        return scenario_id

    # ── View methods ──────────────────────────────────────────────────────────

    @gl.public.view
    def get_trace(self, trace_id: str) -> str:
        if trace_id not in self.traces:
            return json.dumps({"error": "not found"})
        t = self.traces[trace_id]
        return json.dumps({
            "trace_id":       t.trace_id,
            "target_address": t.target_address,
            "function_name":  t.function_name,
            "args":           t.args_json,
            "value_wei":      int(t.value_wei),
            "success":        t.success == "true",
            "result":         t.result_json,
            "explanation":    t.explanation,
            "patterns":       json.loads(t.patterns),
            "recorded_at":    int(t.recorded_at),
        })

    @gl.public.view
    def get_analysis(self, analysis_id: str) -> str:
        if analysis_id not in self.analyses:
            return json.dumps({"error": "not found"})
        a = self.analyses[analysis_id]
        return json.dumps({
            "analysis_id": a.analysis_id,
            "kind":        a.kind,
            "context":     a.context,
            "error":       a.error_message,
            "diagnosis":   a.diagnosis,
            "suggestions": json.loads(a.suggestions),
            "analyzed_at": int(a.analyzed_at),
        })

    @gl.public.view
    def get_scenario(self, scenario_id: str) -> str:
        if scenario_id not in self.scenarios:
            return json.dumps({"error": "not found"})
        s = self.scenarios[scenario_id]
        return json.dumps({
            "scenario_id":      s.scenario_id,
            "contract_address": s.contract_address,
            "description":      s.description,
            "test_cases":       json.loads(s.test_cases),
            "generated_at":     int(s.generated_at),
        })

    @gl.public.view
    def list_traces(self) -> str:
        result = []
        for i in range(len(self.trace_order)):
            tid = self.trace_order[i]
            if tid in self.traces:
                t = self.traces[tid]
                result.append({
                    "trace_id":      t.trace_id,
                    "function_name": t.function_name,
                    "success":       t.success == "true",
                    "recorded_at":   int(t.recorded_at),
                })
        return json.dumps(result)

    @gl.public.view
    def list_analyses(self) -> str:
        result = []
        for i in range(len(self.analysis_order)):
            aid = self.analysis_order[i]
            if aid in self.analyses:
                a = self.analyses[aid]
                result.append({
                    "analysis_id": a.analysis_id,
                    "kind":        a.kind,
                    "context":     a.context,
                    "analyzed_at": int(a.analyzed_at),
                })
        return json.dumps(result)
