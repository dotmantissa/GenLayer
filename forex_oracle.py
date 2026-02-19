# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class FrankfurterOracle(gl.Contract):
    """
    A decentralized Forex Oracle fetching official European Central Bank (ECB) 
    exchange rates via the open-source Frankfurter API.
    """
    
    latest_eur: u256
    latest_gbp: u256
    latest_jpy: u256
    last_update_date: str

    def __init__(self):
        self.latest_eur = u256(0)
        self.latest_gbp = u256(0)
        self.latest_jpy = u256(0)
        self.last_update_date = "1970-01-01"

    @gl.public.write
    def update_rates(self) -> None:
        url = "https://api.frankfurter.app/latest?from=USD&to=EUR,GBP,JPY"

        def fetch_rates_nondet() -> str:
            try:
                api_response = gl.nondet.web.render(url, mode="text")
                data = json.loads(api_response)
                
                date_str = data.get("date", "1970-01-01")
                rates = data.get("rates", {})
                
                multiplier = 10**18
                
                eur = int(rates.get("EUR", 0) * multiplier)
                gbp = int(rates.get("GBP", 0) * multiplier)
                jpy = int(rates.get("JPY", 0) * multiplier)
                
                return json.dumps({
                    "date": date_str,
                    "eur": eur,
                    "gbp": gbp,
                    "jpy": jpy,
                    "success": True
                })
            except Exception as e:
                print(f"API Fetch Failed: {e}")
                return json.dumps({"success": False})

        consensus_result = gl.eq_principle.strict_eq(fetch_rates_nondet)
        parsed_result = json.loads(consensus_result)

        if parsed_result.get("success"):
            fetched_date = parsed_result["date"]
            
            if fetched_date != "1970-01-01":
                self.latest_eur = u256(parsed_result["eur"])
                self.latest_gbp = u256(parsed_result["gbp"])
                self.latest_jpy = u256(parsed_result["jpy"])
                self.last_update_date = fetched_date

    @gl.public.view
    def get_rates(self) -> dict[str, str]:
        """
        Returns the exact, human-readable decimal prices as safe strings.
        """
        return {
            "EUR": str(int(self.latest_eur) / 10**18),
            "GBP": str(int(self.latest_gbp) / 10**18),
            "JPY": str(int(self.latest_jpy) / 10**18),
            "last_update": self.last_update_date
        }

    @gl.public.view
    def get_raw_rates(self) -> dict[str, int]:
        """
        Returns the 18-decimal fixed-point integers for on-chain math and composability.
        """
        return {
            "EUR": int(self.latest_eur),
            "GBP": int(self.latest_gbp),
            "JPY": int(self.latest_jpy)
        }
