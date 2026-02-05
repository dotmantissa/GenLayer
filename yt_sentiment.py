# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class YTSentiment(gl.Contract):
    """
    Analyzes YouTube video sentiment by scraping comments via an Invidious mirror.
    """
    
    # Maps Video ID -> Sentiment ("Positive", "Negative", "Neutral")
    video_sentiments: TreeMap[str, str]

    def __init__(self):
        pass

    @gl.public.write
    def determine_mood(self, video_id: str) -> None:
        """
        Fetches comments and calculates sentiment.
        Uses 'strict_eq' to enforce consensus on the result.
        Returns NONE to avoid simulator serialization crashes.
        """
        
        # Use Invidious (yewtu.be) for static HTML access to comments
        url = f"https://yewtu.be/watch?v={video_id}"

        def get_mood_vote() -> str:
            print(f"Fetching: {url}")
            try:
                # 'text' mode captures the comment section in the static HTML
                web_content = gl.nondet.web.render(url, mode="text")
            except Exception as e:
                print(f"Fetch failed: {e}")
                return "Neutral"

            # Task: Classify sentiment
            # We make the instructions very strict to ensure all validators 
            # produce the exact same string for 'strict_eq'.
            task = f"""
            You are a Sentiment Analyst. Analyze the comments in this text.
            
            Text Content:
            {web_content[:6000]} 

            Instructions:
            1. Find user comments.
            2. Determine the dominant mood.
            3. You must return EXACTLY one of these three strings: "Positive", "Negative", "Neutral".
            4. If the comments are mixed, choose the strongest signal.
            5. If no comments are found, return "Neutral".

            Respond using ONLY JSON:
            {{ "mood": "Positive" | "Negative" | "Neutral" }}
            """

            result_raw = gl.nondet.exec_prompt(task)
            
            try:
                cleaned = result_raw.replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)
                mood = parsed.get("mood", "Neutral")
                
                # Sanitize output to ensure strict equality holds
                if mood not in ["Positive", "Negative", "Neutral"]:
                    return "Neutral"
                    
                return mood
            except:
                return "Neutral"

        # Consensus: Strict Equality
        # Since 'majority_vote' is missing, we use 'strict_eq'.
        # This requires all validators to return the exact same string.
        final_mood = gl.eq_principle.strict_eq(get_mood_vote)

        # Update State
        self.video_sentiments[video_id] = final_mood
        
        return None

    @gl.public.view
    def get_video_mood(self, video_id: str) -> str:
        if video_id in self.video_sentiments:
            return self.video_sentiments[video_id]
        return "Unknown"
