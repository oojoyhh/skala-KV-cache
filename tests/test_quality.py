"""품질 평가 노드 테스트 (6번). 실행: python -m pytest tests/test_quality.py

LLM Judge는 가짜 함수로 주입한다. API 키 없이 동작한다.
"""

import os

import config
import llm
from agents import quality
from agents.quality import JudgeVerdict, quality_node
from agents.report import report_node
from tests.fixtures import sample_state_after_eval


def realistic_generate(prompt: str) -> str:
    """두 기술을 모두 언급하고 입력 근거를 인용하는 가짜 LLM (실제 보고서 문체에 가깝게)."""
    import re

    cites = " ".join(sorted(set(re.findall(r'"cite": "(\[[^"]+\])"', prompt))))
    return f"TurboQuant와 InfiniGen 모두 공개 근거에서 보고된 내용이 확인된다 {cites}.".strip()


def judge(ok: bool = True, reason: str = "확인함"):
    def _judge(prompt: str) -> JudgeVerdict:
        _judge.prompt = prompt
        return JudgeVerdict(groundedness=ok, groundedness_reason=reason, neutrality=ok, neutrality_reason=reason,
                            bias_control=ok, bias_control_reason=reason,
                            perspective_coverage=ok, perspective_coverage_reason=reason)
    return _judge


def failing_judge(prompt: str):
    raise llm.LLMError("[E-1002] Judge 호출 실패")


def built_state(tmp_path, monkeypatch, sufficient=True):
    """실제 보고서를 만들고, 품질 평가 입력이 갖춰진 State를 돌려준다."""
    monkeypatch.setattr(config, "REPORT_PATH", str(tmp_path / config.REPORT_FILENAME))
    state = sample_state_after_eval(sufficient)
    state["control"] = {"dispatch_id": 7, "node_errors": {}}
    return {**state, **report_node(state, generate_fn=realistic_generate)}


def test_generated_report_passes_rules(tmp_path, monkeypatch):
    out = quality_node(built_state(tmp_path, monkeypatch), judge_fn=judge(True))
    result = out["quality_result"]
    assert out["node_result"] == {"node": "quality", "dispatch_id": 7, "status": "success", "error": ""}
    assert result["evaluated_report_version"] == 1
    for item in ("groundedness", "neutrality", "bias_control", "perspective_coverage"):
        assert result[item]["rule_passed"], (item, result[item]["reasons"])
    assert result["passed"] and result["feedback"] == []


def test_judge_failure_fails_the_item_and_report_feedback(tmp_path, monkeypatch):
    out = quality_node(built_state(tmp_path, monkeypatch), judge_fn=judge(False, "SUMMARY에서 우위 암시"))
    result = out["quality_result"]
    assert result["neutrality"]["rule_passed"] and result["neutrality"]["judge_passed"] is False
    assert result["neutrality"]["passed"] is False and result["passed"] is False
    assert any("Judge: SUMMARY에서 우위 암시" in f for f in result["feedback"])


def test_judge_call_failure_returns_failed_node_result(tmp_path, monkeypatch):
    out = quality_node(built_state(tmp_path, monkeypatch), judge_fn=failing_judge)
    assert "quality_result" not in out                      # 계약 §3: 실패 시 node_result만
    assert out["node_result"]["status"] == "failed" and "E-1002" in out["node_result"]["error"]


def test_missing_report_is_failed_not_exception():
    out = quality_node({"control": {"dispatch_id": 3}, "report_md_path": "outputs/없는파일.md"}, judge_fn=judge())
    assert out["node_result"] == {"node": "quality", "dispatch_id": 3, "status": "failed",
                                  "error": out["node_result"]["error"]}
    assert "E-1002" in out["node_result"]["error"]


def test_fixed_paragraphs_are_not_judged_as_missing_citations(tmp_path, monkeypatch):
    """6장 방법론·미달 사유·수집 오류·REFERENCE 설명은 인용이 없어도 Groundedness 미달이 아니다."""
    state = built_state(tmp_path, monkeypatch, sufficient=False)
    markdown = open(state["report_md_path"], encoding="utf-8").read()
    sections = quality.split_sections(markdown)
    assert "충분성 검사 미달 사유" in markdown                  # 고정 문단이 실제로 보고서에 있음
    assert "6" in sections and not quality.cited_numbers(sections["6"])
    passed, reasons = quality.check_groundedness(sections, quality.reference_numbers(sections["REFERENCE"]))
    assert passed, reasons                                   # 평가 대상 절은 3·4-x·5장뿐


def test_rules_catch_broken_citation_and_recommendation():
    sections = {
        "3": "TurboQuant는 압축률이 높다 [99].",
        "4-1": "TRL은 3으로 추정된다 [1].", "4-2": "시장 반응이 있었다 [1].",
        "4-3": "개발자 반응이 있었다 [1].", "4-4": "비용 근거가 있다 [1].",
        "5": "메모리 최소화에는 TurboQuant가 더 적합하다 [1].",
        "SUMMARY": "- 요약 [1]",
    }
    refs = {1: "A(2026-01-01). T. Site, https://a"}
    grounded, reasons = quality.check_groundedness(sections, refs)
    assert not grounded and any("[99]" in r for r in reasons)
    neutral, neutral_reasons = quality.check_neutrality(sections)
    assert not neutral and any("더 적합" in r for r in neutral_reasons)


def test_coverage_accepts_declared_missing_evidence():
    sections = {"4-1": "TurboQuant는 TRL 3이다 [1]. InfiniGen은 공개 근거 미확인이다.",
                "4-2": "TurboQuant 시장 근거 [1]. InfiniGen 공개 근거 미확인.",
                "4-3": "TurboQuant [1]. InfiniGen 공개 근거 미확인.",
                "4-4": "TurboQuant [1]. InfiniGen 공개 근거 미확인."}
    state = {"trl_result": {}, "market_result": {}, "stakeholder_result": {}, "domain_result": {}}
    passed, reasons = quality.check_perspective_coverage(sections, state)
    assert passed, reasons


def test_bias_rule_flags_unused_negative_evidence():
    sections = {"4-3": "InfiniGen은 성능 개선이 보고됐다 [1]."}
    state = {"stakeholder_result": {"InfiniGen": {
        "competitors": [{"claim": "지지", "source_id": "web:aaa", "stance": "positive"},
                        {"claim": "한계", "source_id": "web:bbb", "stance": "negative"}]}}}
    num_to_doc = {1: "web:aaa"}
    passed, reasons = quality.check_bias_control(sections, state, num_to_doc)
    assert not passed and any("한계·반론 근거" in r for r in reasons)


def test_judge_input_is_limited_to_target_sections(tmp_path, monkeypatch):
    j = judge(True)
    quality_node(built_state(tmp_path, monkeypatch), judge_fn=j)
    prompt = j.prompt
    assert "## SUMMARY" in prompt and "## 5\n" in prompt
    assert "## 1\n" not in prompt and "## REFERENCE" not in prompt    # 전문 투입 금지
    assert "보고서가 인용한 근거 목록" in prompt


def test_required_sections_and_page_limit(tmp_path, monkeypatch):
    """필수 목차(SUMMARY·REFERENCE)가 없거나 쪽수를 못 세면 통과시키지 않는다 (계약 §4-2)."""
    md = tmp_path / "report.md"
    md.write_text("# 보고서\n\n## 3. 기술 개요\n본문 [1].\n", encoding="utf-8")
    state = {"report_md_path": str(md), "report_path": str(tmp_path / "없는파일.pdf"), "references": []}
    result = quality.evaluate_report(state, judge_fn=judge(True))
    assert result["required_sections_passed"] is False and result["passed"] is False
    assert result["page_count"] == 0 and result["page_limit_passed"] is False
    assert any("필수 목차 누락: SUMMARY" in f for f in result["feedback"])
    assert any("쪽수 확인 불가" in f for f in result["feedback"])


def test_action_rewrite_when_evidence_exists(tmp_path, monkeypatch):
    """근거는 State에 있는데 보고서 서술이 문제면 rewrite (계약 §4-1)."""
    out = quality_node(built_state(tmp_path, monkeypatch), judge_fn=judge(False, "SUMMARY에서 우위 암시"))
    result = out["quality_result"]
    assert result["passed"] is False
    assert result["action"] == "rewrite" and result["target_node"] == ""


def test_action_research_targets_weakest_perspective():
    """State 근거 자체가 비었거나 쏠려 있으면 research + 근거가 가장 적은 관점 (계약 §4-1)."""
    state = {
        "stakeholder_result": {"TurboQuant": {"competitors": [
            {"claim": "지지", "source_id": "web:a", "stance": "positive"},
            {"claim": "한계", "source_id": "web:b", "stance": "negative"}]}, "InfiniGen": {}},
        "domain_result": {t: {"cost": [{"claim": "c1", "source_id": f"web:{t}1", "stance": "positive"},
                                       {"claim": "c2", "source_id": f"web:{t}2", "stance": "positive"}]}
                          for t in ("TurboQuant", "InfiniGen")},   # 근거 4건(한계·반론 없음)
    }
    result = {
        "passed": False,
        "bias_control": {"reasons": ["4-4/InfiniGen: 한계·반론 근거 1건이 있으나 인용되지 않음"]},
        "perspective_coverage": {"reasons": ["4-3 절에 InfiniGen의 근거 미확보 표시가 없음"]},
    }
    action, target, extra = quality.recommend(result, state)
    assert action == "research"
    assert target == "stakeholder"            # 근거 2건 < 도메인 4건 → 가장 적은 관점
    assert extra and "재조사 권고" in extra[0]


def test_action_pass_when_everything_ok(tmp_path, monkeypatch):
    out = quality_node(built_state(tmp_path, monkeypatch), judge_fn=judge(True))
    assert out["quality_result"]["action"] == "pass" and out["quality_result"]["target_node"] == ""


def test_unexpected_exception_becomes_failed(tmp_path, monkeypatch):
    """계약 §3: 어떤 예외도 그래프 밖으로 던지지 않는다."""
    monkeypatch.setattr(quality, "evaluate_report", lambda *a, **k: (_ for _ in ()).throw(TypeError("boom")))
    out = quality_node(built_state(tmp_path, monkeypatch), judge_fn=judge(True))
    assert out["node_result"]["status"] == "failed" and "TypeError" in out["node_result"]["error"]
    assert "quality_result" not in out


def test_error_message_does_not_leak_exception_text(tmp_path, monkeypatch):
    def leaking_judge(prompt):
        raise llm.LLMError("[E-1002] Incorrect API key provided: sk-proj-SECRET")

    out = quality_node(built_state(tmp_path, monkeypatch), judge_fn=leaking_judge)
    error = out["node_result"]["error"]
    assert out["node_result"]["status"] == "failed"
    assert "sk-proj" not in error and "API key" not in error and "LLMError" in error
