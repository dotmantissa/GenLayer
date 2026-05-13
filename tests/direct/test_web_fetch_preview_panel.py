"""Direct tests for web_fetch_preview_panel.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("web_fetch_preview_panel.py")


def test_preview_fetch_happy_path_json(contract, direct_vm):
    direct_vm.sender = ALICE
    direct_vm.mock_web(r"example\.com/api", {"status": 200, "body": '{"alpha":1,"beta":2}'})

    pid = contract.preview_fetch("https://example.com/api", 200)
    out = json.loads(contract.get_preview(pid))

    assert out["status_code"] == 200
    assert out["parsed_json"] is True
    assert "alpha" in out["parsed_fields"]


def test_preview_fetch_non_json_body(contract, direct_vm):
    direct_vm.sender = ALICE
    direct_vm.mock_web(r"example\.com/text", {"status": 200, "body": "plain text"})

    pid = contract.preview_fetch("https://example.com/text", 200)
    out = json.loads(contract.get_preview(pid))

    assert out["parsed_json"] is False


def test_preview_fetch_size_issue(contract, direct_vm):
    direct_vm.sender = ALICE
    direct_vm.mock_web(r"example\.com/big", {"status": 200, "body": "x" * 500})

    pid = contract.preview_fetch("https://example.com/big", 100)
    out = json.loads(contract.get_preview(pid))

    assert out["size_issue"] is True


def test_preview_fetch_invalid_url(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid url"):
        contract.preview_fetch("ftp://bad", 200)


def test_preview_fetch_invalid_max_bytes(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="max_preview_bytes out of range"):
        contract.preview_fetch("https://example.com", 20)


def test_provider_client_error(contract, direct_vm):
    direct_vm.sender = ALICE
    direct_vm.mock_web(r"example\.com/notfound", {"status": 404, "body": "err"})

    with pytest.raises(Exception, match="client error"):
        contract.preview_fetch("https://example.com/notfound", 200)


def test_provider_server_error(contract, direct_vm):
    direct_vm.sender = ALICE
    direct_vm.mock_web(r"example\.com/oops", {"status": 500, "body": "err"})

    with pytest.raises(Exception, match="server error"):
        contract.preview_fetch("https://example.com/oops", 200)


def test_missing_preview_reverts(contract):
    with pytest.raises(Exception, match="preview not found"):
        contract.get_preview("999")
