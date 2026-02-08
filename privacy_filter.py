# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class PrivacyFilter(gl.Contract):
    """
    Redacts PII (Emails, Phone Numbers) from text.
    Requires Strict Consensus: All validators must redact exactly the same way.
    """
    
    # Stores: Original Input -> Redacted Output
    # Example: "Call me at 555-0199" -> "Call me at [REDACTED]"
    redacted_logs: TreeMap[str, str]

    def __init__(self):
        pass

    @gl.public.write
    def redact_text(self, input_text: str) -> None:
        """
        Uses LLM to identify and scrub private information.
        Returns NONE to avoid simulator serialization crashes.
        """
        
        def redact_nondet() -> str:
            # Task: Mechanical redaction
            task = f"""
            Act as a Data Privacy Engine.
            
            Input Text:
            "{input_text}"
            
            Instructions:
            1. Identify ALL email addresses.
            2. Identify ALL phone numbers (various formats).
            3. Replace the identified entities with the exact string "[REDACTED]".
            4. Do NOT change any other words, punctuation, or whitespace.
            5. Output ONLY the processed text.
            """

            result_raw = gl.nondet.exec_prompt(task)
            
            # Cleanup: Remove Markdown code blocks if the LLM adds them
            cleaned = result_raw.replace("```text", "").replace("```", "").strip()
            return cleaned

        # Consensus: Strict Equality
        # We require all validators to agree on the exact output string.
        # If one validator misses a phone number, consensus will correctly fail 
        # (or return a disagreement error depending on network config).
        result = gl.eq_principle.strict_eq(redact_nondet)

        # Update State
        self.redacted_logs[input_text] = result
    
        return None

    @gl.public.view
    def get_redacted(self, input_text: str) -> str:
        """
        Returns the redacted version of the text.
        """
        if input_text in self.redacted_logs:
            return self.redacted_logs[input_text]
        return "Not processed"
