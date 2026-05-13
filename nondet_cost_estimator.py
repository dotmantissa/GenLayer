# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"


WEB_BASE_UNITS = 1200
WEB_PER_KB_UNITS = 80
LLM_BASE_UNITS = 5000
LLM_PER_TOKEN_UNITS = 6
EXPENSIVE_THRESHOLD_UNITS = 12000


def _raise_user_error(message: str) -> None:
    err_cls = getattr(gl, "UserError", Exception)
    raise err_cls(message)


class NondetCostEstimator(gl.Contract):
    """Estimates cost of web fetch and LLM nondeterministic blocks before deployment."""

    reports: str
    next_report_id: u256

    def __init__(self):
        """Initialize storage state.

        Parameters:
            None.

        Returns:
            None.
        """
        self.reports = "{}"
        self.next_report_id = 1

    @gl.public.write
    def estimate_contract_cost(self, operations_json: str, budget_units: int) -> str:
        """Estimate nondeterministic cost for a list of operations.

        Parameters:
            operations_json: JSON array of operation objects.
            budget_units: Total soft budget threshold for warnings.

        Returns:
            Report id string.
        """
        if budget_units <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} budget_units must be positive")

        try:
            operations = json.loads(operations_json)
        except Exception:
            _raise_user_error(f"{ERROR_EXPECTED} invalid operations_json")

        if not isinstance(operations, list) or len(operations) == 0:
            _raise_user_error(f"{ERROR_EXPECTED} operations must be non empty list")

        breakdown = []
        total_units = 0
        expensive_ops = []

        for idx, op in enumerate(operations):
            if not isinstance(op, dict):
                _raise_user_error(f"{ERROR_EXPECTED} operation must be object")

            op_type = str(op.get("type", "")).strip().lower()
            label = str(op.get("label", f"op_{idx + 1}")).strip()
            if len(label) < 1:
                label = f"op_{idx + 1}"

            cost_units = 0
            details = ""

            if op_type == "web_fetch":
                response_kb = int(op.get("estimated_response_kb", 0))
                if response_kb < 0:
                    _raise_user_error(f"{ERROR_EXPECTED} estimated_response_kb out of range")
                cost_units = WEB_BASE_UNITS + (response_kb * WEB_PER_KB_UNITS)
                details = f"base {WEB_BASE_UNITS} plus {response_kb}kb x {WEB_PER_KB_UNITS}"
            elif op_type == "llm_call":
                prompt_tokens = int(op.get("estimated_prompt_tokens", 0))
                output_tokens = int(op.get("estimated_output_tokens", 0))
                if prompt_tokens < 0 or output_tokens < 0:
                    _raise_user_error(f"{ERROR_EXPECTED} token estimate out of range")
                token_total = prompt_tokens + output_tokens
                cost_units = LLM_BASE_UNITS + (token_total * LLM_PER_TOKEN_UNITS)
                details = f"base {LLM_BASE_UNITS} plus {token_total} tokens x {LLM_PER_TOKEN_UNITS}"
            else:
                _raise_user_error(f"{ERROR_EXPECTED} unsupported operation type")

            total_units += cost_units
            item = {
                "label": label,
                "type": op_type,
                "estimated_units": cost_units,
                "details": details,
                "expensive": cost_units >= EXPENSIVE_THRESHOLD_UNITS,
            }
            breakdown.append(item)
            if item["expensive"]:
                expensive_ops.append(label)

        over_budget = total_units > int(budget_units)

        report_id = str(self.next_report_id)
        self.next_report_id += 1

        reports = json.loads(self.reports)
        reports[report_id] = {
            "report_id": report_id,
            "creator": str(gl.message.sender_account),
            "budget_units": int(budget_units),
            "total_estimated_units": int(total_units),
            "over_budget": bool(over_budget),
            "expensive_operations": expensive_ops,
            "breakdown": breakdown,
            "created_at": str(gl.block.timestamp),
        }
        self.reports = json.dumps(reports)
        return report_id

    @gl.public.view
    def get_report(self, report_id: str) -> str:
        """Read one cost report.

        Parameters:
            report_id: Report id string.

        Returns:
            Report JSON string.
        """
        reports = json.loads(self.reports)
        key = str(report_id)
        if key not in reports:
            _raise_user_error(f"{ERROR_EXPECTED} report not found")
        return json.dumps(reports[key])

    @gl.public.view
    def get_all_reports(self) -> str:
        """Read all cost reports.

        Parameters:
            None.

        Returns:
            Reports map JSON string.
        """
        return self.reports
