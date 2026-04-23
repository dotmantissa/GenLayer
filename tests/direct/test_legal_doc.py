"""Direct-mode tests for legal_doc.py (LegalDocRegistry contract)."""

import hashlib
import json

import pytest

from conftest import _state

# ── Test fixtures ─────────────────────────────────────────────────────────────

BODY_V1   = b"AGREEMENT v1: Party A and Party B agree to the following terms..."
BODY_V2   = b"AGREEMENT v2: Party A and Party B agree to MODIFIED terms..."
HASH_V1   = hashlib.sha256(BODY_V1).hexdigest()
HASH_V2   = hashlib.sha256(BODY_V2).hexdigest()
DOC_URL   = "https://docs.example.com/contract-2024.pdf"
IPFS_URL  = "ipfs://bafybeiabc123def456"


def _deploy(direct_deploy):
    return direct_deploy("legal_doc.py")


def _register(contract, direct_vm, alice, bob, charlie=None, *, url=DOC_URL, doc_hash=HASH_V1):
    direct_vm.sender = alice
    sigs = [alice, bob] if charlie is None else [alice, bob, charlie]
    contract.register_document(url, doc_hash, json.dumps(sigs))
    return HASH_V1 if doc_hash == HASH_V1 else doc_hash


def _mock_doc(direct_vm, body=BODY_V1, status=200):
    direct_vm.mock_web(
        r"docs\.example\.com/contract-2024\.pdf",
        {"status": status, "body": body},
    )


# ── register_document ─────────────────────────────────────────────────────────

class TestRegisterDocument:
    def test_stores_hash_url_timestamp(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        doc_id = _register(contract, direct_vm, direct_alice, direct_bob)

        d = json.loads(contract.get_document(doc_id))
        assert d["hash"] == HASH_V1
        assert d["url"] == DOC_URL
        assert d["registered_at"] == _state["timestamp"]
        assert d["status"] == "PENDING"

    def test_fetched_size_stored(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        doc_id = _register(contract, direct_vm, direct_alice, direct_bob)

        d = json.loads(contract.get_document(doc_id))
        assert d["fetched_size"] == len(BODY_V1)

    def test_fetch_failure_stores_zero_size(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _deploy(direct_deploy)
        # URL returns 503 — fetch fails gracefully
        _mock_doc(direct_vm, status=503)
        doc_id = _register(contract, direct_vm, direct_alice, direct_bob)

        d = json.loads(contract.get_document(doc_id))
        assert d["fetched_size"] == 0

    def test_signatories_stored_lowercase(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        doc_id = _register(contract, direct_vm, direct_alice, direct_bob)

        d = json.loads(contract.get_document(doc_id))
        assert direct_alice.lower() in d["signatories"]
        assert direct_bob.lower() in d["signatories"]

    def test_duplicate_hash_raises(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        _register(contract, direct_vm, direct_alice, direct_bob)

        with direct_vm.expect_revert("already registered"):
            _register(contract, direct_vm, direct_alice, direct_bob)

    def test_empty_hash_raises(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("cannot be empty"):
            contract.register_document(DOC_URL, "", json.dumps([direct_alice]))

    def test_empty_url_raises(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("cannot be empty"):
            contract.register_document("", HASH_V1, json.dumps([direct_alice]))

    def test_empty_signatories_raises(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("non-empty"):
            contract.register_document(DOC_URL, HASH_V1, "[]")

    def test_appears_in_list(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        doc_id = _register(contract, direct_vm, direct_alice, direct_bob)

        docs = json.loads(contract.list_documents())
        assert any(d["id"] == doc_id for d in docs)


# ── sign ──────────────────────────────────────────────────────────────────────

class TestSign:
    def test_signatory_can_sign(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        doc_id = _register(contract, direct_vm, direct_alice, direct_bob)

        direct_vm.sender = direct_alice
        contract.sign(doc_id)

        d = json.loads(contract.get_document(doc_id))
        assert direct_alice.lower() in d["signed_by"]

    def test_non_signatory_cannot_sign(
        self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
    ):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        doc_id = _register(contract, direct_vm, direct_alice, direct_bob)

        direct_vm.sender = direct_charlie
        with direct_vm.expect_revert("not a signatory"):
            contract.sign(doc_id)

    def test_double_sign_raises(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        doc_id = _register(contract, direct_vm, direct_alice, direct_bob)

        direct_vm.sender = direct_alice
        contract.sign(doc_id)

        with direct_vm.expect_revert("already signed"):
            contract.sign(doc_id)

    def test_all_signatures_executes_document(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        doc_id = _register(contract, direct_vm, direct_alice, direct_bob)

        direct_vm.sender = direct_alice
        contract.sign(doc_id)
        assert json.loads(contract.get_document(doc_id))["status"] == "PENDING"

        direct_vm.sender = direct_bob
        contract.sign(doc_id)
        assert json.loads(contract.get_document(doc_id))["status"] == "EXECUTED"

    def test_signing_executed_document_raises(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        doc_id = _register(contract, direct_vm, direct_alice, direct_bob)

        direct_vm.sender = direct_alice
        contract.sign(doc_id)
        direct_vm.sender = direct_bob
        contract.sign(doc_id)  # now EXECUTED

        with direct_vm.expect_revert("not PENDING"):
            contract.sign(doc_id)

    def test_sign_unknown_document_raises(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("not found"):
            contract.sign("deadbeef")


# ── verify_document ───────────────────────────────────────────────────────────

class TestVerifyDocument:
    def test_unchanged_document_passes(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        doc_id = _register(contract, direct_vm, direct_alice, direct_bob)

        direct_vm.sender = direct_alice
        result = json.loads(contract.verify_document(doc_id))
        assert result["verified"] is True
        assert result["current_hash"] == HASH_V1
        assert result["analysis"] == ""

    def test_tampered_document_fails(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        doc_id = _register(contract, direct_vm, direct_alice, direct_bob)

        # Document body changed
        direct_vm.clear_mocks()
        _mock_doc(direct_vm, body=BODY_V2)
        direct_vm.mock_llm(
            r"integrity analyst",
            {"risk_level": "HIGH", "assessment": "Document content was materially altered."},
        )

        direct_vm.sender = direct_alice
        result = json.loads(contract.verify_document(doc_id))
        assert result["verified"] is False
        assert result["current_hash"] == HASH_V2
        assert "altered" in result["analysis"]

    def test_non_signatory_cannot_verify(
        self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
    ):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        doc_id = _register(contract, direct_vm, direct_alice, direct_bob)

        direct_vm.sender = direct_charlie
        with direct_vm.expect_revert("not a signatory"):
            contract.verify_document(doc_id)

    def test_verify_unknown_document_raises(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("not found"):
            contract.verify_document("deadbeef")

    def test_404_raises_external_error(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        doc_id = _register(contract, direct_vm, direct_alice, direct_bob)

        direct_vm.clear_mocks()
        _mock_doc(direct_vm, status=404)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert(r"\[EXTERNAL\]"):
            contract.verify_document(doc_id)

    def test_500_raises_transient_error(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        doc_id = _register(contract, direct_vm, direct_alice, direct_bob)

        direct_vm.clear_mocks()
        _mock_doc(direct_vm, status=503)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert(r"\[TRANSIENT\]"):
            contract.verify_document(doc_id)

    def test_verify_executed_document_still_works(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        doc_id = _register(contract, direct_vm, direct_alice, direct_bob)

        direct_vm.sender = direct_alice
        contract.sign(doc_id)
        direct_vm.sender = direct_bob
        contract.sign(doc_id)

        # Re-mock and verify after execution
        _mock_doc(direct_vm)
        direct_vm.sender = direct_alice
        result = json.loads(contract.verify_document(doc_id))
        assert result["verified"] is True


# ── propose_url_update / approve_url_update ───────────────────────────────────

class TestUrlUpdate:
    def _setup_with_doc(self, direct_vm, direct_deploy, direct_alice, direct_bob, charlie=None):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)
        doc_id = _register(
            contract, direct_vm, direct_alice, direct_bob,
            charlie=charlie,
        )
        return contract, doc_id

    def test_signatory_can_propose(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract, doc_id = self._setup_with_doc(direct_vm, direct_deploy, direct_alice, direct_bob)

        direct_vm.sender = direct_alice
        contract.propose_url_update(doc_id, IPFS_URL)

        p = json.loads(contract.get_url_proposal(doc_id))
        assert p["active"] is True
        assert p["new_url"] == IPFS_URL
        assert direct_alice.lower() in p["approvals"]

    def test_non_signatory_cannot_propose(
        self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
    ):
        contract, doc_id = self._setup_with_doc(direct_vm, direct_deploy, direct_alice, direct_bob)

        direct_vm.sender = direct_charlie
        with direct_vm.expect_revert("not a signatory"):
            contract.propose_url_update(doc_id, IPFS_URL)

    def test_empty_new_url_raises(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract, doc_id = self._setup_with_doc(direct_vm, direct_deploy, direct_alice, direct_bob)

        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("cannot be empty"):
            contract.propose_url_update(doc_id, "")

    def test_no_active_proposal_returns_inactive(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract, doc_id = self._setup_with_doc(direct_vm, direct_deploy, direct_alice, direct_bob)
        p = json.loads(contract.get_url_proposal(doc_id))
        assert p["active"] is False

    def test_approve_without_proposal_raises(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract, doc_id = self._setup_with_doc(direct_vm, direct_deploy, direct_alice, direct_bob)
        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("No active URL proposal"):
            contract.approve_url_update(doc_id)

    def test_double_approve_raises(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract, doc_id = self._setup_with_doc(direct_vm, direct_deploy, direct_alice, direct_bob)

        direct_vm.sender = direct_alice
        contract.propose_url_update(doc_id, IPFS_URL)

        # Alice already auto-approved by proposing
        with direct_vm.expect_revert("already approved"):
            contract.approve_url_update(doc_id)

    def test_majority_triggers_url_update_two_signatories(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        # 2 signatories: threshold = 2//2 + 1 = 2
        contract, doc_id = self._setup_with_doc(direct_vm, direct_deploy, direct_alice, direct_bob)

        direct_vm.sender = direct_alice
        contract.propose_url_update(doc_id, IPFS_URL)  # alice approves automatically

        direct_vm.sender = direct_bob
        contract.approve_url_update(doc_id)  # 2/2 — majority reached

        d = json.loads(contract.get_document(doc_id))
        assert d["url"] == IPFS_URL

        # Proposal cleared after approval
        p = json.loads(contract.get_url_proposal(doc_id))
        assert p["active"] is False

    def test_majority_with_three_signatories(
        self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
    ):
        # 3 signatories: threshold = 3//2 + 1 = 2
        contract, doc_id = self._setup_with_doc(
            direct_vm, direct_deploy, direct_alice, direct_bob, charlie=direct_charlie
        )

        direct_vm.sender = direct_alice
        contract.propose_url_update(doc_id, IPFS_URL)  # alice = 1 approval

        # After 1 approval URL should NOT be updated yet
        assert json.loads(contract.get_document(doc_id))["url"] == DOC_URL

        direct_vm.sender = direct_bob
        contract.approve_url_update(doc_id)  # 2/3 — majority reached

        assert json.loads(contract.get_document(doc_id))["url"] == IPFS_URL

    def test_url_update_proposal_replaced_by_new_proposal(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract, doc_id = self._setup_with_doc(direct_vm, direct_deploy, direct_alice, direct_bob)

        direct_vm.sender = direct_alice
        contract.propose_url_update(doc_id, IPFS_URL)

        # Replace with a different URL
        new_url2 = "https://backup.example.com/contract.pdf"
        contract.propose_url_update(doc_id, new_url2)

        p = json.loads(contract.get_url_proposal(doc_id))
        assert p["new_url"] == new_url2


# ── list_documents / get_document ─────────────────────────────────────────────

class TestViews:
    def test_list_documents_empty(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        assert json.loads(contract.list_documents()) == []

    def test_list_documents_multiple(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _deploy(direct_deploy)
        _mock_doc(direct_vm)

        hash2 = hashlib.sha256(b"second contract").hexdigest()
        _register(contract, direct_vm, direct_alice, direct_bob)
        _register(
            contract, direct_vm, direct_alice, direct_bob,
            url="https://docs.example.com/contract2.pdf", doc_hash=hash2,
        )

        docs = json.loads(contract.list_documents())
        assert len(docs) == 2

    def test_get_document_not_found(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        r = json.loads(contract.get_document("deadbeef"))
        assert r["error"] == "not found"
