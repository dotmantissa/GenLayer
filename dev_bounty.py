# v0.1.0
# { "Depends": "py-genlayer:test" }

from genlayer import *
import json
import re

ERROR_EXPECTED  = "[EXPECTED]"
ERROR_EXTERNAL  = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM       = "[LLM_ERROR]"

GITHUB_API = "https://api.github.com"


@allow_storage
@dataclass
class Bounty:
    owner: Address
    github_repo: str
    issue_number: str
    payment_wei: u256
    test_requirements: str
    expiry_timestamp: u256
    status: str        # OPEN | CLAIMED | RECLAIMED
    claimant: str
    collaborators: str  # JSON list[str] of wallet addresses


class GitHubBounty(gl.Contract):
    bounties: TreeMap[str, Bounty]
    bounty_order: DynArray[str]
    balances: TreeMap[str, u256]

    def __init__(self):
        pass

    # ── Private helpers ───────────────────────────────────────────────────────

    def _bid(self, github_repo: str, issue_number: str) -> str:
        return f"{github_repo}#{issue_number}"

    def _fetch_commit(self, repo: str, sha: str) -> dict:
        url = f"{GITHUB_API}/repos/{repo}/commits/{sha}"
        try:
            res = gl.nondet.web.get(url)
            if res.status == 404:
                raise gl.vm.UserError(f"{ERROR_EXTERNAL} Commit {sha[:12]} not found in {repo}")
            if res.status == 422:
                raise gl.vm.UserError(f"{ERROR_EXPECTED} Invalid commit hash: {sha[:12]}")
            if res.status >= 500:
                raise gl.vm.UserError(f"{ERROR_TRANSIENT} GitHub API unavailable ({res.status})")
            if res.status >= 400:
                raise gl.vm.UserError(f"{ERROR_EXTERNAL} GitHub returned {res.status}")
            return json.loads(res.body.decode("utf-8"))
        except gl.vm.UserError:
            raise
        except Exception as e:
            raise gl.vm.UserError(f"{ERROR_TRANSIENT} Network error fetching commit: {e}")

    def _fetch_pulls_for_commit(self, repo: str, sha: str) -> list:
        url = f"{GITHUB_API}/repos/{repo}/commits/{sha}/pulls"
        try:
            res = gl.nondet.web.get(url)
            if res.status == 200:
                return json.loads(res.body.decode("utf-8"))
        except Exception:
            pass
        return []

    def _commit_in_main(self, repo: str, sha: str) -> bool:
        for branch in ("main", "master"):
            url = f"{GITHUB_API}/repos/{repo}/compare/{sha}...{branch}"
            try:
                res = gl.nondet.web.get(url)
                if res.status == 200:
                    data = json.loads(res.body.decode("utf-8"))
                    if data.get("status") in ("ahead", "identical"):
                        return True
            except Exception:
                pass
        return False

    def _find_merged_pr(self, pulls: list):
        for pr in pulls:
            if pr.get("merged_at") and pr.get("base", {}).get("ref", "") in ("main", "master"):
                return pr
        return None

    # ── Public write methods ──────────────────────────────────────────────────

    @gl.public.write
    def create_bounty(
        self,
        github_repo: str,
        issue_number: str,
        payment_wei: int,
        test_requirements: str,
        expiry_timestamp: int,
    ) -> None:
        """
        Register a new bounty for a GitHub issue.
        expiry_timestamp: Unix timestamp after which the owner can reclaim funds.
        test_requirements: Description of what test evidence is needed, or "" to skip.
        Emits: [BountyCreated]
        """
        bid = self._bid(github_repo, issue_number)
        if bid in self.bounties:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty {bid} already exists")
        if payment_wei <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} payment_wei must be positive")
        if expiry_timestamp <= int(gl.block.timestamp):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} expiry_timestamp must be in the future")

        self.bounties[bid] = Bounty(
            owner=gl.message.sender_account,
            github_repo=str(github_repo).strip(),
            issue_number=str(issue_number).strip(),
            payment_wei=u256(payment_wei),
            test_requirements=str(test_requirements).strip(),
            expiry_timestamp=u256(expiry_timestamp),
            status="OPEN",
            claimant="",
            collaborators="[]",
        )
        self.bounty_order.append(bid)
        print(
            f"[BountyCreated] id={bid} owner={gl.message.sender_account} "
            f"payment_wei={payment_wei} expiry={expiry_timestamp}"
        )

    @gl.public.write
    def add_collaborator(
        self,
        github_repo: str,
        issue_number: str,
        wallet: str,
    ) -> None:
        """
        Owner registers an additional wallet to split the bounty payment with the claimant.
        Emits: [CollaboratorAdded]
        """
        bid = self._bid(github_repo, issue_number)
        if bid not in self.bounties:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty {bid} not found")
        b = self.bounties[bid]
        if gl.message.sender_account != b.owner:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Only bounty owner can add collaborators")
        if b.status != "OPEN":
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty is not OPEN")

        collabs = json.loads(b.collaborators)
        w = str(wallet).strip().lower()
        if w not in [c.lower() for c in collabs]:
            collabs.append(w)
            b.collaborators = json.dumps(collabs)
            self.bounties[bid] = b
        print(f"[CollaboratorAdded] id={bid} wallet={wallet}")

    @gl.public.write
    def reclaim_bounty(self, github_repo: str, issue_number: str) -> None:
        """
        Owner reclaims the bounty payment after expiry if no valid claim was made.
        Emits: [BountyReclaimed]
        """
        bid = self._bid(github_repo, issue_number)
        if bid not in self.bounties:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty {bid} not found")
        b = self.bounties[bid]
        if gl.message.sender_account != b.owner:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Only bounty owner can reclaim")
        if b.status != "OPEN":
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty is not OPEN (status: {b.status})")
        if int(gl.block.timestamp) < int(b.expiry_timestamp):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty has not expired yet")

        b.status = "RECLAIMED"
        self.bounties[bid] = b
        owner_str = str(b.owner).lower()
        prev = int(self.balances[owner_str]) if owner_str in self.balances else 0
        self.balances[owner_str] = u256(prev + int(b.payment_wei))
        print(f"[BountyReclaimed] id={bid} owner={b.owner} payment_wei={int(b.payment_wei)}")

    @gl.public.write
    def claim_bounty(
        self,
        github_repo: str,
        issue_number: str,
        commit_hash: str,
        claimant_wallet: str,
    ) -> None:
        """
        Developer submits a commit hash as proof of completing the bounty issue.
        Validators independently verify against the GitHub API before payment is released.
        Emits: [ClaimSubmitted] → [VerificationPassed] → [PaymentReleased]
        """
        bid = self._bid(github_repo, issue_number)
        if bid not in self.bounties:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty {bid} not found")
        b = self.bounties[bid]
        if b.status != "OPEN":
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty is not OPEN (status: {b.status})")
        if int(gl.block.timestamp) >= int(b.expiry_timestamp):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Bounty has expired")

        repo      = b.github_repo
        issue_num = b.issue_number
        test_req  = b.test_requirements

        print(f"[ClaimSubmitted] id={bid} commit={commit_hash[:12]} claimant={claimant_wallet}")

        def verify() -> dict:
            # 1 — Confirm commit exists in the repo
            commit_data = self._fetch_commit(repo, commit_hash)
            commit_sha  = commit_data.get("sha", "")[:12]
            message     = commit_data.get("commit", {}).get("message", "")

            # 2 — Commit message must reference the issue number
            ref_patterns = [
                rf"#{re.escape(issue_num)}\b",
                rf"(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+#{re.escape(issue_num)}\b",
            ]
            if not any(re.search(p, message, re.IGNORECASE) for p in ref_patterns):
                raise gl.vm.UserError(
                    f"{ERROR_EXPECTED} Commit message does not reference #{issue_num}. "
                    f"Got: {message[:150]}"
                )

            # 3 — Commit must be merged into main/master
            in_main   = self._commit_in_main(repo, commit_hash)
            pulls     = self._fetch_pulls_for_commit(repo, commit_hash)
            merged_pr = self._find_merged_pr(pulls)

            if not in_main and merged_pr is None:
                raise gl.vm.UserError(
                    f"{ERROR_EXPECTED} Commit {commit_sha} is not merged into main/master"
                )

            pr_number = merged_pr.get("number") if merged_pr else None
            pr_body   = (merged_pr.get("body") or "").strip() if merged_pr else ""
            pr_title  = (merged_pr.get("title") or "") if merged_pr else ""

            # 4 — If test_requirements set, verify PR description mentions passing tests
            if test_req:
                if not merged_pr:
                    raise gl.vm.UserError(
                        f"{ERROR_EXPECTED} test_requirements set but no merged PR found — "
                        "cannot verify tests without a PR description"
                    )

                task = f"""You are a code review analyst.

PR #{pr_number}: {pr_title}
Description:
{pr_body[:3000] or "[No description provided]"}

Test requirements to verify: {test_req}

Does this PR description indicate that the tests passed and the stated requirements are met?

Respond ONLY with valid JSON (no markdown):
{{"tests_passing": true/false, "reasoning": "one-line explanation"}}"""

                raw = gl.nondet.exec_prompt(task, response_format="json")
                if not isinstance(raw, dict):
                    raise gl.vm.UserError(f"{ERROR_LLM} Expected dict, got {type(raw)}")
                if not raw.get("tests_passing", False):
                    raise gl.vm.UserError(
                        f"{ERROR_EXPECTED} Tests not verified. "
                        f"Reason: {str(raw.get('reasoning', ''))[:250]}"
                    )

            return {
                "commit_sha": commit_sha,
                "pr_number":  pr_number,
            }

        def validator(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                leader_msg = getattr(leaders_res, "message", "")
                try:
                    verify()
                    return False  # leader errored, validator succeeded — disagree
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
                val = verify()
            except Exception:
                return False

            ld = leaders_res.calldata
            # Stable fields that must match deterministically across validators
            return (
                ld.get("commit_sha") == val.get("commit_sha")
                and ld.get("pr_number") == val.get("pr_number")
            )

        result = gl.vm.run_nondet_unsafe(verify, validator)

        print(
            f"[VerificationPassed] id={bid} commit={result['commit_sha']} "
            f"pr={result['pr_number']} claimant={claimant_wallet}"
        )

        # Split payment evenly among claimant + any registered collaborators
        b          = self.bounties[bid]
        collabs    = json.loads(b.collaborators)
        primary    = str(claimant_wallet).strip().lower()
        recipients = [primary] + [c for c in collabs if c != primary]
        n          = len(recipients)
        total      = int(b.payment_wei)
        share      = total // n
        remainder  = total - share * n  # dust goes to primary claimant

        for i, wallet in enumerate(recipients):
            payout = share + (remainder if i == 0 else 0)
            prev   = int(self.balances[wallet]) if wallet in self.balances else 0
            self.balances[wallet] = u256(prev + payout)
            print(f"[PaymentReleased] id={bid} recipient={wallet} amount_wei={payout}")

        b.status   = "CLAIMED"
        b.claimant = primary
        self.bounties[bid] = b

    @gl.public.write
    def withdraw(self, wallet: str) -> None:
        """Zero out and log the claimable balance for a wallet address."""
        w = str(wallet).strip().lower()
        if w not in self.balances:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} No balance for {w}")
        amount = int(self.balances[w])
        if amount == 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Zero balance for {w}")
        self.balances[w] = u256(0)
        print(f"[Withdrawn] wallet={w} amount_wei={amount}")

    # ── View methods ──────────────────────────────────────────────────────────

    @gl.public.view
    def get_bounty(self, github_repo: str, issue_number: str) -> str:
        bid = self._bid(github_repo, issue_number)
        if bid not in self.bounties:
            return json.dumps({"error": "not found"})
        b = self.bounties[bid]
        return json.dumps({
            "owner":             str(b.owner),
            "github_repo":       b.github_repo,
            "issue_number":      b.issue_number,
            "payment_wei":       int(b.payment_wei),
            "test_requirements": b.test_requirements,
            "expiry_timestamp":  int(b.expiry_timestamp),
            "status":            b.status,
            "claimant":          b.claimant,
            "collaborators":     json.loads(b.collaborators),
        })

    @gl.public.view
    def get_balance(self, wallet: str) -> int:
        w = str(wallet).strip().lower()
        return int(self.balances[w]) if w in self.balances else 0

    @gl.public.view
    def list_bounties(self) -> str:
        result = []
        for i in range(len(self.bounty_order)):
            bid = self.bounty_order[i]
            if bid in self.bounties:
                bnt = self.bounties[bid]
                result.append({
                    "id":               bid,
                    "status":           bnt.status,
                    "payment_wei":      int(bnt.payment_wei),
                    "expiry_timestamp": int(bnt.expiry_timestamp),
                })
        return json.dumps(result)
