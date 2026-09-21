"""The design-specified data contract for the LangGraph workflow."""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict


TechName = Literal["TurboQuant", "InfiniGen"]
Stance = Literal["positive", "negative", "neutral"]


class Reference(TypedDict):
    source_id: str
    kind: Literal["paper", "patent", "web"]
    author: str
    date: str
    title: str
    venue: str
    url: str
    used_by: list[str]
    stance: Stance


class Evidence(TypedDict):
    claim: str
    source_id: str
    stance: Stance


class TechSummary(TypedDict):
    name: TechName
    camp: Literal["SW", "HW"]
    approach: str
    scope: str
    key_metrics: dict
    limitations: list[str]
    evidence: list[Evidence]


class TRLEstimate(TypedDict):
    level: int
    rationale: str
    evidence: list[Evidence]
    uncertainty: str


class MarketResult(TypedDict):
    market_size_growth: list[Evidence]
    adoption: list[Evidence]
    ecosystem: list[Evidence]
    summary: str


class StakeholderResult(TypedDict):
    competitors: list[Evidence]
    adopters_devs: list[Evidence]
    investors: list[Evidence]
    summary: str


class DomainResult(TypedDict):
    domain: str
    cost: list[Evidence]
    throughput: list[Evidence]
    model_quality: list[Evidence]
    transfer_overhead: list[Evidence]
    deployment_barrier: list[Evidence]
    summary: str


class SufficiencyCheck(TypedDict):
    trl: bool
    market: bool
    stakeholder: bool
    domain: bool
    reasons: dict[str, str]


class Conflict(TypedDict):
    perspective_a: str
    perspective_b: str
    tech: TechName
    description: str


class Synthesis(TypedDict):
    agreements: list[str]
    conflicts: list[Conflict]
    neutrality_note: str
    limitations: list[str]


class State(TypedDict):
    tech_sw: TechName
    tech_hw: TechName
    domain: str
    tech_summary: dict[TechName, TechSummary]
    trl_result: dict[TechName, TRLEstimate]
    market_result: dict[TechName, MarketResult]
    stakeholder_result: dict[TechName, StakeholderResult]
    domain_result: dict[TechName, DomainResult]
    sufficiency: SufficiencyCheck
    retry_count: int
    synthesis: Synthesis
    references: Annotated[list[Reference], operator.add]
    report_path: str
