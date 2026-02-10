# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class SimValidator(gl.Contract):
    """
    Simulates validator voting behavior based on a persona.
    - 'Conservative': Rejects anything suspicious or high-risk.
    - 'Risky': Accepts data even if it has minor flags.
    """
    
    # Stores: "Persona + Data Snippet" -> "Vote Result"
    simulation_results: TreeMap[str, str]

    def __init__(self):
        # Initialize storage to prevent AttributeErrors
        self.simulation_results = TreeMap()

    @gl.public.write
    def predict_vote(self, persona_type: str, data: str) -> None:
        """
        Asks the LLM to act as a specific type of validator and vote on the data.
        """
    
        # Create a unique key for storage (limit data length for key)
        storage_key = f"{persona_type}::{data[:50]}"

        def simulate_nondet() -> str:
            # Define Persona Instructions
            persona_prompt = ""
            if persona_type.lower() == "conservative":
                persona_prompt = """
                You are a 'Conservative Validator'. 
                Rules:
                1. You define 'Safety' as the highest priority.
                2. If the data looks scammy, vague, unverified, or too good to be true, REJECT it.
                3. Only APPROVE if it is 100% clear and standard.
                """
            elif persona_type.lower() == "risky":
                persona_prompt = """
                You are a 'Risky Validator' (Degen).
                Rules:
                1. You care about 'Potential Upside' and speed.
                2. If the data is messy but readable, APPROVE it.
                3. Only REJECT if it is obviously broken code or empty.
                """
            else:
                persona_prompt = f"You are a '{persona_type}' validator. Use your best judgment."

            task = f"""
            {persona_prompt}
            
            Data to Validate:
            "{data}"
            
            Task:
            1. Analyze the data based on your persona.
            2. Cast a vote: "Approve" or "Reject".
            3. Provide a brief 1-sentence reason.
            
            Respond using ONLY JSON:
            {{ "vote": "Approve" | "Reject", "reason": "..." }}
            """

            result_raw = gl.nondet.exec_prompt(task)
            try:
                cleaned = result_raw.replace("```json", "").replace("```", "").strip()
                return cleaned
            except:
                return json.dumps({"vote": "Error", "reason": "Parsing failed"})

        # Consensus: Comparative
        # We check if the *Vote Decision* is the same.
        comparison_criteria = """
        Compare the JSON outputs.
        
        Logic:
        1. Parse the 'vote' field (e.g. "Approve", "Reject").
        2. If both validators output the SAME vote, they are EQUAL.
        3. Ignore differences in the 'reason' text.
        """

        consensus_json = gl.eq_principle.prompt_comparative(
            simulate_nondet, 
            comparison_criteria
        )

        try:
            parsed = json.loads(consensus_json)
            vote = parsed.get("vote", "Abstain")
            reason = parsed.get("reason", "No consensus")
            
            # Store the result
            self.simulation_results[storage_key] = f"{vote}: {reason}"
        except:
            self.simulation_results[storage_key] = "Simulation Failed"
        
        return None

    @gl.public.view
    def get_prediction(self, persona_type: str, data_snippet: str) -> str:
        """
        Retrieve the result using the same key logic.
        Note: 'data_snippet' must match the first 50 chars of the original input.
        """
        storage_key = f"{persona_type}::{data_snippet[:50]}"
        if storage_key in self.simulation_results:
            return self.simulation_results[storage_key]
        return "Not found"
