"""Direct tests for job_demand_trend_oracle.py."""

import json
import pytest

ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("job_demand_trend_oracle.py")


def _register(contract, direct_vm):
    direct_vm.sender = ALICE
    contract.register_target("ai_ml", "ML Engineer", "Python", "indeed", 1000)


def test_register_and_get_target(contract, direct_vm):
    _register(contract, direct_vm)
    t = json.loads(contract.get_target("ai_ml"))
    assert t["source"] == "indeed"


def test_register_owner_only(contract, direct_vm):
    direct_vm.sender = BOB
    with pytest.raises(Exception, match="only owner"):
        contract.register_target("x1", "A", "B", "indeed", 1000)


def test_capture_snapshot(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.mock_web(r"indeed\.com/jobs", {"status": 200, "body": "Search results showing 123 jobs for ML Engineer"})
    direct_vm.mock_llm(
        r"Extract active job listing count",
        {"count": 123, "confidence": 93, "rationale": "count visible"},
    )
    sid = contract.capture_snapshot("ai_ml", "2026Q1", "https://indeed.com/jobs")
    s = json.loads(contract.get_snapshot(sid))
    assert s["listing_count"] == 123


def test_analyze_growth_unlock(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.mock_web(r"q1\.jobs", {"status": 200, "body": "Search page text with total jobs 100 for role and skill in this region."})
    direct_vm.mock_llm(r"Extract active job listing count", {"count": 100, "confidence": 90, "rationale": "ok"})
    s1 = contract.capture_snapshot("ai_ml", "2026Q1", "https://q1.jobs")

    direct_vm.mock_web(r"q2\.jobs", {"status": 200, "body": "Search page text with total jobs 120 for role and skill in this region."})
    direct_vm.mock_llm(r"Extract active job listing count", {"count": 120, "confidence": 90, "rationale": "ok"})
    s2 = contract.capture_snapshot("ai_ml", "2026Q2", "https://q2.jobs")

    aid = contract.analyze_quarter_change("ai_ml", s1, s2)
    a = json.loads(contract.get_analysis(aid))
    assert a["materially_grew"] is True
    assert a["unlock_education_investment"] is True


def test_analyze_shrink(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.mock_web(r"p\.jobs", {"status": 200, "body": "Search page text with total jobs 200 for role and skill in this region."})
    direct_vm.mock_llm(r"Extract active job listing count", {"count": 200, "confidence": 90, "rationale": "ok"})
    s1 = contract.capture_snapshot("ai_ml", "2026Q1", "https://p.jobs")

    direct_vm.mock_web(r"c\.jobs", {"status": 200, "body": "Search page text with total jobs 150 for role and skill in this region."})
    direct_vm.mock_llm(r"Extract active job listing count", {"count": 150, "confidence": 90, "rationale": "ok"})
    s2 = contract.capture_snapshot("ai_ml", "2026Q2", "https://c.jobs")

    aid = contract.analyze_quarter_change("ai_ml", s1, s2)
    a = json.loads(contract.get_analysis(aid))
    assert a["materially_shrank"] is True
    assert a["unlock_education_investment"] is False


def test_invalid_register_inputs(contract, direct_vm):
    direct_vm.sender = ALICE
    with pytest.raises(Exception, match="invalid target_id"):
        contract.register_target("x", "ML", "Py", "indeed", 1000)
    with pytest.raises(Exception, match="invalid source"):
        contract.register_target("xx", "ML", "Py", "monster", 1000)
    with pytest.raises(Exception, match="invalid min_change_bps"):
        contract.register_target("xx", "ML", "Py", "indeed", 0)


def test_invalid_capture_inputs(contract, direct_vm):
    _register(contract, direct_vm)
    with pytest.raises(Exception, match="invalid quarter_label"):
        contract.capture_snapshot("ai_ml", "Q", "https://a.com")
    with pytest.raises(Exception, match="invalid search_url"):
        contract.capture_snapshot("ai_ml", "2026Q1", "ftp://a.com")


def test_source_server_error(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.mock_web(r"bad\.jobs", {"status": 500, "body": "err"})
    with pytest.raises(Exception, match="source server error"):
        contract.capture_snapshot("ai_ml", "2026Q1", "https://bad.jobs")


def test_target_or_snapshot_not_found(contract):
    with pytest.raises(Exception, match="target not found"):
        contract.get_target("none")
    with pytest.raises(Exception, match="snapshot not found"):
        contract.get_snapshot("999")


def test_analysis_not_found(contract):
    with pytest.raises(Exception, match="analysis not found"):
        contract.get_analysis("999")


def test_get_all_analyses_contains_created(contract, direct_vm):
    _register(contract, direct_vm)
    direct_vm.mock_web(r"a1\.jobs", {"status": 200, "body": "Search page text with total jobs 100 for role and skill in this region."})
    direct_vm.mock_llm(r"Extract active job listing count", {"count": 100, "confidence": 90, "rationale": "ok"})
    s1 = contract.capture_snapshot("ai_ml", "2026Q1", "https://a1.jobs")
    direct_vm.mock_web(r"a2\.jobs", {"status": 200, "body": "Search page text with total jobs 101 for role and skill in this region."})
    direct_vm.mock_llm(r"Extract active job listing count", {"count": 101, "confidence": 90, "rationale": "ok"})
    s2 = contract.capture_snapshot("ai_ml", "2026Q2", "https://a2.jobs")
    aid = contract.analyze_quarter_change("ai_ml", s1, s2)
    all_a = json.loads(contract.get_all_analyses())
    assert aid in all_a
