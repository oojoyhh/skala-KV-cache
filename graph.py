"""LangGraph 그래프 조립 — Supervisor hub-and-spoke (docs/AGENT_CONTRACT.md 1장). 담당: 5번.

START → select → supervisor ─┬→ research ─┐
                             ├→ market    │
                             ├→ stakeholder│
                             ├→ domain    ├→ supervisor  (모든 하위 노드는 supervisor로 복귀)
                             ├→ synthesis │
                             ├→ report    │
                             └→ quality  ─┘
                  supervisor → END

하위 노드끼리 직접 edge를 두지 않는다. 다음 노드는 supervisor만 정한다(orchestration/supervisor.py).
"""

from langgraph.graph import END, START, StateGraph

import config
from orchestration import supervisor
from state import NODES, State


def select_node(state: State) -> dict:
    """기술 선정 (설계서 B-1 2안: Human 기반) — 조가 정한 값을 config에서 넣는다."""
    return {"tech_sw": config.TECH_SW, "tech_hw": config.TECH_HW, "domain": config.DOMAIN}


def load_nodes(dummy: bool = False) -> dict:
    """하위 노드 7개. dummy=True면 tests/fixtures.py의 가짜 노드를 쓴다."""
    if dummy:
        from tests.fixtures import DUMMY_NODES

        return {name: DUMMY_NODES[name] for name in NODES}

    from agents.domain import domain_node
    from agents.market import market_node
    from agents.quality import quality_node
    from agents.report import report_node
    from agents.research import research_node
    from agents.stakeholder import stakeholder_node
    from agents.synthesis import synthesis_node

    return {
        "research": research_node,
        "market": market_node,
        "stakeholder": stakeholder_node,
        "domain": domain_node,
        "synthesis": synthesis_node,
        "report": report_node,
        "quality": quality_node,
    }


def build_graph(dummy: bool = False, overrides: dict | None = None):
    """overrides로 일부 노드만 dummy/실제로 바꿔 끼울 수 있다 (통합 단계용)."""
    nodes = {**load_nodes(dummy), **(overrides or {})}
    missing = [n for n in NODES if n not in nodes]
    if missing:
        raise ValueError(f"하위 노드가 없습니다: {missing}")

    g = StateGraph(State)
    g.add_node("select", select_node)
    g.add_node("supervisor", supervisor.supervisor_node)
    for name in NODES:
        g.add_node(name, nodes[name])
        g.add_edge(name, "supervisor")      # 하위 노드 → supervisor 복귀 (하위 노드 간 직접 edge 없음)

    g.add_edge(START, "select")
    g.add_edge("select", "supervisor")
    g.add_conditional_edges("supervisor", supervisor.route, {**{n: n for n in NODES}, supervisor.END: END})
    return g.compile()


def export_mermaid(path: str = "docs/graph.mmd", dummy: bool = True) -> str:
    """README Architecture용 실제 그래프 Mermaid 출력."""
    import os

    text = build_graph(dummy=dummy).get_graph().draw_mermaid()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path
