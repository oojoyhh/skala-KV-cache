"""Evidence classification for datacenter and cloud-serving domain analysis."""

from __future__ import annotations

from typing import Any, Callable, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field
import config
from state import DomainResult, Evidence, Reference, State, Stance, TechName, retry_hint


class DomainEvidenceSelection(BaseModel):
    """An LLM selection key and content-derived stance, not copied Evidence."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
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


def build_domain_prompt(
    domain: str,
    evidence: Sequence[Evidence],
    *,
    classify_stance: bool = False,
) -> str:
    """Build an evidence-only classification prompt for the supplied domain."""

    from llm import load_prompt

    evidence_text = "\n".join(
        f"- id: E{index} | claim: {item['claim']}"
        + (f" | stance: {item['stance']}" if not classify_stance else "")
        for index, item in enumerate(evidence, 1)
    ) or "(No Evidence was provided.)"
    stance_instruction = (
        "- 각 claim 본문만 근거로 stance를 분류한다. 검색 의도는 stance 근거가 아니며, "
        "입력 stance는 미분류 placeholder다. positive는 지지 근거, negative는 한계·반론·제약 근거, "
        "neutral은 중립·배경 근거다.\n"
        if classify_stance
        else "- 선택한 id의 stance는 Evidence 목록의 stance를 그대로 사용한다.\n"
    )
    return load_prompt("domain").format(
        domain=domain,
        evidence=evidence_text,
        stance_instruction=stance_instruction,
    )


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
    *,
    classify_stance: bool = False,
) -> DomainResult:
    """Return the exact DomainResult contract after validating Evidence reuse."""

    by_id = {f"E{index}": item for index, item in enumerate(evidence, 1)}
    axes = (
        "cost",
        "throughput",
        "model_quality",
        "transfer_overhead",
        "deployment_barrier",
    )
    validated_axes: dict[str, list[Evidence]] = {}
    classified_stances: dict[tuple[str, str], Stance] = {}

    for axis in axes:
        validated: list[Evidence] = []
        for selected in getattr(result, axis):
            candidate = by_id.get(selected.id)
            if candidate is None:
                continue
            claim_source = (candidate["claim"], candidate["source_id"])
            stance: Stance = selected.stance if classify_stance else candidate["stance"]
            previous_stance = classified_stances.get(claim_source)
            if previous_stance is not None and previous_stance != stance:
                continue
            classified_stances.setdefault(claim_source, stance)
            validated.append(
                {
                    "claim": candidate["claim"],
                    "source_id": candidate["source_id"],
                    "stance": stance,
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
    *,
    classify_stance: bool = False,
) -> DomainResult:
    """Classify supplied Evidence and return the design-specified DomainResult."""

    if not domain.strip():
        raise ValueError("domain must not be blank.")
    prompt = build_domain_prompt(domain, evidence, classify_stance=classify_stance)
    response = structured_output_client.invoke(prompt, DomainStructuredResponse)
    return validate_domain_result(domain, response, evidence, classify_stance=classify_stance)


def _empty_result(domain: str, error: str) -> DomainResult:
    return {
        "domain": domain,
        "cost": [], "throughput": [], "model_quality": [], "transfer_overhead": [],
        "deployment_barrier": [], "summary": error,
    }


def _domain_queries(state: State, technology: TechName, hint: str) -> list[tuple[str, Stance]]:
    """Create distinct support and counter-evidence queries in the selected domain."""

    tech_summary = state.get("tech_summary", {}).get(technology, {})
    description = " ".join(
        value for value in (tech_summary.get("approach", ""), tech_summary.get("scope", "")) if value
    )
    topics = (
        "cost serving throughput", "model quality compression offloading",
        "GPU host transfer memory bandwidth", "deployment barrier infrastructure",
    )
    retry_context = f" retry focus: {hint}" if hint else ""
    queries: list[tuple[str, Stance]] = []
    for stance, intent in (
        ("positive", "supporting deployment evidence"),
        ("negative", "limitations challenges counterevidence"),
    ):
        for index in range(config.QUERIES_PER_STANCE):
            topic = topics[index % len(topics)]
            queries.append(
                (f"{technology} {state['domain']} {description} {topic} {intent}{retry_context}", stance)
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


def domain_node(
    state: State,
    *,
    search_fn: Callable[..., list[dict[str, Any]]] | None = None,
    client: StructuredOutputClient | None = None,
) -> dict:
    """Search and classify domain Evidence for both selected technologies."""

    try:
        search = _resolve_search_fn(search_fn)
    except Exception as exc:  # noqa: BLE001 - unavailable search dependency is E-1002
        message = f"[E-1002] 검색 도구 사용 실패: {exc}"
        return {
            "domain_result": {
                state["tech_sw"]: _empty_result(state["domain"], message),
                state["tech_hw"]: _empty_result(state["domain"], message),
            },
            "references": [],
        }

    if client is None:
        from llm import StructuredClient

        client = StructuredClient()

    results: dict[TechName, DomainResult] = {}
    references: list[Reference] = []
    hint = retry_hint(state, "domain")
    for technology in (state["tech_sw"], state["tech_hw"]):
        candidates: list[Evidence] = []
        references_by_id: dict[str, Reference] = {}
        last_query = ""
        search_failed = False
        try:
            for query, stance in _domain_queries(state, technology, hint):
                last_query = query
                found = search(
                    query,
                    stance,
                    max_results=config.WEB_SEARCH_MAX_RESULTS,
                    used_by="domain",
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
            results[technology] = _empty_result(state["domain"], f"[E-1002] 검색 실패: {exc}")
            continue

        if not candidates:
            code = "[E-1002] 검색 실패" if search_failed else "[E-1001] 검색 결과 없음"
            results[technology] = _empty_result(state["domain"], f"{code}: {last_query}")
            continue

        try:
            result = evaluate_domain(state["domain"], candidates, client, classify_stance=True)
        except Exception as exc:  # noqa: BLE001 - structured LLM/validation failure is E-1002
            results[technology] = _empty_result(state["domain"], f"[E-1002] 도메인 분류 실패: {exc}")
            continue

        results[technology] = result
        used_ids = {
            evidence["source_id"]
            for axis in ("cost", "throughput", "model_quality", "transfer_overhead", "deployment_barrier")
            for evidence in result[axis]
        }
        used_stances = {
            evidence["source_id"]: evidence["stance"]
            for axis in ("cost", "throughput", "model_quality", "transfer_overhead", "deployment_barrier")
            for evidence in result[axis]
        }
        references.extend(
            {**reference, "stance": used_stances[source_id]}
            for source_id, reference in references_by_id.items()
            if source_id in used_ids
        )

    return {"domain_result": results, "references": references}
