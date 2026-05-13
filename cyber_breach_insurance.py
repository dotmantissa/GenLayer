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


class CyberBreachInsurance(gl.Contract):
    """Settles cyber insurance claims when policyholder domain breach is confirmed."""

    policies: str
    balances: str
    next_policy_id: u256

    def __init__(self):
        """Initialize storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.policies = "{}"
        self.balances = "{}"
        self.next_policy_id = 1

    @gl.public.write
    def top_up_balance(self, amount: int) -> None:
        """Credit sender balance.

        Parameters:
            amount: Positive integer amount.

        Returns:
            None.
        """
        if amount <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} amount must be positive")

        sender = str(gl.message.sender_account)
        balances = json.loads(self.balances)
        balances[sender] = int(balances.get(sender, 0)) + int(amount)
        self.balances = json.dumps(balances)

    @gl.public.view
    def balance_of(self, account: str) -> int:
        """Read account balance.

        Parameters:
            account: Address string.

        Returns:
            Integer balance.
        """
        balances = json.loads(self.balances)
        return int(balances.get(str(account), 0))

    @gl.public.write
    def create_policy(self, policyholder_domain: str, payout_amount: int, hibp_api_key: str) -> str:
        """Create cyber breach policy and lock payout reserve.

        Parameters:
            policyholder_domain: Covered domain.
            payout_amount: Settlement payout amount.
            hibp_api_key: API key for Have I Been Pwned endpoint.

        Returns:
            Policy id string.
        """
        creator = str(gl.message.sender_account)
        domain = str(policyholder_domain).strip().lower()
        api_key = str(hibp_api_key).strip()

        if "." not in domain or len(domain) < 4:
            _raise_user_error(f"{ERROR_EXPECTED} invalid policyholder domain")
        if payout_amount <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} payout must be positive")
        if len(api_key) == 0:
            _raise_user_error(f"{ERROR_EXPECTED} hibp api key required")

        balances = json.loads(self.balances)
        if int(balances.get(creator, 0)) < int(payout_amount):
            _raise_user_error(f"{ERROR_EXPECTED} insufficient reserve balance")

        balances[creator] = int(balances.get(creator, 0)) - int(payout_amount)
        self.balances = json.dumps(balances)

        policy_id = str(self.next_policy_id)
        self.next_policy_id += 1

        policies = json.loads(self.policies)
        policies[policy_id] = {
            "policy_id": policy_id,
            "creator": creator,
            "policyholder_domain": domain,
            "payout_amount": int(payout_amount),
            "hibp_api_key": api_key,
            "status": "ACTIVE",
            "breach_confirmed": False,
            "resolution": "",
            "reason": "",
            "resolved_at": "",
        }
        self.policies = json.dumps(policies)
        return policy_id

    @gl.public.write
    def settle_policy(self, policy_id: str) -> str:
        """Settle policy claim by verifying breach disclosures.

        Parameters:
            policy_id: Policy id string.

        Returns:
            Settlement status string.
        """
        policies = json.loads(self.policies)
        key = str(policy_id)
        if key not in policies:
            _raise_user_error(f"{ERROR_EXPECTED} policy not found")

        p = policies[key]
        if p["status"] != "ACTIVE":
            _raise_user_error(f"{ERROR_EXPECTED} policy is not active")

        hibp_url = f"https://haveibeenpwned.com/api/v3/breaches?domain={p['policyholder_domain']}"
        hhs_url = "https://ocrportal.hhs.gov/ocr/breach/breach_report.jsf"

        def fetch_and_verify() -> str:
            hibp = gl.nondet.web.get(hibp_url)
            hhs = gl.nondet.web.get(hhs_url)

            if int(hibp.status) >= 400 and int(hibp.status) < 500:
                _raise_user_error(f"{ERROR_EXTERNAL} hibp client error: {hibp.status}")
            if int(hibp.status) >= 500:
                _raise_user_error(f"{ERROR_EXTERNAL} hibp server error: {hibp.status}")
            if int(hhs.status) >= 400 and int(hhs.status) < 500:
                _raise_user_error(f"{ERROR_EXTERNAL} hhs client error: {hhs.status}")
            if int(hhs.status) >= 500:
                _raise_user_error(f"{ERROR_EXTERNAL} hhs server error: {hhs.status}")

            hibp_body = hibp.body.decode("utf-8") if hibp.body is not None else ""
            hhs_body = hhs.body.decode("utf-8") if hhs.body is not None else ""
            if len((hibp_body + hhs_body).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty breach payload")

            prompt = f"""
You are a cyber breach insurance adjudicator.
Match policyholder domain to live breach disclosures.
Return JSON only.

Rules:
1) breach_confirmed true only if domain identity match is strong and breach disclosure is credible.
2) resolve_to should be policyholder when breach_confirmed true.
3) resolve_to should be insurer otherwise.

Return exactly:
{{
  "breach_confirmed": bool,
  "resolve_to": "policyholder_or_insurer",
  "reason": "string"
}}

Policyholder domain: {p['policyholder_domain']}
Payload:
{json.dumps({"hibp": hibp_body[:5000], "hhs": hhs_body[:5000]})}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            breach_confirmed = bool(parsed.get("breach_confirmed", False))
            resolve_to = str(parsed.get("resolve_to", "insurer")).strip().lower()
            if resolve_to not in ["policyholder", "insurer"]:
                resolve_to = "insurer"

            if breach_confirmed:
                resolve_to = "policyholder"
            else:
                resolve_to = "insurer"

            return json.dumps(
                {
                    "breach_confirmed": breach_confirmed,
                    "resolve_to": resolve_to,
                    "reason": str(parsed.get("reason", ""))[:500],
                }
            )

        principle = "Equivalent when breach_confirmed and resolve_to are exactly the same."
        result_json = _run_prompt_consensus(fetch_and_verify, principle)
        result = json.loads(result_json)

        p["breach_confirmed"] = bool(result.get("breach_confirmed", False))
        p["resolution"] = str(result.get("resolve_to", "insurer"))
        p["reason"] = str(result.get("reason", ""))
        p["resolved_at"] = str(gl.block.timestamp)

        balances = json.loads(self.balances)
        if p["resolution"] == "policyholder":
            balances[str(p["creator"])] = int(balances.get(str(p["creator"]), 0)) + int(p["payout_amount"])
            p["status"] = "SETTLED_POLICYHOLDER"
        else:
            balances[str(p["creator"])] = int(balances.get(str(p["creator"]), 0)) + int(p["payout_amount"])
            p["status"] = "SETTLED_INSURER"

        policies[key] = p
        self.policies = json.dumps(policies)
        self.balances = json.dumps(balances)

        return p["status"]

    @gl.public.view
    def get_policy(self, policy_id: str) -> str:
        """Read one policy.

        Parameters:
            policy_id: Policy id string.

        Returns:
            Policy JSON string.
        """
        policies = json.loads(self.policies)
        key = str(policy_id)
        if key not in policies:
            _raise_user_error(f"{ERROR_EXPECTED} policy not found")
        return json.dumps(policies[key])

    @gl.public.view
    def get_all_policies(self) -> str:
        """Read all policies.

        Parameters:
            None.

        Returns:
            Policies map JSON string.
        """
        return self.policies
