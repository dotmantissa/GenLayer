# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json
import typing

class LegalReader(gl.Contract):
    """
    Extracts specific legal clauses from documents (PDF/HTML) based on keywords.
    Uses LLM-based Fuzzy Consensus to tolerate formatting differences.
    """
    
    # Storage: "URL + Keyword" -> Extracted Clause Text
    clauses: TreeMap[str, str]

    def __init__(self):
        pass

    @gl.public.write
    def extract_clause(self, doc_url: str, keyword: str) -> None:
        """
        Fetches the document and extracts the paragraph containing the keyword.
        Returns NONE to avoid simulator serialization crashes.
        """
        
        # Create a unique key for storage
        storage_key = f"{doc_url}::{keyword}"

        def extract_nondet() -> str:
            print(f"Fetching document: {doc_url}")
            try:
                # 'text' mode attempts to extract readable text from the URL
                # Note: This works best on HTML or text-based PDFs. 
                # Binary PDFs may require a gateway that converts PDF->Text first.
                doc_content = gl.nondet.web.render(doc_url, mode="text")
            except Exception as e:
                print(f"Fetch failed: {e}")
                return json.dumps({"clause": "Error: Fetch failed"})

            task = f"""
            Act as a Legal Assistant.
            
            1. Search this document text for the keyword: "{keyword}".
            2. Extract the FULL paragraph or section that contains this keyword.
            3. If the keyword appears multiple times, extract the most significant/definitional instance.
            4. If not found, return "Not Found".
            
            Document Text (snippet):
            {doc_content[:10000]} 

            Respond using ONLY JSON:
            {{ "clause": "extracted text..." }}
            """

            result_raw = gl.nondet.exec_prompt(task)
            
            try:
                cleaned = result_raw.replace("```json", "").replace("```", "").strip()
                json.loads(cleaned)
                return cleaned
            except:
                return json.dumps({"clause": "Error: Parsing failed"})

        # Consensus: Comparative (LLM-as-a-Judge)
        # We use the LLM to decide if two extracted strings are "effectively" the same.
        comparison_criteria = """
        Compare the two 'clause' strings.
        
        Logic:
        1. Read both text snippets.
        2. Ignore differences in whitespace, newlines, or casing.
        3. Ignore minor artifacts (e.g., page numbers, header artifacts).
        4. If the CORE LEGAL MEANING and wording is identical, they are EQUAL.
        5. If they refer to different sections or have different words, they are DIFFERENT.
        """

        consensus_json = gl.eq_principle.prompt_comparative(
            extract_nondet, 
            comparison_criteria
        )

        try:
            parsed = json.loads(consensus_json)
            clause_text = parsed.get("clause", "Error")
            self.clauses[storage_key] = clause_text
        except:
            self.clauses[storage_key] = "Error: Consensus failed"
        
        return None

    @gl.public.view
    def get_extracted_clause(self, doc_url: str, keyword: str) -> str:
        """
        Returns the extracted clause text.
        """
        storage_key = f"{doc_url}::{keyword}"
        if storage_key in self.clauses:
            return self.clauses[storage_key]
        return "Not found"
