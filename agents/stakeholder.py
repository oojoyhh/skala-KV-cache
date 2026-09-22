"""Evidence classification for stakeholder analysis.

This module classifies supplied Evidence into the stakeholder result defined by
the design specification. It does not assess sufficiency or control workflow.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field
import config
from state import Evidence, Reference, StakeholderResult, State, Stance, TechName, retry_hint


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


def build_stakeholder_prompt(
    technology: str,
    evidence: Sequence[Evidence],
    *,
    classify_stance: bool = False,
) -> str:
    """Build a prompt that permits classification, not invention, of Evidence."""

    from llm import load_prompt

    evidence_text = "\n".join(
        f"- claim: {item['claim']} | source_id: {item['source_id']} | stance: {item['stance']}"
        for item in evidence
    ) or "(No Evidence was provided.)"
    stance_instruction = (
        "- 각 claim 본문만 근거로 positive, negative, neutral 중 stance를 분류한다. "
        "검색 의도는 stance 근거가 아니며, 입력의 stance 값은 미분류 placeholder다.\n"
        if classify_stance
        else "- Evidence의 claim, source_id, stance를 변경하지 않는다.\n"
    )
    return load_prompt("stakeholder").format(
        technology=technology,
        evidence=evidence_text,
        stance_instruction=stance_instruction,
    )


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
    *,
    classify_stance: bool = False,
) -> StakeholderResult:
    """Ensure every returned Evidence item exactly matches an input item.

    This rejects model-created claims and unknown source IDs rather than silently
    removing them, so callers can trace a structured-output contract violation.
    """

    input_items = {
        (item["claim"], item["source_id"], item["stance"])
        for item in evidence
    }
    input_claim_sources = {(item["claim"], item["source_id"]) for item in evidence}
    input_source_ids = {item["source_id"] for item in evidence}

    validated_groups: dict[str, list[Evidence]] = {}
    classified_stances: dict[tuple[str, str], Stance] = {}
    for group_name in ("competitors", "adopters_devs", "investors"):
        selected_items = getattr(result, group_name)
        group: list[Evidence] = []
        for selected in selected_items:
            selected_tuple = (selected.claim, selected.source_id, selected.stance)
            claim_source = (selected.claim, selected.source_id)
            if selected.source_id not in input_source_ids:
                raise StakeholderValidationError(
                    f"{group_name} references an unknown source_id: {selected.source_id}"
                )
            if (claim_source not in input_claim_sources if classify_stance else selected_tuple not in input_items):
                raise StakeholderValidationError(
                    f"{group_name} contains Evidence not present in the input: "
                    f"{selected.source_id}"
                )
            previous_stance = classified_stances.get(claim_source)
            if previous_stance is not None and previous_stance != selected.stance:
                raise StakeholderValidationError(
                    f"{group_name} assigns conflicting stances to {selected.source_id}"
                )
            classified_stances[claim_source] = selected.stance
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
    *,
    classify_stance: bool = False,
) -> StakeholderResult:
    """Classify Evidence with an LLM, then preserve only valid selections."""

    if not technology.strip():
        raise ValueError("technology must not be blank.")
    prompt = build_stakeholder_prompt(technology, evidence, classify_stance=classify_stance)
    response = structured_output_client.invoke(prompt, StakeholderStructuredResponse)
    return validate_stakeholder_result(response, evidence, classify_stance=classify_stance)


def _empty_result(error: str) -> StakeholderResult:
    return {"competitors": [], "adopters_devs": [], "investors": [], "summary": error}


def _stakeholder_queries(technology: TechName, hint: str) -> list[tuple[str, Stance]]:
    """Create distinct support and counter-evidence queries for one technology."""

    topics = (
        "competitors technology ecosystem",
        "enterprise adopters developers deployment",
        "investors funding industry response",
    )
    retry_context = f" retry focus: {hint}" if hint else ""
    queries: list[tuple[str, Stance]] = []
    for stance, intent in (
        ("positive", "adoption support evidence"),
        ("negative", "limitations challenges counterevidence"),
    ):
        for index in range(config.QUERIES_PER_STANCE):
            topic = topics[index % len(topics)]
            queries.append(
                (f"{technology} KV cache optimization {topic} {intent}{retry_context}", stance)
            )
    return queries


def _record_to_reference(record: dict[str, Any]) -> Reference:
    """Use the shared converter when available; fake-search tests use this fallback."""

    try:
        from tools.web_search import to_reference
    except ModuleNotFoundError:
        return {
            "source_id": record["source_id"], "kind": record["kind"], "author": record["author"],
            "date": record["date"], "title": record["title"], "venue": record["venue"],
            "url": record["url"], "used_by": record["used_by"], "stance": record["stance"],
        }
    return to_reference(record)


def _resolve_search_fn(search_fn: Callable[..., list[dict[str, Any]]] | None) -> Callable[..., list[dict[str, Any]]]:
    if search_fn is not None:
        return search_fn
    from tools.web_search import search_web

    return search_web


def stakeholder_node(
    state: State,
    *,
    search_fn: Callable[..., list[dict[str, Any]]] | None = None,
    client: StructuredOutputClient | None = None,
) -> dict:
    """Search and classify stakeholder Evidence for both selected technologies."""

    try:
        search = _resolve_search_fn(search_fn)
    except Exception as exc:  # noqa: BLE001 - unavailable search dependency is E-1002
        message = f"[E-1002] 검색 도구 사용 실패: {exc}"
        return {
            "stakeholder_result": {
                state["tech_sw"]: _empty_result(message),
                state["tech_hw"]: _empty_result(message),
            },
            "references": [],
        }

    if client is None:
        from llm import StructuredClient

        client = StructuredClient()

    results: dict[TechName, StakeholderResult] = {}
    references: list[Reference] = []
    hint = retry_hint(state, "stakeholder")
    for technology in (state["tech_sw"], state["tech_hw"]):
        candidates: list[Evidence] = []
        references_by_id: dict[str, Reference] = {}
        last_query = ""
        search_failed = False
        try:
            for query, stance in _stakeholder_queries(technology, hint):
                last_query = query
                found = search(
                    query,
                    stance,
                    max_results=config.WEB_SEARCH_MAX_RESULTS,
                    used_by="stakeholder",
                )
                if getattr(found, "error_code", None):
                    search_failed = True
                    continue
                for record in found:
                    reference = _record_to_reference(record)
                    references_by_id[reference["source_id"]] = reference
                    candidate = {
                        "claim": record["content"],
                        "source_id": reference["source_id"],
                        # Retrieval intent must not pre-classify Evidence stance.
                        "stance": "neutral",
                    }
                    if candidate not in candidates:
                        candidates.append(candidate)
        except Exception as exc:  # noqa: BLE001 - external search failure must not stop the graph
            results[technology] = _empty_result(f"[E-1002] 검색 실패: {exc}")
            continue

        if not candidates:
            code = "[E-1002] 검색 실패" if search_failed else "[E-1001] 검색 결과 없음"
            results[technology] = _empty_result(f"{code}: {last_query}")
            continue

        try:
            result = evaluate_stakeholders(technology, candidates, client, classify_stance=True)
        except Exception as exc:  # noqa: BLE001 - structured LLM/validation failure is E-1002
            results[technology] = _empty_result(f"[E-1002] 이해관계자 분류 실패: {exc}")
            continue

        results[technology] = result
        used_ids = {
            evidence["source_id"]
            for group in ("competitors", "adopters_devs", "investors")
            for evidence in result[group]
        }
        used_stances = {
            evidence["source_id"]: evidence["stance"]
            for group in ("competitors", "adopters_devs", "investors")
            for evidence in result[group]
        }
        references.extend(
            {**reference, "stance": used_stances[source_id]}
            for source_id, reference in references_by_id.items()
            if source_id in used_ids
        )

    return {"stakeholder_result": results, "references": references}
