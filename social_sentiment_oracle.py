# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"


class SocialSentimentOracle(gl.Contract):
    """Classifies token or project community sentiment from public social posts."""

    queries: str
    next_query_id: u256
    last_sentiment_by_topic: str

    def __init__(self):
        """Initialize storage maps and query counter.

        Parameters:
            None.

        Returns:
            None.
        """
        self.queries = "{}"
        self.next_query_id = 1
        self.last_sentiment_by_topic = "{}"

    @gl.public.write
    def create_query(self, topic: str, source: str, max_posts: int, min_confidence: int) -> str:
        """Create a new sentiment query.

        Parameters:
            topic: Token or project keyword to search.
            source: Data source, nitter or x_public.
            max_posts: Maximum posts to include from source text.
            min_confidence: Minimum accepted confidence from 0 to 100.

        Returns:
            Query id string.
        """
        topic_clean = str(topic).strip()
        source_clean = str(source).strip().lower()

        if len(topic_clean) < 2:
            raise gl.UserError(f"{ERROR_EXPECTED} invalid topic")
        if source_clean not in ["nitter", "x_public"]:
            raise gl.UserError(f"{ERROR_EXPECTED} unsupported source")
        if max_posts < 5 or max_posts > 200:
            raise gl.UserError(f"{ERROR_EXPECTED} max_posts out of range")
        if min_confidence < 0 or min_confidence > 100:
            raise gl.UserError(f"{ERROR_EXPECTED} min_confidence out of range")

        query_id = str(self.next_query_id)
        self.next_query_id += 1

        queries = json.loads(self.queries)
        queries[query_id] = {
            "query_id": query_id,
            "creator": str(gl.message.sender_account),
            "topic": topic_clean,
            "source": source_clean,
            "max_posts": int(max_posts),
            "min_confidence": int(min_confidence),
            "status": "PENDING",
            "sentiment": "",
            "confidence": 0,
            "sample_size": 0,
            "summary": "",
            "resolved_at": "",
        }
        self.queries = json.dumps(queries)
        return query_id

    @gl.public.write
    def resolve_query(self, query_id: str) -> str:
        """Resolve a sentiment query by fetching social data and applying LLM consensus.

        Parameters:
            query_id: Query id string.

        Returns:
            Final sentiment classification string.
        """
        queries = json.loads(self.queries)
        key = str(query_id)
        if key not in queries:
            raise gl.UserError(f"{ERROR_EXPECTED} query not found")

        q = queries[key]
        if q["status"] != "PENDING":
            raise gl.UserError(f"{ERROR_EXPECTED} query already resolved")

        topic = q["topic"]
        if q["source"] == "nitter":
            url = f"https://nitter.net/search?f=tweets&q={topic}"
        else:
            url = f"https://twitter.com/search?q={topic}&src=typed_query"

        def fetch_and_classify() -> str:
            response = gl.nondet.web.get(url)
            status = int(response.status)
            body = ""
            if response.body is not None:
                body = response.body.decode("utf-8")

            if status >= 400 and status < 500:
                raise gl.UserError(f"{ERROR_EXTERNAL} source client error: {status}")
            if status >= 500:
                raise gl.UserError(f"{ERROR_EXTERNAL} source server error: {status}")
            if len(body.strip()) == 0:
                raise gl.UserError(f"{ERROR_EXTERNAL} source response empty")

            prompt = f"""
You are a market sentiment classifier.
Given recent public posts for a token or project, classify aggregate community sentiment.
Return JSON only.

Topic: {q['topic']}
Maximum post sample: {q['max_posts']}

Rules:
1) Label sentiment as exactly one of Bullish Bearish Neutral.
2) Provide confidence from 0 to 100.
3) Provide short neutral summary.
4) Estimate sample_size from visible posts considered.

Return exactly:
{{
  "sentiment": "Bullish_or_Bearish_or_Neutral",
  "confidence": int,
  "sample_size": int,
  "summary": "string"
}}

Source payload:
{body[:14000]}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            sentiment = str(parsed.get("sentiment", "")).strip().title()
            if sentiment not in ["Bullish", "Bearish", "Neutral"]:
                raise gl.UserError(f"{ERROR_EXPECTED} invalid sentiment label")

            confidence = int(parsed.get("confidence", 0))
            if confidence < 0:
                confidence = 0
            if confidence > 100:
                confidence = 100

            sample_size = int(parsed.get("sample_size", 0))
            if sample_size < 0:
                sample_size = 0

            return json.dumps(
                {
                    "sentiment": sentiment,
                    "confidence": confidence,
                    "sample_size": sample_size,
                    "summary": str(parsed.get("summary", ""))[:500],
                }
            )

        principle = "Equivalent when sentiment matches and confidence differs by at most 20."
        result_json = gl.eq_principle.prompt_comparative(fetch_and_classify, principle)
        result = json.loads(result_json)

        sentiment = str(result.get("sentiment", "")).strip().title()
        confidence = int(result.get("confidence", 0))

        q["sentiment"] = sentiment
        q["confidence"] = confidence
        q["sample_size"] = int(result.get("sample_size", 0))
        q["summary"] = str(result.get("summary", ""))
        q["resolved_at"] = str(gl.block.timestamp)

        if confidence >= int(q["min_confidence"]):
            q["status"] = "RESOLVED"
        else:
            q["status"] = "LOW_CONFIDENCE"

        queries[key] = q
        self.queries = json.dumps(queries)

        latest = json.loads(self.last_sentiment_by_topic)
        latest[str(topic).lower()] = sentiment
        self.last_sentiment_by_topic = json.dumps(latest)

        return sentiment

    @gl.public.view
    def get_query(self, query_id: str) -> str:
        """Get one stored query.

        Parameters:
            query_id: Query id string.

        Returns:
            Query JSON string.
        """
        queries = json.loads(self.queries)
        key = str(query_id)
        if key not in queries:
            raise gl.UserError(f"{ERROR_EXPECTED} query not found")
        return json.dumps(queries[key])

    @gl.public.view
    def get_all_queries(self) -> str:
        """Get all query records.

        Parameters:
            None.

        Returns:
            JSON map with all queries.
        """
        return self.queries

    @gl.public.view
    def get_latest_sentiment(self, topic: str) -> str:
        """Get latest resolved sentiment for topic.

        Parameters:
            topic: Topic key.

        Returns:
            Bullish Bearish Neutral or UNKNOWN.
        """
        latest = json.loads(self.last_sentiment_by_topic)
        return str(latest.get(str(topic).strip().lower(), "UNKNOWN"))
