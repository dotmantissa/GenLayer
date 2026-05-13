# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"


def _raise_user_error(message: str) -> None:
    err_cls = getattr(gl, "UserError", Exception)
    raise err_cls(message)


class EquivalenceBoundaryTestGenerator(gl.Contract):
    """Generates boundary focused unit test vectors for equivalence tolerance calibration."""

    suites: str
    next_suite_id: u256

    def __init__(self):
        """Initialize storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.suites = "{}"
        self.next_suite_id = 1

    @gl.public.write
    def generate_suite(
        self,
        metric_label: str,
        baseline_value: int,
        tolerance_percent: int,
        step_tenths_percent: int,
    ) -> str:
        """Generate equivalence boundary vectors around configured tolerance.

        Parameters:
            metric_label: Name of metric under comparison.
            baseline_value: Baseline value used for delta calculations.
            tolerance_percent: Allowed tolerance percentage.
            step_tenths_percent: Step around boundary in tenths of a percent.

        Returns:
            Suite id string.
        """
        label = str(metric_label).strip()
        if len(label) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid metric_label")
        if baseline_value <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} baseline_value must be positive")
        if tolerance_percent < 1 or tolerance_percent > 100:
            _raise_user_error(f"{ERROR_EXPECTED} tolerance_percent out of range")
        if step_tenths_percent < 1 or step_tenths_percent > 50:
            _raise_user_error(f"{ERROR_EXPECTED} step_tenths_percent out of range")

        tol = float(tolerance_percent)
        step = float(step_tenths_percent) / 10.0
        probes = [tol - step, tol - (step / 2.0), tol, tol + (step / 2.0), tol + step]

        vectors = []
        baseline = float(baseline_value)
        for pct in probes:
            abs_pct = abs(pct)
            delta = baseline * (abs_pct / 100.0)
            below = int(round(baseline - delta))
            above = int(round(baseline + delta))

            expected = "EQUIVALENT" if abs_pct <= tol else "DIVERGENT"
            vectors.append(
                {
                    "percent_delta": round(abs_pct, 3),
                    "candidate_low": below,
                    "candidate_high": above,
                    "expected": expected,
                }
            )

        suite_id = str(self.next_suite_id)
        self.next_suite_id += 1

        suites = json.loads(self.suites)
        suites[suite_id] = {
            "suite_id": suite_id,
            "creator": str(gl.message.sender_account),
            "metric_label": label,
            "baseline_value": int(baseline_value),
            "tolerance_percent": int(tolerance_percent),
            "step_tenths_percent": int(step_tenths_percent),
            "vectors": vectors,
            "created_at": str(gl.block.timestamp),
        }
        self.suites = json.dumps(suites)
        return suite_id

    @gl.public.view
    def get_suite(self, suite_id: str) -> str:
        """Read one generated boundary suite.

        Parameters:
            suite_id: Suite id string.

        Returns:
            Suite JSON string.
        """
        suites = json.loads(self.suites)
        key = str(suite_id)
        if key not in suites:
            _raise_user_error(f"{ERROR_EXPECTED} suite not found")
        return json.dumps(suites[key])

    @gl.public.view
    def get_all_suites(self) -> str:
        """Read all generated suites.

        Parameters:
            None.

        Returns:
            Suites map JSON string.
        """
        return self.suites
