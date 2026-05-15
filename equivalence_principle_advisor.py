# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"


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


class EquivalencePrincipleAdvisor(gl.Contract):
    """Interactive rule based advisor for selecting equivalence principle variants."""

    recommendations: str
    next_recommendation_id: u256

    def __init__(self):
        """Initialize advisor storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.recommendations = "{}"
        self.next_recommendation_id = 1

    def _strict_eq_template(self, contract_name: str) -> str:
        return (
            "def leader_fn():\n"
            "    # Deterministic source with stable schema\n"
            "    return fetch_data()\n\n"
            "result = gl.eq_principle.strict_eq(leader_fn)\n"
            f"# Contract: {contract_name}\n"
        )

    def _numeric_template(self, contract_name: str) -> str:
        return (
            "def leader_fn():\n"
            "    return fetch_metric()\n\n"
            "def validator_fn(leaders_res: gl.vm.Result) -> bool:\n"
            "    if not isinstance(leaders_res, gl.vm.Return):\n"
            "        return False\n"
            "    validator_val = leader_fn()\n"
            "    leader_val = leaders_res.calldata\n"
            "    if validator_val == 0:\n"
            "        return leader_val == 0\n"
            "    ratio = float(leader_val) / float(validator_val)\n"
            "    return ratio >= 0.95 and ratio <= 1.05\n\n"
            "result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)\n"
            f"# Contract: {contract_name}\n"
        )

    def _subjective_template(self, contract_name: str) -> str:
        return (
            "def analyze_fn():\n"
            "    return gl.nondet.exec_prompt(task, response_format='json')\n\n"
            "result = gl.eq_principle.prompt_comparative(\n"
            "    analyze_fn,\n"
            "    principle='Must agree on final decision and core rationale'\n"
            ")\n"
            f"# Contract: {contract_name}\n"
        )

    def _boolean_template(self, contract_name: str) -> str:
        return (
            "def leader_fn():\n"
            "    # Return True or False from deterministic checks\n"
            "    return run_boolean_check()\n\n"
            "result = gl.eq_principle.strict_eq(leader_fn)\n"
            f"# Contract: {contract_name}\n"
        )

    @gl.public.write
    def recommend_principle(
        self,
        output_type: str,
        has_stable_schema: bool,
        has_numeric_tolerance: bool,
        has_subjective_judgment: bool,
        needs_exact_match: bool,
        contract_name: str,
    ) -> str:
        """Recommend an equivalence principle variant and return annotated template.

        Parameters:
            output_type: One of deterministic, numeric, subjective, boolean.
            has_stable_schema: True if validator outputs have stable shape and fields.
            has_numeric_tolerance: True if near values should be treated as equivalent.
            has_subjective_judgment: True if interpretation by LLM or human style is needed.
            needs_exact_match: True when byte or field exact matching is required.
            contract_name: Contract label used in generated template comments.

        Returns:
            Recommendation id string.
        """
        out_type = str(output_type).strip().lower()
        cname = str(contract_name).strip()
        if out_type not in ["deterministic", "numeric", "subjective", "boolean"]:
            _raise_user_error(f"{ERROR_EXPECTED} invalid output_type")
        if len(cname) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid contract_name")

        if out_type == "numeric" and not has_numeric_tolerance and not needs_exact_match:
            _raise_user_error(f"{ERROR_EXPECTED} numeric output requires tolerance or exact match decision")
        if out_type == "subjective" and not has_subjective_judgment:
            _raise_user_error(f"{ERROR_EXPECTED} subjective output requires subjective_judgment true")
        if has_subjective_judgment and needs_exact_match:
            _raise_user_error(f"{ERROR_EXPECTED} subjective judgment conflicts with exact match")

        recommendation = ""
        rationale = ""
        template = ""

        if out_type == "subjective" or has_subjective_judgment:
            recommendation = "prompt_comparative"
            rationale = "Use semantic comparison when output meaning matters more than exact tokens."
            template = self._subjective_template(cname)
        elif out_type == "numeric" and has_numeric_tolerance:
            recommendation = "run_nondet_unsafe_custom_validator"
            rationale = "Use custom validator with bounded numeric tolerance to avoid false divergence."
            template = self._numeric_template(cname)
        elif out_type == "boolean":
            recommendation = "strict_eq"
            rationale = "Boolean checks should be exact and deterministic across validators."
            template = self._boolean_template(cname)
        else:
            if has_stable_schema and needs_exact_match:
                recommendation = "strict_eq"
                rationale = "Deterministic and stable outputs are safest under strict exact equality."
                template = self._strict_eq_template(cname)
            elif has_stable_schema and not needs_exact_match:
                recommendation = "run_nondet_unsafe_custom_validator"
                rationale = "Stable schema with flexible semantics benefits from custom field level validator."
                template = self._numeric_template(cname)
            else:
                recommendation = "run_nondet_unsafe_custom_validator"
                rationale = "Unstable schema requires explicit validator logic to normalize and compare safely."
                template = self._numeric_template(cname)

        rid = str(self.next_recommendation_id)
        self.next_recommendation_id += 1

        rec = {
            "recommendation_id": rid,
            "requester": _sender(),
            "contract_name": cname,
            "output_type": out_type,
            "inputs": {
                "has_stable_schema": bool(has_stable_schema),
                "has_numeric_tolerance": bool(has_numeric_tolerance),
                "has_subjective_judgment": bool(has_subjective_judgment),
                "needs_exact_match": bool(needs_exact_match),
            },
            "recommended_variant": recommendation,
            "rationale": rationale,
            "annotated_template": template,
            "created_at": str(gl.block.timestamp),
        }

        recommendations = json.loads(self.recommendations)
        recommendations[rid] = rec
        self.recommendations = json.dumps(recommendations)
        return rid

    @gl.public.view
    def get_recommendation(self, recommendation_id: str) -> str:
        """Read one saved recommendation.

        Parameters:
            recommendation_id: Recommendation identifier.

        Returns:
            Recommendation JSON string.
        """
        key = str(recommendation_id).strip()
        recommendations = json.loads(self.recommendations)
        if key not in recommendations:
            _raise_user_error(f"{ERROR_EXPECTED} recommendation not found")
        return json.dumps(recommendations[key])

    @gl.public.view
    def get_all_recommendations(self) -> str:
        """Read all saved recommendations.

        Parameters:
            None.

        Returns:
            JSON map of all recommendations.
        """
        return self.recommendations
