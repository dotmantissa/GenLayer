# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class MetricSwap(gl.Contract):
    """
    Converts Imperial units to specific Metric units.
    Example: 1 Mile -> 1609.34 Meters (instead of just Km).
    Stores result as Scaled Integer (x1000).
    """
    
    # Storage Key: "{value}_{from}_{to}" (e.g., "1_mile_meter")
    # Storage Value: Metric Amount * 1000
    conversions: TreeMap[str, u256]

    def __init__(self):
        pass

    @gl.public.write
    def convert(self, value: int, from_unit: str, to_unit: str) -> None:
        """
        Converts 'value' from 'from_unit' to 'to_unit'.
        Returns NONE to avoid simulator serialization crashes.
        """
        
        # Create a unique key including the target unit
        # e.g. "10_miles_km" vs "10_miles_m"
        storage_key = f"{value}_{from_unit.lower()}_{to_unit.lower()}"

        def convert_nondet() -> str:
            task = f"""
            Act as a Unit Converter.
            
            Task: Convert {value} {from_unit} INTO {to_unit}.
            
            Instructions:
            1. Calculate the conversion precisely.
            2. Return ONLY the numeric value.
            3. Do not include units in the JSON value.
            
            Respond using ONLY JSON:
            {{ "result": float }}
            """

            result_raw = gl.nondet.exec_prompt(task)
            
            try:
                cleaned = result_raw.replace("```json", "").replace("```", "").strip()
                json.loads(cleaned)
                return cleaned
            except:
                return json.dumps({"result": 0.0})

        # Consensus: Comparative (Float Match with Tolerance)
        # We allow a 1% difference for float rounding
        comparison_criteria = """
        Compare the 'result' floats.
        
        Logic:
        1. Parse float val_a and val_b.
        2. If both are 0.0, EQUAL.
        3. If one is 0.0, DIFFERENT.
        4. Calculate Difference: abs(val_a - val_b) / max(val_a, val_b).
        5. If Difference <= 0.01 (1%), EQUAL.
        6. Otherwise, DIFFERENT.
        """

        consensus_json = gl.eq_principle.prompt_comparative(
            convert_nondet, 
            comparison_criteria
        )

        try:
            parsed = json.loads(consensus_json)
            val = float(parsed.get("result", 0.0))
            
            # Scale by 1000 (preserves 3 decimal places)
            scaled_int = int(val * 1000)
            
            self.conversions[storage_key] = u256(scaled_int)
        except Exception as e:
            print(f"Storage Error: {e}")
            self.conversions[storage_key] = u256(0)
        
        return None

    @gl.public.view
    def get_result(self, value: int, from_unit: str, to_unit: str) -> str:
        """
        Returns the converted value as a STRING.
        """
        storage_key = f"{value}_{from_unit.lower()}_{to_unit.lower()}"
        
        if storage_key in self.conversions:
            scaled = int(self.conversions[storage_key])
            result_float = scaled / 1000.0
            return f"{result_float}"
        return "0.0"
