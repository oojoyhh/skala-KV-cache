"""Supervisor — 다음에 실행할 하위 노드를 State 규칙으로 고른다 (5번 담당).

기준: docs/AGENT_CONTRACT.md v2.2.3 — 1장 (라우팅), 2장 (ControlState), 4-1 (품질 조치 권고), 5장 (상한)

- 하위 노드는 모두 실행 후 supervisor로 돌아오고, supervisor만 다음 노드를 정한다.
- 라우팅은 규칙 기반이다. 같은 State면 같은 경로가 나온다 (LLM 미사용).
- 반환하는 `control`은 항상 전체 dict다. `control`에는 reducer가 없어 일부만 반환하면 통째로 덮어써진다.
- 결정 이력은 State에 쌓지 않는다. supervisor 노드 출력(next_node, route_reason)이 LangSmith에 매번 남는다.
"""

from __future__ import annotations

import copy

import config
from agents.check import evaluate_sufficiency
from state import (
    EVIDENCE_RETRY_NODES,
    PERSPECTIVE_NODE,
    PERSPECTIVES,
    TECHS,
    ControlState,
    State,
    SufficiencyCheck,
    perspective_evidence,
)

END = "END"  # graph.py가 langgraph END로 연결한다

# 관점 후보의 근거 개수가 같을 때만 쓰는 순서 (워크플로 순서가 아니라 재현성용 동점 처리)
TIE_BREAK_ORDER = EVIDENCE_RETRY_NODES
# 실행 재시도 상한에 닿으면 종료하는 마무리 노드
TERMINAL_NODES = ("synthesis", "report", "quality")
# QualityResult의 네 품질 항목 (route_reason에 미달 항목을 남길 때 쓴다)
QUALITY_METRICS = ("groundedness", "neutrality", "bias_control", "perspective_coverage")


# ---------------------------------------------------------------------------
# 후보 계산 · 동점 처리 (계약 1-3)
# ---------------------------------------------------------------------------
def evidence_count(state: State, node: str) -> int:
    """관점 노드가 맡은 관점의 근거 수(두 기술 합계). 관점이 둘이면 더 적은 쪽."""
    counts = [
        sum(len(perspective_evidence(state, p, tech)) for tech in TECHS)
        for p in PERSPECTIVES
        if PERSPECTIVE_NODE[p] == node
    ]
    return min(counts)


def candidate_nodes(state: State, control: ControlState, sufficiency: SufficiencyCheck | None = None) -> list[str]:
    """State로 관점 노드 후보를 만든다.

    sufficiency가 없으면 규칙 4(아직 실행 안 된 관점 노드),
    있으면 규칙 5(부족하고 skipped가 아니며 재조사 여유가 있는 관점 노드).
    """
    status = control["node_status"]
    if sufficiency is None:
        return [n for n in EVIDENCE_RETRY_NODES if n not in status]
    lacking = {PERSPECTIVE_NODE[p] for p in PERSPECTIVES if not sufficiency[p]}
    return [
        n for n in EVIDENCE_RETRY_NODES
        if n in lacking
        and status.get(n) != "skipped"
        and control["evidence_retry_counts"][n] < config.MAX_AGENT_RETRY
    ]


def tie_break(state: State, candidates: list[str]) -> str:
    """근거가 가장 적은 후보부터. 개수가 같으면 TIE_BREAK_ORDER 순."""
    return min(candidates, key=lambda n: (evidence_count(state, n), TIE_BREAK_ORDER.index(n)))


# ---------------------------------------------------------------------------
# 직전 실행 반영 (계약 1-1)
# ---------------------------------------------------------------------------
def reflect_last_run(state: State, control: ControlState) -> str | None:
    """직전에 부른 노드의 결과를 control에 반영하고 그 노드 이름을 돌려준다. 최초 진입이면 None."""
    node = control["next_node"]
    if node in ("", END):
        return None
    result = state.get("node_result")
    if (result and result.get("node") == node and result.get("dispatch_id") == control["dispatch_id"]
            and result.get("status") in ("success", "failed")):    # 계약 밖 status는 반환 누락과 같이 처리
        status, error = result["status"], result.get("error", "")
    else:
        status, error = "failed", f"E-1002 node_result 누락: {node}"
    control["node_status"][node] = status
    if status == "success" and node in control["stale"]:
        control["stale"].remove(node)
    if status == "failed":
        control["node_errors"][node] = error
        control["last_error"] = error
    control["step_count"] += 1
    return node


# ---------------------------------------------------------------------------
# 다음 노드 선택 (계약 1-2)
# ---------------------------------------------------------------------------
def _needs(state: State, control: ControlState, node: str, has_output: bool) -> bool:
    """실행이 필요한가: 다시 만들라고 표시됐거나(stale), 산출물이 없고 이미 성공·제외된 노드가 아닐 때."""
    status = control["node_status"].get(node)
    if node in control["stale"]:
        return status != "skipped"
    return not has_output and status not in ("success", "skipped")


def _has_output(state: State, node: str) -> bool:
    """마무리 노드의 산출물이 State에 있는가. quality는 결과를 이어 쓸 수 없으므로 항상 False."""
    if node == "synthesis":
        return bool(state.get("synthesis"))
    if node == "report":
        return state.get("report_version", 0) > 0
    return False


def _quality_stale(state: State) -> bool:
    quality = state.get("quality_result")
    return not quality or quality["evaluated_report_version"] != state.get("report_version", 0)


def _finalize(state: State, control: ControlState, last: str | None) -> tuple[str, str]:
    """마무리 모드 (F1~F5). 각 노드는 한 번만 시도한다."""
    tried = control["finalize_tried"]

    def can_try(node: str) -> bool:  # 마무리 모드에서도 skipped 노드는 다시 고르지 않는다 (계약 1-2)
        return node not in tried and control["node_status"].get(node) != "skipped"

    # F1: 마무리 시도 중 실패만 종료 사유다. 상한 직전 조사 노드 실패로 보고서 없이 끝나지 않게 한다.
    if last in tried and control["node_status"][last] == "failed":
        return END, f"실행 상한 도달 후 {last} 실패 → 종료"
    if (not state.get("synthesis") or "synthesis" in control["stale"]) and can_try("synthesis"):
        return "synthesis", "실행 상한 도달 → 마무리: 평가 종합 1회"
    if (state.get("report_version", 0) == 0 or "report" in control["stale"]) and can_try("report"):
        return "report", "실행 상한 도달 → 마무리: 보고서 1회"
    if state.get("report_version", 0) > 0 and _quality_stale(state) and can_try("quality"):
        return "quality", "실행 상한 도달 → 마무리: 품질 평가 1회"
    return END, "실행 상한 도달 → 종료"


def _quality_hint(sufficiency: SufficiencyCheck, target: str, feedback: list[str]) -> SufficiencyCheck:
    """품질 기반 재조사 사유를 sufficiency.reasons로 넘긴다. 관점 노드는 기존 retry_hint로 읽는다."""
    suff = copy.deepcopy(sufficiency)
    why = "품질 평가: " + ("; ".join(feedback) or "근거 보강 필요")
    for p in PERSPECTIVES:
        if PERSPECTIVE_NODE[p] == target:
            suff[p] = False
            suff["reasons"][p] = why
    return suff


def _quality_findings(quality) -> str:
    """미달 항목과 첫 사유: "perspective_coverage: InfiniGen 시장성 미확보; neutrality: ..." """
    items = [f"{name}: {(quality[name]['reasons'] or ['사유 없음'])[0]}"
             for name in QUALITY_METRICS if not quality[name]["passed"]]
    if not quality["page_limit_passed"]:
        items.append(f"분량: {quality['page_count']}쪽 > {config.MAX_REPORT_PAGES}쪽")
    if not quality["required_sections_passed"]:
        items.append("필수 목차 누락")
    return "; ".join(items) or "; ".join(quality["feedback"]) or "사유 없음"


def _quality_advice(quality) -> tuple[str, str]:
    """품질 노드의 권고 문구와 재조사 대상."""
    target = quality.get("target_node", "")
    if quality.get("action") == "research":
        return f"품질 평가 권고: {target or '?'} 추가 조사", target
    return "품질 평가 권고: 재작성", ""


def _research_blocked(quality, control: ControlState) -> str:
    """재조사 권고를 따를 수 없는 이유. 따를 수 있으면 ""."""
    target = quality.get("target_node", "")
    if target not in EVIDENCE_RETRY_NODES:
        return f"재조사 대상 아님({target or '미지정'})"
    if control["node_status"].get(target) == "skipped":
        return f"{target} 실행 제외됨(skipped)"
    if control["quality_research_count"] >= config.MAX_QUALITY_RESEARCH:
        return f"기회 소진({control['quality_research_count']}/{config.MAX_QUALITY_RESEARCH})"
    return ""


def decide(state: State) -> tuple[str, ControlState, SufficiencyCheck | None]:
    """State를 보고 (다음 노드, 갱신된 control 전체, 새 sufficiency 또는 None)을 돌려준다. State는 바꾸지 않는다."""
    control: ControlState = copy.deepcopy(state["control"])
    last = reflect_last_run(state, control)
    last_failed = last is not None and control["node_status"][last] == "failed"
    sufficiency: SufficiencyCheck | None = None

    def choose(node: str, reason: str) -> tuple[str, ControlState, SufficiencyCheck | None]:
        control["route_reason"] = reason
        if node == END:
            control["next_node"] = END
            return END, control, sufficiency
        control["next_node"] = node
        control["dispatch_id"] = control["step_count"]
        return node, control, sufficiency

    # 마무리 모드
    if control["step_count"] >= config.MAX_TOTAL_STEPS:
        control["run_status"] = "exhausted"
        # 직전 재조사·품질 힌트 이후 State가 바뀌었을 수 있으므로 충분성을 다시 평가해 synthesis가 최신 사유를 읽게 한다
        sufficiency = evaluate_sufficiency(state)
        node, reason = _finalize(state, control, last)
        if node != END:
            control["finalize_tried"].append(node)
        return choose(node, reason)

    # 1·2. 직전 노드 실행 실패
    if last_failed:
        error = control["node_errors"].get(last, "")
        if control["exec_retry_counts"][last] < config.MAX_EXEC_RETRY:
            control["exec_retry_counts"][last] += 1
            return choose(last, f"{last} 실행 실패({error}) → 재시도 {control['exec_retry_counts'][last]}/{config.MAX_EXEC_RETRY}")
        control["node_status"][last] = "skipped"
        # synthesis·report는 실패해도 쓸 수 있는 산출물이 State에 있으면 계속 진행해 보고서를 남긴다.
        if last in TERMINAL_NODES and not _has_output(state, last):
            control["run_status"] = "exhausted"
            return choose(END, f"{last} 실행 재시도 상한 도달, 산출물 없음 → 종료")

    # 3. 기술 조사
    if _needs(state, control, "research", bool(state.get("tech_summary"))):
        return choose("research", "기술 조사 결과 없음 → research")

    # 4. 아직 실행 안 된 관점 노드
    unrun = candidate_nodes(state, control)
    if unrun:
        node = tie_break(state, unrun)
        return choose(node, f"미실행 관점 노드 {unrun} 중 근거 최소 → {node}")

    # 5. 근거 부족 관점 재조사 (supervisor가 충분성 평가)
    sufficiency = evaluate_sufficiency(state)
    lacking = candidate_nodes(state, control, sufficiency)
    if lacking:
        node = tie_break(state, lacking)
        control["evidence_retry_counts"][node] += 1
        why = "; ".join(sufficiency["reasons"].get(p, "") for p in PERSPECTIVES if PERSPECTIVE_NODE[p] == node and not sufficiency[p])
        return choose(node, f"{node} 근거 부족({why}) → 재조사 {control['evidence_retry_counts'][node]}/{config.MAX_AGENT_RETRY}")

    # 6. 평가 종합
    if _needs(state, control, "synthesis", bool(state.get("synthesis"))):
        missing = [p for p in PERSPECTIVES if not sufficiency[p]]
        reason = "모든 관점 근거 충분" if not missing else f"재조사 상한 도달, 부족 관점 {missing} 한계로 기록"
        return choose("synthesis", f"{reason} → synthesis")

    # 7. 보고서
    if _needs(state, control, "report", state.get("report_version", 0) > 0):
        return choose("report", "평가 종합 완료 → report")

    # 8. 품질 평가 (보고서 버전이 바뀌면 다시 평가)
    if _quality_stale(state) and control["node_status"].get("quality") != "skipped":
        return choose("quality", f"보고서 v{state.get('report_version', 0)} 품질 평가 필요 → quality")

    quality = state.get("quality_result")
    if quality and not quality["passed"]:
        found = _quality_findings(quality)
        advice, target = _quality_advice(quality)
        q_used = control["quality_research_count"]

        # 9a. 근거 자체 부족 → 품질 평가가 권고한 관점 노드 재조사 (1-4)
        blocked = _research_blocked(quality, control)
        if quality.get("action") == "research" and not blocked:
            control["quality_research_count"] += 1
            control["stale"] = ["synthesis", "report"]
            sufficiency = _quality_hint(sufficiency, target, quality["feedback"])
            return choose(target, f"품질 미달({found}) — {advice}, 기회 {q_used}/{config.MAX_QUALITY_RESEARCH} "
                                  f"→ {target} 재조사 {control['quality_research_count']}/{config.MAX_QUALITY_RESEARCH}")

        # 9b. 보고서 재작성 (재조사를 고르지 않은 이유를 함께 남긴다)
        judged = f"{advice}, {blocked}" if quality.get("action") == "research" else advice
        if control["node_status"].get("report") == "skipped":  # skipped 노드는 다시 고르지 않는다
            control["run_status"] = "exhausted"
            return choose(END, f"품질 미달({found}) — {judged}, report 실행 제외됨(skipped) → 종료")
        if control["report_retry_count"] < config.MAX_REPORT_RETRY:
            control["report_retry_count"] += 1
            return choose("report", f"품질 미달({found}) — {judged} → 보고서 재작성 "
                                    f"{control['report_retry_count']}/{config.MAX_REPORT_RETRY}")

        # 10. 미달 종료
        control["run_status"] = "exhausted"
        return choose(END, f"품질 미달({found}) — {judged}, 재작성 기회 소진"
                           f"({control['report_retry_count']}/{config.MAX_REPORT_RETRY}) → 종료")

    # 10. 통과 종료
    control["run_status"] = "completed"
    return choose(END, "품질 평가 통과 → 종료")


# ---------------------------------------------------------------------------
# 그래프용
# ---------------------------------------------------------------------------
def supervisor_node(state: State) -> dict:
    """control 전체(와 새로 평가한 sufficiency)를 반환한다."""
    _, control, sufficiency = decide(state)
    update: dict = {"control": control}
    if sufficiency is not None:
        update["sufficiency"] = sufficiency
    return update


def route(state: State) -> str:
    """add_conditional_edges용: supervisor가 정한 next_node (END 포함)."""
    return state["control"]["next_node"]
