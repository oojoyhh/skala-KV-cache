"""계약 Freeze v2.2.1의 State 기반 라우팅·재작업·종료를 검증한다.

외부 API나 Judge는 호출하지 않는다. 계약 불일치는 실패로 남기며,
테스트를 통과시키기 위해 설정이나 담당 외 구현을 변경하지 않는다.
"""

import copy

import pytest

import config
from graph import build_graph
from orchestration.supervisor import (
    END,
    candidate_nodes,
    decide,
    evidence_count,
    route,
    supervisor_node,
    tie_break,
)
from state import (
    EVIDENCE_RETRY_NODES,
    NODES,
    PERSPECTIVE_FIELDS,
    PERSPECTIVE_NODE,
    TECHS,
    NodeName,
    State,
    make_initial_state,
    retry_hint,
)
from tests.fixtures import (
    DUMMY_NODES,
    sample_state_after_eval,
    sample_state_after_research,
)


# ---------------------------------------------------------------------------
# 1. 테스트용 State — 공용 타입·샘플을 재사용하고 실행 이력만 구성
# ---------------------------------------------------------------------------
def _evaluated_state() -> State:
    """조사·보고서·품질 평가가 모두 성공한 State를 구성한다."""
    state = sample_state_after_eval()
    state["synthesis"] = DUMMY_NODES["synthesis"](state)["synthesis"]
    state["report_version"] = 1
    state["quality_result"] = DUMMY_NODES["quality"](state)["quality_result"]
    state["control"]["node_status"] = {node: "success" for node in NODES}
    return state


def _last_result(state: State, node: NodeName, *, failed=False) -> None:
    """직전 dispatch와 그 실행 결과를 같은 식별자로 설정한다."""
    control = state["control"]
    control["next_node"] = node
    control["dispatch_id"] = control["step_count"]
    state["node_result"] = {
        "node": node,
        "dispatch_id": control["dispatch_id"],
        "status": "failed" if failed else "success",
        "error": "E-1002 테스트용 실행 실패" if failed else "",
    }


def _clear_evidence(state: State, perspective: str) -> None:
    """한 관점의 두 기술 근거만 비워 부족 상태를 구성한다."""
    key, fields = PERSPECTIVE_FIELDS[perspective]
    for tech in TECHS:
        for field in fields:
            state[key][tech][field] = []


def _quality_failure(state: State, *, action="rewrite", target="") -> None:
    """실제 평가를 대신하는 품질 미달 샘플을 구성한다."""
    quality = state["quality_result"]
    metric = "perspective_coverage" if action == "research" else "neutrality"
    quality[metric].update(
        rule_passed=False, passed=False, reasons=["테스트용 품질 미달"]
    )
    quality.update(
        passed=False, action=action, target_node=target,
        feedback=["테스트용 근거 보강 필요"],
    )


# ---------------------------------------------------------------------------
# 2. 최초 진입·후보 선택 — 근거 최소 우선과 결정의 재현성
# ---------------------------------------------------------------------------
def test_first_entry_does_not_count_missing_result():
    state = make_initial_state()
    before = copy.deepcopy(state)
    node, control, sufficiency = decide(state)
    assert node == "research"
    assert control["step_count"] == control["dispatch_id"] == 0
    assert control["node_status"] == control["node_errors"] == {}
    assert sufficiency is None
    assert state == before
    assert decide(state) == (node, control, sufficiency)


def test_existing_research_skips_to_first_unrun_perspective():
    node, control, _ = decide(sample_state_after_research())
    assert node == "market"
    assert control["exec_retry_counts"]["research"] == 0


@pytest.mark.parametrize("perspective", ["market", "stakeholder", "domain"])
def test_less_evidence_precedes_tie_break_order(perspective):
    state = sample_state_after_eval()
    _clear_evidence(state, perspective)
    node, _, _ = decide(state)
    assert node == PERSPECTIVE_NODE[perspective]


@pytest.mark.parametrize("candidates,expected", [
    (["domain", "stakeholder", "market"], "market"),
    (["domain", "stakeholder"], "stakeholder"),
    (["domain"], "domain"),
])
def test_equal_evidence_uses_contract_tie_order(candidates, expected):
    assert tie_break(make_initial_state(), candidates) == expected


@pytest.mark.parametrize("perspective", ["trl", "market"])
def test_market_count_uses_smaller_of_its_two_perspectives(perspective):
    state = sample_state_after_eval()
    _clear_evidence(state, perspective)
    assert evidence_count(state, "market") == 0


def test_trl_and_market_shortage_counts_as_one_retry():
    state = _evaluated_state()
    _clear_evidence(state, "trl")
    _clear_evidence(state, "market")
    node, control, sufficiency = decide(state)
    assert node == "market"
    assert control["evidence_retry_counts"]["market"] == 1
    assert sum(control["evidence_retry_counts"].values()) == 1
    assert not sufficiency["trl"] and not sufficiency["market"]
    assert sufficiency["reasons"]["trl"]
    assert sufficiency["reasons"]["market"]


# ---------------------------------------------------------------------------
# 3. 실행 결과·실패·제외 — 오래된 응답과 재시도 경계
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("invalid", ["missing", "node", "dispatch"])
def test_invalid_result_is_failed_and_retried(invalid):
    state = make_initial_state()
    _last_result(state, "research")
    if invalid == "missing":
        state.pop("node_result")
    elif invalid == "node":
        state["node_result"]["node"] = "market"
    else:
        state["node_result"]["dispatch_id"] += 1
    before = copy.deepcopy(state)
    node, control, _ = decide(state)
    assert node == "research"
    assert control["node_status"]["research"] == "failed"
    assert control["node_errors"]["research"].startswith("E-1002")
    assert control["last_error"] == control["node_errors"]["research"]
    assert control["exec_retry_counts"]["research"] == 1
    assert control["step_count"] == control["dispatch_id"] == 1
    assert state == before


@pytest.mark.parametrize("node", EVIDENCE_RETRY_NODES)
def test_retry_exhaustion_skips_perspective_without_retrying_it(node):
    state = _evaluated_state()
    perspective = next(p for p, n in PERSPECTIVE_NODE.items() if n == node)
    _clear_evidence(state, perspective)
    state["control"]["exec_retry_counts"][node] = config.MAX_EXEC_RETRY
    _last_result(state, node, failed=True)
    next_node, control, sufficiency = decide(state)
    assert next_node != node
    assert control["node_status"][node] == "skipped"
    assert node not in candidate_nodes(state, control, sufficiency)
    assert control["evidence_retry_counts"][node] == 0


@pytest.mark.parametrize("has_output", [False, True])
def test_failed_synthesis_continues_only_with_fallback_output(has_output):
    state = _evaluated_state()
    state["report_version"] = 0
    state.pop("quality_result")
    # 보고서 작성 전 synthesis가 실패한 이력으로 구성한다.
    for node in ("report", "quality"):
        state["control"]["node_status"].pop(node)
    if not has_output:
        state.pop("synthesis")
    state["control"]["exec_retry_counts"]["synthesis"] = config.MAX_EXEC_RETRY
    _last_result(state, "synthesis", failed=True)
    node, control, _ = decide(state)
    assert node == ("report" if has_output else END)
    assert control["node_status"]["synthesis"] == "skipped"
    if not has_output:
        assert control["run_status"] == "exhausted"


@pytest.mark.parametrize("node", ["report", "quality"])
def test_terminal_failure_without_output_ends_exhausted(node):
    state = _evaluated_state()
    state["report_version"] = 0
    state.pop("quality_result")
    state["control"]["exec_retry_counts"][node] = config.MAX_EXEC_RETRY
    _last_result(state, node, failed=True)
    next_node, control, _ = decide(state)
    assert next_node == END
    assert control["run_status"] == "exhausted"


# ---------------------------------------------------------------------------
# 4. 마무리 모드 — 추가 실행 최대 세 번과 실패 시 종료
# ---------------------------------------------------------------------------
def test_finalize_attempts_each_terminal_node_once():
    state = sample_state_after_eval()
    state["control"]["step_count"] = config.MAX_TOTAL_STEPS
    for expected in ("synthesis", "report", "quality"):
        update = supervisor_node(state)
        assert route({**state, **update}) == expected
        state.update(update)
        assert state["control"]["run_status"] == "exhausted"
        state.update(DUMMY_NODES[expected](state))
    state.update(supervisor_node(state))
    assert state["control"]["next_node"] == END
    assert state["control"]["finalize_tried"] == [
        "synthesis", "report", "quality"
    ]
    assert state["control"]["step_count"] == config.MAX_TOTAL_STEPS + 3
    assert state["control"]["run_status"] == "exhausted"


@pytest.mark.parametrize("already_finalizing", [False, True])
def test_finalize_f1_ends_after_previous_failure(already_finalizing):
    state = sample_state_after_eval()
    node = "synthesis" if already_finalizing else "research"
    state["control"]["step_count"] = config.MAX_TOTAL_STEPS - 1
    if already_finalizing:
        state["control"]["finalize_tried"] = [node]
    _last_result(state, node, failed=True)
    # TODO(5번 요청): F1은 finalize_tried 포함 여부와 무관하게 직전 실패 시 END.
    next_node, control, _ = decide(state)
    assert next_node == END
    assert control["run_status"] == "exhausted"
    assert sum(control["exec_retry_counts"].values()) == 0


def test_finalize_never_selects_skipped_node():
    state = sample_state_after_eval()
    state["control"]["step_count"] = config.MAX_TOTAL_STEPS
    state["control"]["node_status"]["synthesis"] = "skipped"
    # TODO(5번 요청): 마무리 후보에도 계약의 skipped 재선택 금지를 적용한다.
    node, _, _ = decide(state)
    assert node != "synthesis"


# ---------------------------------------------------------------------------
# 5. 품질 재작성·재조사 — 버전, 힌트, stale와 각 재작업 상한
# ---------------------------------------------------------------------------
def test_new_report_version_requires_quality_again():
    state = _evaluated_state()
    state["report_version"] += 1
    _last_result(state, "report")
    node, control, _ = decide(state)
    assert node == "quality"
    assert control["step_count"] == control["dispatch_id"] == 1
    state["control"] = control
    state.update(DUMMY_NODES["quality"](state))
    assert state["quality_result"]["evaluated_report_version"] == 2
    node, control, _ = decide(state)
    assert node == END and control["run_status"] == "completed"


@pytest.mark.parametrize("exhausted", [False, True])
def test_quality_rewrite_obeys_report_retry_limit(exhausted):
    state = _evaluated_state()
    _quality_failure(state)
    if exhausted:
        state["control"]["report_retry_count"] = config.MAX_REPORT_RETRY
    node, control, _ = decide(state)
    assert node == (END if exhausted else "report")
    assert control["report_retry_count"] == config.MAX_REPORT_RETRY
    assert "neutrality" in control["route_reason"]
    assert "품질 평가 권고" in control["route_reason"]
    if exhausted:
        assert control["run_status"] == "exhausted"


@pytest.mark.parametrize("target", EVIDENCE_RETRY_NODES)
def test_quality_research_sets_owned_hints_and_stale(target):
    state = _evaluated_state()
    _quality_failure(state, action="research", target=target)
    before = copy.deepcopy(state)
    update = supervisor_node(state)
    assert set(update) == {"control", "sufficiency"}
    control = update["control"]
    assert control["next_node"] == target
    assert control["quality_research_count"] == 1
    assert control["stale"] == ["synthesis", "report"]
    assert sum(control["evidence_retry_counts"].values()) == 0
    for perspective, node in PERSPECTIVE_NODE.items():
        if node == target:
            assert not update["sufficiency"][perspective]
            assert retry_hint({**state, **update}, perspective).startswith(
                "품질 평가: "
            )
        else:
            assert update["sufficiency"][perspective]
    assert "perspective_coverage" in control["route_reason"]
    assert "품질 평가 권고" in control["route_reason"]
    assert state == before


@pytest.mark.parametrize("blocked", ["limit", "skipped", "invalid"])
def test_unavailable_quality_research_falls_back_to_rewrite(blocked):
    state = _evaluated_state()
    _quality_failure(state, action="research", target="market")
    if blocked == "limit":
        state["control"]["quality_research_count"] = config.MAX_QUALITY_RESEARCH
    elif blocked == "skipped":
        state["control"]["node_status"]["market"] = "skipped"
    else:
        state["quality_result"]["target_node"] = "research"
    node, control, _ = decide(state)
    assert node == "report"
    assert control["quality_research_count"] == state["control"]["quality_research_count"]
    assert control["report_retry_count"] == 1
    reason = {"limit": "기회 소진", "skipped": "skipped", "invalid": "재조사 대상 아님"}
    assert reason[blocked] in control["route_reason"]


def test_quality_rewrite_never_selects_skipped_report():
    state = _evaluated_state()
    _quality_failure(state)
    state["control"]["node_status"]["report"] = "skipped"
    # TODO(5번 요청): 규칙 9b에도 skipped 재선택 금지를 적용한다.
    node, _, _ = decide(state)
    assert node == END


# ---------------------------------------------------------------------------
# 6. 그래프 경로 — 품질 재조사부터 최신 보고서 재평가까지 연결 확인
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("target", EVIDENCE_RETRY_NODES)
def test_quality_research_graph_rebuilds_and_reevaluates(target):
    quality_calls = []

    def quality_node(state: State) -> dict:
        result = DUMMY_NODES["quality"](state)
        quality_calls.append(state["report_version"])
        if len(quality_calls) == 1:
            _quality_failure(
                {"quality_result": result["quality_result"]},
                action="research", target=target,
            )
        return result

    nodes = []
    final = None
    graph = build_graph(dummy=True, overrides={"quality": quality_node})
    for mode, chunk in graph.stream(
        make_initial_state(),
        config={"recursion_limit": config.RECURSION_LIMIT},
        stream_mode=["updates", "values"],
    ):
        if mode == "values":
            final = chunk
        else:
            nodes.extend(n for n in chunk if n not in ("select", "supervisor"))
    first_quality = nodes.index("quality")
    assert nodes[first_quality:] == [
        "quality", target, "synthesis", "report", "quality"
    ]
    assert quality_calls == [1, 2]
    assert final["report_version"] == final["quality_result"]["evaluated_report_version"] == 2
    assert final["control"]["quality_research_count"] == 1
    assert final["control"]["report_retry_count"] == 0
    assert final["control"]["stale"] == []
    assert final["control"]["run_status"] == "completed"
    assert all(final["sufficiency"][p] for p in PERSPECTIVE_NODE)


def test_repeated_quality_research_request_rewrites_then_ends():
    versions = []

    def quality_node(state: State) -> dict:
        result = DUMMY_NODES["quality"](state)
        versions.append(state["report_version"])
        _quality_failure(
            {"quality_result": result["quality_result"]},
            action="research", target="market",
        )
        return result

    final = build_graph(
        dummy=True, overrides={"quality": quality_node}
    ).invoke(
        make_initial_state(),
        config={"recursion_limit": config.RECURSION_LIMIT},
    )
    control = final["control"]
    assert versions == [1, 2, 3]
    assert control["quality_research_count"] == config.MAX_QUALITY_RESEARCH
    assert control["report_retry_count"] == config.MAX_REPORT_RETRY
    assert control["run_status"] == "exhausted"
    assert control["next_node"] == END
    assert "기회 소진" in control["route_reason"]
    assert "재작성 기회 소진" in control["route_reason"]
    assert final["report_version"] == final["quality_result"]["evaluated_report_version"]
