# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class PhishGuard(gl.Contract):
    """
    Validates URLs against a hardcoded whitelist of safe domains.
    Uses LLM to detect sophisticated spoofing (homoglyphs, subdomain tricks).
    """
    
    # Stores: URL -> Is Safe (bool)
    safety_cache: TreeMap[str, bool]

    def __init__(self):
        pass

    @gl.public.write
    def is_safe(self, url: str) -> None:
        """
        Checks if the URL belongs to a whitelisted domain.
        Returns NONE to avoid simulator serialization crashes.
        """
        
        # Hardcoded Whitelist
        # In a real app, this could be stored in state and updated via governance.
        safe_domains = [
            "google.com",
            "github.com",
            "stackoverflow.com",
            "genlayer.com",
            "wikipedia.org"
        ]
        
        def check_safety_nondet() -> bool:
            task = f"""
            Act as a Cyber Security Expert.
            
            Target URL: "{url}"
            
            Whitelist: {json.dumps(safe_domains)}
            
            Instructions:
            1. Analyze the Target URL structure.
            2. Determine the *effective second-level domain* (eSLD).
            3. Check if the eSLD matches exactly one of the Whitelisted domains.
            4. BEWARE of phishing tricks:
               - "google.com.phish.net" -> UNSAFE (Host is phish.net)
               - "goog1e.com" -> UNSAFE (Homoglyph)
               - "accounts.google.com" -> SAFE (Valid subdomain)
            
            Respond using ONLY JSON:
            {{ "is_safe": true | false }}
            """

            result_raw = gl.nondet.exec_prompt(task)
            
            try:
                cleaned = result_raw.replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)
                return bool(parsed.get("is_safe", False))
            except:
                return False

        # Consensus: Strict Equality
        # Security requires 100% agreement.
        # If one validator sees a threat, the consensus might fail or default to strict matching.
        result = gl.eq_principle.strict_eq(check_safety_nondet)

        # Update State
        self.safety_cache[url] = result
        
        return None

    @gl.public.view
    def check_status(self, url: str) -> bool:
        """
        Returns the safety status of the URL.
        """
        if url in self.safety_cache:
            return self.safety_cache[url]
        return False
