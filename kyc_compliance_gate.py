# v0.1.0
# { "Depends": "py-genlayer:test" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"

BADGE_TTL_SECONDS = 365 * 24 * 60 * 60


@allow_storage
@dataclass
class KycBadge:
    compliant: bool
    issued_at: u256
    expires_at: u256
    risk_level: str
    country: str
    provider: str
    last_session_ref: str


class KycComplianceGate(gl.Contract):
    badges: TreeMap[str, KycBadge]  # wallet -> badge
    blocked_countries_json: str
    compliance_officer: Address
    kyc_api_key_encrypted: str
    sanctions_api_key_encrypted: str

    def __init__(
        self,
        compliance_officer: str,
        kyc_api_key_encrypted: str,
        sanctions_api_key_encrypted: str,
        blocked_countries_json: str = "[\"IR\", \"KP\", \"SY\", \"CU\", \"RU\"]",
    ):
        self.compliance_officer = Address(str(compliance_officer).strip())
        self.kyc_api_key_encrypted = str(kyc_api_key_encrypted)
        self.sanctions_api_key_encrypted = str(sanctions_api_key_encrypted)
        self.blocked_countries_json = str(blocked_countries_json)

    def _now(self) -> int:
        return int(gl.block.timestamp)

    def _wallet(self, addr: str) -> str:
        return str(addr).lower()

    def _blocked_countries(self) -> list:
        try:
            countries = json.loads(self.blocked_countries_json)
        except Exception:
            countries = []
        return [str(c).upper().strip() for c in countries]

    def _provider_from_session(self, verification_session_id: str) -> str:
        s = str(verification_session_id).strip().lower()
        if s.startswith("jumio_"):
            return "jumio"
        if s.startswith("onfido_"):
            return "onfido"
        if s.startswith("persona_"):
            return "persona"
        return "unknown"

    def _fetch_kyc_result(self, provider: str, verification_session_id: str) -> dict:
        if provider not in {"jumio", "onfido", "persona"}:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} unsupported kyc provider")

        session_id = str(verification_session_id).strip()
        if provider == "jumio":
            url = f"https://api.jumio.com/v1/sessions/{session_id}"
        elif provider == "onfido":
            url = f"https://api.onfido.com/v3.6/checks/{session_id}"
        else:
            url = f"https://withpersona.com/api/v1/inquiries/{session_id}"

        try:
            res = gl.nondet.web.get(url)
        except Exception as e:
            raise gl.vm.UserError(f"{ERROR_TRANSIENT} failed to fetch kyc session: {e}")

        if res.status >= 500:
            raise gl.vm.UserError(f"{ERROR_TRANSIENT} kyc provider unavailable ({res.status})")
        if res.status >= 400:
            raise gl.vm.UserError(f"{ERROR_EXTERNAL} kyc provider error ({res.status})")

        try:
            data = json.loads(res.body.decode("utf-8"))
        except Exception:
            raise gl.vm.UserError(f"{ERROR_EXTERNAL} invalid kyc provider response")

        # Normalize via AI into pass/fail, risk, country without storing PII.
        task = f"""Normalize this KYC session JSON to strict JSON object with keys:
- passed: boolean
- risk_level: one of LOW, MEDIUM, HIGH
- country: ISO2 country code
Do not include names, documents, or any PII.
Input:\n{json.dumps(data)[:12000]}
"""
        out = gl.nondet.exec_prompt(
            task,
            response_format={
                "type": "object",
                "properties": {
                    "passed": {"type": "boolean"},
                    "risk_level": {"type": "string"},
                    "country": {"type": "string"},
                },
            },
        )

        return {
            "passed": bool(out.get("passed", False)),
            "risk_level": str(out.get("risk_level", "HIGH")).upper().strip(),
            "country": str(out.get("country", "")).upper().strip(),
        }

    def _fetch_sanctions_result(self, wallet: str) -> dict:
        # Try Chainalysis and TRM style endpoints. Fail closed if neither returns usable data.
        urls = [
            f"https://api.chainalysis.com/v1/address/{wallet}/screening",
            f"https://api.trmlabs.com/public/v1/wallet/{wallet}/risk",
        ]
        results = []

        for url in urls:
            try:
                res = gl.nondet.web.get(url)
                if res.status >= 400:
                    continue
                data = json.loads(res.body.decode("utf-8"))
                results.append(data)
            except Exception:
                continue

        if not results:
            raise gl.vm.UserError(f"{ERROR_TRANSIENT} sanctions screening unavailable")

        # Normalize to boolean flag.
        task = f"""Given sanctions screening JSON results, return strict JSON:
- sanctioned: boolean
- source: short string
Input:\n{json.dumps(results)[:12000]}
"""
        out = gl.nondet.exec_prompt(
            task,
            response_format={
                "type": "object",
                "properties": {
                    "sanctioned": {"type": "boolean"},
                    "source": {"type": "string"},
                },
            },
        )
        return {"sanctioned": bool(out.get("sanctioned", False)), "source": str(out.get("source", ""))}

    @gl.public.write
    def initiate_kyc(self, verification_session_id: str) -> bool:
        session = str(verification_session_id).strip()
        if not session:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} verification_session_id is required")

        wallet = self._wallet(gl.message.sender_account)
        provider = self._provider_from_session(session)

        print(f"[KYCInitiated] wallet={wallet} provider={provider} session_ref={session[:16]}")

        kyc = self._fetch_kyc_result(provider, session)
        sanctions = self._fetch_sanctions_result(wallet)

        if not kyc["passed"]:
            return False
        if kyc["risk_level"] == "HIGH":
            return False
        if kyc["country"] in self._blocked_countries():
            return False
        if sanctions["sanctioned"]:
            return False

        now = self._now()
        self.badges[wallet] = KycBadge(
            compliant=True,
            issued_at=u256(now),
            expires_at=u256(now + BADGE_TTL_SECONDS),
            risk_level=kyc["risk_level"],
            country=kyc["country"],
            provider=provider,
            last_session_ref=session[:32],
        )

        print(
            f"[ComplianceBadgeIssued] wallet={wallet} expires_at={now + BADGE_TTL_SECONDS} "
            f"risk={kyc['risk_level']} country={kyc['country']}"
        )
        return True

    @gl.public.write
    def renew_kyc(self, verification_session_id: str) -> bool:
        # Renewal uses the same verification pipeline and simply updates expiry.
        return self.initiate_kyc(verification_session_id)

    @gl.public.write
    def revoke_badge(self, wallet: str, reason: str) -> None:
        if gl.message.sender_account != self.compliance_officer:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} only compliance officer can revoke")

        w = self._wallet(wallet)
        if w not in self.badges:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} badge not found")

        b = self.badges[w]
        b.compliant = False
        b.expires_at = u256(self._now())
        self.badges[w] = b

        print(f"[BadgeRevoked] wallet={w} reason={str(reason).strip()[:120]}")

    @gl.public.view
    def is_compliant(self, wallet: str) -> bool:
        w = self._wallet(wallet)
        if w not in self.badges:
            return False
        b = self.badges[w]
        if not bool(b.compliant):
            return False
        return self._now() < int(b.expires_at)

    @gl.public.view
    def get_badge_status(self, wallet: str) -> str:
        w = self._wallet(wallet)
        if w not in self.badges:
            return json.dumps({"exists": False})
        b = self.badges[w]
        return json.dumps(
            {
                "exists": True,
                "compliant": bool(b.compliant) and self._now() < int(b.expires_at),
                "issued_at": int(b.issued_at),
                "expires_at": int(b.expires_at),
                "risk_level": b.risk_level,
                "country": b.country,
                "provider": b.provider,
                "last_session_ref": b.last_session_ref,
            }
        )
