"""보고서 생성 노드 단독 테스트 (6번). 실행: python -m pytest tests/test_report.py

LLM 대신 가짜 generate를 주입한다. API 키 없이 동작한다.
"""

import re

import pytest

import config
import llm
from agents import report
from agents.report import Citations, build_blocks, report_node
from tests.fixtures import sample_state_after_eval


def fake_generate(prompt: str) -> str:
    """입력에 있는 인용을 그대로 쓰고, 존재하지 않는 인용 [99]를 하나 섞는다."""
    cites = sorted(set(re.findall(r'"cite": "(\[[^"]+\])"', prompt)))
    return "샘플 본문 " + " ".join(cites) + " 없는 인용 [99]"


def failing_generate(prompt: str) -> str:
    raise llm.LLMError("[E-1002] LLM 호출 실패: Incorrect API key provided: sk-proj-SECRET")


def _text(blocks) -> str:
    return "\n".join(v if isinstance(v, str) else str(v) for _, v in blocks)


@pytest.mark.parametrize("sufficient", [True, False])
def test_report_node_writes_pdf(tmp_path, monkeypatch, sufficient):
    monkeypatch.setattr(config, "REPORT_PATH", str(tmp_path / config.REPORT_FILENAME))
    out = report_node(sample_state_after_eval(sufficient), generate_fn=fake_generate)
    assert out == {"report_path": config.REPORT_PATH}
    assert (tmp_path / config.REPORT_FILENAME).stat().st_size > 1000


def test_outline_order_and_rules():
    state = sample_state_after_eval(True)
    blocks = build_blocks(state, fake_generate)
    titles = [v for k, v in blocks if k in ("h1", "h2", "h3")]
    assert titles[1] == "SUMMARY" and titles[-1] == "REFERENCE"
    assert titles[2:-1] == ["1. 분석 배경", "2. 기술 선정", "3. 기술 개요", "4. 관점별 평가", "4-1. 기술 성숙도 (TRL)",
                            "4-2. 시장성", "4-3. 이해관계자", "4-4. 도메인 적용", "5. 시사점", "6. 한계점"]
    text = _text(blocks)
    assert "[99]" not in text                   # 없는 인용 제거
    assert "공개 정보 기반 추정" in text          # TRL 명시 문구
    assert "해당 없음" in text                   # TurboQuant transfer_overhead 등 빈 지표


def test_reference_dedup_by_document():
    state = sample_state_after_eval(True)
    blocks = build_blocks(state, fake_generate)
    refs = [v for k, v in blocks if k == "p"][-1].splitlines()
    # 논문은 p.1·p.7 두 청크가 있어도 문서 단위로 한 줄
    assert sum("TurboQuant: Online Vector Quantization" in r for r in refs) == 1
    cited_docs = {r["source_id"].split("#")[0] for r in state["references"]}
    assert len(refs) == len(cited_docs)
    assert refs[0].startswith("[1] ")


def test_citation_numbers_and_used_by_merge():
    refs = [
        {"source_id": "arxiv:1#p1", "kind": "paper", "author": "A", "date": "2025", "title": "T", "venue": "arXiv, 1",
         "url": "u", "used_by": ["research"], "stance": "neutral"},
        {"source_id": "arxiv:1#p7", "kind": "paper", "author": "A", "date": "2025", "title": "T", "venue": "arXiv, 1",
         "url": "u", "used_by": ["domain"], "stance": "neutral"},
        {"source_id": "web:x", "kind": "web", "author": "B", "date": "2026-05-11", "title": "W", "venue": "Blog",
         "url": "https://b", "used_by": [], "stance": "negative"},
    ]
    c = Citations(refs, {"arxiv:1#p1", "arxiv:1#p7"})
    assert c.cite("arxiv:1#p7") == "[1, p.7]" and c.cite("arxiv:1#p1") == "[1, p.1]"
    assert c.cite("web:x") is None               # Evidence가 가리키지 않는 출처는 REFERENCE 제외
    assert c.docs["arxiv:1"]["used_by"] == ["research", "domain"]
    assert c.reference_lines() == ["[1] A(2025). T. arXiv, 1."]
    assert report.format_reference(refs[2]) == "B(2026-05-11). W. Blog, https://b"


def test_llm_failure_does_not_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPORT_PATH", str(tmp_path / config.REPORT_FILENAME))
    out = report_node(sample_state_after_eval(False), generate_fn=failing_generate)
    assert (tmp_path / config.REPORT_FILENAME).exists() and out["report_path"]
    md = (tmp_path / config.REPORT_FILENAME).with_suffix(".md").read_text(encoding="utf-8")
    assert "[E-1002]" in md and "- [샘플]" in md  # 근거 목록으로 대체
    assert "sk-proj" not in md and "API key" not in md  # 오류 원문(비밀값 일부)은 싣지 않음


def test_summary_is_not_an_introduction():
    prompts = []

    def recording_generate(prompt: str) -> str:
        prompts.append(prompt)
        return fake_generate(prompt)

    build_blocks(sample_state_after_eval(True), recording_generate)
    summary_prompt = next(p for p in prompts if "## 챕터: SUMMARY" in p)
    assert "인트로덕션이 아니다" in summary_prompt
    assert "1. 분석 배경" not in summary_prompt and "2. 기술 선정" not in summary_prompt  # 배경·선정 챕터는 넘기지 않음
    assert "3. 기술 개요" in summary_prompt and "6. 한계점" in summary_prompt


def test_reference_keeps_uncited_sources_and_formats():
    refs = [
        {"source_id": "web:a", "kind": "web", "author": "", "date": "Thu, 26 Mar 2026 10:00:00 GMT", "title": "Cited",
         "venue": "Site A", "url": "https://a", "used_by": ["market"], "stance": "positive"},
        {"source_id": "web:b", "kind": "web", "author": "Org B", "date": "", "title": "Consulted",
         "venue": "Site B", "url": "https://b", "used_by": ["stakeholder"], "stance": "negative"},
        {"source_id": "patent:c", "kind": "patent", "author": "NVIDIA", "date": "2025-03-01", "title": "KV Cache Transform Coding",
         "venue": "US-XXXXXXX-A1", "url": "https://c", "used_by": ["market"], "stance": "neutral"},
    ]
    c = Citations(refs, {"web:a", "web:b", "patent:c"})
    c.cite("web:a")
    assert c.reference_lines() == [
        "[1] Site A(2026-03-26). Cited. Site A, https://a",            # 작성자 없음 → 사이트명, RFC 날짜 → YYYY-MM-DD
        "[2] Org B(n.d.). Consulted. Site B, https://b",               # 본문 인용은 없어도 Evidence에 있으면 수록
        "[3] NVIDIA(2025-03). KV Cache Transform Coding, US-XXXXXXX-A1, https://c",
    ]


def test_reference_excludes_search_results_not_used_as_evidence():
    state = sample_state_after_eval(True)
    unused = {"source_id": "web:unused", "kind": "web", "author": "X", "date": "2026-01-01", "title": "검색만 된 자료",
              "venue": "X", "url": "https://x", "used_by": ["market"], "stance": "neutral"}
    state["references"].append(unused)
    text = _text(build_blocks(state, fake_generate))
    assert "검색만 된 자료" not in text


def test_limitations_always_list_sufficiency_reasons():
    state = sample_state_after_eval(False)   # InfiniGen 이해관계자 한계·반론 근거 0건
    blocks = build_blocks(state, lambda prompt: "LLM이 사유를 빠뜨린 본문")
    limits = [v for k, v in blocks if k == "p"][-2]   # 6장 (마지막 p는 REFERENCE)
    assert "충분성 검사 미달 사유" in limits and "- 이해관계자: InfiniGen" in limits


def test_glyphs_missing_from_font_are_spelled_out():
    from fontTools.ttLib import TTFont

    cmap = TTFont(report._font_file()).getBestCmap()
    assert report._printable("alpha=α, λ=0.7, ① ✅", cmap) == "alpha=alpha, lambda=0.7, (1) "


def test_citations_not_in_chapter_input_are_removed():
    # 이번 실행에 논문 Evidence가 없는데 LLM이 [1, p.7]을 지어낸 경우 (실제 통합 실행에서 발견)
    data = {"x": [{"claim": "웹 근거", "stance": "positive", "cite": "[1]"}, {"claim": "논문", "cite": "[2, p.3]"}]}
    allowed = report._allowed_cites(data)
    text = "가 [1, p.7]. 나 [1]. 다 [2, p.3]. 라 [2, p.9]. 마 [3]. 바 [1, 2]."
    assert report._keep_given_citations(text, allowed) == "가. 나 [1]. 다 [2, p.3]. 라. 마. 바 [1] [2]."


def test_repeated_chapter_title_lines_are_stripped():
    for head in ("2. 기술 선정", "### 2. 기술 선정", "**기술 선정**", "\n2. 기술 선정\n"):
        assert report._strip_title_lines(head + "\n본문 첫 줄", "2. 기술 선정") == "본문 첫 줄"
    assert report._strip_title_lines("기술 선정 이유는 다음과 같다.", "2. 기술 선정") == "기술 선정 이유는 다음과 같다."


def test_error_codes_are_not_described_as_tech_limitations():
    state = sample_state_after_eval(True)
    state["tech_summary"]["TurboQuant"]["limitations"] = ["[E-1002] 논문 검색 또는 구조화 출력 실패", "실제 한계"]
    prompts = []

    def recording_generate(prompt: str) -> str:
        prompts.append(prompt)
        return "본문"

    blocks = build_blocks(state, recording_generate)
    assert not any("E-1002" in p for p in prompts)                 # LLM 입력에서 제외
    assert any("실제 한계" in p for p in prompts)                  # 진짜 한계는 그대로
    fixed = [v for k, v in blocks if k == "fixed"]                 # 6장 뒤 고정 문단
    assert len(fixed) == 1 and "데이터 수집 오류" in fixed[0] and "- 기술 조사(TurboQuant): [E-1002]" in fixed[0]


def test_same_web_page_with_and_without_www_is_one_reference():
    refs = [
        {"source_id": "web:a", "kind": "web", "author": "", "date": "", "title": "T", "venue": "tradingkey.com",
         "url": "https://tradingkey.com/x/", "used_by": ["market"], "stance": "neutral"},
        {"source_id": "web:b", "kind": "web", "author": "", "date": "", "title": "T", "venue": "www.tradingkey.com",
         "url": "https://www.tradingkey.com/x", "used_by": ["stakeholder"], "stance": "neutral"},
    ]
    c = Citations(refs, {"web:a", "web:b"})
    assert c.cite("web:a") == c.cite("web:b") == "[1]"
    assert c.reference_lines() == ["[1] tradingkey.com(n.d.). T. tradingkey.com, https://tradingkey.com/x/"]


def test_chapters_without_evidence_are_not_written_by_llm():
    state = sample_state_after_eval(True)
    for tech in state["stakeholder_result"]:
        state["stakeholder_result"][tech] = {"competitors": [], "adopters_devs": [], "investors": [], "summary": ""}
    state["synthesis"] = {"agreements": [], "conflicts": [], "neutrality_note": "", "limitations": []}
    prompts = []

    def recording_generate(prompt: str) -> str:
        prompts.append(prompt)
        return "LLM 추측 문단"

    blocks = build_blocks(state, recording_generate)
    after = lambda title: blocks[blocks.index(next(b for b in blocks if b[1] == title)) + 1][1]
    assert after("4-3. 이해관계자") == report.NO_EVIDENCE
    assert after("5. 시사점") == report.NO_SYNTHESIS
    assert not any("## 챕터: 4-3" in p or "## 챕터: 5." in p for p in prompts)


def test_summary_keeps_only_bullets():
    def gen(prompt: str) -> str:
        if "## 챕터: SUMMARY" in prompt:
            return "두 기술은 상이한 접근을 제시한다.\n- 핵심 1\n- 핵심 2\n결론적으로 선택은 달라질 수 있다."
        return "본문"

    blocks = build_blocks(sample_state_after_eval(True), gen)
    summary = blocks[blocks.index(("h2", "SUMMARY")) + 1][1]
    assert summary == "- 핵심 1\n- 핵심 2"


def test_single_tech_sentence_keeps_only_its_own_citations():
    refs = [
        {"source_id": "web:t", "kind": "web", "author": "A", "date": "", "title": "TQ 자료", "venue": "a.com",
         "url": "https://a.com/t", "used_by": ["market"], "stance": "negative"},
        {"source_id": "web:i", "kind": "web", "author": "B", "date": "", "title": "IG 자료", "venue": "b.com",
         "url": "https://b.com/i", "used_by": ["market"], "stance": "positive"},
    ]
    state = {"market_result": {"TurboQuant": {"adoption": [{"claim": "c", "source_id": "web:t", "stance": "negative"}]},
                               "InfiniGen": {"adoption": [{"claim": "c", "source_id": "web:i", "stance": "positive"}]}}}
    c = Citations(refs, {"web:t", "web:i"})
    assert (c.cite("web:t"), c.cite("web:i")) == ("[1]", "[2]")
    techs_of = report.doc_techs(state, c)
    text = ("TurboQuant는 정밀도 손실이 지적된다 [1][2].\n"
            "InfiniGen은 전송 부담이 보고된다 [1] [2]. 두 기술 모두 TurboQuant·InfiniGen 근거가 있다 [1][2].")
    assert report._keep_same_tech_citations(text, c, techs_of) == (
        "TurboQuant는 정밀도 손실이 지적된다 [1].\n"
        "InfiniGen은 전송 부담이 보고된다 [2]. 두 기술 모두 TurboQuant·InfiniGen 근거가 있다 [1][2].")
