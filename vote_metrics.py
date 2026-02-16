# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class VoteMetrics(gl.Contract):
    """
    Calculates voter turnout percentage for Snapshot proposals.
    Formula: (votes / total_supply) * 100.
    Uses Snapshot GraphQL API for reliable vote counts and LLM knowledge for supply.
    """
    
    # Storage: Proposal ID -> Turnout Percentage (Scaled x100)
    # Example: 15.5% -> 1550
    turnouts: TreeMap[str, u256]

    def __init__(self):
        # Initialize storage to prevent runtime errors
        self.turnouts = TreeMap()

    @gl.public.write
    def get_turnout(self, proposal_url: str) -> None:
        """
        Calculates turnout.
        Extracts ID from URL, queries API, uses LLM to compute % against Total Supply.
        Returns NONE to avoid simulator serialization crashes.
        """
        # 1. Extract Proposal ID from URL
        # Format: https://snapshot.org/#/space/proposal/0x123...
        # We handle trailing slashes robustly
        parts = proposal_url.strip().split('/')
        # Filter empty strings from split (caused by trailing slash)
        valid_parts = [p for p in parts if p]
        pid = valid_parts[-1]
        
        # 2. GraphQL Query
        # We fetch 'scores_total' (votes), 'body' (context), and 'space' (to identify token)
        q_prefix = "query%20%7B%20proposal(id%3A%22"
        q_suffix = "%22)%20%7B%20scores_total%20body%20space%20%7B%20id%20name%20%7D%20%7D%20%7D"
        
        api_url = f"https://hub.snapshot.org/graphql?query={q_prefix}{pid}{q_suffix}"

        def calc_turnout_nondet() -> str:
            print(f"Fetching Proposal: {pid}")
            try:
                # 'text' mode retrieves the raw JSON response from the API
                data = gl.nondet.web.render(api_url, mode="text")
            except Exception as e:
                print(f"Fetch failed: {e}")
                return json.dumps({"percentage": 0.0})

            task = f"""
            Act as a DAO Analyst.
            
            Task: Calculate Voter Turnout Percentage for this Proposal.
            Formula: (Total Votes / Total Token Supply) * 100.
            
            API Data:
            {data[:4000]}
            
            Instructions:
            1. Find 'scores_total' in the JSON (This is the Total Votes).
            2. Identify the DAO/Space from 'space.id' or 'space.name'.
            3. Estimate the Total Token Supply for this DAO based on your internal knowledge.
               - Example: Arbitrum (ARB) ~ 10 Billion
               - Example: ENS ~ 100 Million
               - Example: Aave ~ 16 Million
            4. If the proposal text explicitly mentions a supply, use that.
            5. Calculate the percentage: (scores_total / total_supply) * 100.
            6. If you absolutely cannot determine supply, return 0.0.
            
            Respond using ONLY JSON:
            {{ 
                "total_votes": float,
                "total_supply_used": float,
                "percentage": float 
            }}
            """

            result_raw = gl.nondet.exec_prompt(task)
            try:
                cleaned = result_raw.replace("```json", "").replace("```", "").strip()
                # Validation check
                json.loads(cleaned)
                return cleaned
            except:
                return json.dumps({"percentage": 0.0})

        # Consensus: Comparative (Float Match)
        # We allow a small tolerance (0.1%) as LLMs might use slightly different supply estimates
        comparison_criteria = """
        Compare 'percentage' floats.
        
        Logic:
        1. Parse val_a and val_b.
        2. If abs(val_a - val_b) <= 0.1, EQUAL.
        3. Otherwise, DIFFERENT.
        """

        consensus_json = gl.eq_principle.prompt_comparative(
            calc_turnout_nondet, 
            comparison_criteria
        )

        try:
            parsed = json.loads(consensus_json)
            pct = float(parsed.get("percentage", 0.0))
            
            # Store as Scaled Integer (x100 for 2 decimal places)
            # 12.55% -> 1255
            self.turnouts[pid] = u256(int(pct * 100))
            print(f"Stored Turnout for {pid}: {pct}%")
        except:
            pass
        
        return None

    @gl.public.view
    def read_turnout(self, proposal_url: str) -> str:
        """
        Returns the calculated turnout as a string percentage.
        """
        parts = proposal_url.strip().split('/')
        valid_parts = [p for p in parts if p]
        pid = valid_parts[-1]
        
        if pid in self.turnouts:
            val = int(self.turnouts[pid])
            return f"{val / 100.0}%"
        return "Unknown"
