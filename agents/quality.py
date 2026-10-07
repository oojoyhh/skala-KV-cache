"""✅ 품질 평가 에이전트 (6번) — 생성된 보고서를 Hybrid(규칙 + LLM Judge)로 평가한다.

계약(AGENT_CONTRACT v2.2.1 §4):
- Quality는 보고서를 네 항목으로 평가하고, 미달이면 원인에 따라 조치를 권고한다(`action`, `target_node`).
  다음 노드는 supervisor가 정한다. Quality는 control을 건드리지 않는다.
  - `rewrite`: 근거는 State에 있는데 보고서가 잘못 쓰거나 표시하지 않은 경우(표현·인용 연결·중립성·목차·분량)
  - `research`: State 자체에 그 관점 근거가 없거나 한쪽 stance·단일 출처로 쏠려 보고서 수정으로 해결할 수 없는 경우
- 근거 최소 기준 판정은 supervisor의 `evaluate_sufficiency()`가 맡는다. 여기의 `research`는 그 기준은 통과했지만
  보고서 품질 관점에서 부족이 드러난 경우의 추가 조사 요청이다.
- 평가 대상 절은 3장·4-1~4-4·5장. 코드가 쓰는 고정 문단(6장, 미달 사유·수집 오류 목록, REFERENCE 설명)은 제외된다.
- 규칙 검사는 "인용이 REFERENCE와 연결되는가"까지만 보장한다. 출처가 주장을 실제로 뒷받침하는지는 Judge가 본다.
"""

import os
import re
from typing import Any

from pydantic import BaseModel, Field

import config
import llm
from agents.report import CITE_GROUP, CITE_ITEM, RECOMMEND, _doc_id
from state import PERSPECTIVE_FIELDS, TECHS, perspective_evidence

PERSPECTIVE_SECTIONS = {            # 보고서 절 → State 관점 키
    "4-1": "trl", "4-2": "market", "4-3": "stakeholder", "4-4": "domain",
}
SECTION_OF = {perspective: section for section, perspective in PERSPECTIVE_SECTIONS.items()}
PERSPECTIVE_NODE = {"trl": "market", "market": "market", "stakeholder": "stakeholder", "domain": "domain"}
TARGET_SECTIONS = ("3", "4-1", "4-2", "4-3", "4-4", "5")   # 사실 주장 절 (평가 대상)
JUDGE_SECTIONS = ("SUMMARY", "4-1", "4-2", "4-3", "4-4", "5")
REQUIRED_SECTIONS = ("SUMMARY", "REFERENCE")
METRICS = ("groundedness", "neutrality", "bias_control", "perspective_coverage")
NO_EVIDENCE_MARKS = ("공개 근거 미확인", "근거를 확보하지 못했다", "해당 없음", "근거 없음", "도출되지 않았다")


class JudgeVerdict(BaseModel):
    """LLM Judge 1회 호출로 네 항목을 함께 판정한다 (계약 §4-2)."""

    groundedness: bool = Field(description="인용된 문장이 해당 근거(claim)와 의미상 맞는가")
    groundedness_reason: str
    neutrality: bool = Field(description="특정 기술 추천·우열 판정·우위 암시가 없는가")
    neutrality_reason: str
    bias_control: bool = Field(description="서술이 한쪽 근거로 기울지 않았는가")
    bias_control_reason: str
    perspective_coverage: bool = Field(description="각 관점을 형식적 한 줄이 아니라 실질적으로 평가했는가")
    perspective_coverage_reason: str


# ---------------------------------------------------------------------------
# 보고서 Markdown 파싱
# ---------------------------------------------------------------------------
def split_sections(markdown: str) -> dict[str, str]:
    """'## 3. 기술 개요' → {"3": 본문}. 키는 제목 앞 번호에서 점을 뗀 값(SUMMARY·REFERENCE는 그대로)."""
    sections: dict[str, str] = {}
    key, buf = None, []
    for line in markdown.splitlines():
        heading = re.match(r"^#{2,3}\s+(.+)", line)
        if heading:
            if key:
                sections[key] = "\n".join(buf).strip()
            title = heading[1].strip()
            head = title.split()[0]
            key = head.rstrip(".") if head[0].isdigit() else title
            buf = []
        elif key:
            buf.append(line)
    if key:
        sections[key] = "\n".join(buf).strip()
    return sections


def reference_numbers(reference_section: str) -> dict[int, str]:
    """REFERENCE 절 → {번호: 줄 내용}."""
    return {int(m[1]): m[2].strip() for m in re.finditer(r"^\[(\d+)\]\s+(.*)$", reference_section, re.M)}


def cited_numbers(text: str) -> list[int]:
    return [int(n) for g in CITE_GROUP.findall(text) for n, _ in CITE_ITEM.findall(g)]


def number_to_docs(refs_in_report: dict[int, str], references: list[dict]) -> dict[int, str]:
    """REFERENCE 번호 → State 출처의 문서 id. URL(웹)·제목(논문)으로 맞춘다."""
    out: dict[int, str] = {}
    for number, line in refs_in_report.items():
        for ref in references:
            url, title = (ref.get("url") or "").strip(), (ref.get("title") or "").strip()
            if (url and url in line) or (title and title[:40] in line):
                out[number] = _doc_id(ref["source_id"])
                break
    return out


# ---------------------------------------------------------------------------
# 규칙 검사
# ---------------------------------------------------------------------------
def _has_no_evidence_mark(text: str) -> bool:
    return any(mark in text for mark in NO_EVIDENCE_MARKS)


def check_required_sections(sections: dict[str, str]) -> tuple[bool, list[str]]:
    """필수 목차 형식: SUMMARY 절과 REFERENCE 절이 있어야 한다 (계약 §4-2)."""
    missing = [name for name in REQUIRED_SECTIONS if not (sections.get(name) or "").strip()]
    return not missing, [f"필수 목차 누락: {name}" for name in missing]


def check_groundedness(sections: dict[str, str], refs_in_report: dict[int, str]) -> tuple[bool, list[str]]:
    """대상 절의 인용이 모두 REFERENCE와 연결되고, 근거 서술 절에 인용이 있는지."""
    reasons = []
    for name in TARGET_SECTIONS:
        text = sections.get(name)
        if text is None:
            reasons.append(f"{name} 절이 보고서에 없음")
            continue
        unknown = sorted({n for n in cited_numbers(text) if n not in refs_in_report})
        if unknown:
            reasons.append(f"{name} 절의 인용 {unknown}이 REFERENCE에 없음")
        if not cited_numbers(text) and not _has_no_evidence_mark(text):
            reasons.append(f"{name} 절에 인용이 하나도 없음")
    return not reasons, reasons


def check_perspective_coverage(sections: dict[str, str], state: dict) -> tuple[bool, list[str]]:
    """4관점 × 2기술 각각에 서술이 있거나 '공개 근거 미확인'이 명시됐는지."""
    reasons = []
    for section, perspective in PERSPECTIVE_SECTIONS.items():
        text = sections.get(section)
        if not text:
            reasons.append(f"{section} 절이 비어 있음")
            continue
        for tech in TECHS:
            if tech in text:
                continue
            if perspective_evidence(state, perspective, tech):
                reasons.append(f"{section} 절에 {tech} 서술이 없음 (근거는 있음)")
            elif not _has_no_evidence_mark(text):
                reasons.append(f"{section} 절에 {tech}의 근거 미확보 표시가 없음")
    return not reasons, reasons


def check_neutrality(sections: dict[str, str]) -> tuple[bool, list[str]]:
    """추천·우열 판정 어휘가 남아 있는지 (보고서 생성 단계에서 1차로 제거하지만 최종 확인)."""
    reasons = []
    for name in ("SUMMARY", *TARGET_SECTIONS):
        for line in (sections.get(name) or "").splitlines():
            if RECOMMEND.search(line):
                reasons.append(f"{name} 절에 우열·추천 표현: {line.strip()[:60]}")
    return not reasons, reasons


def check_bias_control(sections: dict[str, str], state: dict, num_to_doc: dict[int, str]) -> tuple[bool, list[str]]:
    """한계·반론 근거가 있는데 인용하지 않았거나, 출처가 여럿인데 하나에만 몰렸는지."""
    reasons = []
    for section, perspective in PERSPECTIVE_SECTIONS.items():
        text = sections.get(section) or ""
        cited_docs = {num_to_doc[n] for n in cited_numbers(text) if n in num_to_doc}
        for tech in TECHS:
            evidence = perspective_evidence(state, perspective, tech)
            negative = {_doc_id(e["source_id"]) for e in evidence if e.get("stance") == "negative"}
            if negative and not (negative & cited_docs):
                reasons.append(f"{section}/{tech}: 한계·반론 근거 {len(negative)}건이 있으나 인용되지 않음")
            docs = {_doc_id(e["source_id"]) for e in evidence}
            if len(docs) >= 2 and len(docs & cited_docs) == 1:
                reasons.append(f"{section}/{tech}: 확보된 출처 {len(docs)}건 중 1건만 인용됨")
    return not reasons, reasons


# ---------------------------------------------------------------------------
# 조치 권고 (계약 §4-1)
# ---------------------------------------------------------------------------
def state_gaps(state: dict) -> dict[str, tuple[int, str]]:
    """보고서 수정으로는 못 고치는 State 근거의 결함. 관점 → (근거 수, 사유)."""
    gaps: dict[str, tuple[int, str]] = {}
    for perspective in PERSPECTIVE_FIELDS:
        total, problems = 0, []
        for tech in TECHS:
            evidence = perspective_evidence(state, perspective, tech)
            total += len(evidence)
            if not evidence:
                problems.append(f"{tech} 근거 없음")
                continue
            if not any(e.get("stance") == "negative" for e in evidence):
                problems.append(f"{tech} 한계·반론 근거 없음")
            if len({_doc_id(e["source_id"]) for e in evidence}) < 2:
                problems.append(f"{tech} 출처 1건")
        if problems:
            gaps[perspective] = (total, ", ".join(problems))
    return gaps


def failing_perspectives(result: dict) -> set[str]:
    """편향·커버리지 미달 사유에 적힌 절 번호 → 관점."""
    found = set()
    for item in ("bias_control", "perspective_coverage"):
        for reason in result[item]["reasons"]:
            section = reason.split("/")[0].split()[0]
            if section in PERSPECTIVE_SECTIONS:
                found.add(PERSPECTIVE_SECTIONS[section])
    return found


def recommend(result: dict, state: dict) -> tuple[str, str, list[str]]:
    """(action, target_node, 추가 feedback). 두 원인이 섞이면 research를 우선한다 (계약 §4-1)."""
    if result["passed"]:
        return "pass", "", []
    gaps = {p: g for p, g in state_gaps(state).items() if p in failing_perspectives(result)}
    if not gaps:
        return "rewrite", "", []
    perspective = min(gaps, key=lambda p: (gaps[p][0], p))       # 근거가 가장 적은 관점
    count, why = gaps[perspective]
    node = PERSPECTIVE_NODE[perspective]
    return "research", node, [
        f"[추가 조사] {SECTION_OF[perspective]} {perspective}: {why} (현재 근거 {count}건) → {node} 재조사 권고"
    ]


# ---------------------------------------------------------------------------
# LLM Judge
# ---------------------------------------------------------------------------
def judge_input(sections: dict[str, str], state: dict, num_to_doc: dict[int, str]) -> str:
    """Judge 입력: SUMMARY·4-1~4-4·5장 본문 + 그 절이 인용한 Evidence claim (전문 금지)."""
    body = "\n\n".join(f"## {name}\n{sections.get(name, '')}" for name in JUDGE_SECTIONS)
    cited_docs = {num_to_doc[n] for n in cited_numbers(body) if n in num_to_doc}
    claims = []
    for perspective in PERSPECTIVE_FIELDS:
        for tech in TECHS:
            for e in perspective_evidence(state, perspective, tech):
                if _doc_id(e["source_id"]) in cited_docs:
                    claims.append(f"- [{perspective}/{tech}] ({e.get('stance')}) {e['claim']}")
    return f"{body}\n\n## 보고서가 인용한 근거 목록\n" + ("\n".join(claims[:60]) or "(없음)")


def run_judge(sections: dict[str, str], state: dict, num_to_doc: dict[int, str], judge_fn=None) -> JudgeVerdict:
    prompt = llm.load_prompt("quality") + "\n\n" + judge_input(sections, state, num_to_doc)
    if judge_fn:
        return judge_fn(prompt)
    return llm.structured(prompt, JudgeVerdict, role="judge")


# ---------------------------------------------------------------------------
# 노드
# ---------------------------------------------------------------------------
def _metric(rule: tuple[bool, list[str]], judge_ok: bool | None, judge_reason: str) -> dict:
    rule_passed, reasons = rule
    if judge_ok is False and judge_reason:
        reasons = [*reasons, f"Judge: {judge_reason}"]
    return {
        "rule_passed": rule_passed,
        "judge_passed": judge_ok,
        "passed": rule_passed and judge_ok is not False,
        "reasons": reasons,
    }


def page_count(pdf_path: str) -> int:
    """PDF 쪽수. 못 세면 0을 돌려주고, 통과로 처리하지 않는다."""
    try:
        import pymupdf

        with pymupdf.open(pdf_path) as doc:
            return doc.page_count
    except Exception:  # noqa: BLE001 — 쪽수는 부가 정보. 실패해도 나머지 평가는 계속한다
        return 0


def evaluate_report(state: dict, judge_fn=None) -> dict:
    """보고서 Markdown·PDF와 State 근거로 QualityResult를 만든다."""
    with open(state["report_md_path"], encoding="utf-8") as f:
        markdown = f.read()
    sections = split_sections(markdown)
    refs_in_report = reference_numbers(sections.get("REFERENCE", ""))
    num_to_doc = number_to_docs(refs_in_report, state.get("references", []))

    verdict = run_judge(sections, state, num_to_doc, judge_fn)
    pages = page_count(state.get("report_path", ""))
    required_passed, required_reasons = check_required_sections(sections)

    result = {
        "groundedness": _metric(check_groundedness(sections, refs_in_report),
                                verdict.groundedness, verdict.groundedness_reason),
        "neutrality": _metric(check_neutrality(sections), verdict.neutrality, verdict.neutrality_reason),
        "bias_control": _metric(check_bias_control(sections, state, num_to_doc),
                                verdict.bias_control, verdict.bias_control_reason),
        "perspective_coverage": _metric(check_perspective_coverage(sections, state),
                                        verdict.perspective_coverage, verdict.perspective_coverage_reason),
        "page_count": pages,
        "page_limit_passed": 0 < pages <= config.MAX_REPORT_PAGES,
        "required_sections_passed": required_passed,
        "evaluated_report_version": state.get("report_version", 0),
    }
    result["passed"] = (all(result[m]["passed"] for m in METRICS)
                        and result["page_limit_passed"] and required_passed)

    feedback = [f"[{m}] {reason}" for m in METRICS for reason in result[m]["reasons"]]
    feedback += [f"[필수 목차] {reason}" for reason in required_reasons]
    if pages == 0:
        feedback.append("[분량] PDF 쪽수 확인 불가 (파일이 없거나 열 수 없음)")
    elif pages > config.MAX_REPORT_PAGES:
        feedback.append(f"[분량] {pages}쪽으로 상한 {config.MAX_REPORT_PAGES}쪽을 넘음. 서술을 줄일 것")

    action, target_node, extra = recommend(result, state)
    result["action"], result["target_node"] = action, target_node
    result["feedback"] = feedback + extra
    return result


def quality_node(state: dict, judge_fn=None) -> dict[str, Any]:
    """계약 §3: quality_result + node_result만 반환한다. control은 읽기만 한다.

    예외를 밖으로 던지지 않는다(§3-2). 오류 원문은 비밀값이 섞일 수 있어 싣지 않는다(AGENTS 규칙 11).
    """
    dispatch_id = (state.get("control") or {}).get("dispatch_id", 0)
    node_result = {"node": "quality", "dispatch_id": dispatch_id, "status": "success", "error": ""}

    def failed(reason: str) -> dict[str, Any]:
        return {"node_result": {**node_result, "status": "failed", "error": f"E-1002 {reason}"}}

    try:
        if not os.path.exists(state.get("report_md_path") or ""):
            return failed("품질 평가 실패: 보고서 Markdown 없음")
        result = evaluate_report(state, judge_fn)
    except llm.LLMError as exc:
        return failed(f"품질 Judge 호출 실패 ({type(exc).__name__})")
    except Exception as exc:  # noqa: BLE001 — 계약 §3: 어떤 예외도 그래프 밖으로 던지지 않는다
        return failed(f"품질 평가 실패 ({type(exc).__name__})")
    return {"quality_result": result, "node_result": node_result}
