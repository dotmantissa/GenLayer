"""
Minimal GenLayer SDK mock that lets contract tests run in-process
without a node, Docker, or the full GenVM runtime.

Provides direct_vm, direct_deploy, and address fixtures that mirror
the genlayer-dev:direct-tests skill interface.
"""

import sys
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import importlib.util

import pytest

# ─── Exception ───────────────────────────────────────────────────────────────

class UserError(Exception):
    def __init__(self, msg=""):
        self.message = msg
        super().__init__(msg)

# ─── Storage types ────────────────────────────────────────────────────────────

class TreeMap(dict):
    pass

class DynArray(list):
    pass

class u256(int):
    pass

class Address(str):
    pass

def allow_storage(cls):
    return cls

# ─── Mutable test state ───────────────────────────────────────────────────────

_state: dict = {
    "sender":    "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "timestamp": 1704067200,  # 2024-01-01T00:00:00Z
}
_web_mocks: dict = {}   # regex -> {"status": int, "body": str|bytes|dict}
_llm_mocks: dict = {}   # regex -> dict  (returned by exec_prompt)

# ─── GL sub-objects ──────────────────────────────────────────────────────────

class _WebResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.body   = body

class _Web:
    def get(self, url: str) -> _WebResponse:
        import re
        for pattern, resp in _web_mocks.items():
            if re.search(pattern, url):
                body = resp.get("body", b"{}")
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode()
                elif isinstance(body, str):
                    body = body.encode()
                return _WebResponse(resp.get("status", 200), body)
        return _WebResponse(404, b'{"error":"not mocked"}')

class _Nondet:
    web = _Web()

    def exec_prompt(self, prompt: str, response_format=None) -> dict:
        import re
        for pattern, response in _llm_mocks.items():
            if re.search(pattern, prompt, re.DOTALL):
                return response
        return {}

class _VM:
    UserError = UserError

    class Return:
        def __init__(self, calldata):
            self.calldata = calldata

    # Sentinel base type referenced in validator signatures — must exist
    Result = type("Result", (), {})

    @staticmethod
    def run_nondet_unsafe(leader_fn, validator_fn):
        """Direct mode: run leader only, no consensus round-trip."""
        return leader_fn()

class _Message:
    @property
    def sender_account(self) -> Address:
        return Address(_state["sender"])

class _Block:
    @property
    def timestamp(self) -> int:
        return _state["timestamp"]

class _GL:
    message  = _Message()
    block    = _Block()
    vm       = _VM()
    nondet   = _Nondet()

    class public:
        @staticmethod
        def write(fn):
            return fn
        @staticmethod
        def view(fn):
            return fn

    class Contract:
        pass

gl = _GL()

# ─── Synthetic genlayer module ────────────────────────────────────────────────

_genlayer_mod       = type(sys)("genlayer")
_genlayer_mod.gl           = gl
_genlayer_mod.TreeMap      = TreeMap
_genlayer_mod.DynArray     = DynArray
_genlayer_mod.u256         = u256
_genlayer_mod.Address      = Address
_genlayer_mod.allow_storage = allow_storage
_genlayer_mod.dataclass    = dataclass
_genlayer_mod.__all__      = [
    "gl", "TreeMap", "DynArray", "u256", "Address",
    "allow_storage", "dataclass",
]
sys.modules["genlayer"] = _genlayer_mod

# ─── Load contract module once ────────────────────────────────────────────────

def _load_contract_class(path: Path):
    """Load a .py file and return the first gl.Contract subclass found."""
    spec   = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in dir(module):
        obj = getattr(module, name)
        if (
            isinstance(obj, type)
            and issubclass(obj, gl.Contract)
            and obj is not gl.Contract
        ):
            return obj
    raise ValueError(f"No gl.Contract subclass found in {path}")

# ─── VMContext (direct_vm fixture API) ───────────────────────────────────────

class VMContext:
    """Exposes cheatcodes for tests — mirrors the skill's direct_vm API."""

    # sender ──────────────────────────────────────────────────────────────────

    @property
    def sender(self) -> str:
        return _state["sender"]

    @sender.setter
    def sender(self, val: str) -> None:
        _state["sender"] = str(val)

    # timestamp ───────────────────────────────────────────────────────────────

    @property
    def timestamp(self) -> int:
        return _state["timestamp"]

    @timestamp.setter
    def timestamp(self, val: int) -> None:
        _state["timestamp"] = int(val)

    def warp(self, ts) -> None:
        """Accept Unix int or ISO-8601 string."""
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            _state["timestamp"] = int(dt.timestamp())
        else:
            _state["timestamp"] = int(ts)

    # mocks ───────────────────────────────────────────────────────────────────

    def mock_web(self, url_pattern: str, response: dict) -> None:
        _web_mocks[url_pattern] = response

    def mock_llm(self, prompt_pattern: str, response) -> None:
        if isinstance(response, str):
            response = json.loads(response)
        _llm_mocks[prompt_pattern] = response

    def clear_mocks(self) -> None:
        _web_mocks.clear()
        _llm_mocks.clear()

    # revert helper ───────────────────────────────────────────────────────────

    @contextmanager
    def expect_revert(self, match_text: str = ""):
        with pytest.raises(UserError, match=match_text):
            yield

# ─── Pytest fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_state():
    """Reset sender, timestamp, and all mocks before every test."""
    _state["sender"]    = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    _state["timestamp"] = 1704067200
    _web_mocks.clear()
    _llm_mocks.clear()
    yield


@pytest.fixture
def direct_vm() -> VMContext:
    return VMContext()


@pytest.fixture
def direct_deploy():
    """Returns a factory that deploys any GenLayer contract with zeroed storage."""
    import types as _types

    def _deploy(_path: str = "dev_bounty.py", *init_args, **init_kwargs):
        contract_path = Path(__file__).parent.parent.parent / _path
        contract_cls  = _load_contract_class(contract_path)
        instance      = object.__new__(contract_cls)

        # Auto-initialize collection fields from class annotations
        for klass in reversed(contract_cls.__mro__):
            for field, ann in getattr(klass, "__annotations__", {}).items():
                origin = getattr(ann, "__origin__", ann)
                if isinstance(origin, type) and issubclass(origin, TreeMap):
                    setattr(instance, field, TreeMap())
                elif isinstance(origin, type) and issubclass(origin, DynArray):
                    setattr(instance, field, DynArray())

        instance.__init__(*init_args, **init_kwargs)
        return instance

    return _deploy


@pytest.fixture
def direct_alice() -> str:
    return "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

@pytest.fixture
def direct_bob() -> str:
    return "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"

@pytest.fixture
def direct_charlie() -> str:
    return "0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
