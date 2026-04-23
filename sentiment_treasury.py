# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

CHALLENGE_WINDOW = 172800  # 48 hours in seconds

class SentimentTreasury(gl.Contract):
    """
    Community treasury whose releases are gated by AI-scored social media sentiment.
    request_release() fetches recent posts, scores sentiment 0-100, and — when the
    score meets the threshold — opens a 48-hour challenge window before funds are
    credited to the beneficiary.

    Events emitted via print():
      EVENT:SentimentMeasured  — every request_release() call, score logged on-chain
      EVENT:ReleaseApproved    — sentiment passed, challenge window opened
      EVENT:ReleaseBlocked     — sentiment failed or challenges upheld
      EVENT:ChallengeSubmitted — a token holder filed a dispute
      EVENT:ReleaseExecuted    — funds credited after clean challenge window
      EVENT:Withdrawn          — beneficiary claimed credits
    """

    # All complex state stored as JSON strings to avoid simulator serialization errors
    config: str      # project_name, keywords[], threshold(0-100), release_amount_wei,
                     # beneficiary, window_days, cooldown_seconds, owner
    treasury: str    # balance_wei, withdrawals{addr:str}, last_release_ts, release_count
    pending: str     # status, score, request_ts, challenge_deadline, requester, post_count, source
    challenges: str  # {addr: {reason, ts}}

    def __init__(
        self,
        project_name: str,
        search_keywords_json: str,   # JSON array e.g. '["GenLayer","GL token","testnet"]'
        sentiment_threshold: int,    # integer 0-100  (75 → require ≥75/100 sentiment)
        release_amount_wei: int,
        beneficiary_address: str,
        measurement_window_days: int,
        cooldown_period_seconds: int,
    ) -> None:
        owner = str(gl.message.sender_account)
        try:
            kws = json.loads(search_keywords_json)
            if not isinstance(kws, list):
                kws = [str(search_keywords_json)]
        except Exception:
            kws = [str(search_keywords_json)]

        self.config = json.dumps({
            "project_name": project_name,
            "keywords": kws,
            "threshold": max(0, min(100, int(sentiment_threshold))),
            "release_amount_wei": str(release_amount_wei),
            "beneficiary": beneficiary_address,
            "window_days": int(measurement_window_days),
            "cooldown_seconds": int(cooldown_period_seconds),
            "owner": owner,
        })
        self.treasury = json.dumps({
            "balance_wei": "0",
            "withdrawals": {},
            "last_release_ts": "0",
            "release_count": 0,
        })
        self.pending = json.dumps({"status": "NONE"})
        self.challenges = "{}"

    # ── Fund management ───────────────────────────────────────────────────────

    @gl.public.write
    def deposit(self, amount_wei: int) -> None:
        """Credit amount_wei to the treasury's internal balance."""
        if amount_wei <= 0:
            print("ERROR: amount_wei must be positive")
            return None
        trs = json.loads(self.treasury)
        trs["balance_wei"] = str(int(trs["balance_wei"]) + amount_wei)
        self.treasury = json.dumps(trs)
        print(f"EVENT:Deposited sender={gl.message.sender_account} amount_wei={amount_wei} new_balance={trs['balance_wei']}")
        return None

    # ── Core: sentiment-gated release ─────────────────────────────────────────

    @gl.public.write
    def request_release(self) -> None:
        """
        Fetches recent social media posts mentioning the project, uses AI to score
        overall sentiment 0-100, and — if score >= threshold — opens a 48-hour
        challenge window.  If the window closes with no challenges, finalize_release()
        credits the release amount to the beneficiary.

        Consensus uses prompt_comparative: validators must agree on the approve/block
        outcome; ±10-point score disagreements on the borderline are tolerated.
        """
        cfg = json.loads(self.config)
        trs = json.loads(self.treasury)
        pnd = json.loads(self.pending)
        now = int(gl.block.timestamp)

        # Only start a new request from a clean NONE state
        if pnd.get("status") != "NONE":
            print(f"ERROR: Cannot request release — current status is {pnd.get('status')}")
            return None

        # Cooldown guard
        last_ts = int(trs["last_release_ts"])
        cooldown = int(cfg["cooldown_seconds"])
        if last_ts > 0 and (now - last_ts) < cooldown:
            remaining = cooldown - (now - last_ts)
            print(f"ERROR: Cooldown active — {remaining}s remaining")
            return None

        # Balance guard
        balance = int(trs["balance_wei"])
        rel_amount = int(cfg["release_amount_wei"])
        if balance < rel_amount:
            print(f"ERROR: Insufficient treasury balance — have {balance} wei, need {rel_amount} wei")
            return None

        # Snapshot config values for the closure
        project_name = cfg["project_name"]
        keywords     = cfg["keywords"]
        threshold    = int(cfg["threshold"])
        window_days  = int(cfg["window_days"])

        def fetch_and_score() -> str:
            # Build search query from project name + up to 2 keywords
            terms     = [project_name] + keywords[:2]
            query_str = "+OR+".join(term.replace(" ", "+") for term in terms)

            posts_text = ""
            source     = "none"

            # Primary: Nitter (public Twitter/X mirror, no API key required)
            nitter_url = f"https://nitter.privacydev.net/search?q={query_str}&f=tweets"
            try:
                raw = gl.nondet.web.render(nitter_url, mode="text")
                if raw and len(raw.strip()) > 300:
                    posts_text = raw[:8000]
                    source     = "Nitter"
            except Exception as e:
                print(f"Nitter fetch failed: {e}")

            # Fallback: DuckDuckGo social search
            if not posts_text:
                ddg_url = (
                    f"https://html.duckduckgo.com/html/?q="
                    f"{query_str}+site:twitter.com+OR+site:x.com+OR+site:reddit.com"
                )
                try:
                    raw = gl.nondet.web.render(ddg_url, mode="text")
                    if raw and len(raw.strip()) > 300:
                        posts_text = raw[:8000]
                        source     = "DuckDuckGo"
                except Exception as e:
                    print(f"DuckDuckGo fetch failed: {e}")
                    return json.dumps({
                        "valid": False, "score": 0, "approved": False,
                        "source": "none", "post_count": 0,
                        "positive_count": 0, "negative_count": 0, "neutral_count": 0,
                    })

            task = f"""
You are a Community Sentiment Analyst.

Project: {project_name}
Search terms: {", ".join(terms)}
Measurement window: last {window_days} days
Data source: {source}

Social media data:
---
{posts_text}
---

Instructions:
1. Find posts, tweets, comments, and discussions about the project from the data above.
2. Analyze the most recent up to 50 relevant posts.
3. Classify each post: Positive, Negative, or Neutral.
4. Compute an overall sentiment score on an integer 0-100 scale:
   0 = fully negative, 50 = perfectly neutral, 100 = fully positive.
5. A release is approved when score >= {threshold} AND post_count >= 5.
6. Set valid=false if fewer than 5 relevant posts are found.

Respond ONLY with valid JSON, no markdown:
{{
    "score": <integer 0-100>,
    "post_count": <integer>,
    "positive_count": <integer>,
    "negative_count": <integer>,
    "neutral_count": <integer>,
    "approved": <true if score >= {threshold} AND post_count >= 5, else false>,
    "valid": <true if post_count >= 5, else false>,
    "source": "{source}"
}}
"""
            result_raw = gl.nondet.exec_prompt(task)
            try:
                cleaned   = result_raw.replace("```json", "").replace("```", "").strip()
                parsed    = json.loads(cleaned)
                score     = max(0, min(100, int(parsed.get("score", 50))))
                post_count = int(parsed.get("post_count", 0))
                valid     = bool(parsed.get("valid", False) and post_count >= 5)
                approved  = bool(valid and score >= threshold)
                return json.dumps({
                    "score": score,
                    "post_count": post_count,
                    "positive_count": int(parsed.get("positive_count", 0)),
                    "negative_count": int(parsed.get("negative_count", 0)),
                    "neutral_count": int(parsed.get("neutral_count", 0)),
                    "approved": approved,
                    "valid": valid,
                    "source": str(parsed.get("source", source)),
                })
            except Exception:
                return json.dumps({
                    "valid": False, "score": 0, "approved": False,
                    "source": source, "post_count": 0,
                    "positive_count": 0, "negative_count": 0, "neutral_count": 0,
                })

        # Tolerant consensus: outcome (approve/block) must match; ±10-point score
        # spread on the borderline is absorbed rather than treated as disagreement.
        criteria = """
Compare two validator sentiment results.
1. Both valid=false                                          → EQUAL (use val_a).
2. Both approved=true                                        → EQUAL (use val_a).
3. Both approved=false                                       → EQUAL (use val_a).
4. approved differs AND |score_a - score_b| <= 10            → EQUAL (use val_a, borderline).
5. approved differs AND |score_a - score_b| > 10             → DIFFERENT (genuine disagreement).
"""
        consensus_json = gl.eq_principle.prompt_comparative(fetch_and_score, criteria)

        try:
            result = json.loads(consensus_json)
        except Exception:
            print("ERROR: Failed to parse consensus result")
            return None

        score      = int(result.get("score", 0))
        approved   = bool(result.get("approved", False))
        valid      = bool(result.get("valid", False))
        post_count = int(result.get("post_count", 0))

        # SentimentMeasured is always emitted — score is logged on-chain regardless of outcome
        print(
            f"EVENT:SentimentMeasured project={project_name} score={score} "
            f"threshold={threshold} post_count={post_count} window_days={window_days} "
            f"valid={valid} approved={approved} "
            f"positive={result.get('positive_count',0)} "
            f"negative={result.get('negative_count',0)} "
            f"neutral={result.get('neutral_count',0)} "
            f"source={result.get('source','unknown')}"
        )

        if not valid:
            print(f"EVENT:ReleaseBlocked project={project_name} score={score} reason=insufficient_data post_count={post_count}")
            return None

        if not approved:
            print(f"EVENT:ReleaseBlocked project={project_name} score={score} threshold={threshold} reason=below_threshold")
            return None

        # Score qualifies — open the 48-hour challenge window
        challenge_deadline = now + CHALLENGE_WINDOW
        requester          = str(gl.message.sender_account)

        self.pending = json.dumps({
            "status":             "PENDING_CHALLENGE",
            "score":              score,
            "request_ts":         str(now),
            "challenge_deadline": str(challenge_deadline),
            "requester":          requester,
            "post_count":         post_count,
            "positive_count":     int(result.get("positive_count", 0)),
            "negative_count":     int(result.get("negative_count", 0)),
            "source":             str(result.get("source", "unknown")),
        })
        self.challenges = "{}"  # fresh slate for this round's challenges

        print(
            f"EVENT:ReleaseApproved project={project_name} score={score} "
            f"threshold={threshold} beneficiary={cfg['beneficiary']} "
            f"amount_wei={cfg['release_amount_wei']} "
            f"challenge_deadline={challenge_deadline} requester={requester}"
        )
        return None

    # ── Challenge mechanism ───────────────────────────────────────────────────

    @gl.public.write
    def submit_challenge(self, reason: str) -> None:
        """
        Register a dispute against the pending release.  Any address may challenge
        within the 48-hour window; in production this should be restricted to verified
        token holders.  A single successful challenge blocks the release until the
        owner resolves it via owner_resolve().
        """
        pnd = json.loads(self.pending)
        now = int(gl.block.timestamp)

        if pnd.get("status") != "PENDING_CHALLENGE":
            print("ERROR: No release is currently pending challenge")
            return None

        deadline = int(pnd["challenge_deadline"])
        if now > deadline:
            print("ERROR: Challenge window has closed")
            return None

        challenger = str(gl.message.sender_account)
        chalgs     = json.loads(self.challenges)

        if challenger in chalgs:
            print(f"ERROR: {challenger} has already submitted a challenge for this release")
            return None

        chalgs[challenger] = {
            "reason": str(reason)[:500],
            "ts":     str(now),
        }
        self.challenges = json.dumps(chalgs)

        print(
            f"EVENT:ChallengeSubmitted challenger={challenger} "
            f"ts={now} pending_score={pnd['score']} reason={str(reason)[:100]}"
        )
        return None

    # ── Finalization ──────────────────────────────────────────────────────────

    @gl.public.write
    def finalize_release(self) -> None:
        """
        Callable by anyone after the 48-hour challenge window expires.
        • No challenges filed  → credits release_amount_wei to beneficiary.
        • Challenges filed     → transitions to CHALLENGED; owner must call owner_resolve().
        """
        pnd = json.loads(self.pending)
        now = int(gl.block.timestamp)

        if pnd.get("status") != "PENDING_CHALLENGE":
            print(f"ERROR: Nothing to finalize — status is {pnd.get('status', 'NONE')}")
            return None

        deadline = int(pnd["challenge_deadline"])
        if now <= deadline:
            print(f"ERROR: Challenge window still active — {deadline - now}s remaining")
            return None

        cfg    = json.loads(self.config)
        trs    = json.loads(self.treasury)
        chalgs = json.loads(self.challenges)
        score  = int(pnd["score"])

        if chalgs:
            # Challenges present — freeze pending state for owner review
            self.pending = json.dumps({
                "status":          "CHALLENGED",
                "score":           score,
                "challenge_count": len(chalgs),
                "request_ts":      pnd.get("request_ts"),
                "requester":       pnd.get("requester"),
            })
            print(
                f"EVENT:ReleaseBlocked project={cfg['project_name']} score={score} "
                f"reason=challenged challenge_count={len(chalgs)} "
                f"beneficiary={cfg['beneficiary']}"
            )
            return None

        # No challenges — execute the release
        balance    = int(trs["balance_wei"])
        rel_amount = int(cfg["release_amount_wei"])

        if balance < rel_amount:
            print(f"ERROR: Treasury underfunded — balance={balance} needed={rel_amount}")
            self.pending = json.dumps({"status": "NONE"})
            return None

        beneficiary                     = cfg["beneficiary"]
        withdrawals                     = trs["withdrawals"]
        withdrawals[beneficiary]        = str(int(withdrawals.get(beneficiary, "0")) + rel_amount)
        trs["balance_wei"]              = str(balance - rel_amount)
        trs["withdrawals"]              = withdrawals
        trs["last_release_ts"]          = str(now)
        trs["release_count"]            = int(trs["release_count"]) + 1
        self.treasury                   = json.dumps(trs)

        self.pending    = json.dumps({"status": "NONE"})
        self.challenges = "{}"

        print(
            f"EVENT:ReleaseExecuted beneficiary={beneficiary} "
            f"amount_wei={rel_amount} score={score} "
            f"release_count={trs['release_count']} ts={now}"
        )
        return None

    # ── Withdraw ──────────────────────────────────────────────────────────────

    @gl.public.write
    def withdraw(self) -> None:
        """Claim all credited funds for the caller (typically the beneficiary)."""
        caller = str(gl.message.sender_account)
        trs    = json.loads(self.treasury)
        wds    = trs["withdrawals"]

        amount = int(wds.get(caller, "0"))
        if amount <= 0:
            print(f"ERROR: No withdrawable balance for {caller}")
            return None

        wds[caller]        = "0"
        trs["withdrawals"] = wds
        self.treasury      = json.dumps(trs)

        print(f"EVENT:Withdrawn recipient={caller} amount_wei={amount}")
        return None

    # ── Owner controls ────────────────────────────────────────────────────────

    @gl.public.write
    def owner_resolve(self, execute: bool) -> None:
        """
        Resolve a CHALLENGED release.
        execute=True  → force the release (override challenges).
        execute=False → permanently block it and reset to NONE.
        """
        cfg = json.loads(self.config)
        if str(gl.message.sender_account) != cfg["owner"]:
            print("ERROR: Not authorized")
            return None

        pnd = json.loads(self.pending)
        if pnd.get("status") != "CHALLENGED":
            print(f"ERROR: No challenged release to resolve — status is {pnd.get('status', 'NONE')}")
            return None

        score = int(pnd.get("score", 0))

        if not execute:
            self.pending    = json.dumps({"status": "NONE"})
            self.challenges = "{}"
            print(f"EVENT:ReleaseBlocked project={cfg['project_name']} score={score} reason=owner_blocked")
            return None

        trs        = json.loads(self.treasury)
        balance    = int(trs["balance_wei"])
        rel_amount = int(cfg["release_amount_wei"])

        if balance < rel_amount:
            print(f"ERROR: Insufficient balance for owner override — {balance} < {rel_amount}")
            return None

        beneficiary              = cfg["beneficiary"]
        wds                      = trs["withdrawals"]
        wds[beneficiary]         = str(int(wds.get(beneficiary, "0")) + rel_amount)
        trs["balance_wei"]       = str(balance - rel_amount)
        trs["withdrawals"]       = wds
        now                      = int(gl.block.timestamp)
        trs["last_release_ts"]   = str(now)
        trs["release_count"]     = int(trs["release_count"]) + 1
        self.treasury            = json.dumps(trs)

        self.pending    = json.dumps({"status": "NONE"})
        self.challenges = "{}"

        print(
            f"EVENT:ReleaseExecuted beneficiary={beneficiary} "
            f"amount_wei={rel_amount} score={score} "
            f"release_count={trs['release_count']} method=owner_override ts={now}"
        )
        return None

    @gl.public.write
    def update_config(self, field: str, value: str) -> None:
        """
        Owner-only config update.
        Updatable fields: threshold, release_amount_wei, beneficiary,
                          cooldown_seconds, window_days, keywords.
        """
        cfg = json.loads(self.config)
        if str(gl.message.sender_account) != cfg["owner"]:
            print("ERROR: Not authorized")
            return None

        allowed = {"threshold", "release_amount_wei", "beneficiary",
                   "cooldown_seconds", "window_days", "keywords"}
        if field not in allowed:
            print(f"ERROR: Field '{field}' is not updatable")
            return None

        # keywords field expects a JSON array string
        if field == "keywords":
            try:
                parsed = json.loads(value)
                if not isinstance(parsed, list):
                    print("ERROR: keywords must be a JSON array")
                    return None
                cfg[field] = parsed
            except Exception:
                print("ERROR: keywords must be valid JSON")
                return None
        elif field == "threshold":
            cfg[field] = max(0, min(100, int(value)))
        else:
            cfg[field] = value

        self.config = json.dumps(cfg)
        print(f"EVENT:ConfigUpdated field={field} value={value}")
        return None

    # ── Views ─────────────────────────────────────────────────────────────────

    @gl.public.view
    def get_config(self) -> str:
        return self.config

    @gl.public.view
    def get_treasury(self) -> str:
        return self.treasury

    @gl.public.view
    def get_pending_release(self) -> str:
        return self.pending

    @gl.public.view
    def get_challenges(self) -> str:
        return self.challenges

    @gl.public.view
    def get_status_summary(self) -> str:
        """Human-readable snapshot of treasury health and pending state."""
        cfg    = json.loads(self.config)
        trs    = json.loads(self.treasury)
        pnd    = json.loads(self.pending)
        chalgs = json.loads(self.challenges)
        now    = int(gl.block.timestamp)

        summary = {
            "project":             cfg["project_name"],
            "threshold":           cfg["threshold"],
            "treasury_balance_wei": trs["balance_wei"],
            "release_amount_wei":  cfg["release_amount_wei"],
            "release_count":       trs["release_count"],
            "pending_status":      pnd.get("status", "NONE"),
            "challenge_count":     len(chalgs),
            "beneficiary":         cfg["beneficiary"],
        }

        # Cooldown info
        last_ts  = int(trs["last_release_ts"])
        cooldown = int(cfg["cooldown_seconds"])
        if last_ts > 0:
            elapsed = now - last_ts
            summary["cooldown_remaining_seconds"] = str(max(0, cooldown - elapsed))
        else:
            summary["cooldown_remaining_seconds"] = "0"

        # Challenge window countdown
        if pnd.get("status") == "PENDING_CHALLENGE":
            deadline = int(pnd["challenge_deadline"])
            summary["challenge_window_remaining_seconds"] = str(max(0, deadline - now))
            summary["latest_score"] = pnd.get("score", 0)

        return json.dumps(summary)
