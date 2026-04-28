"""Direct-mode tests for news_fact_oracle.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
CLAIM = "City council approved a new climate budget today"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("news_fact_oracle.py")


def _mock_all_sources(direct_vm):
    direct_vm.mock_web(r"newsapi\.org", {"status": 200, "body": {"articles": [{"title": "a"}]}})
    direct_vm.mock_web(r"gdeltproject\.org", {"status": 200, "body": {"articles": [{"title": "b"}]}})
    direct_vm.mock_web(r"reuters\.com", {"status": 200, "body": {"results": [{"title": "c"}]}})
    direct_vm.mock_web(r"apnews\.com", {"status": 200, "body": {"results": [{"title": "d"}]}})
    direct_vm.mock_web(r"guardianapis\.com", {"status": 200, "body": {"response": {"results": [{"title": "e"}]}}})
    direct_vm.mock_web(r"nytimes\.com", {"status": 200, "body": {"response": {"docs": [{"headline": "f"}]}}})


def _mock_ai_confirm(direct_vm, confidence=0.82):
    direct_vm.mock_llm(
        r"strict fact checking judge",
        {
            "label": "CONFIRMS",
            "confidence": confidence,
            "reasoning": "Most sources confirm",
            "sources": ["Reuters", "AP", "GDELT", "NewsAPI", "Guardian"],
        },
    )


def _mock_ai_deny(direct_vm, confidence=0.77):
    direct_vm.mock_llm(
        r"strict fact checking judge",
        {
            "label": "DENIES",
            "confidence": confidence,
            "reasoning": "Most sources deny",
            "sources": ["Reuters", "AP", "GDELT", "NewsAPI", "Guardian"],
        },
    )


def test_verify_claim_returns_structured_verdict(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.top_up_balance(1000)
    _mock_all_sources(direct_vm)
    _mock_ai_confirm(direct_vm)

    out = json.loads(contract.verify_claim(CLAIM))
    assert out["is_true"] is True
    assert out["confidence"] == 0.82
    assert len(out["sources"]) >= 5
    assert out["cached"] is False


def test_cache_hit_within_24h(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.top_up_balance(1000)
    _mock_all_sources(direct_vm)
    _mock_ai_confirm(direct_vm)

    first = json.loads(contract.verify_claim(CLAIM))
    direct_vm.clear_mocks()
    second = json.loads(contract.verify_claim(CLAIM))

    assert second["cached"] is True
    assert second["claim_hash"] == first["claim_hash"]


def test_cache_expires_after_24h(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.top_up_balance(1000)
    _mock_all_sources(direct_vm)
    _mock_ai_confirm(direct_vm, confidence=0.75)
    first = json.loads(contract.verify_claim(CLAIM))

    direct_vm.timestamp += 86_401
    _mock_all_sources(direct_vm)
    _mock_ai_confirm(direct_vm, confidence=0.9)
    second = json.loads(contract.verify_claim(CLAIM))

    assert second["cached"] is False
    assert second["claim_hash"] == first["claim_hash"]
    assert second["confidence"] == 0.9


def test_verify_claim_requires_balance(contract, direct_vm):
    direct_vm.sender = ALICE
    _mock_all_sources(direct_vm)
    _mock_ai_confirm(direct_vm)
    with pytest.raises(Exception, match="insufficient requester balance"):
        contract.verify_claim(CLAIM)


def test_raises_when_not_enough_sources(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.top_up_balance(1000)
    direct_vm.mock_web(r"newsapi\.org", {"status": 200, "body": {"articles": []}})
    direct_vm.mock_web(r"gdeltproject\.org", {"status": 500, "body": {}})
    direct_vm.mock_web(r"reuters\.com", {"status": 404, "body": {}})
    _mock_ai_confirm(direct_vm)

    with pytest.raises(Exception, match="could not fetch enough sources"):
        contract.verify_claim(CLAIM)


def test_dispute_upheld_rewards_challenger(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.top_up_balance(1000)
    _mock_all_sources(direct_vm)
    _mock_ai_confirm(direct_vm)
    verdict = json.loads(contract.verify_claim(CLAIM, 120))

    direct_vm.sender = BOB
    contract.top_up_balance(500)
    dispute_id = contract.raise_dispute(verdict["claim_hash"], 50)

    _mock_all_sources(direct_vm)
    _mock_ai_deny(direct_vm)
    status = contract.resolve_dispute(dispute_id)

    assert status == "UPHELD"
    # challenger gets stake back + requester fee (500 - 50 + 50 + 120)
    assert contract.get_balance(BOB) == 620


def test_dispute_rejected_transfers_stake_to_requester(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.top_up_balance(1000)
    _mock_all_sources(direct_vm)
    _mock_ai_confirm(direct_vm)
    verdict = json.loads(contract.verify_claim(CLAIM, 120))

    direct_vm.sender = BOB
    contract.top_up_balance(500)
    dispute_id = contract.raise_dispute(verdict["claim_hash"], 50)

    _mock_all_sources(direct_vm)
    _mock_ai_confirm(direct_vm)
    status = contract.resolve_dispute(dispute_id)

    assert status == "REJECTED"
    assert contract.get_balance(ALICE) == 930  # 1000 - 120 + 50


def test_get_claim_and_get_dispute_views(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.top_up_balance(1000)
    _mock_all_sources(direct_vm)
    _mock_ai_confirm(direct_vm)
    verdict = json.loads(contract.verify_claim(CLAIM))

    direct_vm.sender = BOB
    contract.top_up_balance(300)
    dispute_id = contract.raise_dispute(verdict["claim_hash"], 30)

    c = json.loads(contract.get_claim(verdict["claim_hash"]))
    d = json.loads(contract.get_dispute(dispute_id))

    assert c["claim_hash"] == verdict["claim_hash"]
    assert d["status"] == "OPEN"
