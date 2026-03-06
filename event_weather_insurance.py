# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

class EventWeatherInsurance(gl.Contract):
    """
    Event Weather Insurance & Settlement
    - Accepts deposit info (ticket holders, amounts, organizer).
    - Fetches Open-Meteo forecast 24hrs before the event (with Search Fallback).
    - Uses AI Equivalence to determine Severe Weather (Wind >60km/h, Rain >20mm, Thunderstorms).
    - Refunds ticket holders if severe, otherwise pays organizer.
    """
    
    # Flat storage using JSON strings to prevent complex type serialization errors in the simulator
    event_data: str
    balances: str

    def __init__(self):
        # Initialize default states
        self.event_data = json.dumps({"status": "UNINITIALIZED"})
        self.balances = "{}"

    @gl.public.write
    def initialize_event(self, organizer: str, event_date_yyyy_mm_dd: str, lat: str, lon: str, ticket_holders_json: str) -> None:
        """
        Setup the event.
        ticket_holders_json format: '{"0xAlice": 100, "0xBob": 50}'
        """
        try:
            holders = json.loads(ticket_holders_json)
            # Calculate total deposited
            total = sum(float(v) for v in holders.values())
            
            data = {
                "organizer": str(organizer).strip(),
                "event_date": str(event_date_yyyy_mm_dd).strip(),
                "lat": str(lat).strip(),
                "lon": str(lon).strip(),
                "ticket_holders": holders,
                "total_deposit": total,
                "status": "PENDING"
            }
            self.event_data = json.dumps(data)
            print(f"Event initialized. Total deposits: {total}")
        except Exception as e:
            self.event_data = json.dumps({"status": f"INIT_ERROR: {str(e)}"})
            
        return None

    @gl.public.write
    def settle_event(self) -> None:
        """
        Fetches weather forecast, runs AI equivalence, and settles funds via accounting.
        """
        try:
            data = json.loads(self.event_data)
        except:
            return None
            
        if data.get("status") != "PENDING":
            print("Event is not PENDING. Cannot settle.")
            return None
            
        lat = data.get("lat", "0")
        lon = data.get("lon", "0")
        target_date = data.get("event_date", "")

        # Primary Source: Open-Meteo Daily Forecast
        # We query the specific date to ensure accurate metrics
        url_api = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&start_date={target_date}&end_date={target_date}&daily=weathercode,precipitation_sum,windspeed_10m_max&timezone=auto"
        
        # Fallback Source: Search query
        url_search = f"https://html.duckduckgo.com/html/?q=weather+forecast+{lat}+{lon}+{target_date}+wind+precipitation"

        def fetch_weather() -> str:
            resp = ""
            source = "Open-Meteo API"
            
            # 1. Try Primary API
            try:
                print(f"Trying Primary API: {url_api}")
                resp = gl.nondet.web.render(url_api, mode="text")
                # Basic validation to ensure API didn't return an HTML error page
                if "error" in resp.lower() or not resp.strip().startswith("{"):
                    raise Exception("API Response Invalid")
            except Exception as e:
                # 2. Fallback to Search
                print(f"API failed ({e}), using search fallback...")
                source = "DuckDuckGo Search"
                try:
                    resp = gl.nondet.web.render(url_search, mode="text")
                except:
                    return json.dumps({"valid": False, "error": "All fetches failed"})

            task = f"""
            Act as a Weather Analyst.
            Source: {source}
            Data: {resp[:3000]}
            Target Date: {target_date}

            Task: Determine if "Severe Weather" is forecasted for the target date.
            Criteria for Severe Weather (True if ANY apply):
            1. Wind speed max > 60 km/h
            2. Precipitation sum > 20 mm
            3. Thunderstorms (weather code 95, 96, 99 or text mention of thunderstorm)

            Respond ONLY with JSON:
            {{
                "is_severe": bool,
                "wind_kmh": float,
                "precip_mm": float,
                "valid": true
            }}
            """
            
            result_raw = gl.nondet.exec_prompt(task)
            try:
                cleaned = result_raw.replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)
                # Normalize boolean
                parsed["valid"] = True
                parsed["is_severe"] = bool(parsed.get("is_severe", False))
                return json.dumps(parsed)
            except:
                return json.dumps({"valid": False})

        # Consensus: AI Equivalence
        criteria = """
        Compare the 'is_severe' boolean.
        If both are true, return EQUAL.
        If both are false, return EQUAL.
        If they differ, return DIFFERENT.
        """
        
        consensus_json = gl.eq_principle.prompt_comparative(fetch_weather, criteria)

        try:
            consensus_data = json.loads(consensus_json)
            
            if consensus_data.get("valid") is True:
                is_severe = consensus_data["is_severe"]
                bals = json.loads(self.balances)
                
                if is_severe:
                    # Condition 1: Severe Weather -> Refund ticket holders
                    holders = data["ticket_holders"]
                    for addr, amount in holders.items():
                        bals[addr] = bals.get(addr, 0.0) + float(amount)
                    data["status"] = "SETTLED_REFUNDED_SEVERE_WEATHER"
                else:
                    # Condition 2: Good Weather -> Release funds to organizer
                    org = data["organizer"]
                    total = float(data["total_deposit"])
                    bals[org] = bals.get(org, 0.0) + total
                    data["status"] = "SETTLED_PAID_TO_ORGANIZER"
                
                # Save mutated state back to JSON strings
                self.balances = json.dumps(bals)
                data["last_forecast"] = consensus_data
                self.event_data = json.dumps(data)
                print(f"Settlement complete. Severe: {is_severe}")
            else:
                data["status"] = "ERROR_CONSENSUS_INVALID"
                self.event_data = json.dumps(data)
                
        except Exception as e:
            data["status"] = f"ERROR_SETTLEMENT: {str(e)}"
            self.event_data = json.dumps(data)

        return None

    @gl.public.view
    def get_event_status(self) -> str:
        """
        Returns the event details, conditions, and settlement status.
        """
        return self.event_data

    @gl.public.view
    def get_balances(self) -> str:
        """
        Returns the internal accounting ledger of who owns what funds.
        """
        return self.balances
