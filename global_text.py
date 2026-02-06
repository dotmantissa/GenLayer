# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class GlobalText(gl.Contract):
    """
    Translates arbitrary text to English.
    Uses Semantic Consensus to allow for valid variations in translation phrasing.
    """
    
    # Stores: Original Text -> English Translation
    translations: TreeMap[str, str]

    def __init__(self):
        pass

    @gl.public.write
    def translate_to_english(self, text: str) -> None:
        """
        Translates text to English using LLM.
        Returns NONE to avoid simulator serialization crashes.
        """
        
        def translate_nondet() -> str:
            # Task: Translate and output JSON
            task = f"""
            Act as a Professional Translator.
            Translate the following text into clear, standard English:
            "{text}"
            
            Instructions:
            1. Maintain the original tone and meaning.
            2. If the text is already English, correct any grammar/spelling.
            3. Return ONLY valid JSON.

            Respond using ONLY JSON:
            {{ "translation": "string" }}
            """

            result_raw = gl.nondet.exec_prompt(task)
            
            try:
                cleaned = result_raw.replace("```json", "").replace("```", "").strip()
                # Fast validation
                json.loads(cleaned)
                return cleaned
            except:
                return json.dumps({"translation": "Error: Translation failed"})

        # Consensus: Semantic Similarity
        # We instruct validators to ignore minor phrasing differences.
        comparison_criteria = """
        Compare the 'translation' strings in the two JSON inputs.
        
        Logic:
        1. Read both English translations.
        2. Are they semantically equivalent? (Do they mean the same thing?)
        3. Ignore minor differences in punctuation or synonym choice (e.g., "fast" vs "quick").
        4. If the meaning is preserved, treat them as EQUAL.
        """

        # Returns the JSON from the leader node
        consensus_json = gl.eq_principle.prompt_comparative(
            translate_nondet, 
            comparison_criteria
        )

        # Parse and Store
        try:
            parsed = json.loads(consensus_json)
            english_text = parsed.get("translation", "Error")
            self.translations[text] = english_text
        except:
            self.translations[text] = "Error: Storage failed"
        
        return None

    @gl.public.view
    def get_translation(self, original_text: str) -> str:
        if original_text in self.translations:
            return self.translations[original_text]
        return "Not found"
