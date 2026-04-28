"""Direct tests for esports_tournament_settlement.py."""

import json
import pytest

ORG = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
FAN1 = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
FAN2 = "0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
TEAM1_W = "0x1111111111111111111111111111111111111111"
TEAM2_W = "0x2222222222222222222222222222222222222222"


@pytest.fixture
def c(direct_deploy):
    return direct_deploy("esports_tournament_settlement.py")


def _teams():
    return json.dumps([
        {"name": "Alpha", "wallet_address": TEAM1_W},
        {"name": "Beta", "wallet_address": TEAM2_W},
    ])


def _mock_result(direct_vm, winner="Alpha", agree=True, forfeit=False):
    direct_vm.mock_web(r"faceit|battlefy|pandascore", {"status": 200, "body": {"ok": True}})
    direct_vm.mock_llm(
        r"Given two esports match payloads",
        {
            "winner": winner,
            "forfeit": forfeit,
            "confidence": 0.95,
            "agree": agree,
        },
    )


def test_create_and_resolve_with_prize_payout(c, direct_vm):
    direct_vm.sender = ORG
    c.top_up_balance(2000)
    c.create_tournament("CS2", "t1", 1000, _teams(), json.dumps(["Beta"]))
    c.create_match("t1", "m1", "Alpha", "Beta")

    _mock_result(direct_vm, winner="Beta", agree=True, forfeit=False)
    status = c.resolve_match("t1", "m1")

    assert status == "RESOLVED"
    t = json.loads(c.get_tournament("t1"))
    assert t["complete"] is True
    assert c._balance_of(TEAM2_W) == 1000


def test_disputed_when_sources_disagree(c, direct_vm):
    direct_vm.sender = ORG
    c.top_up_balance(2000)
    c.create_tournament("Overwatch", "t2", 500, _teams(), json.dumps(["Alpha"]))
    c.create_match("t2", "m2", "Alpha", "Beta")

    _mock_result(direct_vm, winner="Alpha", agree=False)
    out = c.resolve_match("t2", "m2")
    assert out == "DISPUTED"


def test_challenge_result_with_evidence(c, direct_vm):
    direct_vm.sender = ORG
    c.top_up_balance(2000)
    c.create_tournament("CS2", "t3", 500, _teams(), json.dumps(["Alpha"]))
    c.create_match("t3", "m3", "Alpha", "Beta")

    _mock_result(direct_vm, winner="Alpha", agree=False)
    c.resolve_match("t3", "m3")

    direct_vm.mock_llm(
        r"Analyze this screenshot OCR text",
        {"overrides": True, "winner": "Alpha"},
    )
    changed = c.challenge_result("t3", "m3", "Screenshot says Alpha won 2-0")
    assert changed is True


def test_forfeit_partial_payout(c, direct_vm):
    direct_vm.sender = ORG
    c.top_up_balance(5000)
    c.create_tournament("CS2", "t4", 1000, _teams(), json.dumps(["Alpha"]))
    c.create_match("t4", "m4", "Alpha", "Beta")

    direct_vm.sender = FAN1
    c.top_up_balance(2000)
    c.bet_match("t4", "m4", "Alpha", 1000)

    direct_vm.sender = FAN2
    c.top_up_balance(2000)
    c.bet_match("t4", "m4", "Beta", 1000)

    direct_vm.sender = ORG
    _mock_result(direct_vm, winner="Alpha", agree=True, forfeit=True)
    c.resolve_match("t4", "m4")

    direct_vm.sender = FAN1
    payout = c.claim_match_bet_payout("t4", "m4")
    assert payout == 1500


def test_normal_payout(c, direct_vm):
    direct_vm.sender = ORG
    c.top_up_balance(5000)
    c.create_tournament("CS2", "t5", 1000, _teams(), json.dumps(["Alpha"]))
    c.create_match("t5", "m5", "Alpha", "Beta")

    direct_vm.sender = FAN1
    c.top_up_balance(2000)
    c.bet_match("t5", "m5", "Alpha", 1000)

    direct_vm.sender = FAN2
    c.top_up_balance(2000)
    c.bet_match("t5", "m5", "Beta", 1000)

    direct_vm.sender = ORG
    _mock_result(direct_vm, winner="Alpha", agree=True, forfeit=False)
    c.resolve_match("t5", "m5")

    direct_vm.sender = FAN1
    payout = c.claim_match_bet_payout("t5", "m5")
    assert payout == 2000
