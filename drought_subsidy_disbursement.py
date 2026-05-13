# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"


class DroughtSubsidyDisbursement(gl.Contract):
    """Releases farm subsidy disbursements when county drought thresholds are crossed."""

    enrollments: str
    balances: str
    next_enrollment_id: u256

    def __init__(self):
        """Initialize empty enrollment and balance storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.enrollments = "{}"
        self.balances = "{}"
        self.next_enrollment_id = 1

    @gl.public.write
    def enroll_farm(
        self,
        county_fips: str,
        farm_wallet: str,
        drought_metric: str,
        threshold_value: int,
        subsidy_amount: int,
        data_source: str,
    ) -> str:
        """Create a drought subsidy enrollment for a farm.

        Parameters:
            county_fips: County FIPS code string.
            farm_wallet: Recipient farm wallet address.
            drought_metric: Either pdsi or spi.
            threshold_value: Trigger threshold in integer scale.
            subsidy_amount: Subsidy amount to disburse on trigger.
            data_source: Either noaa or usda.

        Returns:
            Enrollment id string.
        """
        county = str(county_fips).strip()
        wallet = str(farm_wallet).strip()
        metric = str(drought_metric).strip().lower()
        source = str(data_source).strip().lower()

        if len(county) < 3:
            raise gl.UserError(f"{ERROR_EXPECTED} invalid county fips")
        if len(wallet) < 10:
            raise gl.UserError(f"{ERROR_EXPECTED} invalid farm wallet")
        if metric not in ["pdsi", "spi"]:
            raise gl.UserError(f"{ERROR_EXPECTED} unsupported drought metric")
        if source not in ["noaa", "usda"]:
            raise gl.UserError(f"{ERROR_EXPECTED} unsupported data source")
        if subsidy_amount <= 0:
            raise gl.UserError(f"{ERROR_EXPECTED} subsidy must be positive")

        enrollment_id = str(self.next_enrollment_id)
        self.next_enrollment_id += 1

        enrollments = json.loads(self.enrollments)
        enrollments[enrollment_id] = {
            "enrollment_id": enrollment_id,
            "county_fips": county,
            "farm_wallet": wallet,
            "drought_metric": metric,
            "threshold_value": int(threshold_value),
            "subsidy_amount": int(subsidy_amount),
            "data_source": source,
            "status": "ACTIVE",
            "triggered": False,
            "last_assessment": {},
            "resolved_at": "",
        }
        self.enrollments = json.dumps(enrollments)
        return enrollment_id

    @gl.public.write
    def evaluate_and_disburse(self, enrollment_id: str) -> str:
        """Evaluate drought severity and disburse subsidy when threshold is crossed.

        Parameters:
            enrollment_id: Enrollment id to evaluate.

        Returns:
            Settlement status string.
        """
        enrollments = json.loads(self.enrollments)
        key = str(enrollment_id)
        if key not in enrollments:
            raise gl.UserError(f"{ERROR_EXPECTED} enrollment not found")

        e = enrollments[key]
        if e["status"] != "ACTIVE":
            raise gl.UserError(f"{ERROR_EXPECTED} enrollment is not active")

        if e["data_source"] == "noaa":
            url = f"https://www.ncei.noaa.gov/access/monitoring/drought/county/{e['county_fips']}.json"
        else:
            url = f"https://usdroughtmonitor.com/data/county/{e['county_fips']}.json"

        def fetch_and_assess() -> str:
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
You evaluate drought subsidy triggers from government drought feeds.
Return JSON only.

Enrollment:
- County FIPS: {e['county_fips']}
- Metric: {e['drought_metric']}
- Threshold: {e['threshold_value']}

Rules:
1) Parse the payload and extract latest metric value for the county.
2) For PDSI more negative means worse drought.
3) For SPI more negative means worse drought.
4) triggered must be true when drought severity crosses threshold as interpreted for this metric.

Return exactly:
{{
  "metric": "pdsi_or_spi",
  "value": int,
  "triggered": bool,
  "context": "string"
}}

Payload:
{body[:12000]}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            metric_out = str(parsed.get("metric", e["drought_metric"])).strip().lower()
            value = int(parsed.get("value", 0))
            llm_triggered = bool(parsed.get("triggered", False))

            threshold = int(e["threshold_value"])
            if threshold <= 0:
                threshold_crossed = value <= threshold
            else:
                threshold_crossed = value <= -threshold

            triggered = llm_triggered or threshold_crossed

            return json.dumps(
                {
                    "metric": metric_out,
                    "value": value,
                    "triggered": triggered,
                    "context": str(parsed.get("context", ""))[:500],
                }
            )

        principle = "Equivalent when triggered is identical and value differs by no more than 2 units."
        result_json = gl.eq_principle.prompt_comparative(fetch_and_assess, principle)
        result = json.loads(result_json)

        e["last_assessment"] = result
        e["triggered"] = bool(result.get("triggered", False))
        e["resolved_at"] = str(gl.block.timestamp)

        balances = json.loads(self.balances)
        farm = str(e["farm_wallet"])

        if e["triggered"]:
            balances[farm] = int(balances.get(farm, 0)) + int(e["subsidy_amount"])
            e["status"] = "SETTLED_PAID"
        else:
            e["status"] = "SETTLED_NOT_TRIGGERED"

        enrollments[key] = e
        self.enrollments = json.dumps(enrollments)
        self.balances = json.dumps(balances)

        return e["status"]

    @gl.public.view
    def get_enrollment(self, enrollment_id: str) -> str:
        """Read a single enrollment record.

        Parameters:
            enrollment_id: Enrollment id string.

        Returns:
            Enrollment JSON string.
        """
        enrollments = json.loads(self.enrollments)
        key = str(enrollment_id)
        if key not in enrollments:
            raise gl.UserError(f"{ERROR_EXPECTED} enrollment not found")
        return json.dumps(enrollments[key])

    @gl.public.view
    def get_all_enrollments(self) -> str:
        """Read all enrollment records.

        Parameters:
            None.

        Returns:
            JSON map of enrollments.
        """
        return self.enrollments

    @gl.public.view
    def balance_of(self, farm_wallet: str) -> int:
        """Read internal disbursement balance for a farm wallet.

        Parameters:
            farm_wallet: Farm wallet string.

        Returns:
            Current integer balance.
        """
        balances = json.loads(self.balances)
        return int(balances.get(str(farm_wallet), 0))
