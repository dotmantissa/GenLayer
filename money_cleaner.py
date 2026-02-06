# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class MoneyCleaner(gl.Contract):
    """
    Normalizes arbitrary currency strings into USD Cents.
    Uses fuzzy consensus (±5%) to handle exchange rate fluctuations.
    """
    
    # Stores: Raw String -> USD Cents
    # Example: "£50" -> 6350 (represents $63.50)
    prices_map: TreeMap[str, u256]

    def __init__(self):
        pass

    @gl.public.write
    def normalize_to_usd(self, raw_price_string: str) -> None:
        """
        Converts a price string to USD cents using LLM knowledge.
        Returns NONE to avoid simulator serialization crashes.
        """
        
        def convert_nondet() -> str:
            # Task: Convert and output JSON
            task = f"""
            Act as a Currency Converter.
            Input Price: "{raw_price_string}"
            
            Instructions:
            1. Identify the amount and currency symbol (e.g. £, EUR, ¥).
            2. Convert the amount to USD using approximate current market rates.
            3. Convert the final USD amount into CENTS (integer).
               Example: $50.00 -> 5000.
            4. If the input is invalid or 0, return 0.

            Respond using ONLY JSON:
            {{ "cents": int }}
            """

            result_raw = gl.nondet.exec_prompt(task)
            
            # Clean and return the JSON string for the comparator
            try:
                cleaned = result_raw.replace("```json", "").replace("```", "").strip()
                # fast validation
                parsed = json.loads(cleaned)
                if "cents" not in parsed: 
                    return json.dumps({"cents": 0})
                return cleaned
            except:
                return json.dumps({"cents": 0})

        # Consensus: Comparative (±5% Tolerance)
        # We instruct the consensus engine to accept values if they are close.
        comparison_criteria = """
        Compare the 'cents' integers from the two JSON inputs.
        
        Logic:
        1. Parse the integers val_a and val_b.
        2. If both are 0, they are EQUAL.
        3. Calculate the percentage difference: abs(val_a - val_b) / max(val_a, val_b).
        4. If the difference is less than or equal to 0.05 (5%), they are EQUAL.
        5. Otherwise, they are DIFFERENT.
        """

        # Result is the JSON from the leader node
        consensus_json = gl.eq_principle.prompt_comparative(
            convert_nondet, 
            comparison_criteria
        )

        # Parse and Store
        try:
            parsed = json.loads(consensus_json)
            cents_val = int(parsed.get("cents", 0))
            
            # Store as u256
            self.prices_map[raw_price_string] = u256(cents_val)
        except Exception as e:
            print(f"Storage Error: {e}")
            self.prices_map[raw_price_string] = u256(0)
        
        return None

    @gl.public.view
    def get_usd_cents(self, raw_price_string: str) -> int:
        """
        Returns the normalized value in USD Cents.
        Example: 6350 = $63.50
        """
        if raw_price_string in self.prices_map:
            return int(self.prices_map[raw_price_string])
        return 0
