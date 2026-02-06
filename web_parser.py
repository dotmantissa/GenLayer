# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class WebParser(gl.Contract):
    """
    Extracts structured data from websites based on a user-provided JSON schema.
    """
    
    # Stores: URL -> Extracted JSON String
    parsed_data: TreeMap[str, str]

    def __init__(self):
        pass

    @gl.public.write
    def extract_schema(self, url: str, schema_definition: str) -> None:
        """
        Fetches the URL and extracts data matching the schema_definition.
        Returns NONE to avoid simulator serialization crashes.
        """
        
        # Use a generic web render. 
        # Note: If the target site has heavy JS, this might need a specific 'mode' or proxy,
        # but 'text' mode is the most robust default for the simulator.
        def extract_nondet() -> str:
            print(f"Fetching: {url}")
            try:
                # Grab the text content of the page
                web_content = gl.nondet.web.render(url, mode="text")
            except Exception as e:
                print(f"Fetch failed: {e}")
                return "{}"

            # Task: Extract data matching the schema
            task = f"""
            You are a Data Scraper.
            
            1. Analyze this website content:
            {web_content[:6000]}
            
            2. Extract data to EXACTLY match this JSON Schema:
            {schema_definition}
            
            3. If a field cannot be found, use null or an empty string/list as appropriate.
            4. Do NOT add extra keys.
            5. Respond using ONLY valid JSON.
            """

            result_raw = gl.nondet.exec_prompt(task)
            
            # Clean up potential Markdown formatting
            try:
                cleaned = result_raw.replace("```json", "").replace("```", "").strip()
                # Validation check
                json.loads(cleaned)
                return cleaned
            except:
                return "{}"

        # Consensus: Comparative (JSON Structural Match)
        # We ask the validator to ignore whitespace/sorting and check if the DATA is the same.
        comparison_criteria = """
        Compare the two JSON inputs.
        1. Parse both JSON strings.
        2. Check if they have the same structure (keys, nested objects).
        3. Check if the values are equivalent.
        4. Ignore whitespace, indentation, or key ordering.
        5. They are 'Equal' if the resulting objects are identical.
        """

        consensus_json = gl.eq_principle.prompt_comparative(
            extract_nondet, 
            comparison_criteria
        )

        # Store the result
        self.parsed_data[url] = consensus_json
        
        return None

    @gl.public.view
    def get_parsed_result(self, url: str) -> str:
        """
        Returns the stored JSON string for a URL.
        """
        if url in self.parsed_data:
            return self.parsed_data[url]
        return "{}"
