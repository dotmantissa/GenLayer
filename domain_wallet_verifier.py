# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"


def _raise_user_error(message: str) -> None:
    err_cls = getattr(gl, "UserError", Exception)
    raise err_cls(message)


def _run_prompt_consensus(fn, principle: str) -> str:
    eq = getattr(gl, "eq_principle", None)
    if eq is not None and hasattr(eq, "prompt_comparative"):
        return eq.prompt_comparative(fn, principle)
    return fn()


class DomainWalletVerifier(gl.Contract):
    """Verifies domain ownership by matching DNS TXT wallet record and WHOIS context."""

    claims: str
    next_claim_id: u256

    def __init__(self):
        """Initialize contract state.

        Parameters:
            None.

        Returns:
            None.
        """
        self.claims = "{}"
        self.next_claim_id = 1

    @gl.public.write
    def create_claim(self, domain: str, claimed_wallet: str) -> str:
        """Create a domain ownership claim.

        Parameters:
            domain: Domain name to verify.
            claimed_wallet: Claimed on chain wallet address.

        Returns:
            Claim id string.
        """
        normalized_domain = str(domain).strip().lower()
        wallet = str(claimed_wallet).strip()

        if len(normalized_domain) < 4 or "." not in normalized_domain:
            _raise_user_error(f"{ERROR_EXPECTED} invalid domain")
        if " " in normalized_domain:
            _raise_user_error(f"{ERROR_EXPECTED} invalid domain")
        if not wallet.startswith("0x") or len(wallet) < 10:
            _raise_user_error(f"{ERROR_EXPECTED} invalid wallet")

        claim_id = str(self.next_claim_id)
        self.next_claim_id += 1

        claims = json.loads(self.claims)
        claims[claim_id] = {
            "claim_id": claim_id,
            "requester": str(gl.message.sender_account),
            "domain": normalized_domain,
            "claimed_wallet": wallet,
            "status": "PENDING",
            "verified": False,
            "txt_wallet": "",
            "whois_registrant": "",
            "reason": "",
            "resolved_at": "",
        }
        self.claims = json.dumps(claims)
        return claim_id

    @gl.public.write
    def verify_claim(self, claim_id: str) -> bool:
        """Verify claim by cross validating WHOIS and DNS TXT records.

        Parameters:
            claim_id: Claim id string.

        Returns:
            Boolean verification result.
        """
        claims = json.loads(self.claims)
        key = str(claim_id)
        if key not in claims:
            _raise_user_error(f"{ERROR_EXPECTED} claim not found")

        c = claims[key]
        if c["status"] != "PENDING":
            _raise_user_error(f"{ERROR_EXPECTED} claim already resolved")

        def fetch_and_extract() -> str:
            dns_url = f"https://dns.google/resolve?name={c['domain']}&type=TXT"
            whois_url = f"https://rdap.org/domain/{c['domain']}"

            dns = gl.nondet.web.get(dns_url)
            whois = gl.nondet.web.get(whois_url)

            for name, res in [("dns", dns), ("whois", whois)]:
                status = int(res.status)
                if status >= 400 and status < 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} client error: {status}")
                if status >= 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} server error: {status}")

            dns_body = dns.body.decode("utf-8") if dns.body is not None else ""
            whois_body = whois.body.decode("utf-8") if whois.body is not None else ""
            if len((dns_body + whois_body).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty identity payload")

            prompt = f"""
You are an identity verification analyst.
Extract a wallet address from DNS TXT records and determine if WHOIS data supports ownership continuity.
Return JSON only.

Target domain: {c['domain']}
Claimed wallet: {c['claimed_wallet']}

Rules:
1) Parse DNS TXT entries and find wallet declaration patterns like owner_wallet=0x... or wallet:0x...
2) Parse WHOIS or RDAP registrant fields and summarize registrant identity string.
3) verified is true only if the extracted txt_wallet exactly equals claimed wallet and whois data is present.
4) If no reliable wallet in TXT then verified must be false.

Return exactly:
{{
  "verified": true_or_false,
  "txt_wallet": "string",
  "whois_registrant": "string",
  "reason": "string"
}}

Inputs:
{json.dumps({"dns": dns_body[:5000], "whois": whois_body[:5000]})}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            txt_wallet = str(parsed.get("txt_wallet", "")).strip()
            whois_registrant = str(parsed.get("whois_registrant", "")).strip()
            verified = bool(parsed.get("verified", False))
            reason = str(parsed.get("reason", ""))[:500]

            normalized_txt = txt_wallet.lower()
            normalized_claim = str(c["claimed_wallet"]).lower()
            if normalized_txt != normalized_claim:
                verified = False
            if len(whois_registrant) == 0:
                verified = False

            return json.dumps(
                {
                    "verified": bool(verified),
                    "txt_wallet": txt_wallet,
                    "whois_registrant": whois_registrant,
                    "reason": reason,
                }
            )

        principle = "Equivalent when verified matches and extracted wallet strings are the same after lowercase normalization."
        verdict_json = _run_prompt_consensus(fetch_and_extract, principle)
        verdict = json.loads(verdict_json)

        c["verified"] = bool(verdict.get("verified", False))
        c["txt_wallet"] = str(verdict.get("txt_wallet", ""))
        c["whois_registrant"] = str(verdict.get("whois_registrant", ""))
        c["reason"] = str(verdict.get("reason", ""))
        c["status"] = "VERIFIED" if c["verified"] else "REJECTED"
        c["resolved_at"] = str(gl.block.timestamp)

        claims[key] = c
        self.claims = json.dumps(claims)
        return bool(c["verified"])

    @gl.public.view
    def get_claim(self, claim_id: str) -> str:
        """Read one claim record.

        Parameters:
            claim_id: Claim id string.

        Returns:
            Claim JSON string.
        """
        claims = json.loads(self.claims)
        key = str(claim_id)
        if key not in claims:
            _raise_user_error(f"{ERROR_EXPECTED} claim not found")
        return json.dumps(claims[key])

    @gl.public.view
    def get_all_claims(self) -> str:
        """Read all claim records.

        Parameters:
            None.

        Returns:
            Claims map JSON string.
        """
        return self.claims
