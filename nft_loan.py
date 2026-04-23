# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ANNUAL_RATE_BPS    = 1000       # 10% APR in basis points
MAX_LTV_BPS        = 5000       # 50% max loan-to-value
LIQ_LTV_BPS        = 6000       # 60% liquidation threshold
STALENESS_SECONDS  = 86400      # 24 hours
SECONDS_PER_YEAR   = 31536000


class NFTLoan(gl.Contract):
    """
    NFT-Collateralized Lending
    - Deposit any ERC-721 as collateral via deposit_nft()
    - Borrow up to 50% of the collection floor price (lower of OpenSea / Blur)
    - Simple interest at 10% APR accrues on every interaction
    - Anyone may liquidate a loan whose total debt exceeds 60% of live floor value
    - Floor prices older than 24h are automatically re-fetched
    """

    loans: str        # JSON: {addr: {nft_contract, token_id, principal_wei,
                      #               accrued_interest_wei, borrow_timestamp,
                      #               last_interest_update, status}}
    price_cache: str  # JSON: {nft_contract: {floor_price_wei, timestamp}}

    def __init__(self):
        self.loans = "{}"
        self.price_cache = "{}"

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _fetch_floor_price_live(self, contract_address: str) -> int:
        """
        Fetches floor price from OpenSea and Blur via nondet consensus.
        Returns the lower of the two prices (in wei) for safety.
        Uses prompt_comparative so minor float rounding differences are tolerated.
        """
        addr = contract_address.lower()
        opensea_url = f"https://api.opensea.io/api/v1/collection/{addr}/stats"
        blur_url    = f"https://core-api.prod.blur.io/v1/collections/{addr}"

        def fetch_prices() -> str:
            prices = {}

            # ── OpenSea ──
            try:
                raw_os = gl.nondet.web.render(opensea_url, mode="text")
                task = f"""You are a data parser. Extract the NFT floor price in ETH from this API response.
Data: {raw_os[:2000]}

Respond ONLY with valid JSON, no markdown:
{{"floor_eth": <number>, "valid": true}}
If no valid floor price is found: {{"valid": false}}"""
                result = gl.nondet.exec_prompt(task)
                parsed = json.loads(result.replace("```json", "").replace("```", "").strip())
                if parsed.get("valid") and float(parsed.get("floor_eth", 0)) > 0:
                    prices["opensea"] = float(parsed["floor_eth"])
            except Exception as e:
                print(f"OpenSea fetch failed: {e}")

            # ── Blur ──
            try:
                raw_blur = gl.nondet.web.render(blur_url, mode="text")
                task = f"""You are a data parser. Extract the NFT floor price in ETH from this API response.
Look for fields named floorPrice, floor_price, or minPrice.
Data: {raw_blur[:2000]}

Respond ONLY with valid JSON, no markdown:
{{"floor_eth": <number>, "valid": true}}
If no valid floor price is found: {{"valid": false}}"""
                result = gl.nondet.exec_prompt(task)
                parsed = json.loads(result.replace("```json", "").replace("```", "").strip())
                if parsed.get("valid") and float(parsed.get("floor_eth", 0)) > 0:
                    prices["blur"] = float(parsed["floor_eth"])
            except Exception as e:
                print(f"Blur fetch failed: {e}")

            if not prices:
                return json.dumps({"valid": False, "error": "all_sources_failed"})

            lower_eth = min(prices.values())
            lower_wei = int(lower_eth * 10 ** 18)
            return json.dumps({
                "valid": True,
                "floor_wei": lower_wei,
                "opensea_eth": prices.get("opensea"),
                "blur_eth": prices.get("blur"),
            })

        criteria = """Compare the floor_wei values from two validator runs.
Return EQUAL if:
  - Both are valid and within 5% of each other, OR
  - One source failed and the other has a valid price (use the valid one).
Return DIFFERENT if valid prices diverge by more than 5%."""

        consensus = gl.eq_principle.prompt_comparative(fetch_prices, criteria)
        result = json.loads(consensus)

        if not result.get("valid"):
            raise Exception(f"Floor price fetch failed: {result.get('error', 'unknown')}")

        return int(result["floor_wei"])

    def _get_floor_price(self, contract_address: str, force_fresh: bool = False) -> int:
        """Returns cached price if < 24h old, otherwise re-fetches and updates cache."""
        addr  = contract_address.lower()
        cache = json.loads(self.price_cache)
        now   = int(gl.block.timestamp)

        if not force_fresh and addr in cache:
            age = now - int(cache[addr]["timestamp"])
            if age < STALENESS_SECONDS:
                return int(cache[addr]["floor_price_wei"])

        floor_wei = self._fetch_floor_price_live(addr)
        cache[addr] = {"floor_price_wei": str(floor_wei), "timestamp": str(now)}
        self.price_cache = json.dumps(cache)
        return floor_wei

    def _accrue_interest(self, loan: dict) -> dict:
        """Mutates loan dict in-place with newly accrued simple interest."""
        now     = int(gl.block.timestamp)
        elapsed = now - int(loan["last_interest_update"])
        if elapsed <= 0:
            return loan
        principal    = int(loan["principal_wei"])
        new_interest = principal * ANNUAL_RATE_BPS * elapsed // (10000 * SECONDS_PER_YEAR)
        loan["accrued_interest_wei"]  = str(int(loan["accrued_interest_wei"]) + new_interest)
        loan["last_interest_update"]  = str(now)
        return loan

    def _total_debt(self, loan: dict) -> int:
        return int(loan["principal_wei"]) + int(loan["accrued_interest_wei"])

    # ── Public write methods ──────────────────────────────────────────────────

    @gl.public.write
    def deposit_nft(self, contract_address: str, token_id: str) -> None:
        """
        Register an NFT as collateral for a new loan.
        One active loan slot per address; previous slot must be REPAID or LIQUIDATED.
        Emits: EVENT:NFTDeposited
        """
        borrower = str(gl.message.sender_account)
        loans    = json.loads(self.loans)

        existing = loans.get(borrower, {})
        if existing.get("status") == "ACTIVE":
            print(f"ERROR: {borrower} already has an active loan — repay first")
            return None

        loans[borrower] = {
            "nft_contract":          contract_address.lower(),
            "token_id":              str(token_id),
            "principal_wei":         "0",
            "accrued_interest_wei":  "0",
            "borrow_timestamp":      "0",
            "last_interest_update":  "0",
            "status":                "DEPOSITED",
        }
        self.loans = json.dumps(loans)
        print(f"EVENT:NFTDeposited borrower={borrower} nft_contract={contract_address} token_id={token_id}")
        return None

    @gl.public.write
    def borrow(self, amount_wei: int) -> None:
        """
        Borrow up to 50% of the NFT collection floor price (OpenSea/Blur, lower).
        Floor price is fetched live on first borrow; cached price used if < 24h old.
        Emits: EVENT:Borrowed
        """
        borrower = str(gl.message.sender_account)
        loans    = json.loads(self.loans)

        if borrower not in loans:
            print(f"ERROR: No collateral deposited for {borrower}")
            return None

        loan = loans[borrower]
        if loan["status"] != "DEPOSITED":
            print(f"ERROR: Expected status DEPOSITED, got {loan['status']}")
            return None

        if amount_wei <= 0:
            print("ERROR: amount_wei must be positive")
            return None

        floor_wei     = self._get_floor_price(loan["nft_contract"])
        max_borrow    = floor_wei * MAX_LTV_BPS // 10000

        if amount_wei > max_borrow:
            print(f"ERROR: {amount_wei} wei exceeds max borrow {max_borrow} wei (50% of floor {floor_wei} wei)")
            return None

        now                          = int(gl.block.timestamp)
        loan["principal_wei"]        = str(amount_wei)
        loan["borrow_timestamp"]     = str(now)
        loan["last_interest_update"] = str(now)
        loan["status"]               = "ACTIVE"

        loans[borrower] = loan
        self.loans      = json.dumps(loans)
        print(f"EVENT:Borrowed borrower={borrower} amount_wei={amount_wei} floor_wei={floor_wei} max_borrow_wei={max_borrow}")
        return None

    @gl.public.write
    def repay(self, amount_wei: int) -> None:
        """
        Repay outstanding debt. Interest is paid first, then principal.
        Full repayment (amount >= total debt) marks the loan REPAID and releases the NFT.
        Emits: EVENT:Repaid
        """
        borrower = str(gl.message.sender_account)
        loans    = json.loads(self.loans)

        if borrower not in loans or loans[borrower]["status"] != "ACTIVE":
            print(f"ERROR: No active loan for {borrower}")
            return None

        if amount_wei <= 0:
            print("ERROR: amount_wei must be positive")
            return None

        loan       = self._accrue_interest(loans[borrower])
        total_debt = self._total_debt(loan)

        if amount_wei >= total_debt:
            loan["principal_wei"]        = "0"
            loan["accrued_interest_wei"] = "0"
            loan["status"]               = "REPAID"
            print(f"EVENT:Repaid borrower={borrower} paid_wei={amount_wei} full=true nft_contract={loan['nft_contract']} token_id={loan['token_id']}")
        else:
            # Interest-first waterfall
            remaining = amount_wei
            interest  = int(loan["accrued_interest_wei"])
            if remaining >= interest:
                remaining                    -= interest
                loan["accrued_interest_wei"]  = "0"
                principal                     = int(loan["principal_wei"])
                loan["principal_wei"]         = str(max(0, principal - remaining))
            else:
                loan["accrued_interest_wei"] = str(interest - remaining)
            remaining_debt = self._total_debt(loan)
            print(f"EVENT:Repaid borrower={borrower} paid_wei={amount_wei} full=false remaining_debt_wei={remaining_debt}")

        loans[borrower] = loan
        self.loans      = json.dumps(loans)
        return None

    @gl.public.write
    def liquidate(self, borrower_address: str) -> None:
        """
        Public liquidation: re-fetches live floor price (bypasses cache).
        If total debt > 60% of floor value, seizes the NFT and assigns it to the caller.
        Emits: EVENT:Liquidated
        """
        liquidator = str(gl.message.sender_account)
        loans      = json.loads(self.loans)

        if borrower_address not in loans or loans[borrower_address]["status"] != "ACTIVE":
            print(f"ERROR: No active loan for {borrower_address}")
            return None

        loan          = self._accrue_interest(loans[borrower_address])
        nft_contract  = loan["nft_contract"]

        # Always use a fresh floor price for liquidations — never trust the cache
        floor_wei     = self._get_floor_price(nft_contract, force_fresh=True)
        liq_threshold = floor_wei * LIQ_LTV_BPS // 10000
        total_debt    = self._total_debt(loan)

        if total_debt <= liq_threshold:
            print(f"ERROR: Loan is healthy. debt={total_debt} threshold={liq_threshold} floor={floor_wei}")
            return None

        loan["status"]        = "LIQUIDATED"
        loan["liquidator"]    = liquidator
        loans[borrower_address] = loan
        self.loans            = json.dumps(loans)

        print(f"EVENT:Liquidated borrower={borrower_address} liquidator={liquidator} nft_contract={nft_contract} token_id={loan['token_id']} total_debt_wei={total_debt} floor_wei={floor_wei} threshold_wei={liq_threshold}")
        return None

    # ── Public view methods ───────────────────────────────────────────────────

    @gl.public.view
    def get_loan(self, borrower_address: str) -> str:
        """Full loan record for a borrower address."""
        loans = json.loads(self.loans)
        if borrower_address not in loans:
            return json.dumps({"error": "no loan found"})
        return json.dumps(loans[borrower_address])

    @gl.public.view
    def get_total_debt(self, borrower_address: str) -> str:
        """Current outstanding debt (principal + accrued interest) without mutating state."""
        loans = json.loads(self.loans)
        if borrower_address not in loans:
            return json.dumps({"error": "no loan found"})
        loan       = loans[borrower_address]
        if loan["status"] != "ACTIVE":
            return json.dumps({"status": loan["status"], "total_debt_wei": "0"})
        now        = int(gl.block.timestamp)
        elapsed    = now - int(loan["last_interest_update"])
        principal  = int(loan["principal_wei"])
        pending    = principal * ANNUAL_RATE_BPS * elapsed // (10000 * SECONDS_PER_YEAR)
        total      = int(loan["accrued_interest_wei"]) + pending + principal
        return json.dumps({
            "principal_wei":         loan["principal_wei"],
            "accrued_interest_wei":  loan["accrued_interest_wei"],
            "pending_interest_wei":  str(pending),
            "total_debt_wei":        str(total),
        })

    @gl.public.view
    def get_floor_price_cache(self, contract_address: str) -> str:
        """Cached floor price and its age in seconds."""
        cache = json.loads(self.price_cache)
        addr  = contract_address.lower()
        if addr not in cache:
            return json.dumps({"error": "not cached"})
        entry = cache[addr]
        age   = int(gl.block.timestamp) - int(entry["timestamp"])
        return json.dumps({
            "floor_price_wei":  entry["floor_price_wei"],
            "floor_price_eth":  str(int(entry["floor_price_wei"]) / 10 ** 18),
            "cache_age_seconds": str(age),
            "is_stale":         str(age >= STALENESS_SECONDS),
        })

    @gl.public.view
    def get_max_borrow(self, contract_address: str) -> str:
        """Max borrowable wei based on cached floor price (call borrow() to trigger a fresh fetch)."""
        cache = json.loads(self.price_cache)
        addr  = contract_address.lower()
        if addr not in cache:
            return json.dumps({"error": "price not cached — call borrow() first to populate"})
        floor     = int(cache[addr]["floor_price_wei"])
        max_borrow = floor * MAX_LTV_BPS // 10000
        return json.dumps({
            "floor_price_wei":  str(floor),
            "max_borrow_wei":   str(max_borrow),
            "max_borrow_eth":   str(max_borrow / 10 ** 18),
        })
