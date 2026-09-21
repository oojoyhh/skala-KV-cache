"""단독 테스트·dummy 실행용 샘플 데이터 (DEV_PLAN §3-5). 담당: 5번.

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
import os
from collections import Counter

import config
from state import (
    PERSPECTIVES,
    TECHS,
    Evidence,
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
# 샘플 State
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
    s["retry_count"] = 0 if sufficient else 1
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
# dummy 노드 — python app.py --dummy
# ---------------------------------------------------------------------------


def _dummy_research(state: State) -> dict:
    return sample_research_output()


def _dummy_market(state: State) -> dict:
    return sample_market_output()


def _dummy_stakeholder(state: State) -> dict:
    # 첫 실행은 InfiniGen 한계·반론 근거 없음 → 충분성 검사 불충분 → 재조사(retry_hint 있음) 때 충족
    first_run = not retry_hint(state, "stakeholder")
    return sample_stakeholder_output(insufficient_tech="InfiniGen" if first_run else None)


def _dummy_domain(state: State) -> dict:
    return sample_domain_output()


def _dummy_check(state: State) -> dict:
    suff = expected_sufficiency(state)
    insufficient = not all(suff[p] for p in PERSPECTIVES)
    return {"sufficiency": suff, "retry_count": state.get("retry_count", 0) + (1 if insufficient else 0)}


def _dummy_synthesis(state: State) -> dict:
    limits = [f"{p} 근거 부족: {why}" for p, why in state.get("sufficiency", {}).get("reasons", {}).items()]
    return {"synthesis": {"agreements": ["[샘플] 일치 지점"], "conflicts": [], "neutrality_note": "[샘플] 우열 판정 없음",
                          "limitations": limits or ["[샘플] 공개 정보 기반 추정의 한계"]}}


def _dummy_report(state: State) -> dict:
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    path = os.path.join(config.OUTPUT_DIR, "dummy_report.md")
    unique_docs = {r["source_id"].split("#")[0] for r in state.get("references", [])}
    with open(path, "w", encoding="utf-8") as f:
        f.write("# [DUMMY] KV cache 평가 보고서\n\n")
        f.write(f"- retry_count: {state.get('retry_count')}\n- REFERENCE(문서 단위): {len(unique_docs)}건\n")
        f.write(f"- 한계점: {state.get('synthesis', {}).get('limitations')}\n")
    return {"report_path": path}


DUMMY_NODES = {
    "research": _dummy_research,
    "market": _dummy_market,
    "stakeholder": _dummy_stakeholder,
    "domain": _dummy_domain,
    "check": _dummy_check,
    "synthesis": _dummy_synthesis,
    "report": _dummy_report,
}
