# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class SnapLink(gl.Contract):
    """
    Verifies the outcome of Snapshot.org governance proposals.
    Uses the official Snapshot GraphQL API via GET request to bypass UI/Search scraping issues.
    """
    
    # Storage: Proposal ID -> Passed Status (True/False)
    proposal_results: TreeMap[str, bool]

    def __init__(self):
        self.proposal_results = TreeMap()

    @gl.public.write
    def check_proposal(self, proposal_id: str) -> None:
        """
        Fetches the proposal result via API and determines if 'For' > 'Against'.
        Returns NONE to avoid simulator serialization crashes.
        """
        pid = proposal_id.strip()
        
        # Strategy: Query the Snapshot Hub GraphQL API directly.
        # This returns clean JSON data, bypassing the heavy UI and search engine blocks.
        # We manually URL-encode the query: query { proposal(id: "PID") { choices scores state } }
        
        # Encoded parts
        q_prefix = "query%20%7B%20proposal(id%3A%22"
        q_suffix = "%22)%20%7B%20choices%20scores%20state%20%7D%20%7D"
        
        url = f"https://hub.snapshot.org/graphql?query={q_prefix}{pid}{q_suffix}"

        def fetch_outcome_nondet() -> bool:
            print(f"Querying API: {pid}")
            try:
                # 'text' mode retrieves the raw JSON response from the API
                api_response = gl.nondet.web.render(url, mode="text")
            except Exception as e:
                print(f"API Fetch failed: {e}")
                return False

            task = f"""
            Act as a Governance Analyst.
            
            Task: Analyze the Snapshot API JSON response for Proposal {pid}.
            
            API Response:
            {api_response[:4000]}
            
            Instructions:
            1. Parse the JSON to find 'choices' (labels) and 'scores' (vote totals).
            2. Map the scores to the choices (they match by index).
            3. Find the total votes for affirmative options ("For", "Yes", "Approve").
            4. Find the total votes for negative options ("Against", "No", "Reject").
            5. Logic: If (Affirmative > Negative), return TRUE. Otherwise, FALSE.
            6. If the proposal state is not "closed" or data is missing, return FALSE.
            
            Respond using ONLY JSON:
            {{ "passed": boolean }}
            """

            result_raw = gl.nondet.exec_prompt(task)
            
            try:
                cleaned = result_raw.replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)
                return bool(parsed.get("passed", False))
            except:
                return False

        # Consensus: Strict Boolean Equality
        # All validators process the same JSON and must reach the same conclusion.
        is_passed = gl.eq_principle.strict_eq(fetch_outcome_nondet)

        # Update State
        self.proposal_results[pid] = is_passed
        print(f"Proposal {pid} -> {'Passed' if is_passed else 'Failed/Unknown'}")
        
        return None

    @gl.public.view
    def did_pass(self, proposal_id: str) -> bool:
        """
        Returns True if the proposal passed, False otherwise.
        """
        pid = proposal_id.strip()
        if pid in self.proposal_results:
            return self.proposal_results[pid]
        return False
