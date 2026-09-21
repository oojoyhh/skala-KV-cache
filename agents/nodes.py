"""LangGraph-ready adapters for stakeholder, domain, and sufficiency modules."""

from __future__ import annotations

from typing import Literal, Protocol, Sequence, TypedDict

from agents.check import evaluate_sufficiency, needs_retry
from agents.domain import (
    StructuredOutputClient as DomainStructuredOutputClient,
    evaluate_domain,
)
from agents.stakeholder import (
    StructuredOutputClient as StakeholderStructuredOutputClient,
    evaluate_stakeholders,
)
from state import (
    DomainResult,
    Evidence,
    StakeholderResult,
    State,
    SufficiencyCheck,
    TechName,
)


class EvidenceProvider(Protocol):
    """Supplies already-created Evidence for a technology without retrieving it."""

    def get_evidence(self, state: State, technology: TechName) -> Sequence[Evidence]:
        """Return Evidence available to the selected evaluation node."""


class StakeholderNodeUpdate(TypedDict):
    stakeholder_result: dict[TechName, StakeholderResult]


class DomainNodeUpdate(TypedDict):
    domain_result: dict[TechName, DomainResult]


class SufficiencyNodeUpdate(TypedDict):
    sufficiency: SufficiencyCheck


Route = Literal["market", "stakeholder", "domain", "synthesis"]


def stakeholder_node(
    state: State,
    *,
    technology: TechName,
    evidence_provider: EvidenceProvider,
    structured_output_client: StakeholderStructuredOutputClient,
) -> StakeholderNodeUpdate:
    """Evaluate one technology and return only its merged State update."""

    evidence = evidence_provider.get_evidence(state, technology)
    result = evaluate_stakeholders(technology, evidence, structured_output_client)
    updated_results = dict(state.get("stakeholder_result", {}))
    updated_results[technology] = result
    return {"stakeholder_result": updated_results}


def domain_node(
    state: State,
    *,
    technology: TechName,
    evidence_provider: EvidenceProvider,
    structured_output_client: DomainStructuredOutputClient,
) -> DomainNodeUpdate:
    """Evaluate one technology in State's domain and return its merged update."""

    evidence = evidence_provider.get_evidence(state, technology)
    result = evaluate_domain(state["domain"], evidence, structured_output_client)
    updated_results = dict(state.get("domain_result", {}))
    updated_results[technology] = result
    return {"domain_result": updated_results}


def _evaluate_technology_sufficiency(state: State, technology: TechName) -> SufficiencyCheck:
    """Reuse the single-technology rubric for one of the two State technologies."""

    trl_result = state["trl_result"].get(technology)
    market_result = state["market_result"].get(technology)
    stakeholder_result = state["stakeholder_result"].get(
        technology,
        {"competitors": [], "adopters_devs": [], "investors": [], "summary": ""},
    )
    domain_result = state["domain_result"].get(
        technology,
        {
            "domain": state["domain"], "cost": [], "throughput": [], "model_quality": [],
            "transfer_overhead": [], "deployment_barrier": [], "summary": "",
        },
    )
    market_evidence = [
        *(market_result["market_size_growth"] if market_result else []),
        *(market_result["adoption"] if market_result else []),
        *(market_result["ecosystem"] if market_result else []),
    ]
    return evaluate_sufficiency(
        stakeholder_result,
        domain_result,
        trl_estimate=trl_result["level"] if trl_result else None,
        trl_evidence=trl_result["evidence"] if trl_result else [],
        market_result=market_result,
        market_evidence=market_evidence,
    )


def sufficiency_node(state: State) -> SufficiencyNodeUpdate:
    """Combine both technologies' deterministic sufficiency checks into one State value."""

    checks = {
        state["tech_sw"]: _evaluate_technology_sufficiency(state, state["tech_sw"]),
        state["tech_hw"]: _evaluate_technology_sufficiency(state, state["tech_hw"]),
    }
    reasons: dict[str, str] = {}
    combined: SufficiencyCheck = {"trl": True, "market": True, "stakeholder": True, "domain": True, "reasons": reasons}
    for perspective in ("trl", "market", "stakeholder", "domain"):
        missing = [
            f"{technology}: {check['reasons'][perspective]}"
            for technology, check in checks.items()
            if not check[perspective]
        ]
        if missing:
            combined[perspective] = False
            reasons[perspective] = "; ".join(missing)
    return {"sufficiency": combined}


def route_after_sufficiency(
    check: SufficiencyCheck,
    retry_count: int,
    max_iterations: int,
) -> Route:
    """Choose a bounded next perspective using the design-specified priority.

    A single deterministic route is returned when several perspectives are
    insufficient: market (which covers TRL and market) precedes stakeholder,
    which precedes domain, matching the design flow's branch order.
    """

    if retry_count >= max_iterations or not needs_retry(check):
        return "synthesis"
    if not check["trl"] or not check["market"]:
        return "market"
    if not check["stakeholder"]:
        return "stakeholder"
    return "domain"
