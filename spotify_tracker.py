# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class SpotifyTracker(gl.Contract):
    """
    A tracker contract that monitors the popularity of artists on Spotify
    by verifying their monthly listener count from public web data.
    """

    # Maps Spotify Artist ID (str) -> Monthly Listeners (u256)
    artist_listeners: TreeMap[str, u256]

    def __init__(self):
        pass

    @gl.public.write
    def update_listeners(self, artist_id: str) -> int:
        """
        Fetches the public Spotify artist page, extracts the monthly listener count
        using an LLM, and updates the contract state.

        Args:
            artist_id (str): The unique Spotify artist ID (e.g., "3TVXtAsR1Inumwj472S9r4").

        Returns:
            int: The current number of monthly listeners.
        """
        
        # Use the standard public URL
        url = f"https://open.spotify.com/artist/{artist_id}"

        def fetch_data_nondet() -> int:
            # 1. Render the page to text.
            print(f"Fetching {url}...")
            # We catch errors here to prevent the runner from crashing if the URL is unreachable
            try:
                web_content = gl.nondet.web.render(url, mode="text")
            except Exception as e:
                print(f"Web render failed: {e}")
                return 0

            # 2. Construct the extraction task
            task = f"""
            Analyze the following text content from a Spotify Artist page.
            Find the number associated with "Monthly Listeners".
            
            Target Page Text:
            {web_content[:4000]}
            
            Instructions:
            1. Find the numeric value for monthly listeners (e.g. "84,392,109").
            2. Remove any commas or non-numeric characters.
            3. Return the raw integer.
            4. If not found, return 0.

            Respond using ONLY the following JSON format:
            {{ "listeners": int }}
            """

            # 3. Execute LLM
            result_raw = gl.nondet.exec_prompt(task)
            
            # 4. Clean and Parse
            cleaned_result = result_raw.replace("```json", "").replace("```", "").strip()
            
            try:
                parsed = json.loads(cleaned_result)
                return int(parsed["listeners"])
            except:
                return 0

        # 5. Enforce Strict Equality
        listeners_count = gl.eq_principle.strict_eq(fetch_data_nondet)

        # 6. Update State
        self.artist_listeners[artist_id] = u256(listeners_count)
        
        return listeners_count

    @gl.public.view
    def get_monthly_listeners(self, artist_id: str) -> int:
        if artist_id in self.artist_listeners:
            return int(self.artist_listeners[artist_id])
        return 0
