# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"


def _raise_user_error(message: str) -> None:
    err_cls = getattr(gl, "UserError", Exception)
    raise err_cls(message)


def _run_prompt_consensus(fn, principle: str) -> str:
    eq = getattr(gl, "eq_principle", None)
    if eq is not None and hasattr(eq, "prompt_comparative"):
        return eq.prompt_comparative(fn, principle)
    return fn()


class ModerationQueueRuler(gl.Contract):
    """Applies community guideline rulings to flagged content queue items."""

    policies: str
    items: str
    next_policy_id: u256
    next_item_id: u256

    def __init__(self):
        """Initialize contract storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.policies = "{}"
        self.items = "{}"
        self.next_policy_id = 1
        self.next_item_id = 1

    @gl.public.write
    def create_policy(self, name: str, guidelines_text: str) -> str:
        """Create moderation policy.

        Parameters:
            name: Policy display name.
            guidelines_text: Natural language moderation guidelines.

        Returns:
            Policy id string.
        """
        policy_name = str(name).strip()
        guidelines = str(guidelines_text).strip()

        if len(policy_name) < 3:
            _raise_user_error(f"{ERROR_EXPECTED} invalid policy name")
        if len(guidelines) < 40:
            _raise_user_error(f"{ERROR_EXPECTED} guidelines too short")

        policy_id = str(self.next_policy_id)
        self.next_policy_id += 1

        policies = json.loads(self.policies)
        policies[policy_id] = {
            "policy_id": policy_id,
            "owner": str(gl.message.sender_account),
            "name": policy_name,
            "guidelines_text": guidelines,
        }
        self.policies = json.dumps(policies)
        return policy_id

    @gl.public.write
    def create_case(self, policy_id: str, queue_url: str, content_id: str) -> str:
        """Create moderation case against one queued content item.

        Parameters:
            policy_id: Existing moderation policy id.
            queue_url: Public moderation queue endpoint URL.
            content_id: Identifier of flagged content in that queue.

        Returns:
            Case id string.
        """
        policies = json.loads(self.policies)
        pkey = str(policy_id)
        if pkey not in policies:
            _raise_user_error(f"{ERROR_EXPECTED} policy not found")

        url = str(queue_url).strip()
        cid = str(content_id).strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            _raise_user_error(f"{ERROR_EXPECTED} invalid queue_url")
        if len(cid) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid content_id")

        item_id = str(self.next_item_id)
        self.next_item_id += 1

        items = json.loads(self.items)
        items[item_id] = {
            "item_id": item_id,
            "policy_id": pkey,
            "queue_url": url,
            "content_id": cid,
            "status": "PENDING",
            "ruling": "",
            "confidence": 0,
            "reason": "",
            "resolved_at": "",
        }
        self.items = json.dumps(items)
        return item_id

    @gl.public.write
    def rule_case(self, item_id: str) -> str:
        """Rule on queued content and return binding action.

        Parameters:
            item_id: Moderation case id.

        Returns:
            Ruling string REMOVE or REINSTATE.
        """
        items = json.loads(self.items)
        ikey = str(item_id)
        if ikey not in items:
            _raise_user_error(f"{ERROR_EXPECTED} item not found")

        item = items[ikey]
        if item["status"] != "PENDING":
            _raise_user_error(f"{ERROR_EXPECTED} item already ruled")

        policies = json.loads(self.policies)
        policy = policies[str(item["policy_id"])]

        def fetch_and_rule() -> str:
            queue = gl.nondet.web.get(item["queue_url"])
            status = int(queue.status)
            if status >= 400 and status < 500:
                _raise_user_error(f"{ERROR_EXTERNAL} queue client error: {status}")
            if status >= 500:
                _raise_user_error(f"{ERROR_EXTERNAL} queue server error: {status}")

            queue_body = queue.body.decode("utf-8") if queue.body is not None else ""
            if len(queue_body.strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty queue payload")

            prompt = f"""
You are a platform trust and safety adjudicator.
Evaluate whether flagged content violates the given natural language community guidelines.
Return JSON only.

Guidelines:
{policy['guidelines_text'][:3000]}

Target content_id: {item['content_id']}

Rules:
1) Find the content entry for target content_id in queue payload.
2) If it clearly violates guidelines ruling is REMOVE.
3) If it does not violate or evidence is insufficient ruling is REINSTATE.
4) Return confidence from 0 to 100.

Return exactly:
{{
  "ruling": "REMOVE_or_REINSTATE",
  "confidence": int,
  "reason": "string"
}}

Queue payload:
{queue_body[:7000]}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            ruling = str(parsed.get("ruling", "REINSTATE")).strip().upper()
            if ruling not in ["REMOVE", "REINSTATE"]:
                ruling = "REINSTATE"

            confidence = int(parsed.get("confidence", 0))
            if confidence < 0:
                confidence = 0
            if confidence > 100:
                confidence = 100

            reason = str(parsed.get("reason", ""))[:500]

            return json.dumps(
                {
                    "ruling": ruling,
                    "confidence": confidence,
                    "reason": reason,
                }
            )

        principle = "Equivalent when ruling matches and confidence differs by at most 15 points."
        result_json = _run_prompt_consensus(fetch_and_rule, principle)
        result = json.loads(result_json)

        item["ruling"] = str(result.get("ruling", "REINSTATE"))
        item["confidence"] = int(result.get("confidence", 0))
        item["reason"] = str(result.get("reason", ""))
        item["status"] = "RULED"
        item["resolved_at"] = str(gl.block.timestamp)

        items[ikey] = item
        self.items = json.dumps(items)

        return item["ruling"]

    @gl.public.view
    def get_case(self, item_id: str) -> str:
        """Read moderation case.

        Parameters:
            item_id: Moderation case id.

        Returns:
            Case JSON string.
        """
        items = json.loads(self.items)
        ikey = str(item_id)
        if ikey not in items:
            _raise_user_error(f"{ERROR_EXPECTED} item not found")
        return json.dumps(items[ikey])

    @gl.public.view
    def get_all_cases(self) -> str:
        """Read all moderation cases.

        Parameters:
            None.

        Returns:
            Cases map JSON string.
        """
        return self.items
