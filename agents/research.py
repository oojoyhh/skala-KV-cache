"""논문 RAG를 이용한 기술 조사 노드 (2번 담당).

공유 retriever와 LLM은 실행 시 불러온다. 테스트에서는 같은 계약의 함수를
주입할 수 있으며, 그래프는 ``research_node(state)``를 그대로 호출한다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import config
from state import Evidence, Reference, State, TECHS, TechName, TechSummary


RetrieveFn = Callable[[str, int], Sequence[Mapping[str, Any]]]
GenerateFn = Callable[[TechName, str, Sequence[dict[str, Any]]], Any]
GradeFn = Callable[[str, Mapping[str, Any]], bool]
RewriteFn = Callable[[str, TechName, int], str]
ReferenceFn = Callable[[str], Reference]

QUERY_HINTS = {
    "TurboQuant": "random rotation scalar quantization QJL KV cache LongBench limitations",
    "InfiniGen": "CPU GPU offloading partial query key prefetch throughput limitations",
}


def _empty_summary(name: TechName, reason: str) -> TechSummary:
    return {
        "name": name,
        "camp": "SW" if name == config.TECH_SW else "HW",
        "approach": "",
        "scope": "",
        "key_metrics": {},
        "limitations": [reason],
        "evidence": [],
    }


def _normalize_chunk(raw: Mapping[str, Any]) -> dict[str, Any]:
    """1번 retriever의 source_id/arxiv_id/page 형식을 검증한다."""
    metadata = raw.get("metadata", raw)
    text = raw.get("text", raw.get("page_content", ""))
    source_id = str(metadata.get("source_id", ""))
    arxiv_id = str(metadata.get("arxiv_id", ""))
    page = int(metadata.get("page", 0))
    if not arxiv_id and source_id.startswith("arxiv:"):
        arxiv_id = source_id.split(":", 1)[1].split("#", 1)[0]
    expected_id = f"arxiv:{arxiv_id}#p{page}"
    if not text or not arxiv_id or page < 1 or source_id != expected_id:
        raise ValueError("RAG 청크에 text, arxiv_id, 1-based page, source_id가 필요합니다.")
    return {
        "text": str(text).strip(),
        "source_id": source_id,
        "arxiv_id": arxiv_id,
        "page": page,
        "section": str(metadata.get("section", "")),
    }


def _query(name: TechName) -> str:
    return f"{name} KV cache mechanism evaluation key metrics limitations trade-offs"


def _default_grade(query: str, chunk: Mapping[str, Any]) -> bool:
    from pydantic import BaseModel

    from llm import structured

    class RelevanceDecision(BaseModel):
        relevant: bool

    prompt = (
        "영어 논문 발췌가 질문의 기술 원리, 측정 결과 또는 한계를 직접 뒷받침하는지 "
        "판정하세요. 제목이나 기술명만 일치하면 false입니다.\n"
        f"질문: {query}\n발췌: {chunk['text']}"
    )
    return structured(prompt, RelevanceDecision, role="judge").relevant


def _default_rewrite(query: str, name: TechName, attempt: int) -> str:
    from llm import generate

    prompt = (
        "영어 논문 검색 질의를 더 구체적으로 한 줄로 재작성하세요. "
        "기존 질의를 반복하거나 확인되지 않은 사실을 추가하지 마세요.\n"
        f"기술: {name}\n기존 질의: {query}\n"
        f"찾을 항목: {QUERY_HINTS[name]}\n재검색 회차: {attempt}"
    )
    rewritten = generate(prompt, role="judge").strip()
    if not rewritten or rewritten == query:
        raise ValueError("재작성된 검색 질의가 비어 있거나 기존 질의와 같습니다.")
    return rewritten


def _default_generate(name: TechName, camp: str, chunks: Sequence[dict[str, Any]]) -> Any:
    from pydantic import BaseModel

    from llm import load_prompt, structured

    class CitedClaim(BaseModel):
        claim: str
        source_id: str
        stance: str

    class ResearchDraft(BaseModel):
        name: str
        camp: str
        approach: str
        scope: str
        key_metrics: dict[str, str]
        limitations: list[str]
        evidence: list[CitedClaim]

    excerpts = "\n\n".join(
        f"[{chunk['source_id']}] {chunk['section']}\n{chunk['text']}" for chunk in chunks
    )
    prompt = (
        load_prompt("research")
        + f"\n기술: {name}\n진영: {camp}\n\n검색된 논문 발췌:\n{excerpts}"
    )
    return structured(prompt, ResearchDraft, role="generator")


def _validate_summary(
    raw: Any, name: TechName, chunks: Sequence[dict[str, Any]]
) -> TechSummary:
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    if not isinstance(raw, Mapping):
        raise ValueError("기술 조사 결과가 객체가 아닙니다.")
    camp = "SW" if name == config.TECH_SW else "HW"
    if raw.get("name") != name or raw.get("camp") != camp:
        raise ValueError("기술명 또는 진영이 입력과 다릅니다.")
    metrics = raw.get("key_metrics")
    if not isinstance(metrics, Mapping):
        raise ValueError("key_metrics는 항목명-측정값 사전이어야 합니다.")
    allowed = {chunk["source_id"] for chunk in chunks}
    text_by_source = {
        source_id: " ".join(chunk["text"] for chunk in chunks if chunk["source_id"] == source_id)
        for source_id in allowed
    }
    evidence: list[Evidence] = []
    for item in raw.get("evidence", []):
        if not isinstance(item, Mapping):
            raise ValueError("Evidence 형식이 잘못되었습니다.")
        claim = str(item.get("claim", "")).strip()
        source_id = str(item.get("source_id", ""))
        stance = str(item.get("stance", ""))
        if not claim or source_id not in allowed or stance not in {"positive", "negative", "neutral"}:
            raise ValueError("Evidence 주장, 검색 출처 또는 stance가 유효하지 않습니다.")
        if any(
            number not in text_by_source[source_id]
            for number in re.findall(r"\d+(?:\.\d+)?", claim)
        ):
            raise ValueError("인용된 페이지에 없는 숫자가 Evidence 주장에 있습니다.")
        evidence.append({"claim": claim, "source_id": source_id, "stance": stance})
    if not raw.get("approach") or not raw.get("scope") or not evidence:
        raise ValueError("기술 원리, 적용 범위 또는 인용 근거가 누락되었습니다.")
    # 측정값에 등장하는 숫자는 적어도 검색된 논문 발췌 안에 있어야 한다.
    context = " ".join(chunk["text"] for chunk in chunks)
    for value in metrics.values():
        if not isinstance(value, str) or any(
            number not in context for number in re.findall(r"\d+(?:\.\d+)?", value)
        ):
            raise ValueError("측정값의 숫자가 검색된 논문 발췌에 없습니다.")
    return {
        "name": name,
        "camp": camp,
        "approach": str(raw["approach"]).strip(),
        "scope": str(raw["scope"]).strip(),
        "key_metrics": {str(key): value for key, value in metrics.items()},
        "limitations": [str(item).strip() for item in raw.get("limitations", []) if str(item).strip()],
        "evidence": evidence,
    }


def _research_one(
    name: TechName,
    retrieve_fn: RetrieveFn | None,
    generate_fn: GenerateFn | None,
    grade_fn: GradeFn | None,
    rewrite_fn: RewriteFn | None,
    reference_fn: ReferenceFn | None,
) -> tuple[TechSummary, Reference | None]:
    paper = config.PAPERS[name]
    arxiv_id = paper["arxiv_id"]
    if retrieve_fn is None and not Path(paper["path"]).is_file():
        return _empty_summary(name, f"[E-1003] 논문 PDF를 찾을 수 없음: {paper['path']}"), None

    try:
        if retrieve_fn is None or reference_fn is None:
            from rag.retriever import paper_reference, retrieve

            retrieve_fn = retrieve_fn or retrieve
            reference_fn = reference_fn or paper_reference
        grader = grade_fn or _default_grade
        rewriter = rewrite_fn or _default_rewrite
        generator = generate_fn or _default_generate
        query = _query(name)
        relevant: list[dict[str, Any]] = []
        for attempt in range(config.RAG_MAX_REWRITE + 1):
            # 공용 검색기는 기술 필터 인자를 받지 않으므로 두 논문 분량을 검색한 뒤 ID로 거른다.
            raw_chunks = retrieve_fn(query, config.TOP_K * len(config.PAPERS))
            scoped = [
                chunk for chunk in (_normalize_chunk(raw) for raw in raw_chunks)
                if chunk["arxiv_id"] == arxiv_id
            ][: config.TOP_K]
            relevant = [chunk for chunk in scoped if grader(query, chunk)]
            if len(relevant) >= len(config.PAPERS):
                break
            if attempt < config.RAG_MAX_REWRITE:
                query = rewriter(query, name, attempt + 1)
        if len(relevant) < len(config.PAPERS):
            return _empty_summary(name, f"[E-1001] 관련 논문 근거 부족: {name}"), None
        summary = _validate_summary(generator(name, "SW" if name == config.TECH_SW else "HW", relevant), name, relevant)
        return summary, reference_fn(arxiv_id)
    except (FileNotFoundError, OSError):
        return _empty_summary(name, "[E-1003] 논문 PDF 또는 인덱스를 읽지 못함"), None
    except Exception:  # 외부 검색·LLM 또는 구조화 결과 오류는 전체 그래프를 중단하지 않는다.
        return _empty_summary(name, "[E-1002] 논문 검색 또는 구조화 출력 실패"), None


def research_node(
    state: State,
    *,
    retrieve_fn: RetrieveFn | None = None,
    generate_fn: GenerateFn | None = None,
    grade_fn: GradeFn | None = None,
    rewrite_fn: RewriteFn | None = None,
    reference_fn: ReferenceFn | None = None,
) -> dict[str, Any]:
    """두 기술의 TechSummary와 이번 조사에서 사용한 논문 Reference만 반환한다."""
    summaries: dict[TechName, TechSummary] = {}
    references: list[Reference] = []
    for name in TECHS:
        summary, reference = _research_one(
            name, retrieve_fn, generate_fn, grade_fn, rewrite_fn, reference_fn
        )
        summaries[name] = summary
        if reference is not None:
            references.append(reference)
    succeeded = [name for name in TECHS if summaries[name]["evidence"]]
    if len(succeeded) == 1:
        failed = next(name for name in TECHS if name not in succeeded)
        summaries[failed]["limitations"].insert(0, "[E-1004] 한 기술만 논문 근거를 확보함")
    return {"tech_summary": summaries, "references": references}


def build_research_node(**dependencies: Any) -> Callable[[State], dict[str, Any]]:
    """기존 ZIP의 주입식 테스트를 위한 호환 팩토리."""
    return lambda state: research_node(state, **dependencies)
