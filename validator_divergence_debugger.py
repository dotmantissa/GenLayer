# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"


def _raise_user_error(message: str) -> None:
    err_cls = getattr(gl, "UserError", Exception)
    raise err_cls(message)


class ValidatorDivergenceDebugger(gl.Contract):
    """Runs repeated validator style evaluations and surfaces output divergence."""

    runs: str
    next_run_id: u256

    def __init__(self):
        """Initialize state.

        Parameters:
            None.

        Returns:
            None.
        """
        self.runs = "{}"
        self.next_run_id = 1

    def _normalize(self, value) -> str:
        if isinstance(value, dict) or isinstance(value, list):
            return json.dumps(value, sort_keys=True)
        return str(value)

    @gl.public.write
    def debug_execution(
        self,
        execution_payload: str,
        equivalence_rule: str,
        validator_count: int,
    ) -> str:
        """Execute the same payload across validator slots and compute divergence.

        Parameters:
            execution_payload: Input context for the simulated contract execution.
            equivalence_rule: Human readable equivalence rule reference.
            validator_count: Number of validator slots to sample.

        Returns:
            Run id string.
        """
        payload = str(execution_payload).strip()
        rule = str(equivalence_rule).strip()
        if len(payload) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid execution_payload")
        if len(rule) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid equivalence_rule")
        if validator_count < 2 or validator_count > 25:
            _raise_user_error(f"{ERROR_EXPECTED} validator_count out of range")

        outputs = []
        normalized = []
        parsed_items = []

        for i in range(validator_count):
            prompt = f"""
You are validator slot {i + 1} of {validator_count}.
Evaluate this execution payload and return JSON when possible.

Execution payload:
{payload}

Equivalence rule:
{rule}
"""
            out = gl.nondet.exec_prompt(prompt)
            norm = self._normalize(out)
            outputs.append({"validator": i + 1, "output": norm})
            normalized.append(norm)
            try:
                parsed_items.append(json.loads(norm))
            except Exception:
                parsed_items.append(None)

        first = normalized[0]
        all_equal = True
        for n in normalized[1:]:
            if n != first:
                all_equal = False
                break

        differing_keys = []
        if not all_equal and all(p is not None and isinstance(p, dict) for p in parsed_items):
            all_keys = set()
            for p in parsed_items:
                all_keys.update(p.keys())
            for k in sorted(list(all_keys)):
                vals = [json.dumps(p.get(k, None), sort_keys=True) for p in parsed_items]
                if len(set(vals)) > 1:
                    differing_keys.append(str(k))

        run_id = str(self.next_run_id)
        self.next_run_id += 1

        runs = json.loads(self.runs)
        runs[run_id] = {
            "run_id": run_id,
            "requester": str(gl.message.sender_account),
            "execution_payload": payload,
            "equivalence_rule": rule,
            "validator_count": int(validator_count),
            "equivalent": bool(all_equal),
            "divergence_count": int(len(set(normalized))),
            "differing_keys": differing_keys,
            "validator_outputs": outputs,
            "created_at": str(gl.block.timestamp),
        }
        self.runs = json.dumps(runs)
        return run_id

    @gl.public.view
    def get_run(self, run_id: str) -> str:
        """Read one debug run.

        Parameters:
            run_id: Debug run id.

        Returns:
            Run JSON string.
        """
        runs = json.loads(self.runs)
        key = str(run_id)
        if key not in runs:
            _raise_user_error(f"{ERROR_EXPECTED} run not found")
        return json.dumps(runs[key])

    @gl.public.view
    def get_all_runs(self) -> str:
        """Read all debug runs.

        Parameters:
            None.

        Returns:
            Runs map JSON string.
        """
        return self.runs
