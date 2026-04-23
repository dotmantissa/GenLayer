"""Direct-mode tests for product_auth.py (ProductAuthenticity contract)."""

import json
import pytest

from conftest import _state, _web_mocks

# ── Helpers ───────────────────────────────────────────────────────────────────

BRAND_ADDR  = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
BRAND_ADDR2 = "0xcccccccccccccccccccccccccccccccccccccccc"
ADMIN_ADDR  = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SERIAL      = "LV-2024-001234"
API_EP      = "https://api.louisvuitton.com/verify"
API_KEY     = "enc:abc123"


def _deploy(direct_deploy):
    return direct_deploy("product_auth.py")


def _setup(direct_vm, direct_deploy, direct_alice, direct_bob, *, add_product=False):
    """Deploy, whitelist direct_bob as brand, optionally register a product."""
    contract = _deploy(direct_deploy)

    # admin = alice (deployer)
    direct_vm.sender = direct_alice
    contract.add_verified_brand(direct_bob, "Louis Vuitton")

    if add_product:
        direct_vm.sender = direct_bob
        contract.register_product(SERIAL, "Speedy 30", API_EP, API_KEY)

    return contract


def _mock_authentic(direct_vm):
    direct_vm.mock_web(
        r"api\.louisvuitton\.com/verify",
        {
            "status": 200,
            "body": {
                "authentic":    True,
                "product_name": "Speedy 30",
                "brand":        "Louis Vuitton",
                "model":        "M41108",
                "manufactured": "2024-03",
            },
        },
    )


def _mock_fake(direct_vm):
    direct_vm.mock_web(
        r"api\.louisvuitton\.com/verify",
        {"status": 200, "body": {"authentic": False}},
    )


# ── add_verified_brand ────────────────────────────────────────────────────────

class TestAddVerifiedBrand:
    def test_admin_adds_brand(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        contract.add_verified_brand(direct_bob, "Louis Vuitton")

        b = json.loads(contract.get_brand(direct_bob))
        assert b["name"] == "Louis Vuitton"
        assert b["verified"] is True

    def test_brand_appears_in_list(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob)
        brands = json.loads(contract.list_brands())
        assert any(br["address"] == direct_bob.lower() for br in brands)

    def test_non_admin_cannot_add_brand(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("Only admin"):
            contract.add_verified_brand(direct_bob, "Louis Vuitton")

    def test_empty_address_raises(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("cannot be empty"):
            contract.add_verified_brand("", "Some Brand")

    def test_re_adding_revoked_brand_restores_verified(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob)
        direct_vm.sender = direct_alice
        contract.remove_verified_brand(direct_bob)
        assert json.loads(contract.get_brand(direct_bob))["verified"] is False

        contract.add_verified_brand(direct_bob, "Louis Vuitton")
        assert json.loads(contract.get_brand(direct_bob))["verified"] is True


# ── remove_verified_brand ─────────────────────────────────────────────────────

class TestRemoveVerifiedBrand:
    def test_admin_revokes_brand(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob)
        direct_vm.sender = direct_alice
        contract.remove_verified_brand(direct_bob)
        assert json.loads(contract.get_brand(direct_bob))["verified"] is False

    def test_non_admin_cannot_revoke(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob)
        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("Only admin"):
            contract.remove_verified_brand(direct_bob)

    def test_revoke_unknown_brand_raises(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        with direct_vm.expect_revert("not found"):
            contract.remove_verified_brand("0xdeadbeef")


# ── register_product ──────────────────────────────────────────────────────────

class TestRegisterProduct:
    def test_verified_brand_registers_product(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob)
        direct_vm.sender = direct_bob
        contract.register_product(SERIAL, "Speedy 30", API_EP, API_KEY)

        p = json.loads(contract.get_product(SERIAL))
        assert p["serial_number"] == SERIAL
        assert p["product_name"] == "Speedy 30"
        assert p["brand_address"] == direct_bob.lower()

    def test_duplicate_serial_raises(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob, add_product=True)
        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("already registered"):
            contract.register_product(SERIAL, "Speedy 30", API_EP, API_KEY)

    def test_non_brand_cannot_register(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("not a registered brand"):
            contract.register_product(SERIAL, "Speedy 30", API_EP, API_KEY)

    def test_revoked_brand_cannot_register(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob)
        direct_vm.sender = direct_alice
        contract.remove_verified_brand(direct_bob)

        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("not verified"):
            contract.register_product(SERIAL, "Speedy 30", API_EP, API_KEY)

    def test_empty_serial_raises(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob)
        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("cannot be empty"):
            contract.register_product("", "Speedy 30", API_EP, API_KEY)

    def test_empty_endpoint_raises(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob)
        direct_vm.sender = direct_bob
        with direct_vm.expect_revert("cannot be empty"):
            contract.register_product(SERIAL, "Speedy 30", "", API_KEY)

    def test_brand_api_config_updated_on_register(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob)
        direct_vm.sender = direct_bob
        contract.register_product(SERIAL, "Speedy 30", API_EP, API_KEY)

        b = json.loads(contract.get_brand(direct_bob))
        assert b["api_endpoint"] == API_EP


# ── verify_product ────────────────────────────────────────────────────────────

class TestVerifyProduct:
    def test_authentic_product_returns_true(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob, add_product=True)
        _mock_authentic(direct_vm)

        result = json.loads(contract.verify_product(SERIAL))
        assert result["is_authentic"] is True
        assert result["cached"] is False
        assert result["product_details"]["product_name"] == "Speedy 30"

    def test_fake_product_returns_false(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob, add_product=True)
        _mock_fake(direct_vm)

        result = json.loads(contract.verify_product(SERIAL))
        assert result["is_authentic"] is False
        assert result["cached"] is False

    def test_unregistered_serial_raises(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob)
        with direct_vm.expect_revert("not registered on-chain"):
            contract.verify_product("UNKNOWN-9999")

    def test_revoked_brand_blocks_verification(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob, add_product=True)
        direct_vm.sender = direct_alice
        contract.remove_verified_brand(direct_bob)

        with direct_vm.expect_revert("no longer verified"):
            contract.verify_product(SERIAL)

    def test_cache_hit_skips_api_call(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob, add_product=True)
        _mock_authentic(direct_vm)

        # First call — hits API, populates cache
        r1 = json.loads(contract.verify_product(SERIAL))
        assert r1["cached"] is False

        # Clear mocks so API would 404 if called again
        direct_vm.clear_mocks()

        # Second call — should serve from cache
        r2 = json.loads(contract.verify_product(SERIAL))
        assert r2["cached"] is True
        assert r2["is_authentic"] is True

    def test_cache_expires_after_ttl(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob, add_product=True)
        _mock_authentic(direct_vm)

        contract.verify_product(SERIAL)

        # Advance time past TTL
        direct_vm.warp(_state["timestamp"] + 3601)
        _mock_fake(direct_vm)  # API now returns fake

        r = json.loads(contract.verify_product(SERIAL))
        assert r["cached"] is False
        assert r["is_authentic"] is False  # re-fetched, now fake

    def test_cache_at_ttl_boundary_still_valid(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob, add_product=True)
        _mock_authentic(direct_vm)
        contract.verify_product(SERIAL)

        # Exactly at TTL boundary — still cached (age < CACHE_TTL)
        direct_vm.warp(_state["timestamp"] + 3599)
        direct_vm.clear_mocks()

        r = json.loads(contract.verify_product(SERIAL))
        assert r["cached"] is True

    def test_api_404_raises_external_error(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob, add_product=True)
        direct_vm.mock_web(r"api\.louisvuitton\.com", {"status": 404, "body": "{}"})
        with direct_vm.expect_revert(r"\[EXTERNAL\]"):
            contract.verify_product(SERIAL)

    def test_api_500_raises_transient_error(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob, add_product=True)
        direct_vm.mock_web(r"api\.louisvuitton\.com", {"status": 503, "body": "{}"})
        with direct_vm.expect_revert(r"\[TRANSIENT\]"):
            contract.verify_product(SERIAL)

    def test_api_401_raises_external_error(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob, add_product=True)
        direct_vm.mock_web(r"api\.louisvuitton\.com", {"status": 401, "body": "{}"})
        with direct_vm.expect_revert(r"\[EXTERNAL\]"):
            contract.verify_product(SERIAL)


# ── get_cache ─────────────────────────────────────────────────────────────────

class TestGetCache:
    def test_no_cache_entry(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob)
        result = json.loads(contract.get_cache(SERIAL))
        assert result["cached"] is False

    def test_cache_entry_has_ttl(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob, add_product=True)
        _mock_authentic(direct_vm)
        contract.verify_product(SERIAL)

        result = json.loads(contract.get_cache(SERIAL))
        assert result["cached"] is True
        assert result["is_authentic"] is True
        assert result["ttl_seconds"] > 0
        assert result["age_seconds"] == 0


# ── get_product / get_brand / list_brands ─────────────────────────────────────

class TestViews:
    def test_get_product_not_found(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob)
        r = json.loads(contract.get_product("NOPE"))
        assert r["error"] == "not found"

    def test_get_brand_not_found(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _deploy(direct_deploy)
        r = json.loads(contract.get_brand("0xdeadbeef"))
        assert r["error"] == "not found"

    def test_list_brands_empty(self, direct_vm, direct_deploy, direct_alice):
        contract = _deploy(direct_deploy)
        assert json.loads(contract.list_brands()) == []

    def test_list_brands_multiple(
        self, direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
    ):
        contract = _deploy(direct_deploy)
        direct_vm.sender = direct_alice
        contract.add_verified_brand(direct_bob, "Louis Vuitton")
        contract.add_verified_brand(direct_charlie, "Rolex")

        brands = json.loads(contract.list_brands())
        assert len(brands) == 2
        names = {b["name"] for b in brands}
        assert names == {"Louis Vuitton", "Rolex"}

    def test_revoked_brand_shows_unverified_in_list(
        self, direct_vm, direct_deploy, direct_alice, direct_bob
    ):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob)
        direct_vm.sender = direct_alice
        contract.remove_verified_brand(direct_bob)

        brands = json.loads(contract.list_brands())
        assert any(not b["verified"] for b in brands if b["address"] == direct_bob.lower())

    def test_get_product_fields(self, direct_vm, direct_deploy, direct_alice, direct_bob):
        contract = _setup(direct_vm, direct_deploy, direct_alice, direct_bob, add_product=True)
        p = json.loads(contract.get_product(SERIAL))
        assert p["serial_number"] == SERIAL
        assert p["product_name"] == "Speedy 30"
        assert "registered_at" in p
