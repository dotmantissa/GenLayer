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


class ConsensusExplainPanel(gl.Contract):
    """Explains validator agreement or disagreement in plain language."""

    sessions: str
    next_session_id: u256

    def __init__(self):
        """Initialize storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.sessions = "{}"
        self.next_session_id = 1

    def _normalize_output(self, value):
        if isinstance(value, dict) or isinstance(value, list):
            return json.dumps(value, sort_keys=True)
        return str(value)

    @gl.public.write
    def explain_consensus(
        self,
        equivalence_principle: str,
        validator_outputs_json: str,
        expected_outcome: str,
    ) -> str:
        """Create a human readable explanation of validator consensus behavior.

        Parameters:
            equivalence_principle: Rule used to determine equivalence.
            validator_outputs_json: JSON array of validator outputs.
            expected_outcome: Expected final decision label.

        Returns:
            Session id string.
        """
        principle = str(equivalence_principle).strip()
        expected = str(expected_outcome).strip().lower()
        if len(principle) < 3:
            _raise_user_error(f"{ERROR_EXPECTED} invalid equivalence_principle")
        if expected not in ["allow", "deny"]:
            _raise_user_error(f"{ERROR_EXPECTED} invalid expected_outcome")

        try:
            raw_outputs = json.loads(validator_outputs_json)
        except Exception:
            _raise_user_error(f"{ERROR_EXPECTED} invalid validator_outputs_json")

        if not isinstance(raw_outputs, list) or len(raw_outputs) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} validator_outputs must have at least two entries")

        normalized = []
        parsed = []
        decisions = []

        for idx in range(len(raw_outputs)):
            item = raw_outputs[idx]
            if not isinstance(item, dict):
                _raise_user_error(f"{ERROR_EXPECTED} validator output must be object")
            if "output" not in item:
                _raise_user_error(f"{ERROR_EXPECTED} validator output missing output field")

            out = item.get("output")
            norm = self._normalize_output(out)
            normalized.append(norm)

            parsed_obj = None
            try:
                if isinstance(out, dict):
                    parsed_obj = out
                elif isinstance(out, str):
                    parsed_obj = json.loads(out)
                if parsed_obj is not None and isinstance(parsed_obj, dict):
                    parsed.append(parsed_obj)
                else:
                    parsed.append(None)
            except Exception:
                parsed.append(None)

            decision_value = ""
            if isinstance(parsed[-1], dict):
                for key in ["decision", "verdict", "result", "outcome"]:
                    if key in parsed[-1]:
                        decision_value = str(parsed[-1].get(key, "")).strip().lower()
                        break
            decisions.append(decision_value)

        unique_normalized_count = len(set(normalized))
        agreed = unique_normalized_count == 1

        differing_keys = []
        if not agreed and all(p is not None and isinstance(p, dict) for p in parsed):
            all_keys = set()
            for p in parsed:
                all_keys.update(p.keys())
            for k in sorted(list(all_keys)):
                vals = [json.dumps(p.get(k, None), sort_keys=True) for p in parsed]
                if len(set(vals)) > 1:
                    differing_keys.append(str(k))

        majority_decision = ""
        decision_counts = {}
        for d in decisions:
            if len(d) == 0:
                continue
            if d not in decision_counts:
                decision_counts[d] = 0
            decision_counts[d] += 1

        if len(decision_counts) > 0:
            best = sorted(decision_counts.items(), key=lambda x: (-int(x[1]), str(x[0])))[0]
            majority_decision = best[0]

        outcome_correct = majority_decision == expected and len(majority_decision) > 0

        summary_lines = []
        summary_lines.append(f"Validators were evaluated under principle: {principle}.")
        if agreed:
            summary_lines.append("All validators returned equivalent outputs.")
        else:
            summary_lines.append("Validators disagreed because outputs were not equivalent.")
            if len(differing_keys) > 0:
                summary_lines.append("Main fields that diverged: " + ", ".join(differing_keys[:10]) + ".")
            else:
                summary_lines.append("Divergence was detected at whole output level.")

        if len(majority_decision) > 0:
            summary_lines.append(f"Majority decision was {majority_decision} while expected outcome was {expected}.")
        else:
            summary_lines.append(f"No parseable majority decision was extracted. Expected outcome was {expected}.")

        if outcome_correct:
            summary_lines.append("The final outcome appears correct based on the expected outcome.")
        else:
            summary_lines.append("The final outcome appears incorrect based on the expected outcome.")

        sid = str(self.next_session_id)
        self.next_session_id += 1

        sessions = json.loads(self.sessions)
        sessions[sid] = {
            "session_id": sid,
            "requester": _sender(),
            "equivalence_principle": principle,
            "validator_count": len(raw_outputs),
            "agreed": bool(agreed),
            "unique_output_count": unique_normalized_count,
            "differing_keys": differing_keys,
            "majority_decision": majority_decision,
            "expected_outcome": expected,
            "outcome_correct": bool(outcome_correct),
            "plain_english_explanation": " ".join(summary_lines),
            "validator_outputs": raw_outputs,
            "created_at": str(gl.block.timestamp),
        }
        self.sessions = json.dumps(sessions)
        return sid

    @gl.public.view
    def get_session(self, session_id: str) -> str:
        """Read one explanation session.

        Parameters:
            session_id: Session identifier.

        Returns:
            JSON session string.
        """
        key = str(session_id).strip()
        sessions = json.loads(self.sessions)
        if key not in sessions:
            _raise_user_error(f"{ERROR_EXPECTED} session not found")
        return json.dumps(sessions[key])

    @gl.public.view
    def get_all_sessions(self) -> str:
        """Read all explanation sessions.

        Parameters:
            None.

        Returns:
            JSON object string mapping ids to sessions.
        """
        return self.sessions
