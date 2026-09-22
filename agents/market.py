"""시장성 및 공개 근거 기반 TRL 평가 노드.

웹 검색 결과를 공용 ``StructuredClient``로 구조화한 뒤, source_id와 원문
포함 여부를 코드에서 다시 검증한다. TRL 최종 단계는 LLM 응답이 아니라
서로 다른 공개 출처 수와 설계서 v5 C-2의 보수적 단계 조건으로 결정한다.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

from pydantic import BaseModel, Field

import config
from llm import StructuredClient, load_prompt
from state import (
    TECHS,
    Evidence,
    MarketResult,
    Reference,
    State,
    Stance,
    TRLEstimate,
    TechName,
    retry_hint,
)
from tools.web_search import make_source_id, search_web, to_reference


SearchFunction = Callable[..., list[dict]]
MarketCategory = Literal["market_size_growth", "adoption", "ecosystem", "none"]

_TRL_QUERY_TEMPLATES = (
    "{tech} official paper code reproducibility prototype LLM serving demonstration",
    "{tech} end-to-end serving benchmark pilot preview product customer deployment",
)
_MARKET_QUERY_TEMPLATES: dict[Stance, tuple[str, ...]] = {
    "positive": (
        "{tech} adoption deployment framework support ecosystem LLM serving",
        "{tech} related inference optimization market growth commercial adoption",
        "{tech} production usage integration standardization partner ecosystem",
    ),
    "negative": (
        "{tech} limitation deployment barrier operational overhead production concern",
        "{tech} quality degradation accuracy trade-off framework support challenge",
        "{tech} adoption obstacle integration cost ecosystem limitation",
    ),
    "neutral": (),
}


class MarketEvidenceItem(BaseModel):
    """LLM이 입력 검색 결과에서 선택하는 구조. Reference 생성은 허용하지 않는다."""

    source_id: str
    claim: str
    stance: Stance
    market_category: MarketCategory = "none"
    trl_level: int | None = Field(default=None, ge=1, le=9)


class MarketClassification(BaseModel):
    items: list[MarketEvidenceItem] = Field(default_factory=list)


def _resolve_technologies(state: State) -> tuple[TechName, ...]:
    resolved: list[TechName] = []
    for state_key, expected in (("tech_sw", TECHS[0]), ("tech_hw", TECHS[1])):
        value = state.get(state_key, expected)
        if value != expected:
            raise ValueError(f"{state_key} must be {expected!r}; got {value!r}")
        resolved.append(expected)
    return tuple(resolved)


def _record_reference(record: Mapping[str, Any]) -> Reference:
    if isinstance(record.get("reference"), Mapping):
        return to_reference(record["reference"])
    try:
        return to_reference(record)
    except ValueError:
        url = str(record.get("url") or "")
        raw_used_by = record.get("used_by") or ["market"]
        used_by = [raw_used_by] if isinstance(raw_used_by, str) else list(raw_used_by)
        return {
            "source_id": str(record.get("source_id") or make_source_id(url)),
            "kind": "web",
            "author": str(record.get("author") or ""),
            "date": str(record.get("date") or ""),
            "title": str(record.get("title") or ""),
            "venue": str(record.get("venue") or ""),
            "url": url,
            "used_by": used_by,
            "stance": record.get("stance", "neutral"),
        }


def _content(record: Mapping[str, Any]) -> str:
    return " ".join(str(record.get("content") or record.get("summary") or "").split())


def _contains(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _inferred_trl_ceiling(record: Mapping[str, Any]) -> int:
    """한 출처가 명시적으로 뒷받침하는 최고 TRL 신호를 보수적으로 계산한다."""

    text = f"{record.get('title', '')} {_content(record)}"
    negated_high_stage = _contains(
        text,
        r"\b(no|not|without|lack(?:s|ing|ed)?)\b.{0,35}\b(production|commercial|customer|deployed)\b",
    )
    sustained_production = _contains(
        text,
        r"\b(sustained production|in production|production deployment|commercially deployed|"
        r"cloud adoption|product integration|shipped to customers?)\b",
    )
    if sustained_production and not negated_high_stage:
        return 9

    formal_release = _contains(
        text,
        r"\b(generally available|general availability|official product release|"
        r"commercial release|formally released)\b",
    )
    operational_validation = _contains(
        text,
        r"\b(customer validation|customer deployment|customer use|operational validation|"
        r"operational use|production validation|deployed for customers?)\b",
    )
    if formal_release and operational_validation and not negated_high_stage:
        return 8

    pilot_stage = _contains(text, r"\b(pilot|public preview|private preview|beta)\b")
    operating_environment = _contains(
        text, r"\b(operational environment|customer|cloud|data ?center|production environment)\b"
    )
    if pilot_stage and operating_environment and not negated_high_stage:
        return 7

    serving_context = _contains(
        text, r"\b(llm serving|inference serving|serving system|serving framework|serving cluster)\b"
    )
    integrated_system = _contains(
        text, r"\b(integrated (?:system|prototype|implementation)|system prototype|prototype system)\b"
    )
    end_to_end_demo = _contains(
        text, r"\b(end-to-end (?:serving )?(?:benchmark|evaluation|demonstration)|system demonstration)\b"
    )
    if serving_context and integrated_system and end_to_end_demo:
        return 6

    if _contains(text, r"\b(prototype|testbed|benchmark(?:ed|s)?|evaluation)\b"):
        return 5
    if _contains(
        text,
        r"\b(source code|open-source|open source|github|repository|reproducible|"
        r"implementation (?:is )?available)\b",
    ):
        return 4
    paper_or_study = _contains(text, r"\b(paper|publication|published study|research study)\b")
    experimental_result = _contains(
        text,
        r"\b(experiment(?:al|s)?|evaluat(?:e|ed|ion)|results?|speedup|latency|throughput)\b",
    )
    if paper_or_study and experimental_result:
        return 3
    if _contains(text, r"\b(propose[ds]?|design|architecture|concept|approach)\b"):
        return 2
    return 1


def _dedupe_records(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        try:
            source_id = _record_reference(record)["source_id"]
        except (TypeError, ValueError):
            continue
        if source_id not in unique:
            unique[source_id] = dict(record)
    return list(unique.values())


def _query_specs(technology: TechName, trl_reason: str, market_reason: str) -> list[tuple[str, Stance, str]]:
    specs: list[tuple[str, Stance, str]] = []
    for index in range(config.QUERIES_PER_STANCE):
        template = _TRL_QUERY_TEMPLATES[index % len(_TRL_QUERY_TEMPLATES)]
        query = template.format(tech=technology)
        if trl_reason:
            query += f" retry focus: {trl_reason} independent evidence"
        specs.append(("trl", "neutral", query))

    for stance in ("positive", "negative"):
        templates = _MARKET_QUERY_TEMPLATES[stance]
        for index in range(config.QUERIES_PER_STANCE):
            query = templates[index % len(templates)].format(tech=technology)
            if market_reason:
                focus = (
                    "different sources deployment framework production usage"
                    if stance == "positive"
                    else "different sources limitation barrier operational overhead"
                )
                query += f" retry focus: {market_reason} {focus}"
            specs.append(("market", stance, query))
    return specs


def _search_for_technology(
    technology: TechName,
    search_fn: SearchFunction,
    trl_reason: str,
    market_reason: str,
) -> tuple[list[dict[str, Any]], int, int]:
    records: list[dict[str, Any]] = []
    empty_count = 0
    error_count = 0
    for perspective, stance, query in _query_specs(technology, trl_reason, market_reason):
        try:
            found = search_fn(
                query,
                stance=stance,
                max_results=config.WEB_SEARCH_MAX_RESULTS,
                used_by="market",
            )
        except Exception:  # noqa: BLE001 - injected providers must not stop the graph
            error_count += 1
            continue
        if getattr(found, "error_code", None):
            error_count += 1
            continue
        if not found:
            empty_count += 1
            continue
        for result in found:
            if not isinstance(result, Mapping):
                continue
            record = dict(result)
            record["_perspective"] = perspective
            record["_query_stance"] = stance
            records.append(record)
    return _dedupe_records(records), empty_count, error_count


def _classification_prompt(technology: TechName, records: Sequence[Mapping[str, Any]]) -> str:
    evidence_input = [
        {
            "source_id": _record_reference(record)["source_id"],
            "title": _record_reference(record)["title"],
            "content": _content(record),
            "search_intent": record.get("_query_stance", "neutral"),
            "search_perspective": record.get("_perspective", "market"),
        }
        for record in records
    ]
    return (
        load_prompt("market")
        + "\n\n대상 기술: "
        + technology
        + "\n입력 검색 결과 JSON:\n"
        + json.dumps(evidence_input, ensure_ascii=False)
    )


def _coerce_classification(value: Any) -> MarketClassification:
    if isinstance(value, MarketClassification):
        return value
    if hasattr(MarketClassification, "model_validate"):
        return MarketClassification.model_validate(value)
    return MarketClassification.parse_obj(value)


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _validated_items(
    technology: TechName,
    records: Sequence[Mapping[str, Any]],
    client: Any,
) -> tuple[list[MarketEvidenceItem], bool]:
    if not records:
        return [], False
    try:
        response = client.invoke(_classification_prompt(technology, records), MarketClassification)
        classified = _coerce_classification(response)
    except Exception:  # noqa: BLE001 - LLM failures become E-1002 in node output
        return [], True

    by_source = {_record_reference(record)["source_id"]: record for record in records}
    valid: list[MarketEvidenceItem] = []
    seen: set[tuple[str, MarketCategory]] = set()
    for item in classified.items:
        record = by_source.get(item.source_id)
        if record is None:
            continue
        source_text = _normalize_text(f"{record.get('title', '')} {_content(record)}")
        claim = _normalize_text(item.claim)
        if not claim or claim not in source_text:
            continue

        key = (item.source_id, item.market_category)
        if key in seen:
            continue
        seen.add(key)
        valid.append(
            MarketEvidenceItem(
                source_id=item.source_id,
                claim=item.claim.strip(),
                stance=item.stance,
                market_category=item.market_category,
                trl_level=(
                    min(item.trl_level, _inferred_trl_ceiling(record))
                    if item.trl_level is not None
                    else None
                ),
            )
        )
    return valid, False


def _evidence(item: MarketEvidenceItem, contextual_market: bool = False) -> Evidence:
    claim = item.claim
    if contextual_market:
        claim = f"관련 시장의 맥락적 지표: {claim}"
    return {"claim": claim, "source_id": item.source_id, "stance": item.stance}


def _trl_estimate(
    technology: TechName,
    items: Sequence[MarketEvidenceItem],
    error_prefix: str,
) -> TRLEstimate:
    level_by_source: dict[str, int] = {}
    item_by_source: dict[str, MarketEvidenceItem] = {}
    for item in items:
        if item.trl_level is None:
            continue
        if item.trl_level > level_by_source.get(item.source_id, 0):
            level_by_source[item.source_id] = item.trl_level
            item_by_source[item.source_id] = item

    confirmed_level = 1
    for candidate in range(9, 0, -1):
        if sum(level >= candidate for level in level_by_source.values()) >= 2:
            confirmed_level = candidate
            break

    highest = max(level_by_source.values(), default=1)
    count = sum(level >= confirmed_level for level in level_by_source.values())
    if len(level_by_source) < 2:
        rationale = (
            f"{technology}의 서로 다른 공개 source_id가 2개 미만이어서 TRL 1로 보수적으로 추정했다."
        )
    else:
        rationale = (
            f"{technology}의 TRL {confirmed_level} 조건이 서로 다른 source_id {count}개에서 "
            "확인되어 해당 단계를 공개 근거상 최고 확정 단계로 추정했다."
        )
    if highest > confirmed_level:
        higher_count = sum(level >= highest for level in level_by_source.values())
        rationale += (
            f" TRL {highest} 신호는 {higher_count}개 source_id에서만 확인되어 "
            "승급하지 않고 가능성으로만 기록했다."
        )

    uncertainty = (
        "본 TRL은 공개 정보 기반 추정이며, KV cache 기술은 논문 발표 시점과 실제 채택 간 "
        "시차가 있어 실제 단계와 다를 수 있음."
    )
    if error_prefix:
        uncertainty = f"{error_prefix} {uncertainty}"
    evidence = [_evidence(item_by_source[source_id]) for source_id in level_by_source]
    return {
        "level": confirmed_level,
        "rationale": rationale,
        "evidence": evidence,
        "uncertainty": uncertainty,
    }


def _market_result(
    technology: TechName,
    items: Sequence[MarketEvidenceItem],
    error_prefix: str,
) -> MarketResult:
    categories: dict[str, list[Evidence]] = {
        "market_size_growth": [],
        "adoption": [],
        "ecosystem": [],
    }
    for item in items:
        if item.market_category == "none":
            continue
        categories[item.market_category].append(
            _evidence(item, contextual_market=item.market_category == "market_size_growth")
        )
    all_market = [evidence for values in categories.values() for evidence in values]
    negative_count = sum(evidence["stance"] == "negative" for evidence in all_market)
    summary = (
        f"{technology} 자체의 직접 시장 규모로 확대 해석하지 않고, 시장 규모·성장 근거 "
        f"{len(categories['market_size_growth'])}건은 관련 시장의 맥락적 지표로 구분했다. "
        f"채택 근거 {len(categories['adoption'])}건, 생태계 근거 {len(categories['ecosystem'])}건, "
        f"실제 내용에서 확인된 한계·반론 근거 {negative_count}건을 확보했다. 공개 근거만으로 "
        "기술 추천이나 우열을 판단하지 않는다."
    )
    if error_prefix:
        summary = f"{error_prefix} {summary}"
    return {
        "market_size_growth": categories["market_size_growth"],
        "adoption": categories["adoption"],
        "ecosystem": categories["ecosystem"],
        "summary": summary,
    }


def _merge_reference(references: dict[str, Reference], reference: Reference) -> None:
    source_id = reference["source_id"]
    incoming = dict(reference)
    incoming["used_by"] = list(dict.fromkeys(incoming.get("used_by") or ["market"]))
    existing = references.get(source_id)
    if existing is None:
        references[source_id] = incoming  # type: ignore[assignment]
        return
    existing["used_by"] = list(dict.fromkeys(existing["used_by"] + incoming["used_by"]))
    if existing["stance"] != incoming["stance"]:
        existing["stance"] = "neutral"
    for field in ("author", "date", "title", "venue"):
        if not existing[field] and incoming[field]:
            existing[field] = incoming[field]


def _base_error_prefix(
    records: Sequence[Mapping[str, Any]],
    empty_count: int,
    error_count: int,
    llm_failed: bool,
    retrying: bool,
    retry_count: int,
) -> str:
    if llm_failed or error_count:
        return "[E-1002] 검색 또는 LLM 호출 장애로 일부 근거를 확인하지 못함."
    if not records:
        if retrying and retry_count >= config.MAX_RETRY:
            return "[E-1005] 재조사 상한에 도달했으며 검색 결과를 확보하지 못함."
        if empty_count:
            return "[E-1001] 검색 결과 없음."
    return ""


def market_node(
    state: State,
    *,
    search_fn: SearchFunction | None = None,
    structured_client: Any = None,
) -> dict:
    """``trl_result``·``market_result``·이번 실행에서 사용한 ``references``만 반환한다."""

    technologies = _resolve_technologies(state)
    active_search = search_fn or search_web
    client = structured_client or StructuredClient()
    trl_reason = retry_hint(state, "trl")
    market_reason = retry_hint(state, "market")

    records_by_tech: dict[TechName, list[dict[str, Any]]] = {}
    items_by_tech: dict[TechName, list[MarketEvidenceItem]] = {}
    status: dict[TechName, tuple[int, int, bool]] = {}
    for technology in technologies:
        records, empty_count, error_count = _search_for_technology(
            technology, active_search, trl_reason, market_reason
        )
        items, llm_failed = _validated_items(technology, records, client)
        records_by_tech[technology] = records
        items_by_tech[technology] = items
        status[technology] = (empty_count, error_count, llm_failed)

    successful = [technology for technology in technologies if items_by_tech[technology]]
    one_tech_only = len(successful) == 1
    trl_result: dict[TechName, TRLEstimate] = {}
    market_result: dict[TechName, MarketResult] = {}
    used_source_ids: set[str] = set()

    for technology in technologies:
        empty_count, error_count, llm_failed = status[technology]
        prefix = _base_error_prefix(
            records_by_tech[technology],
            empty_count,
            error_count,
            llm_failed,
            bool(trl_reason or market_reason),
            state.get("retry_count", 0),
        )
        if one_tech_only:
            prefix = "[E-1004] 한 기술만 근거를 확보함. " + prefix
        trl_result[technology] = _trl_estimate(technology, items_by_tech[technology], prefix)
        market_result[technology] = _market_result(technology, items_by_tech[technology], prefix)
        used_source_ids.update(evidence["source_id"] for evidence in trl_result[technology]["evidence"])
        for field in ("market_size_growth", "adoption", "ecosystem"):
            used_source_ids.update(
                evidence["source_id"] for evidence in market_result[technology][field]
            )

    references: dict[str, Reference] = {}
    for technology in technologies:
        for record in records_by_tech[technology]:
            reference = _record_reference(record)
            if reference["source_id"] in used_source_ids:
                _merge_reference(references, reference)

    return {
        "trl_result": trl_result,
        "market_result": market_result,
        "references": list(references.values()),
    }
