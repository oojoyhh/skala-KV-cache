"""Evidence classification for datacenter and cloud-serving domain analysis."""

from __future__ import annotations

from typing import Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field
from state import DomainResult, Evidence, Stance


class DomainEvidenceSelection(BaseModel):
    """Internal Pydantic model constraining an Evidence item selected by the LLM."""

    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    stance: Stance


class DomainStructuredResponse(BaseModel):
    """Internal structured output for classifying Evidence into domain axes."""

    model_config = ConfigDict(extra="forbid")

    cost: list[DomainEvidenceSelection] = Field(default_factory=list)
    throughput: list[DomainEvidenceSelection] = Field(default_factory=list)
    model_quality: list[DomainEvidenceSelection] = Field(default_factory=list)
    transfer_overhead: list[DomainEvidenceSelection] = Field(default_factory=list)
    deployment_barrier: list[DomainEvidenceSelection] = Field(default_factory=list)
    summary: str = Field(min_length=1)


class DomainValidationError(ValueError):
    """Raised when LLM output contains Evidence absent from the input."""


class StructuredOutputClient(Protocol):
    """Adapter for an LLM configured to return the supplied response model."""

    def invoke(
        self,
        prompt: str,
        response_model: type[DomainStructuredResponse],
    ) -> DomainStructuredResponse:
        """Return structured output conforming to ``response_model``."""


def build_domain_prompt(domain: str, evidence: Sequence[Evidence]) -> str:
    """Build an evidence-only classification prompt for the supplied domain."""

    evidence_text = "\n".join(
        f"- claim: {item['claim']} | source_id: {item['source_id']} | stance: {item['stance']}"
        for item in evidence
    ) or "(No Evidence was provided.)"

    return f"""Classify the supplied Evidence for this domain: {domain}.

Use exactly these axes: cost, throughput, model_quality, transfer_overhead,
deployment_barrier.

Rules:
- Select and copy only supplied Evidence. Do not create, edit, combine, or infer
  claim/source_id/stance values, including performance numbers.
- Put Evidence only in axes it directly supports. Empty axes must be empty lists.
- An Evidence item may appear in more than one applicable axis unchanged.
- Use only positive, negative, or neutral for stance.
- Do not judge whether the technology is good or bad, assess sufficiency,
  choose a branch, or request a retry.

Supplied Evidence:
{evidence_text}
"""


def _build_domain_summary(axes: dict[str, list[Evidence]]) -> str:
    """Build a traceable summary from validated claims without LLM generation."""

    parts = [
        f"{axis}: {' | '.join(item['claim'] for item in items)}"
        for axis, items in axes.items()
        if items
    ]
    if not parts:
        return "제공된 Evidence에서 도메인 평가 근거를 확인할 수 없음."
    return "; ".join(parts)


def validate_domain_result(
    domain: str,
    result: DomainStructuredResponse,
    evidence: Sequence[Evidence],
) -> DomainResult:
    """Return the exact DomainResult contract after validating Evidence reuse."""

    input_items = {
        (item["claim"], item["source_id"], item["stance"])
        for item in evidence
    }
    input_source_ids = {item["source_id"] for item in evidence}
    axes = (
        "cost",
        "throughput",
        "model_quality",
        "transfer_overhead",
        "deployment_barrier",
    )
    validated_axes: dict[str, list[Evidence]] = {}

    for axis in axes:
        validated: list[Evidence] = []
        for selected in getattr(result, axis):
            selected_tuple = (selected.claim, selected.source_id, selected.stance)
            if selected.source_id not in input_source_ids:
                raise DomainValidationError(
                    f"{axis} references an unknown source_id: {selected.source_id}"
                )
            if selected_tuple not in input_items:
                raise DomainValidationError(
                    f"{axis} contains Evidence not present in the input: {selected.source_id}"
                )
            validated.append(
                {
                    "claim": selected.claim,
                    "source_id": selected.source_id,
                    "stance": selected.stance,
                }
            )
        validated_axes[axis] = validated

    return {
        "domain": domain,
        "cost": validated_axes["cost"],
        "throughput": validated_axes["throughput"],
        "model_quality": validated_axes["model_quality"],
        "transfer_overhead": validated_axes["transfer_overhead"],
        "deployment_barrier": validated_axes["deployment_barrier"],
        "summary": _build_domain_summary(validated_axes),
    }


def evaluate_domain(
    domain: str,
    evidence: Sequence[Evidence],
    structured_output_client: StructuredOutputClient,
) -> DomainResult:
    """Classify supplied Evidence and return the design-specified DomainResult."""

    if not domain.strip():
        raise ValueError("domain must not be blank.")
    prompt = build_domain_prompt(domain, evidence)
    response = structured_output_client.invoke(prompt, DomainStructuredResponse)
    return validate_domain_result(domain, response, evidence)
