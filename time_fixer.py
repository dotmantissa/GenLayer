# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class TimeFixer(gl.Contract):
    """
    Converts natural language time into Unix Timestamp.
    """
    
    # ERROR FIX: 'int' -> 'u256' for storage
    timestamps: TreeMap[str, u256]

    def __init__(self):
        pass

    @gl.public.write
    def to_unix_timestamp(self, natural_language_time: str) -> None:
        """
        Resolves relative time to Unix timestamp using worldtimeapi.org as anchor.
        Returns NONE to avoid simulator serialization crashes.
        """
        
        # 1. Fetch current time as anchor
        time_api_url = "http://worldtimeapi.org/api/timezone/Etc/UTC"
        
        def resolve_time_nondet() -> str:
            print(f"Fetching Reference Time from: {time_api_url}")
            current_time_str = "Unknown"
            try:
                # 'text' mode gets raw JSON
                api_content = gl.nondet.web.render(time_api_url, mode="text")
                if "datetime" in api_content:
                    current_time_str = api_content
            except Exception as e:
                print(f"Time Fetch failed: {e}")
            
            # 2. Prompt LLM
            task = f"""
            Act as a Time Resolver.
            
            Context:
            - Current Reference Time (UTC): {current_time_str}
            - If Reference is Unknown, use execution time.
            
            Task:
            - Convert this natural language input to UNIX TIMESTAMP (seconds): "{natural_language_time}"
            - Examples: "2 hours ago", "yesterday".
            
            Output:
            - Return ONLY the integer timestamp.
            
            Respond using ONLY JSON:
            {{ "timestamp": int }}
            """

            result_raw = gl.nondet.exec_prompt(task)
            
            try:
                cleaned = result_raw.replace("```json", "").replace("```", "").strip()
                json.loads(cleaned)
                return cleaned
            except:
                return json.dumps({"timestamp": 0})

        # Consensus: Comparative (±3600 seconds)
        comparison_criteria = """
        Compare 'timestamp' integers.
        Equal if abs(val_a - val_b) <= 3600.
        """

        consensus_json = gl.eq_principle.prompt_comparative(
            resolve_time_nondet, 
            comparison_criteria
        )

        try:
            parsed = json.loads(consensus_json)
            ts = int(parsed.get("timestamp", 0))
            # Store as u256
            self.timestamps[natural_language_time] = u256(ts)
        except:
            self.timestamps[natural_language_time] = u256(0)
        
        return None

    @gl.public.view
    def get_timestamp(self, natural_language_time: str) -> int:
        if natural_language_time in self.timestamps:
            return int(self.timestamps[natural_language_time])
        return 0
