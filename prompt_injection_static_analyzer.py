# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"


def _raise_user_error(message: str) -> None:
    err_cls = getattr(gl, "UserError", Exception)
    raise err_cls(message)


def _sender() -> str:
    sender_address = getattr(gl.message, "sender_address", None)
    if sender_address is not None:
        return str(sender_address)
    sender_account = getattr(gl.message, "sender_account", None)
    if sender_account is not None:
        return str(sender_account)
    return ""


class PromptInjectionStaticAnalyzer(gl.Contract):
    """Scans contract web targets and prompts for prompt injection signatures."""

    owner: Address
    trusted_domains: DynArray[str]
    scans: str
    next_scan_id: u256
    severity_weights: str

    def __init__(self):
        """Initialize analyzer storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.owner = Address(_sender())
        self.scans = "{}"
        self.next_scan_id = 1
        self.severity_weights = json.dumps({"high": 10, "medium": 5, "low": 2})

    def _require_owner(self) -> None:
        if _sender().strip().lower() != str(self.owner).strip().lower():
            _raise_user_error(f"{ERROR_EXPECTED} only owner")

    def _validate_domain(self, domain: str) -> str:
        cleaned = str(domain).strip().lower()
        if len(cleaned) < 3 or "." not in cleaned:
            _raise_user_error(f"{ERROR_EXPECTED} invalid domain")
        if " " in cleaned or "/" in cleaned:
            _raise_user_error(f"{ERROR_EXPECTED} invalid domain")
        return cleaned

    def _find_prompt_signals(self, text: str) -> list:
        lower = str(text).lower()
        patterns = [
            ("ignore_previous_instructions", "ignore previous instructions", "high"),
            ("system_prompt_exfiltration", "reveal system prompt", "high"),
            ("developer_message_override", "developer message", "medium"),
            ("jailbreak_dan", "do anything now", "high"),
            ("data_exfiltration_request", "send me your secrets", "high"),
            ("policy_bypass", "bypass safety", "high"),
            ("tool_misuse", "call tool", "medium"),
            ("role_play_override", "you are now", "low"),
            ("prompt_leak", "show hidden instructions", "high"),
            ("html_comment_instruction", "<!--", "low"),
        ]

        findings = []
        for pid, token, severity in patterns:
            if token in lower:
                findings.append({"pattern": pid, "token": token, "severity": severity})
        return findings

    def _find_url_signals(self, url: str, trusted_set: set) -> list:
        raw = str(url).strip()
        lower = raw.lower()
        findings = []

        if not (lower.startswith("http://") or lower.startswith("https://")):
            findings.append({"pattern": "non_http_scheme", "token": lower[:20], "severity": "high"})
            return findings

        host = lower.split("://", 1)[1].split("/", 1)[0]
        if len(host) < 3 or "." not in host:
            findings.append({"pattern": "malformed_host", "token": host, "severity": "high"})
        elif host not in trusted_set:
            findings.append({"pattern": "untrusted_domain", "token": host, "severity": "medium"})

        risky_fragments = [
            ("raw_prompt_param", "prompt=", "medium"),
            ("instruction_param", "instruction=", "high"),
            ("query_override", "system=", "high"),
            ("html_payload", "%3cscript", "high"),
            ("remote_markdown", ".md", "low"),
        ]
        for pid, token, severity in risky_fragments:
            if token in lower:
                findings.append({"pattern": pid, "token": token, "severity": severity})

        return findings

    @gl.public.write
    def set_severity_weights(self, high: int, medium: int, low: int) -> None:
        """Update severity score multipliers.

        Parameters:
            high: Score weight for high severity findings.
            medium: Score weight for medium severity findings.
            low: Score weight for low severity findings.

        Returns:
            None.
        """
        self._require_owner()
        for weight in [high, medium, low]:
            if weight < 1 or weight > 100:
                _raise_user_error(f"{ERROR_EXPECTED} invalid severity weight")
        self.severity_weights = json.dumps(
            {"high": int(high), "medium": int(medium), "low": int(low)}
        )

    @gl.public.write
    def add_trusted_domain(self, domain: str) -> None:
        """Add a trusted web domain for fetch target checks.

        Parameters:
            domain: Domain name without scheme or path.

        Returns:
            None.
        """
        self._require_owner()
        cleaned = self._validate_domain(domain)
        existing = set()
        for item in self.trusted_domains:
            existing.add(str(item).strip().lower())
        if cleaned in existing:
            _raise_user_error(f"{ERROR_EXPECTED} domain already trusted")
        self.trusted_domains.append(cleaned)

    @gl.public.write
    def scan_contract_spec(
        self,
        contract_name: str,
        web_targets_json: str,
        prompts_json: str,
    ) -> str:
        """Scan web targets and prompt templates for injection risk patterns.

        Parameters:
            contract_name: Human readable contract name.
            web_targets_json: JSON array of fetch URLs.
            prompts_json: JSON array of prompt strings.

        Returns:
            Scan id string.
        """
        cname = str(contract_name).strip()
        if len(cname) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid contract_name")

        try:
            web_targets = json.loads(web_targets_json)
        except Exception:
            _raise_user_error(f"{ERROR_EXPECTED} invalid web_targets_json")
        try:
            prompts = json.loads(prompts_json)
        except Exception:
            _raise_user_error(f"{ERROR_EXPECTED} invalid prompts_json")

        if not isinstance(web_targets, list):
            _raise_user_error(f"{ERROR_EXPECTED} web_targets must be list")
        if not isinstance(prompts, list) or len(prompts) == 0:
            _raise_user_error(f"{ERROR_EXPECTED} prompts must be non empty list")

        trusted_set = set()
        for d in self.trusted_domains:
            trusted_set.add(str(d).strip().lower())

        findings = []
        for i in range(len(web_targets)):
            url = str(web_targets[i]).strip()
            if len(url) < 3:
                _raise_user_error(f"{ERROR_EXPECTED} invalid web target")
            if len(url) > 4096:
                _raise_user_error(f"{ERROR_EXPECTED} web target too long")
            signals = self._find_url_signals(url, trusted_set)
            for sig in signals:
                findings.append(
                    {
                        "source_type": "web_target",
                        "source_index": i,
                        "source": url,
                        "pattern": sig["pattern"],
                        "token": sig["token"],
                        "severity": sig["severity"],
                    }
                )

        for i in range(len(prompts)):
            prompt = str(prompts[i])
            if len(prompt.strip()) < 3:
                _raise_user_error(f"{ERROR_EXPECTED} invalid prompt")
            if len(prompt) > 12000:
                _raise_user_error(f"{ERROR_EXPECTED} prompt too long")
            signals = self._find_prompt_signals(prompt)
            for sig in signals:
                findings.append(
                    {
                        "source_type": "prompt",
                        "source_index": i,
                        "source": prompt[:240],
                        "pattern": sig["pattern"],
                        "token": sig["token"],
                        "severity": sig["severity"],
                    }
                )

        weights = json.loads(self.severity_weights)
        total_score = 0
        severity_counts = {"high": 0, "medium": 0, "low": 0}
        for f in findings:
            sev = str(f.get("severity", "low"))
            if sev not in severity_counts:
                sev = "low"
            severity_counts[sev] += 1
            total_score += int(weights.get(sev, 0))

        risk_level = "low"
        if total_score >= 25:
            risk_level = "high"
        elif total_score >= 10:
            risk_level = "medium"

        scan_id = str(self.next_scan_id)
        self.next_scan_id += 1

        scan = {
            "scan_id": scan_id,
            "contract_name": cname,
            "requested_by": _sender(),
            "web_target_count": len(web_targets),
            "prompt_count": len(prompts),
            "finding_count": len(findings),
            "severity_counts": severity_counts,
            "risk_score": int(total_score),
            "risk_level": risk_level,
            "findings": findings,
            "created_at": str(gl.block.timestamp),
        }

        scans = json.loads(self.scans)
        scans[scan_id] = scan
        self.scans = json.dumps(scans)
        return scan_id

    @gl.public.view
    def get_scan(self, scan_id: str) -> str:
        """Read one saved scan report.

        Parameters:
            scan_id: Scan identifier.

        Returns:
            JSON scan report string.
        """
        key = str(scan_id).strip()
        scans = json.loads(self.scans)
        if key not in scans:
            _raise_user_error(f"{ERROR_EXPECTED} scan not found")
        return json.dumps(scans[key])

    @gl.public.view
    def get_all_scans(self) -> str:
        """Read all scan reports.

        Parameters:
            None.

        Returns:
            JSON object string mapping scan ids to reports.
        """
        return self.scans

    @gl.public.view
    def get_trusted_domains(self) -> str:
        """Read trusted domain allowlist.

        Parameters:
            None.

        Returns:
            JSON array string of domains.
        """
        return json.dumps(list(self.trusted_domains))
