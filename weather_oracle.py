# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class WeatherOracle(gl.Contract):
    """
    A persistent Weather Oracle that aggregates temperature data from multiple
    web sources to provide a reliable, consensus-backed temperature reading.
    """
    
    # Stores the last recorded temperature (in Celsius) for a given city.
    # Key: City Name (str) -> Value: Temperature (u256)
    temperatures: TreeMap[str, u256]
    
    # Stores the timestamp (or block equivalent) of the last update
    # to ensure data freshness context if needed.
    last_updated: TreeMap[str, u256]

    def __init__(self):
        pass

    @gl.public.write
    def fetch_temp(self, city: str) -> int:
        """
        Fetches current weather data for 'city' from multiple sources,
        uses an LLM to extract and average the Celsius temperature,
        and updates the contract state.

        Args:
            city (str): The name of the city (e.g., "London", "Tokyo").

        Returns:
            int: The averaged temperature in Celsius.
        """
        
        # 1. Define the targets. 
        # Note: Direct scraping of specific complex DOMs (like AccuWeather/BBC) 
        # can sometimes be blocked or vary by region. We use search queries 
        # and general portals to maximize the chance of getting raw text data.
        url_google = f"https://www.google.com/search?q=weather+{city}"
        url_bbc = f"https://www.bbc.com/weather/search?q={city}"
        # Fallback/General source
        url_yahoo = f"https://news.yahoo.com/weather/{city}" 

        # 2. Define the non-deterministic execution logic
        def get_consensus_weather() -> int:
            # Render the webpages to text
            # We treat failures gracefully by continuing if one fails, 
            # but usually, render throws if totally unreachable.
            try:
                raw_google = gl.nondet.web.render(url_google, mode="text")
            except:
                raw_google = "Source Unavailable"
            
            try:
                raw_bbc = gl.nondet.web.render(url_bbc, mode="text")
            except:
                raw_bbc = "Source Unavailable"

            try:
                raw_yahoo = gl.nondet.web.render(url_yahoo, mode="text")
            except:
                raw_yahoo = "Source Unavailable"

            # 3. Construct the LLM Prompt
            # We give the LLM the messy raw text and ask it to do the heavy lifting:
            # Parsing, outlier removal, and averaging.
            prompt = f"""
            You are a Weather Consensus Agent. Your goal is to determine the current 
            temperature in Celsius for the city of: {city}.

            I will provide text dumps from three different weather/search sources.
            You must:
            1. Extract the current temperature in Celsius from each source.
            2. Ignore any source that has no valid data or seems clearly erroneous (outlier).
            3. Calculate the average of the valid temperatures.
            4. Return that average as a single integer (round to nearest).

            --- SOURCE 1 (Google) ---
            {raw_google[:1500]} 
            
            --- SOURCE 2 (BBC) ---
            {raw_bbc[:1500]}

            --- SOURCE 3 (Yahoo/General) ---
            {raw_yahoo[:1500]}
            --- END SOURCES ---

            Respond using ONLY the following JSON format:
            {{
                "found_values": [int, int, ...],  // The valid temps you found
                "average_celsius": int            // The final rounded integer average
            }}
            
            It is mandatory that you respond only using the JSON format above.
            """

            # Execute LLM
            result_raw = gl.nondet.exec_prompt(prompt)
            
            # Clean generic Markdown wrappers if present
            cleaned_result = result_raw.replace("```json", "").replace("```", "").strip()
            print(f"LLM Result for {city}: {cleaned_result}")
            
            parsed = json.loads(cleaned_result)
            return int(parsed["average_celsius"])

        # 4. Enforce Consensus
        # We use strict_eq here. Because all validators execute this relatively simultaneously,
        # they should receive similar web content. The LLM acts as the "reducer" to
        # ensure they all agree on the final integer.
        # Note: If web content varies wildly between validators (e.g. A/B testing), 
        # this might fail consensus. In production, 'prompt_comparative' with a 
        # ± range logic inside the validator code would be safer, but strict_eq 
        # is the standard v0.1.0 pattern for returning values.
        final_temp = gl.eq_principle.strict_eq(get_consensus_weather)

        # 5. Update State
        self.temperatures[city] = u256(final_temp)
        
        # We assume block.number or similar timestamp is available, 
        # but purely for this example, we just update the temp.
        
        return final_temp

    @gl.public.view
    def get_last_temp(self, city: str) -> int:
        """
        Returns the last stored temperature for a city.
        Returns -999 if the city has never been queried.
        """
        if city in self.temperatures:
            return int(self.temperatures[city])
        return -999
