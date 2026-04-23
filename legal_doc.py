# v0.1.0
# { "Depends": "py-genlayer:test" }

from genlayer import *
import json
import hashlib

ERROR_EXPECTED  = "[EXPECTED]"
ERROR_EXTERNAL  = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM       = "[LLM_ERROR]"


@allow_storage
@dataclass
class Document:
    url: str
    document_hash: str   # SHA-256 hex, user-provided at registration
    fetched_size: u256   # body byte count at registration (0 if URL unreachable)
    registered_at: u256
    registrant: str      # lowercase address
    status: str          # PENDING | EXECUTED | DEPRECATED
    signatories: str     # JSON list[str] of lowercase addresses
    signed_by: str       # JSON list[str] of addresses that have signed


@allow_storage
@dataclass
class UrlProposal:
    new_url: str
    proposed_by: str
    proposed_at: u256
    approvals: str       # JSON list[str]


class LegalDocRegistry(gl.Contract):
    documents: TreeMap[str, Document]
    doc_order: DynArray[str]
    url_proposals: TreeMap[str, UrlProposal]  # one active proposal per doc_id

    def __init__(self):
        pass

    # ── Private helpers ───────────────────────────────────────────────────────

    def _require_signatory(self, doc: Document, addr: str) -> None:
        if addr not in json.loads(doc.signatories):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Caller is not a signatory of this document")

    def _fetch_body(self, url: str) -> bytes:
        try:
            res = gl.nondet.web.get(url)
            if res.status == 404:
                raise gl.vm.UserError(f"{ERROR_EXTERNAL} Document URL returned 404")
            if 400 <= res.status < 500:
                raise gl.vm.UserError(f"{ERROR_EXTERNAL} Document URL error ({res.status})")
            if res.status >= 500:
                raise gl.vm.UserError(f"{ERROR_TRANSIENT} Document URL unavailable ({res.status})")
            return res.body
        except gl.vm.UserError:
            raise
        except Exception as e:
            raise gl.vm.UserError(f"{ERROR_TRANSIENT} Network error fetching document: {e}")

    # ── Write methods ─────────────────────────────────────────────────────────

    @gl.public.write
    def register_document(
        self,
        document_url: str,
        document_hash: str,
        signatories: str,   # JSON-encoded list of wallet addresses
    ) -> str:
        """
        Register a legal document by its SHA-256 hash and the addresses of all
        parties. Fetches the URL once via nondet to anchor the file size as an
        additional integrity marker alongside the hash and timestamp.
        Returns the document_id (= normalised hash).
        Emits: [DocumentRegistered]
        """
        doc_id = str(document_hash).strip().lower()
        if not doc_id:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} document_hash cannot be empty")
        if doc_id in self.documents:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Document {doc_id[:12]}... already registered")

        url = str(document_url).strip()
        if not url:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} document_url cannot be empty")

        sigs_raw = json.loads(signatories) if isinstance(signatories, str) else list(signatories)
        if not sigs_raw:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} signatories must be a non-empty list")
        sigs   = [str(s).strip().lower() for s in sigs_raw]
        caller = str(gl.message.sender_account).lower()
        now    = int(gl.block.timestamp)

        # Nondet: fetch URL to anchor file size (graceful — 0 if unreachable)
        def fetch_meta() -> dict:
            try:
                res = gl.nondet.web.get(url)
                if res.status == 200:
                    return {"size": len(res.body), "ok": True}
                return {"size": 0, "ok": False}
            except Exception:
                return {"size": 0, "ok": False}

        def validate_meta(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                try:
                    fetch_meta()
                    return False
                except Exception:
                    return True
            try:
                val = fetch_meta()
            except Exception:
                return False
            ld = leaders_res.calldata
            if not ld.get("ok") and not val.get("ok"):
                return True  # both failed — agree on size=0
            return ld.get("size") == val.get("size")

        meta = gl.vm.run_nondet_unsafe(fetch_meta, validate_meta)

        self.documents[doc_id] = Document(
            url=url,
            document_hash=doc_id,
            fetched_size=u256(meta.get("size", 0)),
            registered_at=u256(now),
            registrant=caller,
            status="PENDING",
            signatories=json.dumps(sigs),
            signed_by="[]",
        )
        self.doc_order.append(doc_id)
        print(
            f"[DocumentRegistered] id={doc_id[:12]} url={url} "
            f"parties={len(sigs)} size={meta.get('size', 0)}"
        )
        return doc_id

    @gl.public.write
    def sign(self, document_id: str) -> None:
        """
        Signatory marks their cryptographic intent to be bound by the document.
        Once every party has signed, the document status advances to EXECUTED.
        Emits: [Signed], [DocumentExecuted]
        """
        doc_id = str(document_id).strip().lower()
        if doc_id not in self.documents:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Document {doc_id[:12]}... not found")
        doc = self.documents[doc_id]
        if doc.status != "PENDING":
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Document is not PENDING (status: {doc.status})"
            )

        caller = str(gl.message.sender_account).lower()
        self._require_signatory(doc, caller)

        signed = json.loads(doc.signed_by)
        if caller in signed:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Caller has already signed this document")

        signed.append(caller)
        doc.signed_by = json.dumps(signed)

        sigs = json.loads(doc.signatories)
        if set(signed) >= set(sigs):
            doc.status = "EXECUTED"
            print(f"[DocumentExecuted] id={doc_id[:12]}")

        self.documents[doc_id] = doc
        print(f"[Signed] id={doc_id[:12]} signer={caller}")

    @gl.public.write
    def verify_document(self, document_id: str) -> str:
        """
        Any signatory triggers a live re-fetch of the document. The contract
        re-hashes the body and compares against the stored value. If they differ,
        an AI analyst provides a risk assessment of the discrepancy.
        Emits: [VerificationPassed] or [TamperingDetected]
        Returns: JSON {"verified": bool, "current_hash": str, "analysis": str}
        """
        doc_id = str(document_id).strip().lower()
        if doc_id not in self.documents:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Document {doc_id[:12]}... not found")
        doc = self.documents[doc_id]

        caller = str(gl.message.sender_account).lower()
        self._require_signatory(doc, caller)

        stored_hash = doc.document_hash
        stored_size = int(doc.fetched_size)
        url         = doc.url

        def fetch_and_check() -> dict:
            body         = self._fetch_body(url)
            current_hash = hashlib.sha256(body).hexdigest()
            current_size = len(body)
            verified     = current_hash == stored_hash

            analysis = ""
            if not verified:
                prompt = f"""You are a legal document integrity analyst.

A legal document was registered on-chain with:
  SHA-256 hash : {stored_hash}
  File size    : {stored_size} bytes

When re-fetched it has:
  SHA-256 hash : {current_hash}
  File size    : {current_size} bytes

The hashes do NOT match — the document has changed. Provide a concise risk assessment.

Respond ONLY with valid JSON (no markdown):
{{"risk_level": "LOW|MEDIUM|HIGH", "assessment": "one-paragraph explanation"}}"""

                raw = gl.nondet.exec_prompt(prompt, response_format="json")
                if isinstance(raw, dict):
                    analysis = str(raw.get("assessment", ""))

            return {
                "verified":      verified,
                "current_hash":  current_hash,
                "current_size":  current_size,
                "analysis":      analysis,
            }

        def validator(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                leader_msg = getattr(leaders_res, "message", "")
                try:
                    fetch_and_check()
                    return False
                except gl.vm.UserError as e:
                    vmsg = getattr(e, "message", str(e))
                    if vmsg.startswith(ERROR_EXPECTED) or vmsg.startswith(ERROR_EXTERNAL):
                        return vmsg == leader_msg
                    if vmsg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
                        return True
                    return False
                except Exception:
                    return False
            try:
                val = fetch_and_check()
            except Exception:
                return False
            ld = leaders_res.calldata
            # Agree on the binary result and the hash; LLM analysis may vary
            return (
                ld.get("verified")      == val.get("verified")
                and ld.get("current_hash") == val.get("current_hash")
            )

        result = gl.vm.run_nondet_unsafe(fetch_and_check, validator)

        if result["verified"]:
            print(f"[VerificationPassed] id={doc_id[:12]} hash={result['current_hash'][:12]}")
        else:
            print(
                f"[TamperingDetected] id={doc_id[:12]} "
                f"stored={stored_hash[:12]} current={result['current_hash'][:12]}"
            )

        return json.dumps({
            "verified":     result["verified"],
            "current_hash": result["current_hash"],
            "analysis":     result["analysis"],
        })

    @gl.public.write
    def propose_url_update(self, document_id: str, new_url: str) -> None:
        """
        Signatory proposes migrating the document to a new URL (e.g., IPFS CID)
        after the original host goes offline. Replaces any existing proposal.
        Emits: [UrlUpdateProposed]
        """
        doc_id = str(document_id).strip().lower()
        if doc_id not in self.documents:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Document {doc_id[:12]}... not found")
        doc = self.documents[doc_id]

        caller = str(gl.message.sender_account).lower()
        self._require_signatory(doc, caller)

        new = str(new_url).strip()
        if not new:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} new_url cannot be empty")

        self.url_proposals[doc_id] = UrlProposal(
            new_url=new,
            proposed_by=caller,
            proposed_at=u256(int(gl.block.timestamp)),
            approvals=json.dumps([caller]),
        )
        print(f"[UrlUpdateProposed] id={doc_id[:12]} new_url={new} proposer={caller}")

    @gl.public.write
    def approve_url_update(self, document_id: str) -> None:
        """
        Signatory votes to approve the active URL migration proposal.
        When a simple majority approves, the document URL is updated on-chain.
        Emits: [UrlUpdateApproved], [UrlUpdated]
        """
        doc_id = str(document_id).strip().lower()
        if doc_id not in self.documents:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Document {doc_id[:12]}... not found")
        if doc_id not in self.url_proposals:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} No active URL proposal for {doc_id[:12]}..."
            )

        doc      = self.documents[doc_id]
        proposal = self.url_proposals[doc_id]
        caller   = str(gl.message.sender_account).lower()
        self._require_signatory(doc, caller)

        approvals = json.loads(proposal.approvals)
        if caller in approvals:
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Caller already approved this URL proposal"
            )

        approvals.append(caller)
        proposal.approvals = json.dumps(approvals)
        self.url_proposals[doc_id] = proposal
        print(f"[UrlUpdateApproved] id={doc_id[:12]} approver={caller}")

        sigs      = json.loads(doc.signatories)
        threshold = len(sigs) // 2 + 1  # simple majority
        if len(approvals) >= threshold:
            doc.url = proposal.new_url
            self.documents[doc_id] = doc
            del self.url_proposals[doc_id]
            print(f"[UrlUpdated] id={doc_id[:12]} new_url={proposal.new_url}")

    # ── View methods ──────────────────────────────────────────────────────────

    @gl.public.view
    def get_document(self, document_id: str) -> str:
        doc_id = str(document_id).strip().lower()
        if doc_id not in self.documents:
            return json.dumps({"error": "not found"})
        doc = self.documents[doc_id]
        return json.dumps({
            "id":            doc_id,
            "url":           doc.url,
            "hash":          doc.document_hash,
            "fetched_size":  int(doc.fetched_size),
            "registered_at": int(doc.registered_at),
            "registrant":    doc.registrant,
            "status":        doc.status,
            "signatories":   json.loads(doc.signatories),
            "signed_by":     json.loads(doc.signed_by),
        })

    @gl.public.view
    def get_url_proposal(self, document_id: str) -> str:
        doc_id = str(document_id).strip().lower()
        if doc_id not in self.url_proposals:
            return json.dumps({"active": False})
        p = self.url_proposals[doc_id]
        return json.dumps({
            "active":      True,
            "new_url":     p.new_url,
            "proposed_by": p.proposed_by,
            "approvals":   json.loads(p.approvals),
        })

    @gl.public.view
    def list_documents(self) -> str:
        result = []
        for i in range(len(self.doc_order)):
            doc_id = self.doc_order[i]
            if doc_id in self.documents:
                doc = self.documents[doc_id]
                result.append({
                    "id":            doc_id,
                    "status":        doc.status,
                    "url":           doc.url,
                    "registered_at": int(doc.registered_at),
                })
        return json.dumps(result)
