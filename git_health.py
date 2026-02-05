# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class GitHealth(gl.Contract):
    """
    Analyzes GitHub repositories to assign a "Health Score" (0-100) based on
    maintenance activity visible on the main page.
    """

    # Maps Repository URL (str) -> Health Score (u256)
    repo_scores: TreeMap[str, u256]

    def __init__(self):
        pass

    @gl.public.write
    def analyze_repo(self, repo_url: str) -> int:
        """
        Scrapes a GitHub repository, calculates a health score based on recent
        commits and open issues, and updates the state.
        """

        def get_repo_health():
            # 1. Fetch the repo page
            try:
                print(f"Fetching {repo_url}...")
                web_content = gl.nondet.web.render(repo_url, mode="text")
            except Exception as e:
                print(f"Fetch failed: {e}")
                # Return a valid JSON structure even on failure so the parser doesn't break
                return json.dumps({"health_score": 0, "reasoning": "Fetch failed"})

            # 2. LLM Analysis Task
            task = f"""
            You are a Code Repository Auditor. Analyze the text from this GitHub page.
            
            Goal: Calculate a 'Health Score' (0-100) based on:
            1. Recency of the 'Last Commit' (Visible in the file list header).
            2. Count of 'Open Issues' (Visible in the tabs).

            Rubric:
            - Start with 100 points.
            - If last commit was > 1 month ago, deduct 10 points.
            - If last commit was > 6 months ago, deduct 40 points.
            - If last commit was > 1 year ago, deduct 60 points.
            - Deduct 1 point for every 10 Open Issues (max deduction 20).
            - Minimum score is 0.

            Repo Content Snippet:
            {web_content[:6000]} 

            Respond using ONLY the following JSON format:
            {{
                "reasoning": str,       // Explain your deduction math
                "health_score": int     // Final score 0-100
            }}
            """

            # 3. Execute Prompt
            result_raw = gl.nondet.exec_prompt(task)
            cleaned = result_raw.replace("```json", "").replace("```", "").strip()
            print(f"LLM Assessment: {cleaned}")
            return cleaned

        # 4. Comparative Consensus
        # FIX: Passed as a positional argument (removed 'criteria=')
        consensus_instruction = """
        Compare the 'health_score' values in the JSON results.
        They must be effectively equal, defined as being within a difference of 5 points.
        (e.g. 80 and 85 are valid matches. 80 and 86 are not).
        """

        final_json_str = gl.eq_principle.prompt_comparative(
            get_repo_health, 
            consensus_instruction
        )

        # 5. Parse and Store
        parsed = json.loads(final_json_str)
        score = int(parsed["health_score"])
        
        self.repo_scores[repo_url] = u256(score)
        return score

    @gl.public.view
    def get_score(self, repo_url: str) -> int:
        if repo_url in self.repo_scores:
            return int(self.repo_scores[repo_url])
        return 0
