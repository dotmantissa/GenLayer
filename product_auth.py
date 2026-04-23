# v0.1.0
# { "Depends": "py-genlayer:test" }

from genlayer import *
import json

ERROR_EXPECTED  = "[EXPECTED]"
ERROR_EXTERNAL  = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"

CACHE_TTL = 3600  # seconds


@allow_storage
@dataclass
class Brand:
    name: str
    api_endpoint: str
    api_key_encrypted: str
    verified: str        # "true" | "false"
    registered_at: u256


@allow_storage
@dataclass
class Product:
    serial_number: str
    product_name: str
    brand_address: str   # lowercase
    registered_at: u256


@allow_storage
@dataclass
class CacheEntry:
    is_authentic: str    # "true" | "false"
    product_details: str # JSON string
    cached_at: u256


class ProductAuthenticity(gl.Contract):
    admin: Address
    brands: TreeMap[str, Brand]
    brand_list: DynArray[str]
    products: TreeMap[str, Product]
    cache: TreeMap[str, CacheEntry]

    def __init__(self):
        self.admin = gl.message.sender_account

    # ── Private helpers ───────────────────────────────────────────────────────

    def _require_admin(self) -> None:
        if gl.message.sender_account != self.admin:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Only admin can call this")

    def _call_brand_api(self, api_endpoint: str, api_key_encrypted: str, serial: str) -> dict:
        sep = "&" if "?" in api_endpoint else "?"
        url = f"{api_endpoint.rstrip('/')}{sep}serial={serial}"
        if api_key_encrypted:
            url += f"&api_key={api_key_encrypted}"
        try:
            res = gl.nondet.web.get(url)
            if res.status in (401, 403):
                raise gl.vm.UserError(f"{ERROR_EXTERNAL} Brand API auth failed ({res.status})")
            if res.status == 404:
                raise gl.vm.UserError(f"{ERROR_EXTERNAL} Serial not found in brand API (404)")
            if 400 <= res.status < 500:
                raise gl.vm.UserError(f"{ERROR_EXTERNAL} Brand API client error ({res.status})")
            if res.status >= 500:
                raise gl.vm.UserError(f"{ERROR_TRANSIENT} Brand API unavailable ({res.status})")
            return json.loads(res.body.decode("utf-8"))
        except gl.vm.UserError:
            raise
        except Exception as e:
            raise gl.vm.UserError(f"{ERROR_TRANSIENT} Network error calling brand API: {e}")

    # ── Admin methods ─────────────────────────────────────────────────────────

    @gl.public.write
    def add_verified_brand(self, brand_address: str, brand_name: str) -> None:
        """
        Admin whitelists a brand wallet address, granting it permission to
        register products and provide the verification API.
        Emits: [BrandRegistered]
        """
        self._require_admin()
        addr = str(brand_address).strip().lower()
        if not addr:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} brand_address cannot be empty")
        if addr in self.brands:
            b = self.brands[addr]
            b.verified = "true"
            self.brands[addr] = b
        else:
            self.brands[addr] = Brand(
                name=str(brand_name).strip(),
                api_endpoint="",
                api_key_encrypted="",
                verified="true",
                registered_at=u256(int(gl.block.timestamp)),
            )
            self.brand_list.append(addr)
        print(f"[BrandRegistered] address={addr} name={brand_name}")

    @gl.public.write
    def remove_verified_brand(self, brand_address: str) -> None:
        """
        Admin revokes a brand's verified status. Existing cached results remain
        until TTL expires; new verify_product calls will fail for their serials.
        Emits: [BrandRevoked]
        """
        self._require_admin()
        addr = str(brand_address).strip().lower()
        if addr not in self.brands:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Brand {addr} not found")
        b = self.brands[addr]
        b.verified = "false"
        self.brands[addr] = b
        print(f"[BrandRevoked] address={addr}")

    # ── Brand methods ─────────────────────────────────────────────────────────

    @gl.public.write
    def register_product(
        self,
        serial_number: str,
        product_name: str,
        api_endpoint: str,
        api_key_encrypted: str,
    ) -> None:
        """
        Verified brand registers a product serial number on-chain and sets their
        verification API. The api_key_encrypted is stored as-is — brands should
        encrypt it client-side before submitting.
        Emits: [ProductRegistered]
        """
        addr = str(gl.message.sender_account).lower()
        if addr not in self.brands:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Caller is not a registered brand")
        b = self.brands[addr]
        if b.verified != "true":
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Brand is not verified by admin")

        sn = str(serial_number).strip()
        if not sn:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} serial_number cannot be empty")
        if sn in self.products:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Serial {sn} already registered")

        endpoint = str(api_endpoint).strip()
        if not endpoint:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} api_endpoint cannot be empty")

        # Update brand's API config on each product registration
        b.api_endpoint = endpoint
        b.api_key_encrypted = str(api_key_encrypted).strip()
        self.brands[addr] = b

        self.products[sn] = Product(
            serial_number=sn,
            product_name=str(product_name).strip(),
            brand_address=addr,
            registered_at=u256(int(gl.block.timestamp)),
        )
        print(f"[ProductRegistered] serial={sn} brand={addr} product={product_name}")

    # ── Consumer methods ──────────────────────────────────────────────────────

    @gl.public.write
    def verify_product(self, serial_number: str) -> str:
        """
        Consumer verifies a product's authenticity by querying the brand's API.
        Results are cached on-chain for CACHE_TTL seconds (1 hour).
        Emits: [ProductVerified] or [FakeProductDetected]
        Returns: JSON {"is_authentic": bool, "product_details": dict, "cached": bool}
        """
        sn  = str(serial_number).strip()
        now = int(gl.block.timestamp)

        # Return cached result if still fresh
        if sn in self.cache:
            entry = self.cache[sn]
            if now - int(entry.cached_at) < CACHE_TTL:
                return json.dumps({
                    "is_authentic":    entry.is_authentic == "true",
                    "product_details": json.loads(entry.product_details),
                    "cached":          True,
                })

        if sn not in self.products:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Serial {sn} not registered on-chain")

        p = self.products[sn]
        if p.brand_address not in self.brands:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Brand {p.brand_address} not found")

        b = self.brands[p.brand_address]
        if b.verified != "true":
            raise gl.vm.UserError(
                f"{ERROR_EXPECTED} Brand {p.brand_address} is no longer verified"
            )

        api_endpoint      = b.api_endpoint
        api_key_encrypted = b.api_key_encrypted
        product_name      = p.product_name
        brand_address     = p.brand_address

        def fetch() -> dict:
            data = self._call_brand_api(api_endpoint, api_key_encrypted, sn)
            is_authentic = bool(
                data.get("authentic", data.get("is_authentic", False))
            )
            details = {
                "product_name": str(data.get("product_name", product_name)),
                "brand":        str(data.get("brand", brand_address)),
                "model":        str(data.get("model", "")),
                "manufactured": str(data.get("manufactured", "")),
            }
            return {"is_authentic": is_authentic, "product_details": details}

        def validator(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                leader_msg = getattr(leaders_res, "message", "")
                try:
                    fetch()
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
                val = fetch()
            except Exception:
                return False
            # Agree only on the binary authenticity result; details may vary
            return leaders_res.calldata.get("is_authentic") == val.get("is_authentic")

        result       = gl.vm.run_nondet_unsafe(fetch, validator)
        is_authentic = result["is_authentic"]
        details      = result["product_details"]

        self.cache[sn] = CacheEntry(
            is_authentic="true" if is_authentic else "false",
            product_details=json.dumps(details),
            cached_at=u256(now),
        )

        if is_authentic:
            print(f"[ProductVerified] serial={sn} brand={brand_address} product={product_name}")
        else:
            print(
                f"[FakeProductDetected] serial={sn} brand={brand_address} product={product_name}"
            )

        return json.dumps({
            "is_authentic":    is_authentic,
            "product_details": details,
            "cached":          False,
        })

    # ── View methods ──────────────────────────────────────────────────────────

    @gl.public.view
    def get_product(self, serial_number: str) -> str:
        sn = str(serial_number).strip()
        if sn not in self.products:
            return json.dumps({"error": "not found"})
        p = self.products[sn]
        return json.dumps({
            "serial_number": p.serial_number,
            "product_name":  p.product_name,
            "brand_address": p.brand_address,
            "registered_at": int(p.registered_at),
        })

    @gl.public.view
    def get_brand(self, brand_address: str) -> str:
        addr = str(brand_address).strip().lower()
        if addr not in self.brands:
            return json.dumps({"error": "not found"})
        b = self.brands[addr]
        return json.dumps({
            "address":      addr,
            "name":         b.name,
            "verified":     b.verified == "true",
            "api_endpoint": b.api_endpoint,
            "registered_at": int(b.registered_at),
        })

    @gl.public.view
    def list_brands(self) -> str:
        result = []
        for i in range(len(self.brand_list)):
            addr = self.brand_list[i]
            if addr in self.brands:
                b = self.brands[addr]
                result.append({
                    "address":  addr,
                    "name":     b.name,
                    "verified": b.verified == "true",
                })
        return json.dumps(result)

    @gl.public.view
    def get_cache(self, serial_number: str) -> str:
        sn = str(serial_number).strip()
        if sn not in self.cache:
            return json.dumps({"cached": False})
        entry = self.cache[sn]
        now = int(gl.block.timestamp)
        age = now - int(entry.cached_at)
        return json.dumps({
            "cached":       True,
            "is_authentic": entry.is_authentic == "true",
            "age_seconds":  age,
            "ttl_seconds":  max(0, CACHE_TTL - age),
        })
