# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"


def _raise_user_error(message: str) -> None:
    err_cls = getattr(gl, "UserError", Exception)
    raise err_cls(message)


def _sender() -> str:
    sender_address = getattr(gl.message, "sender_address", None)
    if sender_address is not None:
        return str(sender_address)
    sender_account = getattr(gl.message, "sender_account", None)
    if sender_account is not None:
        return str(sender_account)
    return ""


class FDARecallPaymentGuard(gl.Contract):
    """Matches registered products against openFDA recall announcements."""

    owner: Address
    products: str
    checks: str
    next_check_id: u256

    def __init__(self):
        """Initialize storage.

        Parameters:
            None.

        Returns:
            None.
        """
        self.owner = Address(_sender())
        self.products = "{}"
        self.checks = "{}"
        self.next_check_id = 1

    def _require_owner(self) -> None:
        if _sender().strip().lower() != str(self.owner).strip().lower():
            _raise_user_error(f"{ERROR_EXPECTED} only owner")

    def _norm(self, value: str) -> str:
        return str(value).strip().lower()

    def _fetch_recalls(self, search_term: str, limit: int) -> dict:
        query = self._norm(search_term)
        if len(query) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid search_term")
        if limit < 1 or limit > 100:
            _raise_user_error(f"{ERROR_EXPECTED} invalid limit")

        url = f"https://api.fda.gov/drug/enforcement.json?search=product_description:{query}&limit={limit}"
        response = gl.nondet.web.get(url)
        status = int(response.status)
        if status >= 400 and status < 500:
            _raise_user_error(f"{ERROR_EXTERNAL} openfda client error {status}")
        if status >= 500:
            _raise_user_error(f"{ERROR_TRANSIENT} openfda server error {status}")

        try:
            body = response.body.decode("utf-8") if response.body is not None else "{}"
            data = json.loads(body)
            if not isinstance(data, dict):
                _raise_user_error(f"{ERROR_EXTERNAL} invalid openfda response")
            return data
        except Exception:
            _raise_user_error(f"{ERROR_EXTERNAL} invalid openfda json")
        return {}

    @gl.public.write
    def register_product(self, product_id: str, ndc: str, product_name: str) -> None:
        """Register a supply chain product for recall monitoring.

        Parameters:
            product_id: Internal product identifier.
            ndc: National Drug Code or normalized product code.
            product_name: Product display name.

        Returns:
            None.
        """
        self._require_owner()
        pid = self._norm(product_id)
        ndc_norm = self._norm(ndc)
        name = str(product_name).strip()
        if len(pid) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid product_id")
        if len(ndc_norm) < 4:
            _raise_user_error(f"{ERROR_EXPECTED} invalid ndc")
        if len(name) < 2:
            _raise_user_error(f"{ERROR_EXPECTED} invalid product_name")

        products = json.loads(self.products)
        products[pid] = {
            "product_id": pid,
            "ndc": ndc_norm,
            "product_name": name,
            "active": True,
            "updated_at": str(gl.block.timestamp),
        }
        self.products = json.dumps(products)

    @gl.public.write
    def set_product_active(self, product_id: str, active: bool) -> None:
        """Enable or disable product monitoring.

        Parameters:
            product_id: Internal product identifier.
            active: Monitoring state.

        Returns:
            None.
        """
        self._require_owner()
        pid = self._norm(product_id)
        products = json.loads(self.products)
        if pid not in products:
            _raise_user_error(f"{ERROR_EXPECTED} product not found")
        products[pid]["active"] = bool(active)
        products[pid]["updated_at"] = str(gl.block.timestamp)
        self.products = json.dumps(products)

    @gl.public.write
    def check_recall_and_halt(self, search_term: str, limit: int) -> str:
        """Fetch recalls and determine if registered products must halt payment.

        Parameters:
            search_term: Product keyword for openFDA recall query.
            limit: Maximum recall records to inspect.

        Returns:
            Check id string.
        """

        def leader_fn():
            data = self._fetch_recalls(search_term, limit)
            recalls = data.get("results", []) if isinstance(data, dict) else []
            if not isinstance(recalls, list):
                recalls = []

            products = json.loads(self.products)
            matches = []

            for pid in products.keys():
                p = products[pid]
                if not bool(p.get("active", False)):
                    continue
                ndc = self._norm(p.get("ndc", ""))
                pname = self._norm(p.get("product_name", ""))
                for rec in recalls:
                    if not isinstance(rec, dict):
                        continue
                    rec_desc = self._norm(rec.get("product_description", ""))
                    rec_reason = self._norm(rec.get("reason_for_recall", ""))
                    rec_class = self._norm(rec.get("classification", ""))
                    if (len(ndc) > 0 and ndc in rec_desc) or (len(pname) > 0 and pname in rec_desc):
                        matches.append(
                            {
                                "product_id": pid,
                                "ndc": ndc,
                                "product_name": p.get("product_name", ""),
                                "classification": rec_class,
                                "reason_for_recall": rec_reason,
                                "status": self._norm(rec.get("status", "")),
                            }
                        )

            halt_payments = len(matches) > 0
            return {
                "search_term": self._norm(search_term),
                "recall_count": len(recalls),
                "match_count": len(matches),
                "halt_payments": halt_payments,
                "bucket": 1 if halt_payments else 0,
                "matches": matches,
            }

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                try:
                    leader_fn()
                    return False
                except Exception as e:
                    leader_msg = leaders_res.message if hasattr(leaders_res, "message") else ""
                    validator_msg = str(e)
                    if validator_msg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
                        return True
                    return False

            validator_out = leader_fn()
            leader_out = leaders_res.calldata
            if bool(validator_out.get("halt_payments", False)) != bool(leader_out.get("halt_payments", False)):
                return False
            return int(validator_out.get("bucket", -1)) == int(leader_out.get("bucket", -2))

        out = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        cid = str(self.next_check_id)
        self.next_check_id += 1

        checks = json.loads(self.checks)
        checks[cid] = {
            "check_id": cid,
            "requester": _sender(),
            "search_term": out["search_term"],
            "recall_count": int(out["recall_count"]),
            "match_count": int(out["match_count"]),
            "halt_payments": bool(out["halt_payments"]),
            "compliance_alert": bool(out["halt_payments"]),
            "matches": out["matches"],
            "created_at": str(gl.block.timestamp),
        }
        self.checks = json.dumps(checks)
        return cid

    @gl.public.view
    def get_check(self, check_id: str) -> str:
        """Read one recall check result.

        Parameters:
            check_id: Check identifier.

        Returns:
            Check JSON string.
        """
        key = self._norm(check_id)
        checks = json.loads(self.checks)
        if key not in checks:
            _raise_user_error(f"{ERROR_EXPECTED} check not found")
        return json.dumps(checks[key])

    @gl.public.view
    def get_product(self, product_id: str) -> str:
        """Read one registered product.

        Parameters:
            product_id: Product identifier.

        Returns:
            Product JSON string.
        """
        pid = self._norm(product_id)
        products = json.loads(self.products)
        if pid not in products:
            _raise_user_error(f"{ERROR_EXPECTED} product not found")
        return json.dumps(products[pid])

    @gl.public.view
    def get_all_checks(self) -> str:
        """Read all recall check results.

        Parameters:
            None.

        Returns:
            JSON map of checks.
        """
        return self.checks
