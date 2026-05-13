"""Direct tests for social_sentiment_oracle.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("social_sentiment_oracle.py")


def _mock_sentiment(direct_vm, sentiment="Bullish", confidence=85, sample_size=42):
    direct_vm.mock_web(
        r"nitter|twitter",
        {"status": 200, "body": "post1 great project post2 moon post3 strong community"},
    )
    direct_vm.mock_llm(
        r"You are a market sentiment classifier",
        {
            "sentiment": sentiment,
            "confidence": confidence,
            "sample_size": sample_size,
            "summary": "community trend summary",
        },
    )


def test_create_query_and_read(contract, direct_vm):
    direct_vm.sender = ALICE
    qid = contract.create_query("ETH", "nitter", 50, 60)
    q = json.loads(contract.get_query(qid))

    assert q["topic"] == "ETH"
    assert q["status"] == "PENDING"


def test_reject_invalid_source(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="unsupported source"):
        contract.create_query("ETH", "bad", 50, 60)


def test_reject_max_posts_bounds(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="max_posts out of range"):
        contract.create_query("ETH", "nitter", 3, 60)


def test_reject_invalid_confidence(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="min_confidence out of range"):
        contract.create_query("ETH", "nitter", 50, 101)


def test_resolve_query_happy_path(contract, direct_vm):
    direct_vm.sender = ALICE
    qid = contract.create_query("BTC", "nitter", 50, 60)
    _mock_sentiment(direct_vm, sentiment="Bullish", confidence=88)

    out = contract.resolve_query(qid)
    q = json.loads(contract.get_query(qid))

    assert out == "Bullish"
    assert q["status"] == "RESOLVED"
    assert contract.get_latest_sentiment("BTC") == "Bullish"


def test_low_confidence_status(contract, direct_vm):
    direct_vm.sender = ALICE
    qid = contract.create_query("SOL", "x_public", 50, 75)
    _mock_sentiment(direct_vm, sentiment="Neutral", confidence=50)

    contract.resolve_query(qid)
    q = json.loads(contract.get_query(qid))

    assert q["status"] == "LOW_CONFIDENCE"
    assert q["sentiment"] == "Neutral"


def test_non_creator_can_resolve_without_changing_topic_mapping(contract, direct_vm):
    direct_vm.sender = ALICE
    qid = contract.create_query("RITUAL", "nitter", 40, 10)
    _mock_sentiment(direct_vm, sentiment="Bearish", confidence=80)

    direct_vm.sender = BOB
    out = contract.resolve_query(qid)

    assert out == "Bearish"
    assert contract.get_latest_sentiment("RITUAL") == "Bearish"


def test_cannot_resolve_twice(contract, direct_vm):
    direct_vm.sender = ALICE
    qid = contract.create_query("ATOM", "nitter", 40, 10)
    _mock_sentiment(direct_vm, sentiment="Neutral", confidence=80)
    contract.resolve_query(qid)

    with pytest.raises(Exception, match="query already resolved"):
        contract.resolve_query(qid)


def test_provider_client_error(contract, direct_vm):
    direct_vm.sender = ALICE
    qid = contract.create_query("DOGE", "nitter", 40, 10)
    direct_vm.mock_web(r"nitter|twitter", {"status": 404, "body": "not found"})

    with pytest.raises(Exception, match="source client error"):
        contract.resolve_query(qid)


def test_invalid_sentiment_label_reverts(contract, direct_vm):
    direct_vm.sender = ALICE
    qid = contract.create_query("AVAX", "nitter", 40, 10)
    direct_vm.mock_web(r"nitter|twitter", {"status": 200, "body": "payload"})
    direct_vm.mock_llm(
        r"You are a market sentiment classifier",
        {"sentiment": "Very Bull", "confidence": 90, "sample_size": 10, "summary": "bad"},
    )

    with pytest.raises(Exception, match="invalid sentiment label"):
        contract.resolve_query(qid)
