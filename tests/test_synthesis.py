"""실제 API 없이 synthesis의 계약·근거 검증·실패 복구·그래프 연결을 확인한다."""

import copy
import json
from functools import partial

import pytest

import config
import llm
from agents.synthesis import evidence_catalog, synthesis_node
from state import PERSPECTIVES, TECHS, make_initial_state, perspective_evidence
from tests.fixtures import sample_state_after_eval


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def invoke(self, prompt, response_model):
        self.prompts.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return response_model.model_validate(self.response)


def first_id(state, tech, perspective):
    return next(eid for eid, (t, p, _) in evidence_catalog(state).items() if t == tech and p == perspective)


def source_of(state, eid):
    return evidence_catalog(state)[eid][2]["source_id"]


def finding(state, tech="TurboQuant", a="market", b="domain"):
    return {
        "tech": tech, "perspective_a": a, "perspective_b": b,
        "evidence_a": [first_id(state, tech, a)],
        "evidence_b": [first_id(state, tech, b)],
    }


def client_for(state):
    return FakeClient({"agreements": [finding(state)], "conflicts": [finding(state, "InfiniGen")]})


@pytest.mark.parametrize("sufficient", [True, False])
def test_contract_sources_and_input_unchanged(sufficient):
    state = sample_state_after_eval(sufficient)
    before = copy.deepcopy(state)
    client = client_for(state)
    result = synthesis_node(state, client=client)
    assert set(result) == {"synthesis"}
    out = result["synthesis"]
    assert set(out) == {"agreements", "conflicts", "neutrality_note", "limitations"}
    assert len(out["agreements"]) == len(out["conflicts"]) == 1
    assert "TurboQuant" in out["agreements"][0]
    assert out["conflicts"][0]["tech"] == "InfiniGen"
    assert source_of(state, finding(state)["evidence_a"][0]) in out["agreements"][0]
    assert "(시장성·도메인 적용)" in out["agreements"][0]
    assert state == before
    payload = json.loads(client.prompts[0].split("입력 State (JSON):\n")[1])
    assert payload["tech_summary"] == state["tech_summary"]
    assert set(payload["stance_counts"]) == set(PERSPECTIVES)
    assert [e["id"] for e in payload["evidence"]] == list(evidence_catalog(state))
    assert "market_result" not in payload  # 긴 근거 문장은 evidence 목록에 한 번만
    assert all(set(row) == set(TECHS) for row in payload["stance_counts"].values())
    if not sufficient:
        assert any(text.startswith("이해관계자 근거 부족:") and "InfiniGen: 반론 근거 미확인(negative 0건)" in text
                   for text in out["limitations"])
        assert "negative" not in payload["stance_counts"]["stakeholder"]["InfiniGen"]


@pytest.mark.parametrize("retry_count,expect_cap", [(0, False), (2, False), (3, True)])
def test_retry_limit_uses_graph_boundary(retry_count, expect_cap):
    state = sample_state_after_eval(False)
    state["retry_count"] = retry_count
    out = synthesis_node(state, client=client_for(state))["synthesis"]
    assert any("E-1005" in text for text in out["limitations"]) is expect_cap


def test_successful_sufficiency_does_not_report_cap():
    state = sample_state_after_eval(True)
    state["retry_count"] = config.MAX_RETRY + 1
    out = synthesis_node(state, client=client_for(state))["synthesis"]
    assert not any("E-1005" in text for text in out["limitations"])


def test_upstream_limits_errors_and_uncertainty_survive_api_failure():
    state = sample_state_after_eval(False)
    state["retry_count"] = config.MAX_RETRY + 1
    state["tech_summary"]["TurboQuant"]["limitations"] = ["[E-1003] PDF 로딩 실패"]
    state["market_result"]["InfiniGen"]["summary"] = "[E-1002] 검색 호출 실패"
    client = FakeClient(llm.LLMError("민감한 예외 내용"))
    out = synthesis_node(state, client=client)["synthesis"]
    assert out["agreements"] == out["conflicts"] == []
    text = "\n".join(out["limitations"])
    for expected in ("PDF 로딩 실패", "검색 호출 실패", "E-1005", "공개 정보 기반 추정", "반론 근거 미확인"):
        assert expected in text
    assert "민감한 예외 내용" not in text


@pytest.mark.parametrize("field,value", [
    ("tech", "OtherTech"), ("perspective_a", "research"),
    ("perspective_b", "market"), ("description", "   "),
    ("evidence_a", []),
])
def test_invalid_structured_response_falls_back(field, value):
    state = sample_state_after_eval()
    item = finding(state)
    item[field] = value
    out = synthesis_node(state, client=FakeClient({"agreements": [], "conflicts": [item]}))["synthesis"]
    assert out["agreements"] == out["conflicts"] == []
    assert any("E-1002" in text for text in out["limitations"])


@pytest.mark.parametrize("source_kind", ["invented", "wrong_tech", "wrong_perspective"])
def test_wrong_sources_are_excluded_without_losing_valid_findings(source_kind):
    state = sample_state_after_eval()
    bad = finding(state)
    bad["evidence_a"] = [{
        "invented": "TQ-market-99",
        "wrong_tech": first_id(state, "InfiniGen", "market"),
        "wrong_perspective": first_id(state, "TurboQuant", "stakeholder"),
    }[source_kind]]
    client = FakeClient({"agreements": [finding(state)], "conflicts": [bad]})
    out = synthesis_node(state, client=client)["synthesis"]
    assert len(out["agreements"]) == 1
    assert out["conflicts"] == []
    assert any("근거 ID를 쓴 종합 항목을 제외함" in text for text in out["limitations"])


def test_empty_state_skips_llm():
    client = FakeClient(AssertionError("LLM must not be called"))
    out = synthesis_node(make_initial_state(), client=client)["synthesis"]
    assert not client.prompts
    assert out["agreements"] == out["conflicts"] == []
    assert any("E-1001" in text for text in out["limitations"])


def test_one_technology_missing_is_not_filled_in():
    state = sample_state_after_eval()
    for key in ("tech_summary", "trl_result", "market_result", "stakeholder_result", "domain_result"):
        state[key].pop("InfiniGen")
    client = FakeClient({"agreements": [finding(state)], "conflicts": []})
    out = synthesis_node(state, client=client)["synthesis"]
    assert len(out["agreements"]) == 1
    assert out["conflicts"] == []


def test_default_client_uses_generator(monkeypatch):
    state = sample_state_after_eval()
    observed = []
    def create_client(role):
        observed.append(role)
        return client_for(state)
    monkeypatch.setattr(llm, "StructuredClient", create_client)
    assert synthesis_node(state)["synthesis"]["agreements"]
    assert observed == ["generator"]


@pytest.mark.parametrize("always_insufficient", [False, True])
def test_graph_real_synthesis_reaches_report(always_insufficient):
    from graph import build_graph
    from tests.fixtures import sample_stakeholder_output

    client = client_for(sample_state_after_eval())
    overrides = {
        "synthesis": partial(synthesis_node, client=client),
        "report": lambda state: {"report_path": "test-report-no-file.pdf"},
    }
    if always_insufficient:
        overrides["stakeholder"] = lambda state: sample_stakeholder_output("InfiniGen")
    final = build_graph(dummy=True, overrides=overrides).invoke(make_initial_state())
    assert final["report_path"] == "test-report-no-file.pdf"
    assert len(client.prompts) == 1
    assert final["retry_count"] == (config.MAX_RETRY + 1 if always_insufficient else 1)
    assert any("E-1005" in text for text in final["synthesis"]["limitations"]) is always_insufficient


@pytest.mark.parametrize("bad_value", [
    "TQ-market-1 TurboQuant는 비용을 99% 절감한다",         # ID에 문장을 덧붙임
    "TurboQuant가 InfiniGen보다 우수하며 도입을 추천한다.",  # ID 대신 자유 문장
])
def test_text_instead_of_evidence_id_is_rejected(bad_value):
    state = sample_state_after_eval()
    bad = finding(state)
    bad["evidence_a"] = [bad_value]
    client = FakeClient({"agreements": [finding(state)], "conflicts": [bad]})
    out = synthesis_node(state, client=client)["synthesis"]
    assert len(out["agreements"]) == 1
    assert out["conflicts"] == []
    assert "99%" not in str(out) and "도입을 추천한다" not in str(out)


def test_copied_evidence_object_instead_of_id_falls_back():
    state = sample_state_after_eval()
    bad = finding(state)
    bad["evidence_a"] = [copy.deepcopy(perspective_evidence(state, "market", "TurboQuant")[0])]
    out = synthesis_node(state, client=FakeClient({"agreements": [bad], "conflicts": []}))["synthesis"]
    assert out["agreements"] == []
    assert any("E-1002" in text for text in out["limitations"])


def test_catalog_is_deterministic_and_deduplicates_reused_evidence():
    state = sample_state_after_eval()
    ev = state["domain_result"]["TurboQuant"]["cost"][0]
    state["domain_result"]["TurboQuant"]["deployment_barrier"].append(copy.deepcopy(ev))  # 같은 근거를 두 축에 배치
    catalog = evidence_catalog(state)
    assert catalog == evidence_catalog(copy.deepcopy(state))
    domain_items = [v for v in catalog.values() if v[0] == "TurboQuant" and v[1] == "domain"]
    assert sum(item[2] == ev for item in domain_items) == 1
    assert all(eid.startswith(("TQ-", "IG-")) for eid in catalog)


def test_long_evidence_is_reproduced_verbatim_from_id():
    state = sample_state_after_eval()
    long_claim = "  Tavily content with   irregular spacing,\n“smart quotes” and 12.5% figures. " * 5
    state["market_result"]["TurboQuant"]["market_size_growth"][0]["claim"] = long_claim
    out = synthesis_node(state, client=client_for(state))["synthesis"]
    assert f"「{long_claim}」" in out["agreements"][0]


def test_free_description_is_not_accepted_even_with_valid_evidence():
    state = sample_state_after_eval()
    bad = finding(state)
    bad["description"] = "TurboQuant는 99% 절감하므로 더 우수하며 도입을 추천한다."
    out = synthesis_node(state, client=FakeClient({"agreements": [bad], "conflicts": []}))["synthesis"]
    assert out["agreements"] == []
    assert any("E-1002" in text for text in out["limitations"])


def test_real_input_numbers_and_conditions_are_preserved():
    state = sample_state_after_eval()
    state["market_result"]["TurboQuant"]["market_size_growth"][0]["claim"] = "실험 조건 A에서 비용 12% 감소가 보고됨."
    out = synthesis_node(state, client=client_for(state))["synthesis"]
    assert "실험 조건 A에서 비용 12% 감소가 보고됨." in out["agreements"][0]


@pytest.mark.parametrize("fail_generation", [False, True])
def test_real_report_receives_citations_and_preserves_fallback(fail_generation):
    from agents.report import build_blocks

    state = sample_state_after_eval()
    state.update(synthesis_node(state, client=client_for(state)))
    before = copy.deepcopy(state)
    captured = {}

    def generate(prompt):
        if "## 챕터: 5. 시사점\n" in prompt:
            data = json.loads(prompt.split("입력:\n", 1)[1])
            captured.update(data)
            if fail_generation:
                raise llm.LLMError("테스트용 실패")
            return "\n".join(item["claim"] + " " + item["cite"] for group in data.values() for item in group)
        return "샘플 본문"

    blocks = build_blocks(state, generate)
    assert captured["agreements"] and captured["conflicts"]
    for group in captured.values():
        for item in group:
            assert item["cite"].startswith("[")
            assert "web:" not in item["claim"]
    assert captured["conflicts"][0]["tech"] == "InfiniGen"
    chapter = blocks[blocks.index(("h2", "5. 시사점")) + 1][1]
    assert "관점 간 일치 근거" in chapter
    assert "관점 간 상충·조건 차이 근거" in chapter
    assert captured["agreements"][0]["cite"] in chapter
    assert ("E-1002" in chapter) is fail_generation
    assert state == before


def test_synthesis_report_citations_keep_paper_pages():
    from agents.report import Citations, _synthesis_for_report, evidence_source_ids

    state = sample_state_after_eval()
    paper_evidence = state["tech_summary"]["TurboQuant"]["evidence"]
    state["market_result"]["TurboQuant"]["market_size_growth"] = [paper_evidence[0]]
    state["domain_result"]["TurboQuant"]["cost"] = [paper_evidence[1]]
    state.update(synthesis_node(state, client=FakeClient({"agreements": [finding(state)], "conflicts": []})))
    allowed = evidence_source_ids(state)
    cites = Citations(state["references"], allowed)
    out = _synthesis_for_report(state["synthesis"], cites, allowed)
    assert out["agreements"][0]["cite"] == "[1, p.1] [1, p.7]"
    assert len(cites.order) == 1


def test_report_rejects_partly_unresolvable_synthesis_citations():
    from agents.report import Citations, _synthesis_for_report, evidence_source_ids

    state = sample_state_after_eval()
    state.update(synthesis_node(state, client=client_for(state)))
    sid = source_of(state, finding(state)["evidence_b"][0])
    state["references"] = [ref for ref in state["references"] if ref["source_id"] != sid]
    allowed = evidence_source_ids(state)
    cites = Citations(state["references"], allowed)
    out = _synthesis_for_report(state["synthesis"], cites, allowed)
    assert out["agreements"] == []
    assert len(out["conflicts"]) == 1
