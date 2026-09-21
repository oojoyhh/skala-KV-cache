"""Evidence classification for stakeholder analysis.

This module classifies supplied Evidence into the stakeholder result defined by
the design specification. It does not assess sufficiency or control workflow.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field
from state import Evidence, StakeholderResult, Stance


class EvidenceSelection(BaseModel):
    """Internal Pydantic schema used to constrain an LLM response."""

    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    stance: Stance


class StakeholderStructuredResponse(BaseModel):
    """Internal LLM schema that mirrors the external StakeholderResult contract."""

    model_config = ConfigDict(extra="forbid")

    competitors: list[EvidenceSelection] = Field(default_factory=list)
    adopters_devs: list[EvidenceSelection] = Field(default_factory=list)
    investors: list[EvidenceSelection] = Field(default_factory=list)
    summary: str = Field(min_length=1)


class StakeholderValidationError(ValueError):
    """Raised when an LLM output is not a selection of the supplied Evidence."""


class StructuredOutputClient(Protocol):
    """Adapter for an LLM configured to produce the supplied Pydantic model."""

    def invoke(
        self,
        prompt: str,
        response_model: type[StakeholderStructuredResponse],
    ) -> StakeholderStructuredResponse:
        """Return structured output matching ``response_model``."""


def build_stakeholder_prompt(technology: str, evidence: Sequence[Evidence]) -> str:
    """Build a prompt that permits classification, not invention, of Evidence."""

    evidence_text = "\n".join(
        f"- claim: {item['claim']} | source_id: {item['source_id']} | stance: {item['stance']}"
        for item in evidence
    ) or "(No Evidence was provided.)"

    return f"""Classify the supplied Evidence about {technology} into exactly these
stakeholder groups: competitors, adopters_devs, investors.

Rules:
- Select and copy only Evidence supplied below. Do not create, edit, combine, or
  infer new claim/source_id/stance values.
- Use only the stance values positive, negative, or neutral.
- A group with no relevant Evidence must be an empty list.
- Do not assess whether evidence is sufficient, request retry, choose a next
  action, or judge whether the technology is good or bad.

Supplied Evidence:
{evidence_text}
"""


def _build_stakeholder_summary(groups: dict[str, list[Evidence]]) -> str:
    """Build a traceable summary from validated claims without LLM generation."""

    parts = [
        f"{group_name}: {' | '.join(item['claim'] for item in group)}"
        for group_name, group in groups.items()
        if group
    ]
    if not parts:
        return "제공된 Evidence에서 이해관계자 관련 근거를 확인할 수 없음."
    return "; ".join(parts)


def validate_stakeholder_result(
    result: StakeholderStructuredResponse,
    evidence: Sequence[Evidence],
) -> StakeholderResult:
    """Ensure every returned Evidence item exactly matches an input item.

    This rejects model-created claims and unknown source IDs rather than silently
    removing them, so callers can trace a structured-output contract violation.
    """

    input_items = {
        (item["claim"], item["source_id"], item["stance"])
        for item in evidence
    }
    input_source_ids = {item["source_id"] for item in evidence}

    validated_groups: dict[str, list[Evidence]] = {}
    for group_name in ("competitors", "adopters_devs", "investors"):
        selected_items = getattr(result, group_name)
        group: list[Evidence] = []
        for selected in selected_items:
            selected_tuple = (selected.claim, selected.source_id, selected.stance)
            if selected.source_id not in input_source_ids:
                raise StakeholderValidationError(
                    f"{group_name} references an unknown source_id: {selected.source_id}"
                )
            if selected_tuple not in input_items:
                raise StakeholderValidationError(
                    f"{group_name} contains Evidence not present in the input: "
                    f"{selected.source_id}"
                )
            group.append(
                {
                    "claim": selected.claim,
                    "source_id": selected.source_id,
                    "stance": selected.stance,
                }
            )
        validated_groups[group_name] = group

    return {
        "competitors": validated_groups["competitors"],
        "adopters_devs": validated_groups["adopters_devs"],
        "investors": validated_groups["investors"],
        "summary": _build_stakeholder_summary(validated_groups),
    }


def evaluate_stakeholders(
    technology: str,
    evidence: Sequence[Evidence],
    structured_output_client: StructuredOutputClient,
) -> StakeholderResult:
    """Classify Evidence with an LLM, then preserve only valid selections."""

    if not technology.strip():
        raise ValueError("technology must not be blank.")
    prompt = build_stakeholder_prompt(technology, evidence)
    response = structured_output_client.invoke(prompt, StakeholderStructuredResponse)
    return validate_stakeholder_result(response, evidence)
