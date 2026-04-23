"""
Direct-mode tests for dev_bounty.py (GitHubBounty).
All tests run in-process against a mocked GenLayer SDK — no node required.
"""

import json
import pytest

# ─── Constants ────────────────────────────────────────────────────────────────

REPO  = "octocat/hello-world"
ISSUE = "42"
SHA   = "abc123def456abc123def456abc123def456abc123"

T0     = 1704067200        # 2024-01-01T00:00:00Z
EXPIRY = T0 + 86_400       # +1 day

ALICE   = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB     = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
CAROL   = "0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
PAYMENT = 1_000_000

# ─── Mock helpers ─────────────────────────────────────────────────────────────

def _mock_commit(direct_vm, message: str = f"Fixes #{ISSUE}"):
    # $ anchors to end-of-URL so this pattern doesn't accidentally match the /pulls suffix
    direct_vm.mock_web(
        rf"api\.github\.com/repos/{REPO}/commits/{SHA}$",
        {"status": 200, "body": {"sha": SHA, "commit": {"message": message}}},
    )

def _mock_compare(direct_vm, status: str = "ahead"):
    direct_vm.mock_web(
        rf"api\.github\.com/repos/{REPO}/compare/",
        {"status": 200, "body": {"status": status}},
    )

def _mock_pulls(direct_vm, merged: bool = True, base: str = "main"):
    direct_vm.mock_web(
        rf"api\.github\.com/repos/{REPO}/commits/{SHA[:7]}.*?/pulls",
        {
            "status": 200,
            "body": [
                {
                    "number":    101,
                    "title":     f"Fix #{ISSUE}",
                    "body":      "All unit tests pass. CI is green.",
                    "state":     "closed",
                    "merged_at": "2024-01-10T10:00:00Z" if merged else None,
                    "base":      {"ref": base},
                }
            ],
        },
    )

def _mock_llm(direct_vm, passing: bool = True):
    direct_vm.mock_llm(
        r"Test requirements",
        {
            "tests_passing": passing,
            "reasoning":     "CI is green, all tests pass." if passing else "No test mention.",
        },
    )

def _full_happy_path_mocks(direct_vm):
    _mock_commit(direct_vm)
    _mock_compare(direct_vm)
    _mock_pulls(direct_vm)

# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def contract(direct_deploy):
    return direct_deploy()

@pytest.fixture
def open_bounty(contract, direct_vm):
    """Contract with one open OPEN bounty already created by ALICE."""
    direct_vm.sender = ALICE
    contract.create_bounty(REPO, ISSUE, PAYMENT, "", EXPIRY)
    return contract

# ─── create_bounty ────────────────────────────────────────────────────────────

def test_create_stores_all_fields(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.create_bounty(REPO, ISSUE, PAYMENT, "tests must pass", EXPIRY)
    data = json.loads(contract.get_bounty(REPO, ISSUE))
    assert data["github_repo"]       == REPO
    assert data["issue_number"]      == ISSUE
    assert data["payment_wei"]       == PAYMENT
    assert data["test_requirements"] == "tests must pass"
    assert data["expiry_timestamp"]  == EXPIRY
    assert data["status"]            == "OPEN"
    assert data["owner"]             == ALICE
    assert data["claimant"]          == ""
    assert data["collaborators"]     == []

def test_create_duplicate_raises(open_bounty, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="already exists"):
        open_bounty.create_bounty(REPO, ISSUE, 500_000, "", EXPIRY)

def test_create_zero_payment_raises(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="payment_wei must be positive"):
        contract.create_bounty(REPO, ISSUE, 0, "", EXPIRY)

def test_create_negative_payment_raises(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="payment_wei must be positive"):
        contract.create_bounty(REPO, ISSUE, -1, "", EXPIRY)

def test_create_past_expiry_raises(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="expiry_timestamp must be in the future"):
        contract.create_bounty(REPO, ISSUE, PAYMENT, "", T0 - 1)

def test_create_expiry_at_now_raises(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="expiry_timestamp must be in the future"):
        contract.create_bounty(REPO, ISSUE, PAYMENT, "", T0)

# ─── add_collaborator ─────────────────────────────────────────────────────────

def test_add_collaborator_succeeds(open_bounty, direct_vm):
    direct_vm.sender = ALICE
    open_bounty.add_collaborator(REPO, ISSUE, BOB)
    data = json.loads(open_bounty.get_bounty(REPO, ISSUE))
    assert BOB.lower() in data["collaborators"]

def test_add_collaborator_non_owner_raises(open_bounty, direct_vm):
    direct_vm.sender = BOB
    with pytest.raises(Exception, match="Only bounty owner"):
        open_bounty.add_collaborator(REPO, ISSUE, BOB)

def test_add_collaborator_deduplicates(open_bounty, direct_vm):
    direct_vm.sender = ALICE
    open_bounty.add_collaborator(REPO, ISSUE, BOB)
    open_bounty.add_collaborator(REPO, ISSUE, BOB)
    data = json.loads(open_bounty.get_bounty(REPO, ISSUE))
    assert data["collaborators"].count(BOB.lower()) == 1

def test_add_collaborator_multiple_wallets(open_bounty, direct_vm):
    direct_vm.sender = ALICE
    open_bounty.add_collaborator(REPO, ISSUE, BOB)
    open_bounty.add_collaborator(REPO, ISSUE, CAROL)
    data = json.loads(open_bounty.get_bounty(REPO, ISSUE))
    assert len(data["collaborators"]) == 2

def test_add_collaborator_unknown_bounty_raises(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="not found"):
        contract.add_collaborator(REPO, ISSUE, BOB)

# ─── reclaim_bounty ───────────────────────────────────────────────────────────

def test_reclaim_before_expiry_raises(open_bounty, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="not expired"):
        open_bounty.reclaim_bounty(REPO, ISSUE)

def test_reclaim_at_exact_expiry_succeeds(open_bounty, direct_vm):
    direct_vm.sender    = ALICE
    direct_vm.timestamp = EXPIRY  # at expiry: timestamp < expiry is False → reclaim allowed
    open_bounty.reclaim_bounty(REPO, ISSUE)
    assert open_bounty.get_balance(ALICE) == PAYMENT

def test_reclaim_after_expiry_credits_owner(open_bounty, direct_vm):
    direct_vm.sender    = ALICE
    direct_vm.timestamp = EXPIRY + 1
    open_bounty.reclaim_bounty(REPO, ISSUE)
    assert open_bounty.get_balance(ALICE) == PAYMENT  # get_balance normalizes to lowercase

def test_reclaim_after_expiry_sets_status(open_bounty, direct_vm):
    direct_vm.sender    = ALICE
    direct_vm.timestamp = EXPIRY + 1
    open_bounty.reclaim_bounty(REPO, ISSUE)
    data = json.loads(open_bounty.get_bounty(REPO, ISSUE))
    assert data["status"] == "RECLAIMED"

def test_reclaim_non_open_bounty_raises(open_bounty, direct_vm):
    direct_vm.sender    = ALICE
    direct_vm.timestamp = EXPIRY + 1
    open_bounty.reclaim_bounty(REPO, ISSUE)
    with pytest.raises(Exception, match="is not OPEN"):
        open_bounty.reclaim_bounty(REPO, ISSUE)

def test_reclaim_non_owner_raises(open_bounty, direct_vm):
    direct_vm.sender    = BOB
    direct_vm.timestamp = EXPIRY + 1
    with pytest.raises(Exception, match="Only bounty owner"):
        open_bounty.reclaim_bounty(REPO, ISSUE)

def test_reclaim_unknown_bounty_raises(contract, direct_vm):
    direct_vm.sender    = ALICE
    direct_vm.timestamp = EXPIRY + 1
    with pytest.raises(Exception, match="not found"):
        contract.reclaim_bounty(REPO, ISSUE)

# ─── withdraw ─────────────────────────────────────────────────────────────────

def test_withdraw_clears_balance(contract):
    from conftest import u256
    contract.balances[BOB.lower()] = u256(500_000)
    contract.withdraw(BOB)
    assert contract.get_balance(BOB) == 0

def test_withdraw_unknown_wallet_raises(contract):
    with pytest.raises(Exception, match="No balance"):
        contract.withdraw(BOB)

def test_withdraw_zero_balance_raises(contract):
    from conftest import u256
    contract.balances[BOB.lower()] = u256(0)
    with pytest.raises(Exception, match="Zero balance"):
        contract.withdraw(BOB)

# ─── View methods ─────────────────────────────────────────────────────────────

def test_get_bounty_not_found_returns_error(contract):
    result = json.loads(contract.get_bounty(REPO, ISSUE))
    assert result == {"error": "not found"}

def test_get_balance_unknown_wallet_returns_zero(contract):
    assert contract.get_balance(BOB) == 0

def test_list_bounties_empty(contract):
    result = json.loads(contract.list_bounties())
    assert result == []

def test_list_bounties_shows_all(open_bounty, direct_vm):
    direct_vm.sender = ALICE
    open_bounty.create_bounty(REPO, "99", 500_000, "", EXPIRY)
    result = json.loads(open_bounty.list_bounties())
    ids = [b["id"] for b in result]
    assert f"{REPO}#42" in ids
    assert f"{REPO}#99" in ids
    assert len(result) == 2

def test_list_bounties_includes_status_and_payment(open_bounty):
    result = json.loads(open_bounty.list_bounties())
    entry = result[0]
    assert entry["status"]      == "OPEN"
    assert entry["payment_wei"] == PAYMENT

# ─── claim_bounty — pre-flight guard errors ───────────────────────────────────

def test_claim_unknown_bounty_raises(contract, direct_vm):
    with pytest.raises(Exception, match="not found"):
        contract.claim_bounty(REPO, ISSUE, SHA, BOB)

def test_claim_expired_bounty_raises(open_bounty, direct_vm):
    direct_vm.timestamp = EXPIRY + 1
    with pytest.raises(Exception, match="Bounty has expired"):
        open_bounty.claim_bounty(REPO, ISSUE, SHA, BOB)

def test_claim_reclaimed_bounty_raises(open_bounty, direct_vm):
    direct_vm.sender    = ALICE
    direct_vm.timestamp = EXPIRY + 1
    open_bounty.reclaim_bounty(REPO, ISSUE)
    direct_vm.timestamp = T0
    with pytest.raises(Exception, match="is not OPEN"):
        open_bounty.claim_bounty(REPO, ISSUE, SHA, BOB)

# ─── claim_bounty — GitHub verification failures ──────────────────────────────

def test_claim_commit_not_found_raises(open_bounty, direct_vm):
    direct_vm.mock_web(
        rf"api\.github\.com/repos/{REPO}/commits/{SHA[:7]}",
        {"status": 404, "body": b"{}"},
    )
    with pytest.raises(Exception, match="not found"):
        open_bounty.claim_bounty(REPO, ISSUE, SHA, BOB)

def test_claim_commit_missing_issue_reference_raises(open_bounty, direct_vm):
    _mock_commit(direct_vm, message="Refactor internals — no issue reference here")
    _mock_compare(direct_vm)
    _mock_pulls(direct_vm)
    with pytest.raises(Exception, match="does not reference"):
        open_bounty.claim_bounty(REPO, ISSUE, SHA, BOB)

def test_claim_commit_not_in_main_raises(open_bounty, direct_vm):
    _mock_commit(direct_vm)
    _mock_compare(direct_vm, status="behind")  # commit is not in main
    direct_vm.mock_web(
        rf"api\.github\.com/repos/{REPO}/commits/{SHA[:7]}.*?/pulls",
        {"status": 200, "body": []},  # no merged PRs either
    )
    with pytest.raises(Exception, match="not merged into main"):
        open_bounty.claim_bounty(REPO, ISSUE, SHA, BOB)

def test_claim_test_requirements_failing_raises(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.create_bounty(REPO, ISSUE, PAYMENT, "unit tests must pass", EXPIRY)
    _full_happy_path_mocks(direct_vm)
    _mock_llm(direct_vm, passing=False)
    with pytest.raises(Exception, match="Tests not verified"):
        contract.claim_bounty(REPO, ISSUE, SHA, BOB)

# ─── claim_bounty — success paths ────────────────────────────────────────────

def test_claim_releases_full_payment_to_claimant(open_bounty, direct_vm):
    _full_happy_path_mocks(direct_vm)
    open_bounty.claim_bounty(REPO, ISSUE, SHA, BOB)
    assert open_bounty.get_balance(BOB) == PAYMENT  # get_balance normalizes to lowercase

def test_claim_marks_bounty_as_claimed(open_bounty, direct_vm):
    _full_happy_path_mocks(direct_vm)
    open_bounty.claim_bounty(REPO, ISSUE, SHA, BOB)
    data = json.loads(open_bounty.get_bounty(REPO, ISSUE))
    assert data["status"]   == "CLAIMED"
    assert data["claimant"] == BOB.lower()

def test_claim_splits_payment_evenly_with_collaborator(open_bounty, direct_vm):
    direct_vm.sender = ALICE
    open_bounty.add_collaborator(REPO, ISSUE, CAROL)
    _full_happy_path_mocks(direct_vm)
    open_bounty.claim_bounty(REPO, ISSUE, SHA, BOB)
    bob_share   = open_bounty.get_balance(BOB)    # normalizes to lowercase internally
    carol_share = open_bounty.get_balance(CAROL)
    assert bob_share + carol_share == PAYMENT
    assert bob_share   >= PAYMENT // 2
    assert carol_share == PAYMENT // 2

def test_claim_three_way_split(open_bounty, direct_vm):
    DAVE = "0xDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD"
    direct_vm.sender = ALICE
    open_bounty.add_collaborator(REPO, ISSUE, CAROL)
    open_bounty.add_collaborator(REPO, ISSUE, DAVE)
    _full_happy_path_mocks(direct_vm)
    open_bounty.claim_bounty(REPO, ISSUE, SHA, BOB)
    total_paid = sum(open_bounty.get_balance(w) for w in [BOB, CAROL, DAVE])
    assert total_paid == PAYMENT

def test_claim_via_merged_pr_fallback_when_compare_fails(open_bounty, direct_vm):
    _mock_commit(direct_vm)
    _mock_compare(direct_vm, status="diverged")   # compare says not ahead
    _mock_pulls(direct_vm, merged=True)            # but a merged PR exists → fallback passes
    open_bounty.claim_bounty(REPO, ISSUE, SHA, BOB)
    assert open_bounty.get_balance(BOB.lower()) == PAYMENT

def test_claim_accepts_closes_keyword_in_message(open_bounty, direct_vm):
    _mock_commit(direct_vm, message=f"closes #{ISSUE}: implement feature")
    _mock_compare(direct_vm)
    _mock_pulls(direct_vm)
    open_bounty.claim_bounty(REPO, ISSUE, SHA, BOB)
    assert open_bounty.get_balance(BOB) == PAYMENT

def test_claim_accepts_resolves_keyword_in_message(open_bounty, direct_vm):
    _mock_commit(direct_vm, message=f"resolves #{ISSUE}")
    _mock_compare(direct_vm)
    _mock_pulls(direct_vm)
    open_bounty.claim_bounty(REPO, ISSUE, SHA, BOB)
    assert open_bounty.get_balance(BOB) == PAYMENT

def test_claim_with_test_requirements_passing(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.create_bounty(REPO, ISSUE, PAYMENT, "unit tests must pass", EXPIRY)
    _full_happy_path_mocks(direct_vm)
    _mock_llm(direct_vm, passing=True)
    contract.claim_bounty(REPO, ISSUE, SHA, BOB)
    assert contract.get_balance(BOB) == PAYMENT

def test_claimant_excluded_from_collaborator_double_pay(open_bounty, direct_vm):
    direct_vm.sender = ALICE
    open_bounty.add_collaborator(REPO, ISSUE, BOB)  # BOB is both claimant and collaborator
    _full_happy_path_mocks(direct_vm)
    open_bounty.claim_bounty(REPO, ISSUE, SHA, BOB)
    assert open_bounty.get_balance(BOB) == PAYMENT  # full amount, not doubled
