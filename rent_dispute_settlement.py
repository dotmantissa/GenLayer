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


class RentDisputeSettlement(gl.Contract):
    """Settles landlord tenant rent disputes using comparable market listings."""

    disputes: str
    balances: str
    next_dispute_id: u256

    def __init__(self):
        """Initialize storage fields.

        Parameters:
            None.

        Returns:
            None.
        """
        self.disputes = "{}"
        self.balances = "{}"
        self.next_dispute_id = 1

    @gl.public.write
    def top_up_balance(self, amount: int) -> None:
        """Credit sender internal balance.

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
        """Read balance for account.

        Parameters:
            account: Address string.

        Returns:
            Integer balance.
        """
        balances = json.loads(self.balances)
        return int(balances.get(str(account), 0))

    @gl.public.write
    def create_dispute(
        self,
        landlord_wallet: str,
        tenant_wallet: str,
        neighbourhood: str,
        unit_descriptor: str,
        proposed_rent: int,
        max_fair_rent: int,
        escrow_amount: int,
    ) -> str:
        """Create rent dispute with escrowed funds.

        Parameters:
            landlord_wallet: Landlord address.
            tenant_wallet: Tenant address.
            neighbourhood: Area name for comparables.
            unit_descriptor: Property attributes text.
            proposed_rent: Claimed rent amount.
            max_fair_rent: Maximum acceptable fair rent for settlement.
            escrow_amount: Escrow amount staked by caller.

        Returns:
            Dispute id string.
        """
        creator = str(gl.message.sender_account)
        landlord = str(landlord_wallet).strip()
        tenant = str(tenant_wallet).strip()
        hood = str(neighbourhood).strip()
        unit = str(unit_descriptor).strip()

        if len(landlord) < 10 or len(tenant) < 10:
            _raise_user_error(f"{ERROR_EXPECTED} invalid participant wallet")
        if len(hood) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid neighbourhood")
        if len(unit) < 3:
            _raise_user_error(f"{ERROR_EXPECTED} invalid unit descriptor")
        if proposed_rent <= 0 or max_fair_rent <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} rent values must be positive")
        if escrow_amount <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} escrow amount must be positive")

        balances = json.loads(self.balances)
        if int(balances.get(creator, 0)) < int(escrow_amount):
            _raise_user_error(f"{ERROR_EXPECTED} insufficient escrow balance")

        balances[creator] = int(balances.get(creator, 0)) - int(escrow_amount)
        self.balances = json.dumps(balances)

        dispute_id = str(self.next_dispute_id)
        self.next_dispute_id += 1

        disputes = json.loads(self.disputes)
        disputes[dispute_id] = {
            "dispute_id": dispute_id,
            "creator": creator,
            "landlord_wallet": landlord,
            "tenant_wallet": tenant,
            "neighbourhood": hood,
            "unit_descriptor": unit,
            "proposed_rent": int(proposed_rent),
            "max_fair_rent": int(max_fair_rent),
            "escrow_amount": int(escrow_amount),
            "status": "ACTIVE",
            "fair_rent_low": 0,
            "fair_rent_high": 0,
            "resolved_for": "",
            "reason": "",
            "resolved_at": "",
        }
        self.disputes = json.dumps(disputes)
        return dispute_id

    @gl.public.write
    def settle_dispute(self, dispute_id: str) -> str:
        """Settle dispute from comparable listings and LLM fair range analysis.

        Parameters:
            dispute_id: Dispute id string.

        Returns:
            Settlement status string.
        """
        disputes = json.loads(self.disputes)
        key = str(dispute_id)
        if key not in disputes:
            _raise_user_error(f"{ERROR_EXPECTED} dispute not found")

        d = disputes[key]
        if d["status"] != "ACTIVE":
            _raise_user_error(f"{ERROR_EXPECTED} dispute is not active")

        zillow_url = f"https://www.zillow.com/homes/for_rent/{d['neighbourhood'].replace(' ', '-')}_rb/"
        apartments_url = f"https://www.apartments.com/{d['neighbourhood'].replace(' ', '-')}/"

        def fetch_and_assess() -> str:
            z = gl.nondet.web.get(zillow_url)
            a = gl.nondet.web.get(apartments_url)

            if int(z.status) >= 400 and int(z.status) < 500:
                _raise_user_error(f"{ERROR_EXTERNAL} zillow client error: {z.status}")
            if int(z.status) >= 500:
                _raise_user_error(f"{ERROR_EXTERNAL} zillow server error: {z.status}")
            if int(a.status) >= 400 and int(a.status) < 500:
                _raise_user_error(f"{ERROR_EXTERNAL} apartments client error: {a.status}")
            if int(a.status) >= 500:
                _raise_user_error(f"{ERROR_EXTERNAL} apartments server error: {a.status}")

            z_body = z.body.decode("utf-8") if z.body is not None else ""
            a_body = a.body.decode("utf-8") if a.body is not None else ""
            if len((z_body + a_body).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty comparable listing payload")

            prompt = f"""
You are a rental adjudicator.
Determine a defensible fair market rent range from comparable listings.
Return JSON only.

Dispute:
- neighbourhood: {d['neighbourhood']}
- unit_descriptor: {d['unit_descriptor']}
- proposed_rent: {d['proposed_rent']}
- max_fair_rent: {d['max_fair_rent']}

Rules:
1) Infer comparables using bedrooms bathrooms unit type and location clues.
2) Return fair_rent_low and fair_rent_high as integers.
3) Decide if dispute should resolve for landlord or tenant.
4) If proposed rent is above defensible range resolve for tenant.

Return exactly:
{{
  "fair_rent_low": int,
  "fair_rent_high": int,
  "resolved_for": "landlord_or_tenant",
  "reason": "string"
}}

Payload:
{json.dumps({"zillow": z_body[:5000], "apartments": a_body[:5000]})}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            low = int(parsed.get("fair_rent_low", 0))
            high = int(parsed.get("fair_rent_high", 0))
            resolved_for = str(parsed.get("resolved_for", "tenant")).strip().lower()
            if resolved_for not in ["tenant", "landlord"]:
                resolved_for = "tenant"
            if high < low:
                high = low

            if int(d["proposed_rent"]) > high:
                resolved_for = "tenant"
            elif int(d["proposed_rent"]) <= int(d["max_fair_rent"]) and int(d["proposed_rent"]) <= high:
                resolved_for = "landlord"

            return json.dumps(
                {
                    "fair_rent_low": low,
                    "fair_rent_high": high,
                    "resolved_for": resolved_for,
                    "reason": str(parsed.get("reason", ""))[:500],
                }
            )

        principle = "Equivalent when resolved_for matches and fair rent bounds differ by at most 15 percent."
        result_json = _run_prompt_consensus(fetch_and_assess, principle)
        result = json.loads(result_json)

        d["fair_rent_low"] = int(result.get("fair_rent_low", 0))
        d["fair_rent_high"] = int(result.get("fair_rent_high", 0))
        d["resolved_for"] = str(result.get("resolved_for", "tenant"))
        d["reason"] = str(result.get("reason", ""))
        d["resolved_at"] = str(gl.block.timestamp)

        balances = json.loads(self.balances)
        if d["resolved_for"] == "landlord":
            balances[str(d["landlord_wallet"])] = int(balances.get(str(d["landlord_wallet"]), 0)) + int(d["escrow_amount"])
            d["status"] = "SETTLED_LANDLORD"
        else:
            balances[str(d["tenant_wallet"])] = int(balances.get(str(d["tenant_wallet"]), 0)) + int(d["escrow_amount"])
            d["status"] = "SETTLED_TENANT"

        disputes[key] = d
        self.disputes = json.dumps(disputes)
        self.balances = json.dumps(balances)

        return d["status"]

    @gl.public.view
    def get_dispute(self, dispute_id: str) -> str:
        """Read one dispute.

        Parameters:
            dispute_id: Dispute id string.

        Returns:
            Dispute JSON string.
        """
        disputes = json.loads(self.disputes)
        key = str(dispute_id)
        if key not in disputes:
            _raise_user_error(f"{ERROR_EXPECTED} dispute not found")
        return json.dumps(disputes[key])

    @gl.public.view
    def get_all_disputes(self) -> str:
        """Read all disputes.

        Parameters:
            None.

        Returns:
            Disputes JSON map.
        """
        return self.disputes
