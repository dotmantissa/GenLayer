"""Direct tests for real_estate_price_benchmark_oracle.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("real_estate_price_benchmark_oracle.py")


def _register(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_region("sf_94103", "zillow", "94103", "https://zillow.example/94103", 2500)


def test_register_and_get_region(contract, direct_vm):
    _register(contract, direct_vm)
    r = json.loads(contract.get_region("sf_94103"))
    assert r["source"] == "zillow"


def test_owner_only_register(contract, direct_vm):
    direct_vm.sender = BOB
    with pytest.raises(Exception, match="only owner"):
        contract.register_region("x1", "zillow", "94103", "https://x", 2500)


def test_capture_benchmark_happy_path(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.mock_web(
        r"zillow\.example/94103",
        {"status": 200, "body": "ZHVI page text with market value data for San Francisco 94103."},
    )
    direct_vm.mock_llm(
        r"Extract real estate index value",
        {
            "price_usd": 1245000,
            "metric_name": "ZHVI",
            "jurisdiction": "San Francisco 94103",
            "confidence": 91,
        },
    )
    bid = contract.capture_benchmark("sf_94103")
    b = json.loads(contract.get_benchmark(bid))
    assert b["price_usd"] == 1245000


def test_invalid_register_inputs(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid region_id"):
        contract.register_region("x", "zillow", "94103", "https://x", 2500)
    with pytest.raises(Exception, match="invalid source"):
        contract.register_region("xx", "trulia", "94103", "https://x", 2500)
    with pytest.raises(Exception, match="invalid region_query"):
        contract.register_region("xx", "zillow", "", "https://x", 2500)
    with pytest.raises(Exception, match="invalid source_url"):
        contract.register_region("xx", "zillow", "94103", "ftp://x", 2500)
    with pytest.raises(Exception, match="invalid max_deviation_bps"):
        contract.register_region("xx", "zillow", "94103", "https://x", 10)


def test_region_not_found(contract):
    with pytest.raises(Exception, match="region not found"):
        contract.capture_benchmark("missing")


def test_source_server_error(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.mock_web(r"zillow\.example/94103", {"status": 500, "body": "err"})
    with pytest.raises(Exception, match="source server error"):
        contract.capture_benchmark("sf_94103")


def test_source_client_error(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.mock_web(r"zillow\.example/94103", {"status": 404, "body": "nf"})
    with pytest.raises(Exception, match="source client error"):
        contract.capture_benchmark("sf_94103")


def test_benchmark_not_found(contract):
    with pytest.raises(Exception, match="benchmark not found"):
        contract.get_benchmark("999")


def test_get_all_benchmarks_contains_created(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.mock_web(
        r"zillow\.example/94103",
        {"status": 200, "body": "ZHVI page text with market value data for San Francisco 94103."},
    )
    direct_vm.mock_llm(
        r"Extract real estate index value",
        {
            "price_usd": 1245000,
            "metric_name": "ZHVI",
            "jurisdiction": "San Francisco 94103",
            "confidence": 91,
        },
    )
    bid = contract.capture_benchmark("sf_94103")
    all_b = json.loads(contract.get_all_benchmarks())
    assert bid in all_b
