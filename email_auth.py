# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class EmailAuth(gl.Contract):
    """
    Verifies DKIM Alignment: Checks if the signed domain (d=) 
    matches the sender's domain (From:).
    """
    
    # Stores: Header Hash (or snippet) -> Verified Boolean
    # We map the full header text to a boolean result.
    verification_results: TreeMap[str, bool]
    
    def __init__(self):
        pass

    @gl.public.write
    def verify_dkim(self, header_text: str) -> None:
        """
        Parses raw email headers to check DKIM domain alignment.
        Returns NONE to avoid simulator serialization crashes.
        """
        
        def check_alignment_nondet() -> bool:
            # Task: Parse headers and compare domains
            task = f"""
            Act as an Email Security Analyst.
            
            Task: Check DKIM Alignment for these headers.
            
            Headers:
            {header_text}
            
            Instructions:
            1. Extract the domain from the 'From:' header (e.g. 'bob@example.com' -> 'example.com').
            2. Extract the 'd=' value from the 'DKIM-Signature' header.
            3. Compare them.
            4. Return TRUE if they match exactly (ignoring case).
            5. Return FALSE if they differ or if DKIM is missing.
            
            Respond using ONLY JSON:
            {{ "is_aligned": true | false }}
            """

            result_raw = gl.nondet.exec_prompt(task)
            
            try:
                cleaned = result_raw.replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)
                return bool(parsed.get("is_aligned", False))
            except:
                return False

        # Consensus: Strict Equality
        # Security checks require 100% agreement. 
        # All validators must agree the domains match.
        result = gl.eq_principle.strict_eq(check_alignment_nondet)

        # Store the result
        self.verification_results[header_text] = result
        
        return None

    @gl.public.view
    def is_verified(self, header_text: str) -> bool:
        """
        Returns true if the email headers passed the alignment check.
        """
        if header_text in self.verification_results:
            return self.verification_results[header_text]
        return False
