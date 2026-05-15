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


class DAOConstitutionGuard(gl.Contract):
    """Checks whether governance actions violate constitutional clauses."""

    owner: Address
    constitutions: str
    decisions: str
    next_decision_id: u256

    def __init__(self):
        """Initialize storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.owner = Address(_sender())
        self.constitutions = "{}"
        self.decisions = "{}"
        self.next_decision_id = 1

    def _require_owner(self) -> None:
        if _sender().strip().lower() != str(self.owner).strip().lower():
            _raise_user_error(f"{ERROR_EXPECTED} only owner")

    def _normalize_constitution_url(self, raw_url: str) -> str:
        url = str(raw_url).strip()
        if url.startswith("ipfs://"):
            cid_path = url[len("ipfs://"):]
            if len(cid_path) < 10:
                _raise_user_error(f"{ERROR_EXPECTED} invalid ipfs url")
            return "https://ipfs.io/ipfs/" + cid_path
        if url.startswith("https://") or url.startswith("http://"):
            return url
        _raise_user_error(f"{ERROR_EXPECTED} invalid constitution url")
        return ""

    def _fetch_constitution(self, url: str) -> str:
        response = gl.nondet.web.get(url)
        status = int(response.status)
        if status >= 400 and status < 500:
            _raise_user_error(f"{ERROR_EXTERNAL} constitution fetch client error {status}")
        if status >= 500:
            _raise_user_error(f"{ERROR_TRANSIENT} constitution fetch server error {status}")
        text = response.body.decode("utf-8") if response.body is not None else ""
        if len(text.strip()) < 50:
            _raise_user_error(f"{ERROR_EXTERNAL} constitution text too short")
        return text

    def _interpret_action(self, constitution_text: str, action_text: str) -> dict:
        prompt = f"""
You are a constitutional compliance reviewer for a DAO.
Given the constitution and a proposed governance action, decide if the action violates any clause.
Return JSON with keys:
- violation (boolean)
- violating_clauses (array of short strings)
- reasoning (string)
- confidence (integer 0-100)

Constitution:
{constitution_text}

Proposed Action:
{action_text}
"""
        result = gl.nondet.exec_prompt(prompt, response_format="json")
        if not isinstance(result, dict):
            _raise_user_error(f"{ERROR_LLM} non-dict response")

        violation = bool(result.get("violation", False))
        raw_clauses = result.get("violating_clauses", [])
        if not isinstance(raw_clauses, list):
            raw_clauses = []
        clauses = []
        for c in raw_clauses[:10]:
            clauses.append(str(c).strip())

        reasoning = str(result.get("reasoning", "")).strip()
        if len(reasoning) < 3:
            reasoning = "Insufficient reasoning returned"

        conf_raw = result.get("confidence", 0)
        try:
            confidence = int(round(float(str(conf_raw).strip())))
        except Exception:
            _raise_user_error(f"{ERROR_LLM} invalid confidence")
        if confidence < 0:
            confidence = 0
        if confidence > 100:
            confidence = 100

        return {
            "violation": violation,
            "violating_clauses": clauses,
            "reasoning": reasoning,
            "confidence": confidence,
        }

    @gl.public.write
    def register_constitution(self, dao_id: str, constitution_url: str) -> None:
        """Register or update constitution location for a DAO.

        Parameters:
            dao_id: DAO identifier.
            constitution_url: IPFS or HTTP URL to constitution text.

        Returns:
            None.
        """
        self._require_owner()
        key = str(dao_id).strip().lower()
        if len(key) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid dao_id")
        normalized = self._normalize_constitution_url(constitution_url)

        constitutions = json.loads(self.constitutions)
        constitutions[key] = {
            "dao_id": key,
            "constitution_url": normalized,
            "updated_at": str(gl.block.timestamp),
        }
        self.constitutions = json.dumps(constitutions)

    @gl.public.write
    def evaluate_action(self, dao_id: str, action_text: str) -> str:
        """Evaluate whether action violates constitution.

        Parameters:
            dao_id: DAO identifier.
            action_text: Proposed governance action text.

        Returns:
            Decision id string.
        """
        key = str(dao_id).strip().lower()
        action = str(action_text).strip()
        if len(key) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid dao_id")
        if len(action) < 10:
            _raise_user_error(f"{ERROR_EXPECTED} invalid action_text")

        constitutions = json.loads(self.constitutions)
        if key not in constitutions:
            _raise_user_error(f"{ERROR_EXPECTED} constitution not registered")
        constitution_url = str(constitutions[key].get("constitution_url", "")).strip()

        def leader_fn():
            constitution_text = self._fetch_constitution(constitution_url)
            interpretation = self._interpret_action(constitution_text, action)
            blocked = bool(interpretation.get("violation", False))
            return {
                "dao_id": key,
                "constitution_url": constitution_url,
                "blocked": blocked,
                "bucket": 1 if blocked else 0,
                "interpretation": interpretation,
            }

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                try:
                    leader_fn()
                    return False
                except Exception as e:
                    leader_message = leaders_res.message if hasattr(leaders_res, "message") else ""
                    validator_message = str(e)
                    if validator_message.startswith(ERROR_TRANSIENT) and leader_message.startswith(ERROR_TRANSIENT):
                        return True
                    return False

            validator_out = leader_fn()
            leader_out = leaders_res.calldata
            if bool(leader_out.get("blocked", False)) != bool(validator_out.get("blocked", False)):
                return False
            if int(leader_out.get("bucket", -1)) != int(validator_out.get("bucket", -2)):
                return False
            return True

        out = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        did = str(self.next_decision_id)
        self.next_decision_id += 1

        decisions = json.loads(self.decisions)
        decisions[did] = {
            "decision_id": did,
            "requester": _sender(),
            "dao_id": out["dao_id"],
            "constitution_url": out["constitution_url"],
            "blocked": bool(out["blocked"]),
            "interpretation": out["interpretation"],
            "created_at": str(gl.block.timestamp),
        }
        self.decisions = json.dumps(decisions)
        return did

    @gl.public.view
    def get_decision(self, decision_id: str) -> str:
        """Read one action decision.

        Parameters:
            decision_id: Decision identifier.

        Returns:
            Decision JSON string.
        """
        key = str(decision_id).strip()
        decisions = json.loads(self.decisions)
        if key not in decisions:
            _raise_user_error(f"{ERROR_EXPECTED} decision not found")
        return json.dumps(decisions[key])

    @gl.public.view
    def get_constitution(self, dao_id: str) -> str:
        """Read registered constitution metadata for a DAO.

        Parameters:
            dao_id: DAO identifier.

        Returns:
            Constitution metadata JSON string.
        """
        key = str(dao_id).strip().lower()
        constitutions = json.loads(self.constitutions)
        if key not in constitutions:
            _raise_user_error(f"{ERROR_EXPECTED} constitution not registered")
        return json.dumps(constitutions[key])

    @gl.public.view
    def get_all_decisions(self) -> str:
        """Read all decisions.

        Parameters:
            None.

        Returns:
            JSON map of decisions.
        """
        return self.decisions
