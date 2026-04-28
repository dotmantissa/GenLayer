# v0.1.0
# { "Depends": "py-genlayer:test" }

from genlayer import *
import hashlib
import json

ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"

CACHE_TTL_SECONDS = 86_400
MIN_API_SOURCES = 5
DEFAULT_REQUEST_FEE_WEI = 100


@allow_storage
@dataclass
class Verdict:
    claim_text: str
    claim_hash: str
    is_true: bool
    confidence_bps: u256
    sources_json: str
    summary: str
    verified_at: u256
    requester: Address
    requester_fee_wei: u256


@allow_storage
@dataclass
class Dispute:
    dispute_id: str
    claim_hash: str
    challenger: Address
    stake_wei: u256
    created_at: u256
    status: str  # OPEN | UPHELD | REJECTED
    resolution_note: str


class NewsFactOracle(gl.Contract):
    cache_by_hash: TreeMap[str, Verdict]
    disputes: TreeMap[str, Dispute]
    balances: TreeMap[str, u256]
    dispute_order: DynArray[str]

    def __init__(self):
        pass

    def _norm(self, text: str) -> str:
        return " ".join(str(text).strip().lower().split())

    def _claim_hash(self, claim_text: str) -> str:
        return hashlib.sha256(self._norm(claim_text).encode("utf-8")).hexdigest()

    def _now(self) -> int:
        return int(gl.block.timestamp)

    def _balance_of(self, wallet: str) -> int:
        key = str(wallet).lower()
        return int(self.balances[key]) if key in self.balances else 0

    def _set_balance(self, wallet: str, amount: int) -> None:
        self.balances[str(wallet).lower()] = u256(amount)

    def _parse_json_or_empty(self, body: bytes):
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:
            return {}

    def _fetch_news_candidates(self, claim_text: str) -> list:
        claim_q = claim_text.replace(" ", "%20")
        endpoints = [
            ("NewsAPI", f"https://newsapi.org/v2/everything?q={claim_q}"),
            ("GDELT", f"https://api.gdeltproject.org/api/v2/doc/doc?query={claim_q}&mode=ArtList&format=json"),
            ("Reuters", f"https://www.reuters.com/site-search/?query={claim_q}"),
            ("AP", f"https://apnews.com/search?q={claim_q}"),
            ("Guardian", f"https://content.guardianapis.com/search?q={claim_q}"),
            ("NYTimes", f"https://api.nytimes.com/svc/search/v2/articlesearch.json?q={claim_q}"),
        ]

        collected = []
        for source_name, url in endpoints:
            try:
                res = gl.nondet.web.get(url)
                if res.status >= 500:
                    continue
                if res.status >= 400:
                    continue
                payload = self._parse_json_or_empty(res.body)
                collected.append(
                    {
                        "source": source_name,
                        "url": url,
                        "status": int(res.status),
                        "payload": payload,
                    }
                )
            except Exception:
                continue

        if len(collected) < MIN_API_SOURCES:
            raise gl.vm.UserError(
                f"{ERROR_TRANSIENT} could not fetch enough sources (need {MIN_API_SOURCES})"
            )
        return collected

    def _filter_authoritative(self, candidates: list) -> list:
        authoritative_domains = {
            "reuters.com",
            "apnews.com",
            "bbc.com",
            "nytimes.com",
            "theguardian.com",
            "wsj.com",
            "washingtonpost.com",
            "newsapi.org",
            "gdeltproject.org",
        }

        filtered = []
        for c in candidates:
            url = str(c.get("url", "")).lower()
            if any(d in url for d in authoritative_domains):
                filtered.append(c)

        if len(filtered) < MIN_API_SOURCES:
            raise gl.vm.UserError(
                f"{ERROR_TRANSIENT} not enough authoritative sources after filtering"
            )
        return filtered

    def _ai_verdict(self, claim_text: str, authoritative_sources: list) -> dict:
        task = f"""You are a strict fact checking judge.
Return JSON with fields:
- label: one of CONFIRMS, DENIES, SILENT
- confidence: float from 0.0 to 1.0
- reasoning: short string
- sources: list of source names used

Claim:
{claim_text}

Authoritative source payload summary:
{json.dumps(authoritative_sources)[:14000]}
"""
        out = gl.nondet.exec_prompt(
            task,
            response_format={
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reasoning": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "string"}},
                },
            },
        )

        label = str(out.get("label", "SILENT")).upper().strip()
        conf = float(out.get("confidence", 0.0))
        conf = max(0.0, min(1.0, conf))
        sources = out.get("sources", [])
        if not isinstance(sources, list):
            sources = []
        reasoning = str(out.get("reasoning", ""))

        return {
            "label": label,
            "confidence": conf,
            "reasoning": reasoning,
            "sources": [str(s) for s in sources],
        }

    @gl.public.write
    def top_up_balance(self, amount: int) -> None:
        if amount <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} amount must be positive")
        sender = str(gl.message.sender_account).lower()
        self._set_balance(sender, self._balance_of(sender) + int(amount))
        print(f"[BalanceToppedUp] wallet={sender} amount_wei={amount}")

    @gl.public.write
    def verify_claim(self, claim_text: str, request_fee_wei: int = DEFAULT_REQUEST_FEE_WEI) -> str:
        claim = str(claim_text).strip()
        if not claim:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} claim_text is required")
        if request_fee_wei <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} request_fee_wei must be positive")

        sender = str(gl.message.sender_account).lower()
        bal = self._balance_of(sender)
        if bal < int(request_fee_wei):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} insufficient requester balance")

        claim_hash = self._claim_hash(claim)
        now = self._now()

        if claim_hash in self.cache_by_hash:
            cached = self.cache_by_hash[claim_hash]
            age = now - int(cached.verified_at)
            if age <= CACHE_TTL_SECONDS:
                print(
                    f"[ClaimVerified] claim_hash={claim_hash} is_true={cached.is_true} "
                    f"confidence={int(cached.confidence_bps) / 10000.0} cached=true"
                )
                return json.dumps(
                    {
                        "is_true": bool(cached.is_true),
                        "confidence": int(cached.confidence_bps) / 10000.0,
                        "sources": json.loads(cached.sources_json),
                        "claim_hash": claim_hash,
                        "cached": True,
                    }
                )

        self._set_balance(sender, bal - int(request_fee_wei))

        def leader():
            candidates = self._fetch_news_candidates(claim)
            filtered = self._filter_authoritative(candidates)
            ai = self._ai_verdict(claim, filtered)

            label = ai["label"]
            is_true = label == "CONFIRMS"
            confidence = float(ai["confidence"])
            sources = ai["sources"]

            if label not in {"CONFIRMS", "DENIES", "SILENT"}:
                raise gl.vm.UserError(f"{ERROR_EXTERNAL} invalid AI label")

            return gl.vm.Return(
                {
                    "is_true": is_true,
                    "confidence_bps": int(round(confidence * 10000)),
                    "sources": sources,
                    "summary": ai["reasoning"],
                }
            )

        def validator(result: gl.vm.Result):
            return result

        outcome = gl.vm.run_nondet_unsafe(leader, validator).calldata

        verdict = Verdict(
            claim_text=claim,
            claim_hash=claim_hash,
            is_true=bool(outcome["is_true"]),
            confidence_bps=u256(int(outcome["confidence_bps"])),
            sources_json=json.dumps(outcome["sources"]),
            summary=str(outcome["summary"]),
            verified_at=u256(now),
            requester=gl.message.sender_account,
            requester_fee_wei=u256(int(request_fee_wei)),
        )
        self.cache_by_hash[claim_hash] = verdict

        print(
            f"[ClaimVerified] claim_hash={claim_hash} is_true={verdict.is_true} "
            f"confidence={int(verdict.confidence_bps) / 10000.0} cached=false"
        )

        return json.dumps(
            {
                "is_true": bool(verdict.is_true),
                "confidence": int(verdict.confidence_bps) / 10000.0,
                "sources": json.loads(verdict.sources_json),
                "claim_hash": claim_hash,
                "cached": False,
            }
        )

    @gl.public.write
    def raise_dispute(self, claim_hash: str, stake_wei: int) -> str:
        h = str(claim_hash).strip().lower()
        if h not in self.cache_by_hash:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} unknown claim_hash")
        if stake_wei <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} stake_wei must be positive")

        challenger = str(gl.message.sender_account).lower()
        bal = self._balance_of(challenger)
        if bal < int(stake_wei):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} insufficient challenger balance")

        self._set_balance(challenger, bal - int(stake_wei))

        dispute_id = f"{h}:{len(self.dispute_order)}"
        d = Dispute(
            dispute_id=dispute_id,
            claim_hash=h,
            challenger=gl.message.sender_account,
            stake_wei=u256(int(stake_wei)),
            created_at=u256(self._now()),
            status="OPEN",
            resolution_note="",
        )
        self.disputes[dispute_id] = d
        self.dispute_order.append(dispute_id)

        print(
            f"[DisputeRaised] dispute_id={dispute_id} claim_hash={h} challenger={challenger} stake_wei={stake_wei}"
        )
        return dispute_id

    @gl.public.write
    def resolve_dispute(self, dispute_id: str) -> str:
        did = str(dispute_id).strip()
        if did not in self.disputes:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} dispute not found")
        d = self.disputes[did]
        if d.status != "OPEN":
            raise gl.vm.UserError(f"{ERROR_EXPECTED} dispute already resolved")

        v = self.cache_by_hash[d.claim_hash]
        challenger = str(d.challenger).lower()
        requester = str(v.requester).lower()

        # Re-check claim. If verdict flips, the dispute is upheld.
        # The fresh check charges no additional fee.
        candidates = self._fetch_news_candidates(v.claim_text)
        filtered = self._filter_authoritative(candidates)
        ai = self._ai_verdict(v.claim_text, filtered)
        new_is_true = str(ai["label"]).upper() == "CONFIRMS"

        if new_is_true != bool(v.is_true):
            d.status = "UPHELD"
            d.resolution_note = "fresh verification changed verdict"

            reward = int(v.requester_fee_wei)
            self._set_balance(challenger, self._balance_of(challenger) + reward + int(d.stake_wei))
            self._set_balance(requester, self._balance_of(requester))

            # Update cache with new verdict from the dispute resolution pass.
            v.is_true = bool(new_is_true)
            v.confidence_bps = u256(int(round(float(ai["confidence"]) * 10000)))
            v.sources_json = json.dumps(ai["sources"])
            v.summary = str(ai["reasoning"])
            v.verified_at = u256(self._now())
            self.cache_by_hash[d.claim_hash] = v
        else:
            d.status = "REJECTED"
            d.resolution_note = "fresh verification matched original verdict"
            self._set_balance(requester, self._balance_of(requester) + int(d.stake_wei))

        self.disputes[did] = d

        print(
            f"[DisputeResolved] dispute_id={did} claim_hash={d.claim_hash} status={d.status} "
            f"challenger={str(d.challenger).lower()}"
        )
        return d.status

    @gl.public.view
    def get_claim(self, claim_hash: str) -> str:
        h = str(claim_hash).strip().lower()
        if h not in self.cache_by_hash:
            return json.dumps({"error": "not found"})
        v = self.cache_by_hash[h]
        return json.dumps(
            {
                "claim_hash": v.claim_hash,
                "claim_text": v.claim_text,
                "is_true": bool(v.is_true),
                "confidence": int(v.confidence_bps) / 10000.0,
                "sources": json.loads(v.sources_json),
                "summary": v.summary,
                "verified_at": int(v.verified_at),
                "requester": str(v.requester),
                "requester_fee_wei": int(v.requester_fee_wei),
            }
        )

    @gl.public.view
    def get_dispute(self, dispute_id: str) -> str:
        did = str(dispute_id).strip()
        if did not in self.disputes:
            return json.dumps({"error": "not found"})
        d = self.disputes[did]
        return json.dumps(
            {
                "dispute_id": d.dispute_id,
                "claim_hash": d.claim_hash,
                "challenger": str(d.challenger),
                "stake_wei": int(d.stake_wei),
                "created_at": int(d.created_at),
                "status": d.status,
                "resolution_note": d.resolution_note,
            }
        )

    @gl.public.view
    def get_balance(self, wallet: str) -> int:
        return self._balance_of(wallet)
