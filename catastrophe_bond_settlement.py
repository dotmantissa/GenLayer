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


class CatastropheBondSettlement(gl.Contract):
    """Settles catastrophe bond escrow using multi source seismic verification."""

    bonds: str
    balances: str
    next_bond_id: u256

    def __init__(self):
        """Initialize contract state.

        Parameters:
            None.

        Returns:
            None.
        """
        self.bonds = "{}"
        self.balances = "{}"
        self.next_bond_id = 1

    @gl.public.write
    def top_up_balance(self, amount: int) -> None:
        """Increase sender internal settlement balance.

        Parameters:
            amount: Non negative integer amount.

        Returns:
            None.
        """
        if int(amount) <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} amount must be positive")

        sender = str(gl.message.sender_account)
        balances = json.loads(self.balances)
        balances[sender] = int(balances.get(sender, 0)) + int(amount)
        self.balances = json.dumps(balances)

    @gl.public.view
    def balance_of(self, account: str) -> int:
        """Read balance for an account.

        Parameters:
            account: Account address string.

        Returns:
            Integer balance amount.
        """
        balances = json.loads(self.balances)
        return int(balances.get(str(account), 0))

    @gl.public.write
    def create_bond(
        self,
        event_name: str,
        min_magnitude: float,
        center_lat: float,
        center_lon: float,
        max_distance_km: float,
        payout_amount: int,
    ) -> str:
        """Create catastrophe bond settlement rule.

        Parameters:
            event_name: Human event label.
            min_magnitude: Minimum qualifying magnitude.
            center_lat: Contract location latitude.
            center_lon: Contract location longitude.
            max_distance_km: Maximum event distance for qualification.
            payout_amount: Escrow amount reserved from sponsor.

        Returns:
            Bond id string.
        """
        name = str(event_name).strip()
        if len(name) < 3:
            _raise_user_error(f"{ERROR_EXPECTED} invalid event_name")
        if float(min_magnitude) < 3.0 or float(min_magnitude) > 10.0:
            _raise_user_error(f"{ERROR_EXPECTED} min_magnitude out of range")
        if float(center_lat) < -90.0 or float(center_lat) > 90.0:
            _raise_user_error(f"{ERROR_EXPECTED} center_lat out of range")
        if float(center_lon) < -180.0 or float(center_lon) > 180.0:
            _raise_user_error(f"{ERROR_EXPECTED} center_lon out of range")
        if float(max_distance_km) <= 0.0 or float(max_distance_km) > 2000.0:
            _raise_user_error(f"{ERROR_EXPECTED} max_distance_km out of range")
        if int(payout_amount) <= 0:
            _raise_user_error(f"{ERROR_EXPECTED} payout_amount must be positive")

        sponsor = str(gl.message.sender_account)
        balances = json.loads(self.balances)
        sponsor_balance = int(balances.get(sponsor, 0))
        if sponsor_balance < int(payout_amount):
            _raise_user_error(f"{ERROR_EXPECTED} insufficient sponsor balance")

        balances[sponsor] = sponsor_balance - int(payout_amount)
        self.balances = json.dumps(balances)

        bond_id = str(self.next_bond_id)
        self.next_bond_id += 1

        bonds = json.loads(self.bonds)
        bonds[bond_id] = {
            "bond_id": bond_id,
            "sponsor": sponsor,
            "event_name": name,
            "min_magnitude": float(min_magnitude),
            "center_lat": float(center_lat),
            "center_lon": float(center_lon),
            "max_distance_km": float(max_distance_km),
            "payout_amount": int(payout_amount),
            "status": "PENDING",
            "settled_to": "",
            "matched_magnitude": 0.0,
            "matched_distance_km": 0.0,
            "consensus_source_count": 0,
            "resolution_reason": "",
            "resolved_at": "",
        }
        self.bonds = json.dumps(bonds)
        return bond_id

    @gl.public.write
    def settle_bond(self, bond_id: str, beneficiary: str) -> str:
        """Settle bond by cross validating seismic event parameters.

        Parameters:
            bond_id: Bond id string.
            beneficiary: Payout receiver if event qualifies.

        Returns:
            Status string after settlement.
        """
        bonds = json.loads(self.bonds)
        key = str(bond_id)
        if key not in bonds:
            _raise_user_error(f"{ERROR_EXPECTED} bond not found")

        b = bonds[key]
        if b["status"] != "PENDING":
            _raise_user_error(f"{ERROR_EXPECTED} bond already settled")

        receiver = str(beneficiary).strip()
        if len(receiver) < 8:
            _raise_user_error(f"{ERROR_EXPECTED} invalid beneficiary")

        def fetch_and_assess() -> str:
            usgs_url = "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&limit=20&orderby=time"
            swissre_url = "https://catnet.swissre.com/api/events?limit=20"

            usgs = gl.nondet.web.get(usgs_url)
            swiss = gl.nondet.web.get(swissre_url)

            for name, res in [("usgs", usgs), ("swissre", swiss)]:
                status = int(res.status)
                if status >= 400 and status < 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} client error: {status}")
                if status >= 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} server error: {status}")

            usgs_body = usgs.body.decode("utf-8") if usgs.body is not None else ""
            swiss_body = swiss.body.decode("utf-8") if swiss.body is not None else ""
            if len((usgs_body + swiss_body).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty seismic payload")

            prompt = f"""
You are a catastrophe risk assessor.
Cross validate event parameters across USGS and Swiss Re style feeds.
Return JSON only.

Contract criteria:
- event_name: {b['event_name']}
- min_magnitude: {b['min_magnitude']}
- center_lat: {b['center_lat']}
- center_lon: {b['center_lon']}
- max_distance_km: {b['max_distance_km']}

Rules:
1) Identify matching named event candidates from both sources.
2) Estimate consensus magnitude and distance from contract center.
3) A valid trigger requires source agreement and both thresholds met.
4) If not enough agreement then do not trigger.

Return exactly:
{{
  "triggered": true_or_false,
  "consensus_magnitude": float,
  "consensus_distance_km": float,
  "source_count": int,
  "reason": "string"
}}

Inputs:
{json.dumps({"usgs": usgs_body[:5000], "swissre": swiss_body[:5000]})}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            triggered = bool(parsed.get("triggered", False))
            magnitude = float(parsed.get("consensus_magnitude", 0.0))
            distance_km = float(parsed.get("consensus_distance_km", 999999.0))
            source_count = int(parsed.get("source_count", 0))
            reason = str(parsed.get("reason", ""))[:500]

            if magnitude < 0.0 or distance_km < 0.0:
                _raise_user_error(f"{ERROR_EXTERNAL} invalid parsed seismic metrics")

            thresholds_met = magnitude >= float(b["min_magnitude"]) and distance_km <= float(b["max_distance_km"])
            cross_validated = source_count >= 2
            triggered = bool(triggered and thresholds_met and cross_validated)

            return json.dumps(
                {
                    "triggered": triggered,
                    "consensus_magnitude": magnitude,
                    "consensus_distance_km": distance_km,
                    "source_count": source_count,
                    "reason": reason,
                }
            )

        principle = "Equivalent when triggered matches and magnitude and distance are within 0.3 and 30km."
        verdict_json = _run_prompt_consensus(fetch_and_assess, principle)
        verdict = json.loads(verdict_json)

        b["matched_magnitude"] = float(verdict.get("consensus_magnitude", 0.0))
        b["matched_distance_km"] = float(verdict.get("consensus_distance_km", 0.0))
        b["consensus_source_count"] = int(verdict.get("source_count", 0))
        b["resolution_reason"] = str(verdict.get("reason", ""))
        b["resolved_at"] = str(gl.block.timestamp)

        balances = json.loads(self.balances)
        payout = int(b["payout_amount"])

        if bool(verdict.get("triggered", False)):
            b["status"] = "TRIGGERED"
            b["settled_to"] = receiver
            balances[receiver] = int(balances.get(receiver, 0)) + payout
        else:
            b["status"] = "NOT_TRIGGERED"
            b["settled_to"] = str(b["sponsor"])
            sponsor = str(b["sponsor"])
            balances[sponsor] = int(balances.get(sponsor, 0)) + payout

        bonds[key] = b
        self.bonds = json.dumps(bonds)
        self.balances = json.dumps(balances)

        return b["status"]

    @gl.public.view
    def get_bond(self, bond_id: str) -> str:
        """Read one bond record.

        Parameters:
            bond_id: Bond id string.

        Returns:
            Bond JSON string.
        """
        bonds = json.loads(self.bonds)
        key = str(bond_id)
        if key not in bonds:
            _raise_user_error(f"{ERROR_EXPECTED} bond not found")
        return json.dumps(bonds[key])

    @gl.public.view
    def get_all_bonds(self) -> str:
        """Read all bond records.

        Parameters:
            None.

        Returns:
            Bonds map JSON string.
        """
        return self.bonds
