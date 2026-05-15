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


class TravelDisruptionClaimSettler(gl.Contract):
    """Verifies travel disruptions from multiple sources and settles covered claims."""

    owner: Address
    min_advisory_level: u256
    min_cancel_rate_bps: u256
    claims: str
    next_claim_id: u256

    def __init__(self, min_advisory_level: int, min_cancel_rate_bps: int):
        """Initialize policy thresholds.

        Parameters:
            min_advisory_level: Minimum TSA advisory level that can trigger disruption.
            min_cancel_rate_bps: Minimum cancellation rate in basis points.

        Returns:
            None.
        """
        if min_advisory_level < 1 or min_advisory_level > 4:
            _raise_user_error(f"{ERROR_EXPECTED} min_advisory_level out of range")
        if min_cancel_rate_bps < 1 or min_cancel_rate_bps > 10000:
            _raise_user_error(f"{ERROR_EXPECTED} min_cancel_rate_bps out of range")

        self.owner = Address(_sender())
        self.min_advisory_level = int(min_advisory_level)
        self.min_cancel_rate_bps = int(min_cancel_rate_bps)
        self.claims = "{}"
        self.next_claim_id = 1

    def _require_owner(self) -> None:
        if _sender().strip().lower() != str(self.owner).strip().lower():
            _raise_user_error(f"{ERROR_EXPECTED} only owner")

    def _fetch_json(self, url: str):
        response = gl.nondet.web.get(url)
        status = int(response.status)
        if status >= 400 and status < 500:
            _raise_user_error(f"{ERROR_EXTERNAL} API client error {status}")
        if status >= 500:
            _raise_user_error(f"{ERROR_TRANSIENT} API server error {status}")
        try:
            body = response.body.decode("utf-8") if response.body is not None else "{}"
            return json.loads(body)
        except Exception:
            _raise_user_error(f"{ERROR_EXTERNAL} API invalid json")

    def _parse_int(self, value, label: str) -> int:
        try:
            return int(str(value).strip())
        except Exception:
            _raise_user_error(f"{ERROR_EXTERNAL} invalid {label}")
        return 0

    def _collect_disruption_signals(self, origin_airport: str, destination_airport: str, carrier_code: str, flight_date: str) -> dict:
        origin = str(origin_airport).strip().upper()
        destination = str(destination_airport).strip().upper()
        carrier = str(carrier_code).strip().upper()
        date = str(flight_date).strip()

        if len(origin) != 3 or len(destination) != 3:
            _raise_user_error(f"{ERROR_EXPECTED} invalid airport code")
        if len(carrier) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid carrier code")
        if len(date) != 10:
            _raise_user_error(f"{ERROR_EXPECTED} invalid flight_date")

        tsa_url = f"https://api.tsa.gov/travel/advisories?airport={origin}&date={date}"
        airline_url = f"https://api.transportation.gov/airline/cancellations?carrier={carrier}&airport={origin}&date={date}"

        tsa_data = self._fetch_json(tsa_url)
        airline_data = self._fetch_json(airline_url)

        advisory_level = self._parse_int(tsa_data.get("advisory_level", 0), "advisory_level")
        cancelled = self._parse_int(airline_data.get("cancelled_flights", 0), "cancelled_flights")
        scheduled = self._parse_int(airline_data.get("scheduled_flights", 0), "scheduled_flights")
        if scheduled <= 0:
            _raise_user_error(f"{ERROR_EXTERNAL} invalid scheduled_flights")
        if cancelled < 0 or cancelled > scheduled:
            _raise_user_error(f"{ERROR_EXTERNAL} invalid cancelled_flights")

        cancel_rate_bps = int((int(cancelled) * 10000) / int(scheduled))
        advisory_trigger = advisory_level >= int(self.min_advisory_level)
        cancel_trigger = cancel_rate_bps >= int(self.min_cancel_rate_bps)
        disruption_verified = advisory_trigger or cancel_trigger

        return {
            "origin": origin,
            "destination": destination,
            "carrier": carrier,
            "flight_date": date,
            "advisory_level": advisory_level,
            "cancelled_flights": cancelled,
            "scheduled_flights": scheduled,
            "cancel_rate_bps": cancel_rate_bps,
            "advisory_trigger": advisory_trigger,
            "cancel_trigger": cancel_trigger,
            "disruption_verified": disruption_verified,
            "bucket": 1 if disruption_verified else 0,
        }

    def _determine_coverage(self, policy_text: str, signals: dict) -> dict:
        prompt = f"""
You are an insurance claims analyst.
Determine if this disruption is covered by the policy.
Return JSON with keys: covered (boolean), reason (string), confidence (integer 0-100).

Policy:
{policy_text}

Signals:
{json.dumps(signals, sort_keys=True)}
"""
        result = gl.nondet.exec_prompt(prompt, response_format="json")
        if not isinstance(result, dict):
            _raise_user_error(f"{ERROR_LLM} non-dict response")

        covered = bool(result.get("covered", False))
        reason = str(result.get("reason", "")).strip()
        conf_raw = result.get("confidence", 0)
        try:
            confidence = int(round(float(str(conf_raw).strip())))
        except Exception:
            _raise_user_error(f"{ERROR_LLM} invalid confidence")

        if confidence < 0:
            confidence = 0
        if confidence > 100:
            confidence = 100
        if len(reason) < 3:
            reason = "Coverage rationale unavailable"

        return {"covered": covered, "reason": reason, "confidence": confidence}

    @gl.public.write
    def set_thresholds(self, min_advisory_level: int, min_cancel_rate_bps: int) -> None:
        """Update disruption trigger thresholds.

        Parameters:
            min_advisory_level: Minimum advisory level threshold.
            min_cancel_rate_bps: Minimum cancellation rate threshold.

        Returns:
            None.
        """
        self._require_owner()
        if min_advisory_level < 1 or min_advisory_level > 4:
            _raise_user_error(f"{ERROR_EXPECTED} min_advisory_level out of range")
        if min_cancel_rate_bps < 1 or min_cancel_rate_bps > 10000:
            _raise_user_error(f"{ERROR_EXPECTED} min_cancel_rate_bps out of range")
        self.min_advisory_level = int(min_advisory_level)
        self.min_cancel_rate_bps = int(min_cancel_rate_bps)

    @gl.public.write
    def evaluate_claim(
        self,
        origin_airport: str,
        destination_airport: str,
        carrier_code: str,
        flight_date: str,
        policy_text: str,
        claim_amount_usd: int,
    ) -> str:
        """Evaluate and settle a travel disruption claim.

        Parameters:
            origin_airport: IATA origin airport code.
            destination_airport: IATA destination airport code.
            carrier_code: Airline carrier code.
            flight_date: Scheduled flight date as YYYY-MM-DD.
            policy_text: Policy language used for coverage interpretation.
            claim_amount_usd: Claimed payout amount in USD.

        Returns:
            Claim id string.
        """
        policy = str(policy_text).strip()
        if len(policy) < 10:
            _raise_user_error(f"{ERROR_EXPECTED} invalid policy_text")
        if claim_amount_usd < 1:
            _raise_user_error(f"{ERROR_EXPECTED} claim_amount_usd out of range")

        def leader_fn():
            signals = self._collect_disruption_signals(origin_airport, destination_airport, carrier_code, flight_date)
            coverage = self._determine_coverage(policy, signals)
            approved = bool(signals.get("disruption_verified", False) and coverage.get("covered", False))
            return {
                "signals": signals,
                "coverage": coverage,
                "approved": approved,
                "bucket": int(signals.get("bucket", 0)),
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

            validator_result = leader_fn()
            leader_result = leaders_res.calldata
            if bool(leader_result.get("approved", False)) != bool(validator_result.get("approved", False)):
                return False
            if int(leader_result.get("bucket", -1)) != int(validator_result.get("bucket", -2)):
                return False
            return True

        outcome = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        cid = str(self.next_claim_id)
        self.next_claim_id += 1

        payout_usd = int(claim_amount_usd) if bool(outcome.get("approved", False)) else 0
        status = "approved" if payout_usd > 0 else "denied"

        claims = json.loads(self.claims)
        claims[cid] = {
            "claim_id": cid,
            "requester": _sender(),
            "status": status,
            "payout_usd": payout_usd,
            "claim_amount_usd": int(claim_amount_usd),
            "signals": outcome.get("signals", {}),
            "coverage": outcome.get("coverage", {}),
            "created_at": str(gl.block.timestamp),
        }
        self.claims = json.dumps(claims)
        return cid

    @gl.public.view
    def get_claim(self, claim_id: str) -> str:
        """Read one claim result.

        Parameters:
            claim_id: Claim identifier.

        Returns:
            Claim JSON string.
        """
        key = str(claim_id).strip()
        claims = json.loads(self.claims)
        if key not in claims:
            _raise_user_error(f"{ERROR_EXPECTED} claim not found")
        return json.dumps(claims[key])

    @gl.public.view
    def get_all_claims(self) -> str:
        """Read all claim results.

        Parameters:
            None.

        Returns:
            JSON map of claims.
        """
        return self.claims

    @gl.public.view
    def get_thresholds(self) -> str:
        """Read current disruption trigger thresholds.

        Parameters:
            None.

        Returns:
            Threshold JSON string.
        """
        return json.dumps(
            {
                "min_advisory_level": int(self.min_advisory_level),
                "min_cancel_rate_bps": int(self.min_cancel_rate_bps),
            }
        )
