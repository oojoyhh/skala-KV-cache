"""Market and public-evidence TRL assessment node for TurboQuant/InfiniGen.

This module has no LLM dependency because the repository does not yet provide an
LLM wrapper.  It uses conservative, deterministic evidence classification and
plain dictionaries compatible with the agreed State interface.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from tools.web_search import (
    MissingTavilyAPIKeyError,
    make_source_id,
    search_web,
    to_reference,
)


SearchFunction = Callable[..., List[Dict[str, Any]]]

_TECH_ROLES = (("tech_sw", "TurboQuant"), ("tech_hw", "InfiniGen"))
_QUERY_SPECS = (
    ("trl", "neutral", "{tech} KV cache official paper implementation experiments"),
    ("adoption", "positive", "{tech} adoption deployment framework support LLM serving"),
    ("ecosystem", "positive", "{tech} vLLM SGLang Hugging Face integration"),
    ("adoption", "negative", "{tech} limitations barriers quality degradation deployment"),
    (
        "market_size_growth",
        "neutral",
        "{tech} LLM inference optimization KV cache AI infrastructure market growth",
    ),
)

_NEGATIVE_TERMS = re.compile(
    r"\b(limit(?:ation|ed|s)?|barrier|concern|degrad(?:e|ation)|overhead|"
    r"bottleneck|challenge|drawback|trade-?off|accuracy loss|not support(?:ed)?|"
    r"not available|no production)\b",
    re.IGNORECASE,
)
_POSITIVE_TERMS = re.compile(
    r"\b(adopt(?:ed|ion)?|support(?:ed|s)?|integrat(?:e|ed|ion)|improv(?:e|ed|ement)|"
    r"speedup|reduce[ds]?|benefit|deploy(?:ed|ment)?|available|release[ds]?)\b",
    re.IGNORECASE,
)


class MarketSearchError(RuntimeError):
    """Raised when no market search can be completed."""


def _resolve_technologies(state: Mapping[str, Any]) -> List[str]:
    technologies = []
    for state_key, expected_name in _TECH_ROLES:
        value = state.get(state_key) or expected_name
        if value != expected_name:
            raise ValueError(
                f"{state_key} must be {expected_name!r} under the current State contract; "
                f"got {value!r}"
            )
        technologies.append(value)
    return technologies


def _record_reference(record: Mapping[str, Any]) -> Dict[str, Any]:
    if "reference" in record and isinstance(record["reference"], Mapping):
        return dict(record["reference"])
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
            "title": str(record.get("title") or "Untitled web source"),
            "venue": str(record.get("venue") or ""),
            "url": url,
            "used_by": used_by,
            "stance": str(record.get("stance") or "neutral"),
        }


def _content(record: Mapping[str, Any]) -> str:
    return " ".join(str(record.get("content") or record.get("summary") or "").split())


def _display_excerpt(record: Mapping[str, Any], limit: int = 280) -> str:
    text = _content(record) or str(record.get("title") or "내용 미제공")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _actual_evidence_stance(record: Mapping[str, Any]) -> str:
    text = f"{record.get('title', '')} {_content(record)}"
    requested = str(record.get("_query_stance") or record.get("stance") or "neutral")
    if requested == "negative" and _NEGATIVE_TERMS.search(text):
        return "negative"
    if requested == "positive" and _POSITIVE_TERMS.search(text):
        return "positive"
    return "neutral"


def _evidence(record: Mapping[str, Any], category: str) -> Dict[str, str]:
    reference = _record_reference(record)
    title = reference["title"]
    excerpt = _display_excerpt(record)
    if category == "market_size_growth":
        claim = (
            f"'{title}'은(는) 관련 LLM 추론·인프라 배경 자료로 분류된다. "
            f"이는 해당 기술 자체의 직접 시장 규모로 해석하지 않는다. 출처 내용: {excerpt}"
        )
    else:
        claim = f"'{title}' 자료가 보고한 내용: {excerpt}"
    return {
        "claim": claim,
        "source_id": reference["source_id"],
        "stance": _actual_evidence_stance(record),
    }


def _contains(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _inferred_trl_ceiling(record: Mapping[str, Any]) -> int:
    """Infer only the highest maturity indicator explicitly visible in a source."""

    text = f"{record.get('title', '')} {_content(record)}"
    high_stage_negated = _contains(
        text,
        r"\b(no|not|without|lack(?:s|ing|ed)?)\b.{0,35}\b(production|commercial|customer|deployed)\b",
    )
    if not high_stage_negated and _contains(
        text,
        r"\b(in production|production deployment|commercially deployed|customer deployment|"
        r"officially adopted)\b",
    ):
        return 9
    if not high_stage_negated and _contains(
        text,
        r"\b(generally available|general availability|official product release|commercial release)\b",
    ):
        return 8
    if not high_stage_negated and _contains(text, r"\b(pilot|public preview|private preview|beta)\b"):
        return 7

    serving_context = _contains(text, r"\b(llm serving|inference serving|serving system|cluster|end-to-end)\b")
    demonstrated = _contains(text, r"\b(demonstrat(?:e|ed|ion)|benchmark(?:ed|s)?|evaluat(?:e|ed|ion))\b")
    if serving_context and demonstrated:
        return 6

    prototype_or_integration = _contains(text, r"\b(prototype|integrat(?:e|ed|ion)|testbed)\b")
    if prototype_or_integration and demonstrated:
        return 5
    if _contains(
        text,
        r"\b(source code|open-source|open source|github|repository|reproducible|"
        r"implementation (?:is )?available)\b",
    ):
        return 4
    if _contains(
        text,
        r"\b(experiment(?:al|s)?|evaluat(?:e|ed|ion)|benchmark(?:ed|s)?|results?|"
        r"speedup|latency|throughput)\b",
    ):
        return 3
    if _contains(text, r"\b(propose[ds]?|design|architecture|concept|approach)\b"):
        return 2
    return 1


def _dedupe_records(records: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    unique: Dict[str, Dict[str, Any]] = {}
    for record in records:
        reference = _record_reference(record)
        source_id = reference["source_id"]
        if source_id not in unique:
            unique[source_id] = dict(record)
    return list(unique.values())


def _trl_estimate(technology: str, records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    candidates = _dedupe_records(
        record for record in records if record.get("_dimension") != "market_size_growth"
    )
    levels = [(record, _inferred_trl_ceiling(record)) for record in candidates]

    confirmed_level = 1
    for level in range(9, 0, -1):
        supporting_ids = {
            _record_reference(record)["source_id"]
            for record, ceiling in levels
            if ceiling >= level
        }
        if len(supporting_ids) >= 2:
            confirmed_level = level
            break

    sorted_levels = sorted(levels, key=lambda item: item[1], reverse=True)
    trl_evidence = []
    for record, _ in sorted_levels[:6]:
        trl_evidence.append(_evidence(record, "trl"))

    highest_observed = max((level for _, level in levels), default=1)
    confirmed_count = sum(1 for _, level in levels if level >= confirmed_level)
    rationale = (
        f"{technology}의 공개 자료에서 TRL {confirmed_level} 조건과 양립하는 "
        f"서로 다른 source_id {confirmed_count}개를 확인해 해당 단계로 보수적으로 추정했다."
    )
    if len(levels) < 2:
        rationale = (
            f"{technology}의 TRL을 뒷받침할 서로 다른 공개 source_id가 2개 미만이어서 "
            "기초 공개 정보 단계인 TRL 1로 보수적으로 추정했다."
        )
    elif highest_observed > confirmed_level:
        higher_count = sum(1 for _, level in levels if level >= highest_observed)
        rationale += (
            f" TRL {highest_observed} 수준의 신호는 {higher_count}개 source_id에서만 보여 "
            "확정하지 않고 가능성으로만 남겼다."
        )
    if confirmed_level < 9:
        rationale += " 지속적인 상용 production 운용은 확인되지 않았다."

    return {
        "level": confirmed_level,
        "rationale": rationale,
        "evidence": trl_evidence,
        "uncertainty": (
            "본 TRL은 공개 정보 기반 추정이며, 자료 간 독립성 및 실제 적용 수준의 확인이 "
            "제한적이다. 논문 저자와 동일 연구팀의 구현은 독립적인 시장 채택 근거로 "
            "간주하지 않았다."
        ),
    }


def _category_evidence(
    records: Sequence[Mapping[str, Any]], category: str, limit: int = 6
) -> List[Dict[str, str]]:
    selected = _dedupe_records(record for record in records if record.get("_dimension") == category)
    return [_evidence(record, category) for record in selected[:limit]]


def _market_result(
    technology: str,
    records: Sequence[Mapping[str, Any]],
    search_error_count: int,
) -> Dict[str, Any]:
    market_size_growth = _category_evidence(records, "market_size_growth")
    adoption = _category_evidence(records, "adoption")
    ecosystem = _category_evidence(records, "ecosystem")
    negative_count = sum(
        evidence["stance"] == "negative" for evidence in adoption + ecosystem
    )

    summary = (
        f"{technology} 자체의 직접 시장 규모 자료는 이 공개 검색에서 확인하지 않았으며, "
        f"시장 규모·성장 자료 {len(market_size_growth)}건은 관련 LLM 추론 및 AI 인프라의 "
        f"배경 정보로만 분류했다. 채택·배포 관련 근거 {len(adoption)}건과 생태계·연동 "
        f"관련 근거 {len(ecosystem)}건을 확인했으며, 이 중 명시적 제약 또는 도입 장벽을 "
        f"포함한 근거는 {negative_count}건이다. 연구팀 자체 발표는 상용 채택으로 확대 "
        "해석하지 않았고, 공개 근거만으로 기술 추천이나 우열 판단을 하지 않는다."
    )
    if search_error_count:
        summary += f" 검색 {search_error_count}건은 호출 오류로 확인하지 못했다."

    return {
        "market_size_growth": market_size_growth,
        "adoption": adoption,
        "ecosystem": ecosystem,
        "summary": summary,
    }


def _merge_reference(
    references: Dict[str, Dict[str, Any]], reference: Mapping[str, Any]
) -> None:
    source_id = str(reference["source_id"])
    incoming = dict(reference)
    incoming["used_by"] = list(dict.fromkeys(incoming.get("used_by") or ["market"]))
    existing = references.get(source_id)
    if existing is None:
        references[source_id] = incoming
        return
    existing["used_by"] = list(
        dict.fromkeys(list(existing.get("used_by", [])) + incoming["used_by"])
    )
    if existing.get("stance") != incoming.get("stance"):
        existing["stance"] = "neutral"
    for field in ("author", "date", "title", "venue"):
        if not existing.get(field) and incoming.get(field):
            existing[field] = incoming[field]


def _search_for_technology(
    technology: str,
    search_fn: SearchFunction,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    records: List[Dict[str, Any]] = []
    errors: List[str] = []
    for dimension, stance, query_template in _QUERY_SPECS:
        query = query_template.format(tech=technology)
        try:
            found = search_fn(query, stance=stance, max_results=5, used_by="market")
        except MissingTavilyAPIKeyError:
            raise
        except Exception as exc:
            errors.append(f"{query}: {exc}")
            continue
        for result in found or []:
            record = dict(result)
            record["_dimension"] = dimension
            record["_query_stance"] = stance
            records.append(record)
    return records, errors


def market_node(
    state: Mapping[str, Any],
    search_fn: Optional[SearchFunction] = None,
) -> Dict[str, Any]:
    """Return only ``trl_result``, ``market_result``, and ``references``.

    LangGraph can call this with only ``state``. Tests may inject ``search_fn`` to
    run fully offline. Existing State references are intentionally not copied
    into the return value because list reducers (such as ``operator.add``) should
    merge only this node's newly produced references.
    """

    if not isinstance(state, Mapping):
        raise TypeError("state must be a mapping compatible with the shared State")
    technologies = _resolve_technologies(state)
    active_search = search_fn or search_web

    records_by_technology: Dict[str, List[Dict[str, Any]]] = {}
    errors_by_technology: Dict[str, List[str]] = {}
    references: Dict[str, Dict[str, Any]] = {}

    for technology in technologies:
        records, errors = _search_for_technology(technology, active_search)
        records_by_technology[technology] = records
        errors_by_technology[technology] = errors
        for record in records:
            _merge_reference(references, _record_reference(record))

    if not references and all(errors_by_technology.values()):
        details = " | ".join(
            errors_by_technology[technology][0]
            for technology in technologies
            if errors_by_technology[technology]
        )
        raise MarketSearchError(f"All market web searches failed. First errors: {details}")

    trl_result = {
        technology: _trl_estimate(technology, records_by_technology[technology])
        for technology in technologies
    }
    market_result = {
        technology: _market_result(
            technology,
            records_by_technology[technology],
            len(errors_by_technology[technology]),
        )
        for technology in technologies
    }

    return {
        "trl_result": trl_result,
        "market_result": market_result,
        "references": list(references.values()),
    }
