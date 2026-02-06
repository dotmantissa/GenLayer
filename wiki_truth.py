# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *

class WikiTruth(gl.Contract):
    """
    Deterministic Wikipedia fact checker.
    Only checks whether a literal phrase appears in a Wikipedia article.
    """

    verified_facts: TreeMap[str, bool] = TreeMap()

    def __init__(self):
        pass

    @gl.public.write
    def verify_fact(self, page_title: str, expected_phrase: str) -> None:
        """
        page_title: Wikipedia page title, e.g. "Albert_Einstein"
        expected_phrase: Phrase you expect to find on the page
        """

        page_title = page_title.replace(" ", "_")
        expected_phrase = expected_phrase.strip().lower()

        url = f"https://en.wikipedia.org/wiki/{page_title}"

        def check_wiki_nondet() -> bool:
            try:
                web_content = gl.nondet.web.render(url, mode="text")
            except Exception as e:
                print(f"Fetch failed: {e}")
                return False

            text = web_content.lower()

            found = expected_phrase in text
            print(f"Checking page {page_title}, found={found}")
            return found

        result = gl.eq_principle.strict_eq(check_wiki_nondet)
        self.verified_facts[f"{page_title}:{expected_phrase}"] = result

        print(f"Stored result: {result}")
        return None

    @gl.public.view
    def is_fact_true(self, page_title: str, expected_phrase: str) -> bool:
        key = f"{page_title.replace(' ', '_')}:{expected_phrase.strip().lower()}"
        return self.verified_facts.get(key, False)
