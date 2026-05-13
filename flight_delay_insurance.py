# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json


ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"


class FlightDelayInsurance(gl.Contract):
    """Flight delay insurance contract with automated claim settlement."""

    policies: str
    balances: str
    next_policy_id: u256

    def __init__(self):
        """Initialize contract storage for policies, balances, and ID counter."""
        self.policies = "{}"
        self.balances = "{}"
        self.next_policy_id = 1

    @gl.public.write
    def top_up_balance(self, amount: int) -> None:
        """Credit sender internal balance.

        Parameters:
            amount: Positive integer amount to credit.

        Returns:
            None.
        """
        if amount <= 0:
            raise gl.UserError(f"{ERROR_EXPECTED} amount must be positive")

        sender = str(gl.message.sender_account)
        ledger = json.loads(self.balances)
        ledger[sender] = int(ledger.get(sender, 0)) + int(amount)
        self.balances = json.dumps(ledger)

    @gl.public.view
    def balance_of(self, account: str) -> int:
        """Get internal balance for an account.

        Parameters:
            account: Wallet address string.

        Returns:
            Current balance as integer.
        """
        ledger = json.loads(self.balances)
        return int(ledger.get(str(account), 0))

    @gl.public.write
    def create_policy(
        self,
        flight_number: str,
        departure_iata: str,
        arrival_iata: str,
        scheduled_departure_iso: str,
        scheduled_arrival_iso: str,
        delay_threshold_minutes: int,
        premium_amount: int,
        payout_amount: int,
        provider: str,
        api_key: str,
    ) -> str:
        """Create a flight delay insurance policy.

        Parameters:
            flight_number: Flight identifier, for example "UA100".
            departure_iata: Departure airport IATA code.
            arrival_iata: Arrival airport IATA code.
            scheduled_departure_iso: Scheduled departure datetime string.
            scheduled_arrival_iso: Scheduled arrival datetime string.
            delay_threshold_minutes: Delay threshold for payout eligibility.
            premium_amount: Premium amount deducted from buyer.
            payout_amount: Amount credited if claim is covered.
            provider: Data provider selector, aviationstack or flightaware_public.
            api_key: API key for provider if required.

        Returns:
            Newly created policy ID.
        """
        if len(str(flight_number).strip()) < 2:
            raise gl.UserError(f"{ERROR_EXPECTED} invalid flight number")
        if len(str(departure_iata).strip()) != 3 or len(str(arrival_iata).strip()) != 3:
            raise gl.UserError(f"{ERROR_EXPECTED} invalid IATA code")
        if delay_threshold_minutes < 0:
            raise gl.UserError(f"{ERROR_EXPECTED} threshold must be >= 0")
        if premium_amount <= 0 or payout_amount <= 0:
            raise gl.UserError(f"{ERROR_EXPECTED} premium and payout must be positive")

        provider_lc = str(provider).strip().lower()
        if provider_lc not in ["aviationstack", "flightaware_public"]:
            raise gl.UserError(f"{ERROR_EXPECTED} unsupported provider")
        if provider_lc == "aviationstack" and len(str(api_key).strip()) == 0:
            raise gl.UserError(f"{ERROR_EXPECTED} api key required for aviationstack")

        holder = str(gl.message.sender_account)
        ledger = json.loads(self.balances)
        holder_balance = int(ledger.get(holder, 0))
        if holder_balance < int(premium_amount):
            raise gl.UserError(f"{ERROR_EXPECTED} insufficient balance for premium")

        policy_id = str(self.next_policy_id)
        self.next_policy_id += 1

        ledger[holder] = holder_balance - int(premium_amount)
        self.balances = json.dumps(ledger)

        policies = json.loads(self.policies)
        policies[policy_id] = {
            "policy_id": policy_id,
            "holder": holder,
            "flight_number": str(flight_number).strip().upper(),
            "departure_iata": str(departure_iata).strip().upper(),
            "arrival_iata": str(arrival_iata).strip().upper(),
            "scheduled_departure_iso": str(scheduled_departure_iso).strip(),
            "scheduled_arrival_iso": str(scheduled_arrival_iso).strip(),
            "delay_threshold_minutes": int(delay_threshold_minutes),
            "premium_amount": int(premium_amount),
            "payout_amount": int(payout_amount),
            "provider": provider_lc,
            "api_key": str(api_key).strip(),
            "status": "ACTIVE",
            "covered": False,
            "resolved_at": "",
            "last_result": {},
        }
        self.policies = json.dumps(policies)
        return policy_id

    @gl.public.write
    def resolve_policy(self, policy_id: str) -> str:
        """Resolve an active policy using live flight data and LLM interpretation.

        Parameters:
            policy_id: Policy ID string.

        Returns:
            Resolution status string.
        """
        policies = json.loads(self.policies)
        key = str(policy_id)
        if key not in policies:
            raise gl.UserError(f"{ERROR_EXPECTED} policy not found")

        policy = policies[key]
        if policy["status"] != "ACTIVE":
            raise gl.UserError(f"{ERROR_EXPECTED} policy is not active")

        provider = policy["provider"]
        if provider == "aviationstack":
            url = (
                "http://api.aviationstack.com/v1/flights"
                f"?access_key={policy['api_key']}"
                f"&flight_iata={policy['flight_number']}"
                f"&dep_iata={policy['departure_iata']}"
                f"&arr_iata={policy['arrival_iata']}"
            )
        else:
            url = (
                "https://aeroapi.flightaware.com/aeroapi/flights/"
                f"{policy['flight_number']}"
            )

        def fetch_and_interpret() -> str:
            response = gl.nondet.web.get(url)
            status = int(response.status)
            body = ""
            if response.body is not None:
                body = response.body.decode("utf-8")

            if status >= 400 and status < 500:
                raise gl.UserError(f"{ERROR_EXTERNAL} provider client error: {status}")
            if status >= 500:
                raise gl.UserError(f"{ERROR_EXTERNAL} provider server error: {status}")
            if len(body.strip()) == 0:
                raise gl.UserError(f"{ERROR_EXTERNAL} empty provider response")

            prompt = f"""
You are a flight claims adjudicator.
Interpret this provider payload and return JSON only.

Policy:
- Flight number: {policy['flight_number']}
- Departure airport: {policy['departure_iata']}
- Arrival airport: {policy['arrival_iata']}
- Scheduled departure: {policy['scheduled_departure_iso']}
- Scheduled arrival: {policy['scheduled_arrival_iso']}
- Delay threshold minutes: {policy['delay_threshold_minutes']}

Rules:
1) Extract departure and arrival delay in whole minutes if available.
2) If delay code is ambiguous (for example code shares weather or operations delays), infer if it is covered.
3) Mark covered_event true if departure delay OR arrival delay exceeds threshold, or if ambiguous code indicates covered delay.

Return exactly this JSON object:
{{
  "departure_delay_minutes": int,
  "arrival_delay_minutes": int,
  "ambiguous_delay_code": "string",
  "covered_event": bool,
  "reason": "string"
}}

Provider payload:
{body[:12000]}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            dep_delay = int(parsed.get("departure_delay_minutes", 0))
            arr_delay = int(parsed.get("arrival_delay_minutes", 0))
            ambiguous_code = str(parsed.get("ambiguous_delay_code", "")).strip()
            llm_covered = bool(parsed.get("covered_event", False))

            threshold = int(policy["delay_threshold_minutes"])
            covered = dep_delay > threshold or arr_delay > threshold or llm_covered

            return json.dumps(
                {
                    "departure_delay_minutes": dep_delay,
                    "arrival_delay_minutes": arr_delay,
                    "ambiguous_delay_code": ambiguous_code,
                    "covered_event": covered,
                    "reason": str(parsed.get("reason", ""))[:500],
                }
            )

        principle = (
            "Two outputs are equivalent when covered_event matches and both delay "
            "minute fields are within +/- 20 minutes."
        )
        result_json = gl.eq_principle.prompt_comparative(fetch_and_interpret, principle)
        result = json.loads(result_json)

        ledger = json.loads(self.balances)
        holder = str(policy["holder"])

        policy["last_result"] = result
        policy["covered"] = bool(result.get("covered_event", False))
        policy["resolved_at"] = str(gl.block.timestamp)

        if policy["covered"]:
            ledger[holder] = int(ledger.get(holder, 0)) + int(policy["payout_amount"])
            policy["status"] = "SETTLED_PAID"
        else:
            policy["status"] = "SETTLED_DENIED"

        policies[key] = policy
        self.policies = json.dumps(policies)
        self.balances = json.dumps(ledger)

        return policy["status"]

    @gl.public.view
    def get_policy(self, policy_id: str) -> str:
        """Get one policy by ID.

        Parameters:
            policy_id: Policy ID string.

        Returns:
            Serialized policy JSON string.
        """
        policies = json.loads(self.policies)
        key = str(policy_id)
        if key not in policies:
            raise gl.UserError(f"{ERROR_EXPECTED} policy not found")
        return json.dumps(policies[key])

    @gl.public.view
    def get_all_policies(self) -> str:
        """Get all policies.

        Parameters:
            None.

        Returns:
            Serialized map of all policies.
        """
        return self.policies
