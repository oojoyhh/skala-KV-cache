"""📝 보고서 생성 에이전트 (6번) — State → 설계서 E 목차 순서의 평가 보고서 PDF.

- 챕터마다 필요한 State만 LLM에 넘겨 본문을 쓰고, SUMMARY는 본문을 다 쓴 뒤 작성해 맨 앞에 둔다.
- 한 기술만 다루는 문장에는 그 기술 근거의 인용만 남긴다 (다른 기술 자료 번호가 섞이지 않게).
- 인용 번호는 코드가 매긴다: references를 문서 단위 source_id(#p 제거)로 중복 제거·used_by 병합 →
  본문 등장 순서로 번호, 논문 청크는 [n, p.쪽]. REFERENCE에는 State의 Evidence가 가리키는 출처만 싣는다
  (설계서 D-1 "실제 사용한 자료만". 검색만 되고 근거로 쓰이지 않은 결과는 제외). 본문 인용 순서대로, 인용되지 않은 출처는 그 뒤에.
- SUMMARY는 소개(인트로덕션)가 아니라 결론 요약이다: 배경·기술 선정 챕터는 빼고 평가 결과만 넘긴다.
- 표(요약 매트릭스, 4-4 도메인 5개 지표)와 REFERENCE는 LLM 없이 코드로 만든다.
- 인용·출처 처리는 output.citations, Markdown·PDF 출력은 output.renderer가 맡는다 (이 파일은 보고서 내용 생성).
- LLM 출력 검사: 챕터 입력에 없던 인용(지어낸 번호·쪽수)과 다시 쓴 제목 줄은 지운다.
- 에이전트 오류 코드([E-10xx])는 LLM에 넘기지 않고 6장 "데이터 수집 오류"로 따로 적는다 (기술의 한계로 서술되는 것 방지).
- LLM 호출이 실패해도 멈추지 않는다: 해당 챕터는 근거 목록으로 대신 싣고 "[E-1002]"를 남긴다.
"""

import datetime
import json
import os
import re

import config
import llm
from output.citations import (  # noqa: F401 — 외부에서 agents.report 경유로 쓰는 이름 재수출
    CITE_GROUP,
    CITE_ITEM,
    SENTENCE,
    Citations,
    _allowed_cites,
    _cite_marks,
    _doc_id,
    _keep_given_citations,
    _keep_same_tech_citations,
    doc_techs,
    evidence_source_ids,
    fill_missing_dates,
    format_reference,
    _published_date,
)
from output.renderer import render_pdf, to_markdown
from state import PERSPECTIVES, TECHS, State, perspective_evidence, trl_evidence_count

TITLE = "KV cache 최적화 기술 다관점 평가 보고서"
TRL_NOTE = ("본 TRL은 공개 정보 기반 추정이며, KV cache 기술은 논문 발표 시점과 실제 채택 간 시차가 있어 "
            "실제 단계와 다를 수 있다.")
PERSPECTIVE_NAMES = {"trl": "TRL", "market": "시장성", "stakeholder": "이해관계자", "domain": "도메인"}
DOMAIN_METRICS = {"cost": "비용", "throughput": "처리량", "model_quality": "모델 품질",
                  "transfer_overhead": "전송 오버헤드", "deployment_barrier": "도입 난이도"}


# ---------------------------------------------------------------------------
# 인용 · REFERENCE
# ---------------------------------------------------------------------------






















def _strip_title_lines(text: str, title: str) -> str:
    """LLM이 본문 앞에 다시 쓴 챕터 제목 줄("2. 기술 선정", "### 기술 선정")을 걷어낸다."""
    norm = lambda t: re.sub(r"[\W_]", "", t)
    keys = {norm(title), norm(title.split(" ", 1)[-1])}
    lines = text.strip().splitlines()
    while lines and (not lines[0].strip() or norm(lines[0]) in keys):
        lines.pop(0)
    return "\n".join(lines)


NO_EVIDENCE = "이번 실행에서 이 관점의 공개 근거를 확보하지 못했다 (6장 충분성 검사 미달 사유·데이터 수집 오류 참고)."
NO_SYNTHESIS = "출처가 확인된 관점 간 일치·상충 지점이 도출되지 않았다 (6장 참고)."
EVIDENCE_CHAPTERS = ("3.", "4-2", "4-3", "4-4", "5.")   # 근거(claim)가 없으면 LLM이 추측으로 채우는 챕터


def limits_text(state: State) -> str:
    """6장 본문: 방법론 한계와 확증편향 방지 조치 (config 기준값으로 고정 작성)."""
    lines = [
        "- 본 평가는 논문과 웹 공개 자료에 기반한 추정이다. TRL은 논문 발표와 실제 채택 사이의 시차가 있고, "
        "TRL 4~6 구간은 수율·실제 성능 같은 비공개 정보가 많아 실제 단계와 다를 수 있다.",
        f"- 확증편향 방지: 관점마다 지지 쿼리와 한계·반론 쿼리를 각각 {config.QUERIES_PER_STANCE}개씩 검색했다. "
        f"충분성 검사(관점·기술별 근거 {config.MIN_EVIDENCE}개 이상, 지지·한계·반론 각 1건 이상, 한 출처 비율 "
        f"{config.SAME_SOURCE_CAP:.0%} 이하)에 미달한 관점만 쿼리를 바꿔 관점별 최대 {config.MAX_AGENT_RETRY}회 재조사했으며, "
        f"보고서 품질 평가 결과에 따른 추가 조사도 최대 {config.MAX_QUALITY_RESEARCH}회 수행했다. "
        "끝까지 확보하지 못한 근거는 만들지 않고 아래에 기록했다.",
        "- 웹 자료 중 게시일·작성자가 확인되지 않는 경우(REFERENCE의 n.d.)가 있어 발표 시점 판단에 한계가 있다.",
        "- 본 보고서는 기술의 우열이나 추천을 판단하지 않으며, 관점별로 확인된 근거의 차이만 정리했다.",
    ]
    # 평가 종합이 남긴 한계(재조사 상한 도달 E-1005, 종합 실패 등)를 버리지 않고 함께 싣는다
    recorded = [x for x in (state.get("synthesis") or {}).get("limitations", []) if str(x).strip()]
    lines += [f"- {' '.join(str(x).split())}" for x in dict.fromkeys(recorded)]
    return "\n".join(lines)


def _has_claims(data) -> bool:
    if isinstance(data, list):
        return any(_has_claims(v) for v in data)
    if isinstance(data, dict):
        return "claim" in data or any(_has_claims(v) for v in data.values())
    return False








RECOMMEND = re.compile(
    r"(더|가장)\s*(적합|유리|나은|우수|효과적|바람직)"      # 더 적합 / 가장 우수
    r"|보다\s*(우수|유리|낫|뛰어)"                          # B보다 우수하다
    r"|우위(에 있|를 가진|가 있)"                            # 우위에 있다
    r"|나은 선택|권장|선택하는 것이|다른 기술(들)?보다"
    r"|추천(?!\s*시스템)"                                   # '추천 시스템'은 기술 용어라 제외
)
FILLER = ("결론적으로", "결국,", "결국 ", "이와 같이", "이처럼", "종합하면")
HW_WORDING = re.compile(r"하드웨어\s*(기반|자원|의존)")


def _drop_recommendations(text: str) -> str:
    """조건별 기술 추천·우열 문장을 지운다 (과제 원칙: 추천·우열 판정 금지)."""
    lines = []
    for line in text.split("\n"):
        if line.strip().startswith(FILLER):   # 챕터 끝 일반론 요약 문단
            continue
        if "InfiniGen" in line:
            line = HW_WORDING.sub("메모리 계층 활용", line)
        kept = "".join(m[0] for m in SENTENCE.finditer(line) if not RECOMMEND.search(m[0])).strip()
        bullet = line.lstrip().startswith("- ")
        if kept and not (bullet and kept == "-"):
            lines.append(("- " + kept.lstrip("- ").lstrip()) if bullet and not kept.startswith("-") else kept)
        elif not line.strip():
            lines.append("")
    return "\n".join(lines)


ERROR_CODE = re.compile(r"\[E-10\d\d\]")
RESULT_NAMES = {"tech_summary": "기술 조사", "trl_result": "TRL", "market_result": "시장성",
                "stakeholder_result": "이해관계자", "domain_result": "도메인"}
NODE_NAMES = {"research": "기술 조사", "market": "시장·TRL", "stakeholder": "이해관계자",
              "domain": "도메인", "synthesis": "평가 종합", "report": "보고서 생성", "quality": "품질 평가"}


def collect_errors(state: State) -> list[str]:
    """에이전트가 남긴 오류 코드 문장을 모은다 (6장에 '수집 오류'로 따로 기록)."""
    found = []

    def walk(x, where):
        if isinstance(x, str) and ERROR_CODE.search(x):
            found.append(f"{where}: {' '.join(x.split())[:160]}")
        elif isinstance(x, list):
            for v in x:
                walk(v, where)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v, where)

    for key, name in RESULT_NAMES.items():
        for tech, result in (state.get(key) or {}).items():
            walk(result, f"{name}({tech})")
    # 결과 없이 실패한 노드는 페이로드에 E-코드가 남지 않으므로 control.node_errors도 읽는다 (계약 §3-1)
    for node, error in ((state.get("control") or {}).get("node_errors") or {}).items():
        found.append(f"{NODE_NAMES.get(node, node)}: {' '.join(str(error).split())[:160]}")
    return list(dict.fromkeys(found))


def _drop_errors(data):
    """오류 코드 문장은 LLM 입력에서 뺀다. 넘기면 기술의 한계처럼 서술된다."""
    if isinstance(data, str):
        return "" if ERROR_CODE.search(data) else data
    if isinstance(data, list):
        return [v for v in (_drop_errors(x) for x in data) if v != ""]
    if isinstance(data, dict):
        return {k: _drop_errors(v) for k, v in data.items()}
    return data


# ---------------------------------------------------------------------------
# 표 (LLM 없이 State에서 계산)
# ---------------------------------------------------------------------------
STANCE_NOTE = "표의 숫자: 지지 / 한계·반론 / 중립 근거 수"


def _stance_counts(evs) -> str:
    if not evs:
        return "근거 없음"
    return " / ".join(str(sum(e["stance"] == s for e in evs)) for s in ("positive", "negative", "neutral"))


def summary_matrix(state: State) -> list[list[str]]:
    """기술 × 관점 근거 분포 (DEV_PLAN §8 #10: perspective_evidence의 stance 개수)."""
    rows = [["기술", *(PERSPECTIVE_NAMES[p] for p in PERSPECTIVES)]]
    for tech in TECHS:
        rows.append([tech, *(_stance_counts(perspective_evidence(state, p, tech)) for p in PERSPECTIVES)])
    return rows


def domain_table(state: State, cites: Citations) -> list[list[str]]:
    """4-4 도메인 5개 지표 표. 근거가 없는 지표는 "해당 없음"."""
    rows = [["지표", *TECHS]]
    for key, name in DOMAIN_METRICS.items():
        row = [name]
        for tech in TECHS:
            evs = state.get("domain_result", {}).get(tech, {}).get(key, [])
            marks = list(dict.fromkeys(c for c in (cites.cite(e["source_id"]) for e in evs) if c))
            row.append(f"{_stance_counts(evs)} {' '.join(marks)}" if evs else "해당 없음")
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 본문 (설계서 E 목차 순서)
# ---------------------------------------------------------------------------
CAMP_LABEL = {"SW": "SW 진영: KV cache 자체를 작게 만듦", "HW": "메모리 계층 진영: 새 하드웨어 없이 기존 CPU 메모리로 담을 공간을 넓힘"}


def _by_tech(state, key, drop=()):
    out = {t: {k: v for k, v in state.get(key, {}).get(t, {}).items() if k not in drop} for t in TECHS}
    for r in out.values():
        if r.get("camp") in CAMP_LABEL:
            r["camp"] = CAMP_LABEL[r["camp"]]
    return out


def _trl_view(s) -> dict:
    """TRL 근거가 판정 규칙(config.MIN_TRL_EVIDENCE건)에 못 미치면 숫자 단계를 넘기지 않는다.
    근거가 부족하다는 것은 '판정 근거 부족'이지 'TRL 1'이라는 증거가 아니기 때문."""
    out = {}
    for tech, r in _by_tech(s, "trl_result").items():
        n = trl_evidence_count(s, tech)   # 팀 결정 D11: 충분성·품질·보고서가 같은 공용 함수를 쓴다
        r = dict(r)
        if n < config.MIN_TRL_EVIDENCE:
            r["level"] = f"확정 곤란 (TRL 근거 {n}건 < {config.MIN_TRL_EVIDENCE}건)"
            r.pop("rationale", None)   # "TRL 1로 보수적 추정" 같은 문장이 숫자를 되살리지 않게
        out[tech] = r
    return out


def trl_line(s) -> str:
    """4-1 첫 줄: 기술별 판정 결과를 코드로 고정 (본문·요약 표와 같은 값)."""
    parts = []
    for tech, r in _trl_view(s).items():
        level = r.get("level")
        parts.append(f"{tech} {'TRL ' + str(level) if isinstance(level, int) else (level or '판정 없음')}")
    return "판정 결과: " + " / ".join(parts)


def _domain_view(s) -> dict:
    """4-4 입력: 도메인 결과 + 표와 같은 지표별 근거 수 (표·본문 불일치 방지)."""
    data = {"domain_result": _by_tech(s, "domain_result")}
    data["지표별 근거 수"] = {tech: {name: len(r.get(key, [])) for key, name in DOMAIN_METRICS.items()}
                          for tech, r in data["domain_result"].items()}
    return data


# (제목 수준, 제목, 분량, 지시, State → 입력). pick이 None이면 제목만 쓴다.
CHAPTERS = [
    (2, "1. 분석 배경", "400~500자",
     "KV cache가 왜 메모리 병목이 되는지, 선정 도메인(데이터센터/클라우드 서빙)에서 왜 분석해야 하는지 서술",
     lambda s: {"domain": s.get("domain"), "tech_summary": _by_tech(s, "tech_summary")}),
    (2, "2. 기술 선정", "400~500자",
     "SW 진영(데이터를 작게)과 HW 진영(담을 공간을 넓게)에서 1건씩 고른 이유와, 같은 병목을 반대 방향에서 푼다는 대비를 서술",
     lambda s: {"tech_sw": s.get("tech_sw"), "tech_hw": s.get("tech_hw"),
                "tech_summary": _by_tech(s, "tech_summary", drop=("evidence",))}),
    (2, "3. 기술 개요", "1,600~2,000자",
     "기술별로 접근 방식, 적용 범위, 핵심 수치(비교 기준·실험 조건 포함), 한계를 나눠 서술. key_metrics 수치는 입력에 적힌 뜻 그대로만 쓰고 "
     "의미를 추측해 붙이지 않는다. 수치에는 그 수치가 들어 있는 evidence claim의 cite만 붙이고, 그런 claim이 없으면 인용 없이 쓴다",
     lambda s: {"tech_summary": _by_tech(s, "tech_summary")}),
    (2, "4. 관점별 평가", None, None, None),
    (3, "4-1. 기술 성숙도 (TRL)", "800~1,000자",
     "기술별 TRL 판정과 근거를 서술. level이 '확정 곤란'이면 숫자 단계를 쓰지 말고 '공개 자료만으로 단계 확정 곤란'으로 쓰며, "
     "그보다 높은 단계의 신호가 있으면 신호로만 소개한다. 성숙도 판단과 출처 신뢰도(출처 수)를 구분해 쓴다",
     lambda s: {"trl_result": _trl_view(s)}),
    (3, "4-2. 시장성", "800~1,000자",
     "기술별로 시장 규모·성장성(market_size_growth), 상용화·채택 사례(adoption), 생태계 지지(ecosystem)를 이 순서로 나눠 서술. "
     "항목에 근거가 없으면 그 항목은 '공개 근거 미확인'이라고 쓴다. 성능·비용 절감 수치는 채택 사례가 아니다. 관련 시장의 성장과 해당 기술의 채택을 구분",
     lambda s: {"market_result": _by_tech(s, "market_result")}),
    (3, "4-3. 이해관계자", "800~1,000자",
     "경쟁 기술 진영, 도입 기업·개발자, 투자 업계별로 서술. 해당 주체의 발언·도입·투자 사실이 담긴 근거만 '반응'이라고 쓰고, "
     "성능 비교 자료나 논문 소개 페이지는 '반응'이 아니라 배경 자료로 표현한다. 집단별 근거가 없으면 '공개 근거 미확인'",
     lambda s: {"stakeholder_result": _by_tech(s, "stakeholder_result")}),
    (3, "4-4. 도메인 적용", "600~800자",
     "위 표의 5개 지표(비용, 처리량, 모델 품질, 전송 오버헤드, 도입 난이도)를 기준으로 데이터센터/클라우드 서빙에서의 평가를 서술. "
     "'지표별 근거 수'가 1 이상인 지표는 반드시 그 근거로 서술하고, 0인 지표만 해당 없음으로 쓴다 (표와 일치해야 함)",
     _domain_view),
    (2, "5. 시사점", "900~1,000자",
     "관점에 따라 평가가 엇갈리는 지점(conflicts)을 중심으로, 관점 간 일치 지점(agreements)과 함께 서술. "
     "사용 조건별로 어느 기술이 적합한지 제안하지 않는다('~에는 A가 적합', '~에는 B가 나은 선택' 금지)",
     lambda s: {k: s.get("synthesis", {}).get(k, []) for k in ("agreements", "conflicts")}),
    (2, "6. 한계점", "400~500자",
     "공개 정보 기반 추정의 한계와 확증편향을 막기 위해 취한 조치(지지·한계·반론 쿼리 병행, 충분성 검사와 재조사)를 방법론 차원에서 서술. "
     "관점·기술별 근거 개수나 부족 현황은 바로 아래에 코드가 목록으로 붙이므로 다시 쓰지 않는다",
     lambda s: {"limitations": [x for x in s.get("synthesis", {}).get("limitations", []) if "근거" not in x or "미확인" not in x],
                "neutrality_note": s.get("synthesis", {}).get("neutrality_note", ""),
                "retry_counts": (s.get("control") or {}).get("evidence_retry_counts", {})}),
]


def _synthesis_for_report(data: dict, cites: Citations, allowed_ids: set[str]) -> dict:
    """State의 문자열 계약을 유지하며 종합 문장 끝의 출처를 cite로 변환한다."""
    def cited_claim(text: str) -> dict | None:
        match = re.fullmatch(r"(.*) \(출처: ([^)]+)\)", text, flags=re.DOTALL)
        if not match:
            return None
        claim, raw_ids = match.groups()
        ids = list(dict.fromkeys(s.strip() for s in raw_ids.split(",")))
        # 일부 출처만 존재해도 문장 전체를 승인하지 않는다.
        if not claim.strip() or any(s not in allowed_ids or _doc_id(s) not in cites.alias for s in ids):
            return None
        marks = list(dict.fromkeys(cites.cite(s) for s in ids))
        return {"claim": claim, "cite": " ".join(marks)}

    agreements = []
    conflicts = []
    for text in data.get("agreements", []):
        item = cited_claim(text)
        if item:
            agreements.append(item)
    for conflict in data.get("conflicts", []):
        item = cited_claim(conflict["description"])
        if item:
            conflicts.append({k: conflict[k] for k in ("tech", "perspective_a", "perspective_b")} | item)
    return {"agreements": agreements, "conflicts": conflicts}


def _claims(data) -> list[str]:
    """LLM 실패 시 대체 본문: 입력에 있는 근거를 인용과 함께 나열."""
    if isinstance(data, list):
        return [c for v in data for c in _claims(v)]
    if isinstance(data, dict):
        if "claim" in data:
            return [f"- {data['claim']} {data['cite']}"] if data.get("cite") else []
        return [c for v in data.values() for c in _claims(v)]
    return []


def _write(generate, prompt_head, title, length, instruction, data, allowed=None) -> str:
    """LLM으로 챕터를 쓰고, 다시 쓴 제목 줄과 입력에 없던 인용을 걷어낸다."""
    prompt = (f"{prompt_head}\n\n## 챕터: {title}\n분량: {length}\n지시: {instruction}\n"
              f"입력:\n{json.dumps(data, ensure_ascii=False, indent=1)}")
    try:
        text = _strip_title_lines(generate(prompt), title)
    except llm.LLMError:
        # 오류 원문은 싣지 않는다: API 키 일부 등 비밀값이 보고서에 노출될 수 있음 (AGENTS 11번)
        fallback = "\n".join(_claims(data)) or "공개 근거 미확인"
        return f"[E-1002] 문장 생성 실패로 근거 목록을 그대로 싣는다.\n{fallback}"
    return _keep_given_citations(text, _allowed_cites(data) if allowed is None else allowed)


def build_blocks(state: State, generate) -> list[tuple]:
    """보고서를 (종류, 내용) 블록 목록으로 만든다. 종류: h1·h2·h3·p·note·table·fixed(코드가 쓴 고정 문단)."""
    head = llm.load_prompt("report")
    cites = Citations(state.get("references", []), evidence_source_ids(state))
    errors = collect_errors(state)
    techs_of = doc_techs(state, cites)
    feedback = feedback_note(state)
    body = []
    for level, title, length, instruction, pick in CHAPTERS:
        body.append((f"h{level}", title))
        if pick is None:
            continue
        if title.startswith("4-4"):
            body += [("note", STANCE_NOTE), ("table", domain_table(state, cites))]
        data = _drop_errors(pick(state))
        if title == "5. 시사점":
            data = _synthesis_for_report(data, cites, evidence_source_ids(state))
        else:
            data = cites.attach(data)
        if title.startswith("6."):
            text = limits_text(state)
        elif title.startswith(EVIDENCE_CHAPTERS) and not _has_claims(data):
            text = NO_SYNTHESIS if title.startswith("5.") else NO_EVIDENCE
        else:
            text = _drop_recommendations(_keep_same_tech_citations(
                _write(generate, head, title, length, instruction + feedback, data), cites, techs_of))
        if title.startswith("4-1"):
            body.append(("fixed", trl_line(state)))
        if title.startswith("4-1") and "공개 정보 기반 추정" not in text:
            text += "\n" + TRL_NOTE
        if title.startswith("6.") and state.get("sufficiency", {}).get("reasons"):
            # 충분성 미달 사유는 LLM이 빠뜨려도 남도록 코드로 붙인다
            text += "\n\n충분성 검사 미달 사유 (재조사 상한 도달 시 근거를 만들지 않고 기록):\n" + "\n".join(
                f"- {PERSPECTIVE_NAMES.get(p, p)}: {why}" for p, why in state["sufficiency"]["reasons"].items())
        body.append(("p", text))
        if title.startswith("6.") and errors:
            # 별도 블록("fixed"): PDF에는 본문처럼 나오지만 SUMMARY 입력(LLM)에는 넘기지 않는다
            body.append(("fixed", "데이터 수집 오류 (에이전트 실행 오류로, 기술의 한계가 아님):\n"
                         + "\n".join(f"- {e}" for e in errors)))

    # SUMMARY 입력은 평가 결과 챕터(3장~6장)만. 배경·기술 선정을 넘기면 소개문이 되기 쉽다
    start = body.index(("h2", "3. 기술 개요"))
    findings = "\n\n".join(t for kind, t in body[start:] if kind in ("h2", "h3", "p"))
    summary = _write(generate, head, "SUMMARY", "400~600자 (반 페이지 이내)",
                     "보고서 전체의 결론 요약을 쓴다. 인트로덕션이 아니다. '본 보고서는', 배경·목적·기술 소개 문장으로 시작하지 않고 "
                     "첫 문장부터 평가 결과를 쓴다. 관점별 핵심 평가(TRL·시장성·이해관계자·도메인)와 관점 간 평가가 엇갈리는 지점을 "
                     "\"- \"로 시작하는 4~5개 항목(항목당 2문장 이내)으로 쓰고, 본문의 인용 표기를 유지. 조건별 기술 추천은 쓰지 않는다"
                     + feedback,
                     {"평가 결과": findings}, allowed=_cite_marks(findings))
    summary = _drop_recommendations(_keep_same_tech_citations(summary, cites, techs_of))
    bullets = [line for line in summary.splitlines() if line.lstrip().startswith("- ")][:5]   # 반 페이지 이내
    summary = "\n".join(bullets) if bullets else summary

    refs = cites.reference_lines()
    blocks = [("h1", TITLE), ("h2", "SUMMARY"), ("p", summary), ("note", STANCE_NOTE), ("table", summary_matrix(state)),
              *body, ("h2", "REFERENCE"), ("p", "\n".join(refs) or "인용된 자료 없음")]
    if any("(n.d.)" in r for r in refs):
        blocks.append(("note", f"※ n.d.: 게시일이 확인되지 않은 웹 자료 (검색 도구가 게시일을 제공하지 않음). "
                               f"검색일: {datetime.date.today().isoformat()}"))
    return blocks


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------
















def feedback_note(state: State) -> str:
    """재작성일 때 품질 평가 피드백을 챕터 지시에 덧붙인다 (계약 §4: report가 feedback을 읽는다)."""
    feedback = (state.get("quality_result") or {}).get("feedback") or []
    if not feedback:
        return ""
    return ("\n[직전 보고서의 품질 평가 미달 사유 — 이번 작성에서 고칠 것, 분량은 늘리지 말 것]\n"
            + "\n".join(f"- {f}" for f in feedback[:12]))


def report_node(state: State, generate_fn=None, fetch_fn=None) -> dict:
    """계약 §3: report_path·report_md_path·report_version·node_result를 반환한다."""
    dispatch_id = (state.get("control") or {}).get("dispatch_id", 0)
    node_result = {"node": "report", "dispatch_id": dispatch_id, "status": "success", "error": ""}
    md_path = os.path.splitext(config.REPORT_PATH)[0] + ".md"
    try:
        state = {**state, "references": fill_missing_dates(state.get("references", []), fetch_fn or _published_date)}
        blocks = build_blocks(state, generate_fn or llm.generate)
        os.makedirs(os.path.dirname(config.REPORT_PATH), exist_ok=True)
        render_pdf(blocks, config.REPORT_PATH)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(to_markdown(blocks))
    except Exception as exc:  # noqa: BLE001 — 계약 §3: 어떤 예외도 그래프 밖으로 던지지 않는다
        # 오류 원문은 싣지 않는다 (API 키 일부 등 비밀값이 섞일 수 있음, AGENTS 규칙 11)
        return {"node_result": {**node_result, "status": "failed",
                                "error": f"E-1002 보고서 생성 실패 ({type(exc).__name__})"}}
    return {
        "report_path": config.REPORT_PATH,
        "report_md_path": md_path,
        "report_version": state.get("report_version", 0) + 1,
        "node_result": node_result,
    }
