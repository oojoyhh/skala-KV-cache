"""LangGraph 그래프 조립 (설계서 D-2, DEV_PLAN §3). 담당: 5번.

START → select → research → (market | stakeholder | domain) → check
      → [부족 관점만 재조사 → check] | synthesis → report → END
"""

from langgraph.graph import END, START, StateGraph

import config
from state import PERSPECTIVES, State

# 부족 관점 → 다시 실행할 노드 (trl·market은 같은 시장 평가 노드)
RETRY_NODE = {"trl": "market", "market": "market", "stakeholder": "stakeholder", "domain": "domain"}
EVAL_NODES = ("market", "stakeholder", "domain")


def select_node(state: State) -> dict:
    """기술 선정 (설계서 B-1 2안: Human 기반) — 조가 정한 값을 config에서 넣는다."""
    return {"tech_sw": config.TECH_SW, "tech_hw": config.TECH_HW, "domain": config.DOMAIN}


def route_after_check(state: State) -> list[str]:
    """모두 충분 → synthesis / 불충분 & retry_count ≤ MAX_RETRY → 부족 관점 노드 / 상한 초과 → synthesis (E-1005)."""
    suff = state["sufficiency"]
    missing = [p for p in PERSPECTIVES if not suff[p]]
    if not missing or state.get("retry_count", 0) > config.MAX_RETRY:
        return ["synthesis"]
    return sorted({RETRY_NODE[p] for p in missing})


def load_nodes(dummy: bool = False) -> dict:
    """노드 함수 모음. dummy=True면 tests/fixtures.py의 가짜 노드를 쓴다."""
    if dummy:
        from tests.fixtures import DUMMY_NODES

        return dict(DUMMY_NODES)

    from agents.check import check_node
    from agents.domain import domain_node
    from agents.market import market_node
    from agents.report import report_node
    from agents.research import research_node
    from agents.stakeholder import stakeholder_node
    from agents.synthesis import synthesis_node

    return {
        "research": research_node,
        "market": market_node,
        "stakeholder": stakeholder_node,
        "domain": domain_node,
        "check": check_node,
        "synthesis": synthesis_node,
        "report": report_node,
    }


def build_graph(dummy: bool = False, overrides: dict | None = None):
    """overrides로 일부 노드만 dummy/실제로 바꿔 끼울 수 있다 (통합 단계용)."""
    nodes = {**load_nodes(dummy), **(overrides or {})}

    g = StateGraph(State)
    g.add_node("select", select_node)
    for name, fn in nodes.items():
        g.add_node(name, fn)

    g.add_edge(START, "select")
    g.add_edge("select", "research")
    for n in EVAL_NODES:
        g.add_edge("research", n)   # Fan-out
        g.add_edge(n, "check")      # 노드별 개별 연결 — 합류 대기 edge를 쓰면 재조사 때 그래프가 멈춤 (DEV_PLAN §3-4)
    g.add_conditional_edges("check", route_after_check, [*EVAL_NODES, "synthesis"])
    g.add_edge("synthesis", "report")
    g.add_edge("report", END)
    return g.compile()


def export_mermaid(path: str = "docs/graph.mmd", dummy: bool = True) -> str:
    """README Architecture용 실제 그래프 Mermaid 출력."""
    import os

    text = build_graph(dummy=dummy).get_graph().draw_mermaid()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path
