import hashlib
import json

import config
from agents.market import (
    MarketEvidenceItem,
    _inferred_trl_ceiling,
    _trl_estimate,
    _validated_items,
    market_node,
)
from state import TECHS, make_initial_state
from tests.fixtures import sample_state_after_research
from tools.web_search import search_web


def _record(query, stance, index=0):
    tech = "TurboQuant" if "TurboQuant" in query else "InfiniGen"
    digest = hashlib.sha1(f"{query}:{index}".encode()).hexdigest()[:10]
    url = f"https://evidence.example/{tech.lower()}/{digest}"
    if stance == "negative":
        content = f"{tech} deployment limitation includes operational overhead and quality degradation."
    elif "official paper" in query or "end-to-end" in query:
        content = (
            f"{tech} open source prototype reports benchmark evaluation and reproducible experiments."
        )
    else:
        content = f"{tech} adoption and framework support improve deployment ecosystem growth."
    return {
        "source_id": f"web:{digest}",
        "kind": "web",
        "author": "Evidence Org",
        "date": "2026-01-01",
        "title": query,
        "venue": "evidence.example",
        "url": url,
        "used_by": ["market"],
        "stance": stance,
        "content": content,
    }


def fake_search(query, stance="neutral", max_results=None, used_by="market", **_):
    assert max_results == config.WEB_SEARCH_MAX_RESULTS
    return [_record(query, stance)]


def _prompt_rows(prompt):
    candidate_text = prompt.split("입력 검색 결과 후보:\n", 1)[1]
    rows = []
    for line in candidate_text.splitlines():
        candidate_id, payload = line.split(" | ", 1)
        rows.append({"id": candidate_id, **json.loads(payload)})
    return rows


class FakeStructuredClient:
    def invoke(self, prompt, response_model):
        rows = _prompt_rows(prompt)
        items = []
        for row in rows:
            text = row["content"]
            lowered = f"{row['title']} {text}".lower()
            if "limitation" in lowered or "overhead" in lowered:
                stance = "negative"
            elif "adoption" in lowered or "support" in lowered or "growth" in lowered:
                stance = "positive"
            else:
                stance = "neutral"
            if row["search_perspective"] == "trl":
                category = "none"
            elif "market growth" in lowered:
                category = "market_size_growth"
            elif "framework" in lowered or "ecosystem" in lowered:
                category = "ecosystem"
            else:
                category = "adoption"
            items.append(
                {
                    "id": row["id"],
                    "stance": stance,
                    "market_category": category,
                    "trl_level": 5 if row["search_perspective"] == "trl" else None,
                }
            )
        return response_model(items=items)


def _run(state=None, search_fn=fake_search, client=None):
    return market_node(
        state or sample_state_after_research(),
        search_fn=search_fn,
        structured_client=client or FakeStructuredClient(),
    )


def test_node_contract_and_both_technologies():
    result = _run()
    assert set(result) == {"trl_result", "market_result", "references"}
    assert set(result["trl_result"]) == set(TECHS)
    assert set(result["market_result"]) == set(TECHS)


def test_result_schemas_and_reference_links():
    result = _run()
    reference_ids = {reference["source_id"] for reference in result["references"]}
    for tech in TECHS:
        assert set(result["trl_result"][tech]) == {"level", "rationale", "evidence", "uncertainty"}
        assert set(result["market_result"][tech]) == {
            "market_size_growth",
            "adoption",
            "ecosystem",
            "summary",
        }
        evidence = list(result["trl_result"][tech]["evidence"])
        for field in ("market_size_growth", "adoption", "ecosystem"):
            evidence.extend(result["market_result"][tech][field])
        assert evidence
        assert all(item["source_id"] in reference_ids for item in evidence)


def test_duplicate_source_id_is_not_two_independent_trl_sources():
    item = MarketEvidenceItem(
        source_id="web:same", claim="validated claim", stance="neutral", trl_level=8
    )
    estimate = _trl_estimate("TurboQuant", [item, item], "")
    assert estimate["level"] == 1


def test_same_source_uses_highest_trl_item_for_level_and_evidence():
    low = MarketEvidenceItem(
        source_id="web:same", claim="개념을 제안했다", stance="neutral", trl_level=2
    )
    high = MarketEvidenceItem(
        source_id="web:same",
        claim="정식 출시 후 고객 운영 환경에서 적용이 검증됐다",
        stance="positive",
        trl_level=8,
    )
    estimate = _trl_estimate("TurboQuant", [low, high], "")
    assert estimate["level"] == 1
    assert "TRL 8 신호" in estimate["rationale"]
    assert estimate["evidence"] == [
        {"claim": high.claim, "source_id": "web:same", "stance": "positive"}
    ]


def test_two_distinct_highest_trl_sources_still_confirm_level():
    items = [
        MarketEvidenceItem(
            source_id="web:first", claim="개념을 제안했다", stance="neutral", trl_level=2
        ),
        MarketEvidenceItem(
            source_id="web:first", claim="첫 번째 고객 운영 검증", stance="positive", trl_level=8
        ),
        MarketEvidenceItem(
            source_id="web:second", claim="두 번째 고객 운영 검증", stance="positive", trl_level=8
        ),
    ]
    estimate = _trl_estimate("TurboQuant", items, "")
    assert estimate["level"] == 8
    assert {evidence["claim"] for evidence in estimate["evidence"]} == {
        "첫 번째 고객 운영 검증",
        "두 번째 고객 운영 검증",
    }


def test_benchmark_alone_cannot_reach_trl6():
    assert _inferred_trl_ceiling({"title": "benchmark", "content": "benchmark results"}) < 6


def test_trl3_requires_paper_or_study_and_experimental_result():
    assert _inferred_trl_ceiling({"title": "paper", "content": "experimental results"}) == 3
    assert _inferred_trl_ceiling({"title": "results", "content": "speedup and latency"}) < 3


def test_without_pilot_preview_or_beta_cannot_reach_trl7():
    record = {"title": "serving result", "content": "customer operational benchmark evaluation"}
    assert _inferred_trl_ceiling(record) < 7


def test_ga_or_release_alone_cannot_reach_trl8():
    record = {"title": "Generally available", "content": "official product release"}
    assert _inferred_trl_ceiling(record) < 8


def test_trl8_requires_release_and_customer_or_operational_validation():
    record = {
        "title": "Official product release",
        "content": "generally available with customer validation in operational use",
    }
    assert _inferred_trl_ceiling(record) == 8


def test_one_high_stage_source_records_possibility_without_promotion():
    item = MarketEvidenceItem(
        source_id="web:only", claim="official release", stance="positive", trl_level=8
    )
    estimate = _trl_estimate("InfiniGen", [item], "")
    assert estimate["level"] == 1
    assert "가능성" in estimate["rationale"]


class NeutralStructuredClient(FakeStructuredClient):
    def invoke(self, prompt, response_model):
        result = super().invoke(prompt, response_model)
        for item in result.items:
            item.stance = "neutral"
        return result


def test_negative_evidence_is_not_fabricated():
    result = _run(client=NeutralStructuredClient())
    for tech in TECHS:
        market_evidence = [
            evidence
            for field in ("market_size_growth", "adoption", "ecosystem")
            for evidence in result["market_result"][tech][field]
        ]
        assert all(evidence["stance"] != "negative" for evidence in market_evidence)


def test_validated_korean_limitation_keeps_llm_negative_stance():
    claim = "기존 서빙 환경과 호환되지 않아 별도 커널 구현이 필요하다"
    record = {
        **_record("TurboQuant adoption", "negative"),
        "title": "TurboQuant 배포 검토",
        "content": claim,
        "_perspective": "market",
        "_query_stance": "negative",
    }

    class KoreanLimitationClient:
        def invoke(self, prompt, response_model):
            return response_model(
                items=[
                    {
                        "id": "S1",
                        "stance": "negative",
                        "market_category": "adoption",
                    }
                ]
            )

    items, failed = _validated_items("TurboQuant", [record], KoreanLimitationClient())
    assert not failed
    assert items[0].source_id == record["source_id"]
    assert items[0].claim == claim
    assert items[0].stance == "negative"


def test_validated_korean_support_keeps_llm_positive_stance():
    claim = "운영 환경에 통합되어 처리량이 개선됐다"
    record = {
        **_record("TurboQuant adoption", "positive"),
        "title": "TurboQuant 운영 결과",
        "content": claim,
        "_perspective": "market",
        "_query_stance": "positive",
    }

    class KoreanSupportClient:
        def invoke(self, prompt, response_model):
            return response_model(
                items=[
                    {
                        "id": "S1",
                        "stance": "positive",
                        "market_category": "adoption",
                    }
                ]
            )

    items, failed = _validated_items("TurboQuant", [record], KoreanSupportClient())
    assert not failed
    assert items[0].stance == "positive"


def test_unknown_short_id_only_drops_that_selection():
    first = {
        **_record("TurboQuant first adoption", "positive", 1),
        "content": "TurboQuant framework support improves adoption.",
        "_perspective": "market",
    }
    second = {
        **_record("TurboQuant second adoption", "negative", 2),
        "content": "TurboQuant requires a separate kernel integration.",
        "_perspective": "market",
    }

    class PartlyInvalidClient:
        def invoke(self, prompt, response_model):
            return response_model(
                items=[
                    {"id": "S1", "stance": "positive", "market_category": "ecosystem"},
                    {"id": "S99", "stance": "neutral", "market_category": "none"},
                    {"id": "S2", "stance": "negative", "market_category": "adoption"},
                ]
            )

    items, failed = _validated_items(
        "TurboQuant", [first, second], PartlyInvalidClient()
    )
    assert not failed
    assert [item.source_id for item in items] == [first["source_id"], second["source_id"]]
    assert [item.claim for item in items] == [first["content"], second["content"]]


def test_dedupe_replaces_missing_trl_with_valid_level():
    source_id = "web:shared"
    records = [
        {
            **_record("TurboQuant adoption", "neutral", 1),
            "source_id": source_id,
            "content": "TurboQuant adoption background.",
        },
        {
            **_record("TurboQuant prototype", "neutral", 2),
            "source_id": source_id,
            "content": "TurboQuant prototype benchmark evaluation.",
        },
    ]

    class NoneThenFiveClient:
        def invoke(self, prompt, response_model):
            return response_model(
                items=[
                    {"id": "S1", "stance": "neutral", "market_category": "adoption"},
                    {
                        "id": "S2",
                        "stance": "positive",
                        "market_category": "adoption",
                        "trl_level": 5,
                    },
                ]
            )

    items, failed = _validated_items("TurboQuant", records, NoneThenFiveClient())
    assert not failed
    assert len(items) == 1
    assert items[0].trl_level == 5
    assert items[0].claim == records[1]["content"]


def test_dedupe_keeps_highest_trl_when_lower_level_follows():
    source_id = "web:shared"
    records = [
        {
            **_record("TurboQuant prototype", "positive", 1),
            "source_id": source_id,
            "content": "TurboQuant prototype benchmark evaluation.",
        },
        {
            **_record("TurboQuant paper", "neutral", 2),
            "source_id": source_id,
            "content": "TurboQuant paper reports experimental results.",
        },
    ]

    class FiveThenThreeClient:
        def invoke(self, prompt, response_model):
            return response_model(
                items=[
                    {
                        "id": "S1",
                        "stance": "positive",
                        "market_category": "adoption",
                        "trl_level": 5,
                    },
                    {
                        "id": "S2",
                        "stance": "neutral",
                        "market_category": "adoption",
                        "trl_level": 3,
                    },
                ]
            )

    items, failed = _validated_items("TurboQuant", records, FiveThenThreeClient())
    assert not failed
    assert len(items) == 1
    assert items[0].trl_level == 5
    assert items[0].claim == records[0]["content"]


def test_trl_evidence_uses_candidate_that_supports_selected_high_level():
    shared_source = "web:shared"
    records = [
        {
            **_record("TurboQuant background", "neutral", 1),
            "source_id": shared_source,
            "content": "TurboQuant adoption background.",
        },
        {
            **_record("TurboQuant first prototype", "positive", 2),
            "source_id": shared_source,
            "content": "TurboQuant prototype benchmark evaluation in a testbed.",
        },
        {
            **_record("TurboQuant second prototype", "positive", 3),
            "content": "TurboQuant independent prototype benchmark evaluation.",
        },
    ]

    class HighestCandidatesClient:
        def invoke(self, prompt, response_model):
            return response_model(
                items=[
                    {"id": "S1", "stance": "neutral", "market_category": "none"},
                    {
                        "id": "S2",
                        "stance": "positive",
                        "market_category": "none",
                        "trl_level": 5,
                    },
                    {
                        "id": "S3",
                        "stance": "positive",
                        "market_category": "none",
                        "trl_level": 5,
                    },
                ]
            )

    items, failed = _validated_items("TurboQuant", records, HighestCandidatesClient())
    estimate = _trl_estimate("TurboQuant", items, "")
    assert not failed
    assert estimate["level"] == 5
    assert {evidence["claim"] for evidence in estimate["evidence"]} == {
        records[1]["content"],
        records[2]["content"],
    }


def test_retry_hint_changes_queries():
    first_queries = []
    retry_queries = []

    def capture(target):
        def search(query, stance="neutral", **kwargs):
            target.append(query)
            return [_record(query, stance)]

        return search

    state = sample_state_after_research()
    _run(state, search_fn=capture(first_queries))
    retry_state = dict(state)
    retry_state["sufficiency"] = {
        "trl": False,
        "market": False,
        "stakeholder": True,
        "domain": True,
        "reasons": {
            "trl": "InfiniGen: Evidence 부족",
            "market": "TurboQuant: 반론 근거 미확인(negative 0건)",
        },
    }
    retry_state["retry_count"] = 1
    _run(retry_state, search_fn=capture(retry_queries))
    assert set(first_queries).isdisjoint(retry_queries)


def test_all_search_failures_do_not_escape_and_keep_both_keys():
    def failing_search(*args, **kwargs):
        raise RuntimeError("provider failure")

    result = _run(search_fn=failing_search)
    assert set(result["trl_result"]) == set(TECHS)
    assert set(result["market_result"]) == set(TECHS)
    assert result["references"] == []
    for tech in TECHS:
        assert result["trl_result"][tech]["uncertainty"].startswith("[E-1002]")
        assert result["market_result"][tech]["summary"].startswith("[E-1002]")


def _provider_search(client):
    def search(query, stance="neutral", max_results=None, used_by="market", **kwargs):
        return search_web(
            query,
            stance,
            max_results=max_results,
            used_by=used_by,
            client=client,
        )

    return search


class _Provider:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def search(self, **kwargs):
        if self.error:
            raise self.error
        return self.response


def test_provider_zero_results_reaches_market_as_e1001(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SEARCH_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "USE_SEARCH_CACHE", False)
    result = _run(search_fn=_provider_search(_Provider({"results": []})))
    for tech in TECHS:
        assert result["trl_result"][tech]["uncertainty"].startswith("[E-1001]")
        assert result["market_result"][tech]["summary"].startswith("[E-1001]")


def test_provider_exception_reaches_market_as_e1002_without_escaping(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SEARCH_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "USE_SEARCH_CACHE", False)
    result = _run(search_fn=_provider_search(_Provider(error=RuntimeError("provider down"))))
    for tech in TECHS:
        assert result["trl_result"][tech]["uncertainty"].startswith("[E-1002]")
        assert result["market_result"][tech]["summary"].startswith("[E-1002]")


def test_invalid_provider_response_reaches_market_as_e1002(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SEARCH_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "USE_SEARCH_CACHE", False)
    result = _run(search_fn=_provider_search(_Provider({"unexpected": []})))
    for tech in TECHS:
        assert result["trl_result"][tech]["uncertainty"].startswith("[E-1002]")
        assert result["market_result"][tech]["summary"].startswith("[E-1002]")


def test_one_technology_success_keeps_both_keys_and_marks_e1004():
    def partial_search(query, stance="neutral", **kwargs):
        if "InfiniGen" in query:
            return []
        return [_record(query, stance)]

    result = _run(search_fn=partial_search)
    assert set(result["trl_result"]) == set(TECHS)
    assert set(result["market_result"]) == set(TECHS)
    assert all(
        result["market_result"][tech]["summary"].startswith("[E-1004]") for tech in TECHS
    )


def test_invalid_llm_candidate_id_is_rejected():
    class HallucinatingClient:
        def invoke(self, prompt, response_model):
            return response_model(
                items=[
                    {
                        "id": "S99",
                        "stance": "positive",
                        "market_category": "adoption",
                        "trl_level": 9,
                    }
                ]
            )

    result = _run(client=HallucinatingClient())
    assert result["references"] == []
    assert all(result["trl_result"][tech]["evidence"] == [] for tech in TECHS)


def test_no_real_tavily_or_openai_calls_are_needed():
    state = make_initial_state()
    result = market_node(
        state,
        search_fn=fake_search,
        structured_client=FakeStructuredClient(),
    )
    assert set(result["trl_result"]) == set(TECHS)
