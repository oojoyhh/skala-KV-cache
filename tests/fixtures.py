"""계약 v2.2의 단독 테스트·dummy 실행용 샘플 데이터. 담당: 1번.

모든 근거와 품질 결과는 연결 확인용 샘플이며 실제 조사·평가 결과가 아니다.

제공하는 것
- sample_state_after_research()          : 기술 조사까지 끝난 State      → 3·4번 평가 노드 단독 실행용
- sample_state_after_eval(sufficient=...) : 4개 관점 결과까지 채운 State  → 충분성 검사·평가 종합·보고서 단독 실행용
- fake_search(query, stance, ...)         : Tavily 없이 쓰는 가짜 검색 (tools.web_search.search_web과 같은 반환 형태)
- expected_sufficiency(state)             : 설계서 D-2 기준으로 계산한 "정답" SufficiencyCheck (4번 check 결과와 비교용)
- DUMMY_NODES                             : `python app.py --dummy`에서 쓰는 가짜 노드 (START→END·루프 확인용)

사용 예
    python -c "from tests.fixtures import sample_state_after_research; from agents.market import market_node; print(market_node(sample_state_after_research()))"
"""

import copy
import hashlib
from collections import Counter
from collections.abc import Callable
from functools import wraps
from pathlib import Path

import config
from state import (
    PERSPECTIVES,
    TECHS,
    Evidence,
    MetricResult,
    NodeName,
    NodeResult,
    QualityResult,
    Reference,
    State,
    make_initial_state,
    perspective_evidence,
    retry_hint,
)

# ---------------------------------------------------------------------------
# 기본 부품
# ---------------------------------------------------------------------------


def web_ref(url: str, title: str, stance: str, used_by: str, date: str = "2026-03-27") -> Reference:
    sid = "web:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return {
        "source_id": sid, "kind": "web", "author": "Sample Org", "date": date, "title": title,
        "venue": "example.com", "url": url, "used_by": [used_by], "stance": stance,
    }


def paper_ref(arxiv_id: str, page: int, title: str) -> Reference:
    return {
        "source_id": f"arxiv:{arxiv_id}#p{page}", "kind": "paper", "author": "Sample Author et al.",
        "date": "2025", "title": title, "venue": f"arXiv, {arxiv_id}",
        "url": f"https://arxiv.org/abs/{arxiv_id}", "used_by": ["research"], "stance": "neutral",
    }


def ev(claim: str, ref: Reference, stance: str | None = None) -> Evidence:
    return {"claim": claim, "source_id": ref["source_id"], "stance": stance or ref["stance"]}


def _evidence_set(tech: str, perspective: str, n: int = 4, with_negative: bool = True) -> tuple[list[Evidence], list[Reference]]:
    """서로 다른 출처 n개, 지지(positive) 1+ · 한계·반론(negative) 1+ 를 만족하는 Evidence 묶음."""
    stances = ["positive", "negative", "neutral", "positive"][:n] if with_negative else ["positive", "neutral", "positive", "neutral"][:n]
    refs = [
        web_ref(f"https://example.com/{tech}/{perspective}/{i}", f"[샘플] {tech} {perspective} 자료 {i}", s, perspective)
        for i, s in enumerate(stances)
    ]
    evs = [ev(f"[샘플] {tech}의 {perspective} 관련 주장 {i}", r) for i, r in enumerate(refs)]
    return evs, refs


# ---------------------------------------------------------------------------
# 노드별 샘플 출력 (dummy 노드와 sample_state가 함께 사용)
# ---------------------------------------------------------------------------

_PAPER_TITLES = {
    "TurboQuant": "TurboQuant: Online Vector Quantization with Near-optimal Distortion Rate",
    "InfiniGen": "InfiniGen: Efficient Generative Inference of Large Language Models with Dynamic KV Cache Management",
}


def sample_research_output() -> dict:
    tech_summary, refs = {}, []
    for tech in TECHS:
        arxiv_id = config.PAPERS[tech]["arxiv_id"]
        r1, r2 = paper_ref(arxiv_id, 1, _PAPER_TITLES[tech]), paper_ref(arxiv_id, 7, _PAPER_TITLES[tech])
        refs += [r1, r2]
        tech_summary[tech] = {
            "name": tech,
            "camp": "SW" if tech == config.TECH_SW else "HW",
            "approach": f"[샘플] {tech} 핵심 접근",
            "scope": f"[샘플] {tech} 적용 범위",
            "key_metrics": {"예시 지표": "논문 보고 수치 자리"},
            "limitations": [f"[샘플] {tech} 한계"],
            "evidence": [ev(f"[샘플] {tech} 개요", r1), ev(f"[샘플] {tech} 실험 결과", r2)],
        }
    return {"tech_summary": tech_summary, "references": refs}


def sample_market_output() -> dict:
    trl, market, refs = {}, {}, []
    for tech in TECHS:
        e_trl, r_trl = _evidence_set(tech, "trl", n=2)
        e1, r1 = _evidence_set(tech, "market", n=4)
        refs += r_trl + r1
        trl[tech] = {"level": 4, "rationale": "[샘플] 공개 코드·재현 실험 확인", "evidence": e_trl,
                     "uncertainty": "공개 정보 기반 추정이며 실제 적용 수준은 확인이 제한적임"}
        market[tech] = {"market_size_growth": e1[:1], "adoption": e1[1:3], "ecosystem": e1[3:], "summary": "[샘플] 시장성 요약"}
    return {"trl_result": trl, "market_result": market, "references": refs}


def sample_stakeholder_output(insufficient_tech: str | None = None) -> dict:
    """insufficient_tech를 주면 그 기술은 한계·반론 근거가 없어 충분성 검사에서 불충분이 된다."""
    result, refs = {}, []
    for tech in TECHS:
        e, r = _evidence_set(tech, "stakeholder", n=4, with_negative=(tech != insufficient_tech))
        refs += r
        result[tech] = {"competitors": e[:2], "adopters_devs": e[2:3], "investors": e[3:], "summary": "[샘플] 이해관계자 요약"}
    return {"stakeholder_result": result, "references": refs}


def sample_domain_output() -> dict:
    result, refs = {}, []
    for tech in TECHS:
        e, r = _evidence_set(tech, "domain", n=4)
        refs += r
        result[tech] = {
            "domain": config.DOMAIN, "cost": e[:1], "throughput": e[1:2],
            # 해당 없는 지표는 빈 리스트 (TurboQuant의 transfer_overhead 등) — 충분성은 관점 전체 개수로 판정
            "model_quality": e[2:3] if tech == "TurboQuant" else [],
            "transfer_overhead": e[2:3] if tech == "InfiniGen" else [],
            "deployment_barrier": e[3:], "summary": "[샘플] 도메인 적합성 요약",
        }
    return {"domain_result": result, "references": refs}


# ---------------------------------------------------------------------------
# 충분성 기준 "정답" (설계서 D-2, config 값) — 4번 check 구현 결과와 비교하는 용도
# ---------------------------------------------------------------------------


def _perspective_ok(evs: list[Evidence], perspective: str) -> tuple[bool, str]:
    if perspective == "trl":
        return (len(evs) >= config.MIN_TRL_EVIDENCE, f"TRL 근거 {len(evs)}개")
    stance = Counter(e["stance"] for e in evs)
    top_share = max(Counter(e["source_id"] for e in evs).values(), default=0) / max(len(evs), 1)
    problems = []
    if len(evs) < config.MIN_EVIDENCE:
        problems.append(f"Evidence {len(evs)}개")
    if stance["positive"] < config.MIN_POSITIVE:
        problems.append("지지(positive) 근거 미확인")
    if stance["negative"] < config.MIN_NEGATIVE:
        problems.append("반론 근거 미확인(negative 0건)")
    if top_share > config.SAME_SOURCE_CAP:
        problems.append(f"한 출처 비율 {top_share:.0%}")
    return (not problems, ", ".join(problems))


def expected_sufficiency(state: State) -> dict:
    check, reasons = {}, {}
    for p in PERSPECTIVES:
        bad = []
        for tech in TECHS:
            ok, why = _perspective_ok(perspective_evidence(state, p, tech), p)
            if not ok:
                bad.append(f"{tech}: {why}")
        check[p] = not bad
        if bad:
            reasons[p] = " / ".join(bad)
    check["reasons"] = reasons
    return check


# ---------------------------------------------------------------------------
# 1. 샘플 State — 제어 초기값은 공용 생성 함수를 그대로 사용
# ---------------------------------------------------------------------------


def _merge(state: dict, update: dict) -> dict:
    out = dict(state)
    for k, v in update.items():
        out[k] = out.get(k, []) + v if k == "references" else v
    return out


def sample_state_after_research() -> State:
    return _merge(make_initial_state(), sample_research_output())


def sample_state_after_eval(sufficient: bool = True) -> State:
    s = sample_state_after_research()
    for upd in (sample_market_output(),
                sample_stakeholder_output(insufficient_tech=None if sufficient else "InfiniGen"),
                sample_domain_output()):
        s = _merge(s, upd)
    s["sufficiency"] = expected_sufficiency(s)
    return copy.deepcopy(s)


# ---------------------------------------------------------------------------
# 가짜 검색 (tools.web_search.search_web 과 같은 형태: Reference 필드 9개 + content)
# ---------------------------------------------------------------------------


def fake_search(query: str, stance: str = "neutral", max_results: int = 5, used_by="market", **_) -> list[dict]:
    used = [used_by] if isinstance(used_by, str) else list(used_by)
    out = []
    for i in range(min(max_results, 3)):
        r = web_ref(f"https://example.com/search/{hashlib.md5(query.encode()).hexdigest()[:6]}/{i}",
                    f"[가짜 검색] {query} #{i}", stance, used[0])
        r["used_by"] = used
        out.append({**r, "content": f"[가짜 본문] '{query}'에 대한 검색 결과 {i}. benchmark evaluation limitation"})
    return out


# ---------------------------------------------------------------------------
# 2. 더미 실행 결과 — 공통 래퍼로 성공·실패 모두 dispatch_id를 보고
# ---------------------------------------------------------------------------


def _dummy_node(node: NodeName):
    """페이로드 생성 함수에 계약의 실행 결과와 예외 처리를 붙인다."""
    def decorate(fn: Callable[[State], dict]) -> Callable[[State], dict]:
        @wraps(fn)
        def wrapped(state: State) -> dict:
            result: NodeResult = {
                "node": node,
                "dispatch_id": state["control"]["dispatch_id"],
                "status": "success",
                "error": "",
            }
            try:
                return {**fn(state), "node_result": result}
            except Exception as exc:
                # 실패한 페이로드는 반환하지 않아 기존 State 결과를 보존한다.
                result["status"] = "failed"
                result["error"] = f"E-1002 [DUMMY] {node}: {exc}"
                return {"node_result": result}

        return wrapped

    return decorate


@_dummy_node("research")
def _dummy_research(state: State) -> dict:
    return sample_research_output()


@_dummy_node("market")
def _dummy_market(state: State) -> dict:
    return sample_market_output()


@_dummy_node("stakeholder")
def _dummy_stakeholder(state: State) -> dict:
    # 첫 실행은 InfiniGen 한계·반론 근거 없음 → 충분성 검사 불충분 → 재조사(retry_hint 있음) 때 충족
    first_run = not retry_hint(state, "stakeholder")
    return sample_stakeholder_output(insufficient_tech="InfiniGen" if first_run else None)


@_dummy_node("domain")
def _dummy_domain(state: State) -> dict:
    return sample_domain_output()


@_dummy_node("synthesis")
def _dummy_synthesis(state: State) -> dict:
    limits = [f"{p} 근거 부족: {why}" for p, why in state.get("sufficiency", {}).get("reasons", {}).items()]
    return {"synthesis": {"agreements": ["[샘플] 일치 지점"], "conflicts": [], "neutrality_note": "[샘플] 우열 판정 없음",
                          "limitations": limits or ["[샘플] 공개 정보 기반 추정의 한계"]}}


# ---------------------------------------------------------------------------
# 3. 더미 보고서 — 실제 보고서와 구분되는 Markdown·한 페이지 PDF
# ---------------------------------------------------------------------------


@_dummy_node("report")
def _dummy_report(state: State) -> dict:
    """샘플 파일을 생성하고 두 경로와 증가한 버전을 반환한다."""
    from fpdf import FPDF

    output_dir = Path(config.OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "dummy_" + Path(config.REPORT_FILENAME).stem
    md_path = output_dir / f"{stem}.md"
    pdf_path = output_dir / f"{stem}.pdf"
    version = state["report_version"] + 1
    unique_docs = {r["source_id"].split("#")[0] for r in state.get("references", [])}
    with md_path.open("w", encoding="utf-8") as f:
        f.write("# [DUMMY] KV cache 평가 보고서\n\n")
        f.write("연결 확인용 샘플이며 실제 조사·품질 평가 결과가 아닙니다.\n\n")
        f.write(f"- report_version: {version}\n- REFERENCE(샘플 문서): {len(unique_docs)}건\n")
        f.write(f"- 한계점: {state.get('synthesis', {}).get('limitations')}\n")

    # 전문은 Markdown에만 남겨 샘플 근거 수와 무관하게 PDF 한 쪽을 유지한다.
    pdf = FPDF()
    pdf.add_font("ko", fname=str(Path(config.FONT_DIR) / "NanumGothic-Regular.ttf"))
    pdf.add_page()
    pdf.set_font("ko", size=12)
    pdf.multi_cell(0, 8, text=(
        "[DUMMY] KV cache 평가 보고서\n"
        "연결 확인용 샘플이며 실제 조사·품질 평가 결과가 아닙니다.\n"
        f"보고서 버전: {version} / 샘플 문서: {len(unique_docs)}건"
    ))
    pdf.output(str(pdf_path))
    return {
        "report_path": str(pdf_path),
        "report_md_path": str(md_path),
        "report_version": version,
    }


# ---------------------------------------------------------------------------
# 4. 더미 품질 평가 — 실제 규칙 검사·Judge 호출 없이 통과 샘플 반환
# ---------------------------------------------------------------------------


@_dummy_node("quality")
def _dummy_quality(state: State) -> dict:
    """현재 보고서 버전에 대한 가짜 통과 결과를 반환한다."""
    metric: MetricResult = {
        "rule_passed": True, "judge_passed": None,
        "passed": True, "reasons": [],
    }
    quality: QualityResult = {
        "groundedness": copy.deepcopy(metric),
        "neutrality": copy.deepcopy(metric),
        "bias_control": copy.deepcopy(metric),
        "perspective_coverage": copy.deepcopy(metric),
        "page_count": 1,
        "page_limit_passed": 1 <= config.MAX_REPORT_PAGES,
        "required_sections_passed": True,
        "passed": 1 <= config.MAX_REPORT_PAGES,
        "feedback": ["[DUMMY] 연결 확인용 통과 샘플이며 실제 품질 평가 결과가 아닙니다."],
        "evaluated_report_version": state["report_version"],
        "action": "pass",
        "target_node": "",
    }
    return {"quality_result": quality}


DUMMY_NODES = {
    "research": _dummy_research,
    "market": _dummy_market,
    "stakeholder": _dummy_stakeholder,
    "domain": _dummy_domain,
    "synthesis": _dummy_synthesis,
    "report": _dummy_report,
    "quality": _dummy_quality,
}
