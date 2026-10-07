"""state.py 공용 함수·설정값 기준 테스트 (5번)."""

import config
import state


def test_tech_and_domain_come_from_config():
    assert (state.TECH_SW, state.TECH_HW, state.DOMAIN) == (config.TECH_SW, config.TECH_HW, config.DOMAIN)
    assert state.TECHS == (config.TECH_SW, config.TECH_HW)


def test_paper_source_id_and_doc_id():
    assert state.paper_source_id("2504.19874", 3) == "arxiv:2504.19874#p3"
    assert state.doc_id("arxiv:2504.19874#p3") == "arxiv:2504.19874"
    assert state.doc_id("web:0123456789") == "web:0123456789"


def _ref(**overrides):
    base = {"source_id": "web:a", "kind": "web", "author": "", "date": "", "title": "T", "venue": "",
            "url": "https://example.com", "used_by": ["market"], "stance": "positive"}
    return {**base, **overrides}


def test_merge_references_unions_and_fills():
    refs = [_ref(), _ref(used_by=["stakeholder", "market"], stance="negative", author="A", date="2026-01-01"),
            _ref(source_id="web:b")]
    before = [dict(r) for r in refs]
    merged = state.merge_references(refs)
    assert [r["source_id"] for r in merged] == ["web:a", "web:b"]
    assert merged[0]["used_by"] == ["market", "stakeholder"]
    assert merged[0]["stance"] == "neutral"                     # stance가 다르면 중립
    assert (merged[0]["author"], merged[0]["date"]) == ("A", "2026-01-01")
    assert refs == before                                       # 입력은 바꾸지 않음


def test_merge_references_keeps_pages_separate():
    pages = [_ref(source_id=state.paper_source_id("2406.19707", p), kind="paper") for p in (1, 2)]
    assert len(state.merge_references(pages)) == 2              # 문서 단위 병합은 보고서가 doc_id로 한다
