"""관점 간 일치·상충 종합. 반환 계약은 state.Synthesis 그대로 유지한다.

LLM은 근거 문장을 다시 쓰지 않고, 입력 Evidence에 붙인 짧은 ID(예: "TQ-market-2")만 고른다.
코드가 ID를 원래 Evidence로 되돌려 고정 연결 문구로 종합하므로 허위 수치·추천 문장·복사 오류가 들어가지 않는다.
"""

import json
from collections import Counter
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

import config
import llm
from state import (PERSPECTIVES, PERSPECTIVE_FIELDS, TECHS, Conflict, Evidence, Perspective, State, Synthesis,
                   TechName, perspective_evidence)

PERSPECTIVE_NAMES = {"trl": "기술 성숙도(TRL)", "market": "시장성", "stakeholder": "이해관계자", "domain": "도메인 적용"}


class _Finding(BaseModel):
    """LLM 내부 출력: 근거 ID만 받고, 검증 뒤 공용 State 형태로 변환한다."""

    model_config = ConfigDict(extra="forbid")
    tech: TechName
    # Literal이라야 구조화 출력 스키마에 enum이 실려 모델이 관점 자리에 기술명 등 다른 값을 넣지 못한다.
    perspective_a: Perspective
    perspective_b: Perspective
    evidence_a: list[str] = Field(min_length=1)
    evidence_b: list[str] = Field(min_length=1)


class _SynthesisDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agreements: list[_Finding]
    conflicts: list[_Finding]


class SynthesisClient(Protocol):
    def invoke(self, prompt: str, response_model: type[_SynthesisDraft]) -> _SynthesisDraft: ...


def _tech_code(tech: str) -> str:
    return "".join(ch for ch in tech if ch.isupper()) or tech  # TurboQuant → TQ, InfiniGen → IG


def evidence_catalog(state: State) -> dict[str, tuple[TechName, str, Evidence]]:
    """기술·관점별 고유 Evidence에 결정적 ID를 붙인다. 같은 State면 항상 같은 ID."""
    catalog: dict[str, tuple[TechName, str, Evidence]] = {}
    for tech in TECHS:
        for p in PERSPECTIVES:
            # perspective_evidence가 (source_id, claim) 기준으로 이미 중복을 제거해 돌려준다.
            for i, ev in enumerate(perspective_evidence(state, p, tech), 1):
                catalog[f"{_tech_code(tech)}-{p}-{i}"] = (tech, p, ev)
    return catalog


def _limitations(state: State) -> list[str]:
    suff = state.get("sufficiency", {})
    reasons = suff.get("reasons", {})
    limits = [f"{PERSPECTIVE_NAMES.get(p, p)} 근거 부족: {why}" for p, why in reasons.items()]
    missing = [p for p in PERSPECTIVES if not suff.get(p, False)]
    for p in missing:
        if p not in reasons:
            limits.append(f"{PERSPECTIVE_NAMES[p]} 충분성 확인 불가: 판정 또는 부족 사유 미제공")
    if missing and state.get("retry_count", 0) > config.MAX_RETRY:
        names = ", ".join(PERSPECTIVE_NAMES[p] for p in missing)
        limits.append(f"[E-1005] 재조사 상한 도달: {names} 근거 부족 상태로 종합")
    for tech in TECHS:
        for limit in state.get("tech_summary", {}).get(tech, {}).get("limitations", []):
            if limit:
                limits.append(f"{tech} 기술 조사: {limit}")
        for p, (key, _) in PERSPECTIVE_FIELDS.items():
            result = state.get(key, {}).get(tech, {})
            text = result.get("uncertainty" if p == "trl" else "summary", "")
            if text and (p == "trl" or text.lstrip().startswith(tuple(f"[E-100{i}]" for i in range(1, 6)))):
                limits.append(f"{tech} {PERSPECTIVE_NAMES[p]}: {text}")
    limits.append("TRL은 공개 정보 기반 추정이며 실제 적용 수준과 다를 수 있음")
    return list(dict.fromkeys(limits))


def _resolve(finding: _Finding, catalog: dict) -> tuple[list[Evidence], list[Evidence]] | None:
    """모든 ID가 카탈로그에 있고 finding의 기술·관점과 일치할 때만 Evidence로 되돌린다.

    검증 실패는 draft 전체가 아니라 항목 단위로만 배제한다(한 항목이 틀려도 나머지는 살린다).
    """
    if finding.perspective_a == finding.perspective_b:
        return None  # 관점 간 비교가 아니므로 이 항목만 버린다.
    resolved = []
    for perspective, ids in ((finding.perspective_a, finding.evidence_a), (finding.perspective_b, finding.evidence_b)):
        evs = []
        for eid in dict.fromkeys(ids):
            entry = catalog.get(eid)
            if entry is None or entry[0] != finding.tech or entry[1] != perspective:
                return None
            evs.append(entry[2])
        resolved.append(evs)
    return resolved[0], resolved[1]


def _description(finding: _Finding, kind: str, evs_a: list[Evidence], evs_b: list[Evidence]) -> str:
    # 자유 생성 문장은 받지 않는다. 입력 원문과 고정 연결 문구만 출력한다.
    parts = []
    for perspective, evs in ((finding.perspective_a, evs_a), (finding.perspective_b, evs_b)):
        claims = list(dict.fromkeys(ev["claim"] for ev in evs))
        parts.append(f"{PERSPECTIVE_NAMES[perspective]} 관점의 근거: " + " / ".join(f"「{c}」" for c in claims))
    relation = "관점 간 일치 근거" if kind == "agreements" else "관점 간 상충·조건 차이 근거"
    ids = list(dict.fromkeys(ev["source_id"] for ev in evs_a + evs_b))
    return f"{relation} — {'; '.join(parts)} (출처: {', '.join(ids)})"


def _payload(state: State, catalog: dict, counts: dict) -> dict:
    """LLM 입력: 기술 맥락 + 관점별 요약 + ID가 붙은 근거 목록. 긴 근거 문장은 한 번만 넣는다."""
    summaries: dict[str, dict[str, str]] = {}
    for p, (key, _) in PERSPECTIVE_FIELDS.items():
        for tech in TECHS:
            result = state.get(key, {}).get(tech, {})
            if p == "trl":
                text = f"TRL {result.get('level')} — {result.get('rationale', '')}" if result else ""
            else:
                text = result.get("summary", "")
            summaries.setdefault(p, {})[tech] = text
    return {
        "tech_summary": state.get("tech_summary", {}),
        "perspective_summaries": summaries,
        "evidence": [{"id": eid, "tech": tech, "perspective": p, "stance": ev["stance"], "claim": ev["claim"]}
                     for eid, (tech, p, ev) in catalog.items()],
        "sufficiency": state.get("sufficiency", {}),
        "stance_counts": counts,
    }


def synthesis_node(state: State, *, client: SynthesisClient | None = None) -> dict:
    """새 검색 없이 입력 State를 종합한다. client는 API 없는 테스트용 주입점."""
    synthesis: Synthesis = {
        "agreements": [], "conflicts": [],
        "neutrality_note": "공개 정보에 근거해 관점별 일치·상충을 정리하며 기술의 우열을 판정하거나 추천하지 않음. "
                           "근거 수와 stance 개수는 성능 점수나 우열을 의미하지 않음.",
        "limitations": _limitations(state),
    }
    catalog = evidence_catalog(state)
    counts = {p: {tech: dict(Counter(ev["stance"] for t, q, ev in catalog.values() if t == tech and q == p))
                  for tech in TECHS} for p in PERSPECTIVES}
    if not catalog:
        synthesis["limitations"].append("[E-1001] 관점별 근거가 없어 일치·상충을 종합하지 못함")
        return {"synthesis": synthesis}

    try:
        prompt = (llm.load_prompt("synthesis") + "\n\n입력 State (JSON):\n"
                  + json.dumps(_payload(state, catalog, counts), ensure_ascii=False))
        response = (client if client is not None else llm.StructuredClient(role="generator")).invoke(prompt, _SynthesisDraft)
        # 실제 클라이언트와 테스트용 dict 모두 동일한 스키마 검증을 거친다.
        draft = _SynthesisDraft.model_validate(response)
    except (llm.LLMError, ValidationError):
        # 외부 예외 원문은 키·요청 내용이 포함될 수 있으므로 보고서에 복사하지 않는다.
        synthesis["limitations"].append("[E-1002] 평가 종합 LLM 호출 또는 구조화 출력 검증 실패")
        return {"synthesis": synthesis}

    rejected = False
    for kind in ("agreements", "conflicts"):
        for finding in getattr(draft, kind):
            resolved = _resolve(finding, catalog)
            if resolved is None:
                rejected = True
                continue
            description = _description(finding, kind, *resolved)
            if kind == "agreements":
                pair = f"{PERSPECTIVE_NAMES[finding.perspective_a]}·{PERSPECTIVE_NAMES[finding.perspective_b]}"
                text = f"{finding.tech} ({pair}): {description}"
                if text not in synthesis["agreements"]:
                    synthesis["agreements"].append(text)
            else:
                conflict: Conflict = {
                    "tech": finding.tech, "perspective_a": finding.perspective_a,
                    "perspective_b": finding.perspective_b, "description": description,
                }
                if conflict not in synthesis["conflicts"]:
                    synthesis["conflicts"].append(conflict)
    if rejected:
        synthesis["limitations"].append("입력 근거 목록에 없거나 기술·관점이 맞지 않는 근거 ID를 쓴 종합 항목을 제외함")
    return {"synthesis": synthesis}
