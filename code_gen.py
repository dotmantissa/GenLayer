# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class CodeGen(gl.Contract):
    """
    Converts natural language intents into Python code snippets.
    Uses Functional Consensus to accept code that works the same way
    even if variable names or formatting differ.
    """

    # Stores: Intent -> Python Code
    snippets: TreeMap[str, str]

    def __init__(self):
        pass

    @gl.public.write
    def generate_python(self, intent: str) -> None:
        """
        Generates Python code based on the user's intent.
        Returns NONE to avoid simulator serialization crashes.
        """
        
        def generate_nondet() -> str:
            task = f"""
            Act as a Python Developer.
            
            Task: Write a simple Python code block for this intent:
            "{intent}"
            
            Instructions:
            1. Use standard, readable variable names.
            2. Keep logic simple and direct.
            3. Do NOT include comments or explanations.
            4. Return ONLY the code.
            """

            result_raw = gl.nondet.exec_prompt(task)
            
            # Clean up Markdown
            cleaned = result_raw.replace("```python", "").replace("```", "").strip()
            return cleaned

        # Consensus: Functional Equivalence
        # We ask the LLM to judge if two code snippets do the same thing.
        comparison_criteria = """
        Compare the two Python code snippets.
        
        Logic:
        1. Ignore variable naming differences (e.g., 'x' vs 'val').
        2. Ignore whitespace, indentation style, or comments.
        3. Check if the LOGIC and FLOW are identical.
        4. If both snippets implement the exact same intent, they are EQUAL.
        """

        consensus_code = gl.eq_principle.prompt_comparative(
            generate_nondet, 
            comparison_criteria
        )

        # Store the result
        self.snippets[intent] = consensus_code
        
        return None

    @gl.public.view
    def get_code(self, intent: str) -> str:
        """
        Returns the generated Python code.
        """
        if intent in self.snippets:
            return self.snippets[intent]
        return "# No code generated"
