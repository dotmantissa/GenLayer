# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

class RuleExplain(gl.Contract):
    """
    Translates complex legal text into Simple English.
    Uses Semantic Equivalence to reach consensus on the meaning, 
    ignoring differences in phrasing.
    """
    
    # Storage: "First 60 chars of clause" -> "Simplified Explanation"
    simplifications: TreeMap[str, str]

    def __init__(self):
        self.simplifications = TreeMap()

    @gl.public.write
    def explain_clause(self, legal_text: str) -> None:
        """
        Accepts a legal clause, simplifies it, and stores the result.
        """
        # Defensive check for storage
        if not hasattr(self, 'simplifications'):
            self.simplifications = TreeMap()

        # Create a lookup key (first 60 chars)
        # In a real app, you might use a hash, but this is readable for testing.
        key = legal_text[:60].strip()

        def simplify_nondet() -> str:
            task = f"""
            Act as a Legal Assistant.
            
            Task: Translate the following legal clause into simple, plain English (5th-grade level).
            
            Legal Text:
            "{legal_text}"
            
            Instructions:
            1. Keep it short (1-2 sentences).
            2. Remove jargon (e.g., "heretofore", "indemnify").
            3. Focus on the core obligation or right.
            
            Respond using ONLY JSON:
            {{ "explanation": "string" }}
            """

            result_raw = gl.nondet.exec_prompt(task)
            try:
                cleaned = result_raw.replace("```json", "").replace("```", "").strip()
                return cleaned
            except:
                return json.dumps({"explanation": "Error processing text"})

        # Consensus: Semantic Similarity
        # We cannot use string equality because "Pay $50" != "$50 fee", 
        # but they mean the same thing.
        comparison_criteria = """
        Compare the 'explanation' fields.
        
        Logic:
        1. Read Explanation A and Explanation B.
        2. Do they convey the SAME meaning?
        3. Ignore minor wording differences (e.g. "must pay" vs "payment required").
        4. If semantically equivalent, return EQUAL.
        5. If they describe different rules, return DIFFERENT.
        """

        consensus_json = gl.eq_principle.prompt_comparative(
            simplify_nondet, 
            comparison_criteria
        )

        try:
            parsed = json.loads(consensus_json)
            explanation = parsed.get("explanation", "Consensus Failed")
            
            self.simplifications[key] = explanation
            print(f"Stored explanation for '{key}...'")
        except Exception as e:
            print(f"Update failed: {e}")
        
        return None

    @gl.public.view
    def get_explanation(self, text_snippet: str) -> str:
        """
        Retrieve the explanation using the start of the original text.
        """
        key = text_snippet[:60].strip()
        if self.simplifications is not None and key in self.simplifications:
            return self.simplifications[key]
        return "Explanation not found"
