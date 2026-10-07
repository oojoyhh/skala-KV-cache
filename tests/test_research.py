"""2번 기술 조사 노드의 공유 State 계약·오류 처리 테스트."""

import unittest
from unittest.mock import patch

from agents.research import _default_generate, _validate_summary, research_node
from rag.loader import PaperLoadError
from state import TECHS, make_initial_state
from tests.fixtures import sample_state_after_research


def fake_reference(arxiv_id, page):
    """페이지 단위 논문 Reference를 반환하는 테스트 대역."""
    return {
        "source_id": f"arxiv:{arxiv_id}#p{page}",
        "kind": "paper",
        "author": "Test Author",
        "date": "2025",
        "title": "Test Paper",
        "venue": "arXiv",
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "used_by": ["research"],
        "stance": "neutral",
    }


def fake_generate(name, camp, chunks):
    """검색된 청크만 사용해 정상적인 TechSummary 초안을 생성한다."""
    return {
        "name": name,
        "camp": camp,
        "approach": "논문에서 확인한 방식",
        "scope": "논문에서 확인한 적용 범위",
        "key_metrics": {
            "측정 결과": "41.99 tokens/s",
        },
        "limitations": [
            "논문에서 확인한 한계",
        ],
        "evidence": [
            {
                "claim": "측정 결과 41.99 tokens/s",
                "source_id": chunks[0]["source_id"],
                "stance": "positive",
            }
        ],
    }


def fake_retrieve(query, k):
    """첫 검색은 실패하고 재작성된 질의에는 논문 청크를 반환한다."""
    if "retry" not in query:
        return []

    return [
        {
            "text": f"{tech} evaluation 41.99 tokens/s on page {page}",
            "source_id": f"arxiv:{arxiv_id}#p{page}",
            "arxiv_id": arxiv_id,
            "page": page,
            "section": "Evaluation",
        }
        for tech, arxiv_id in (
            ("TurboQuant", "2504.19874"),
            ("InfiniGen", "2406.19707"),
        )
        for page in (2, 3)
    ][:k]


class ResearchNodeTest(unittest.TestCase):
    def assert_success_node_result(self, output, dispatch_id=0):
        """research 노드의 정상 NodeResult를 공통 검증한다."""
        self.assertEqual(
            output["node_result"],
            {
                "node": "research",
                "dispatch_id": dispatch_id,
                "status": "success",
                "error": "",
            },
        )

    def assert_failed_node_result(self, output, error_code, dispatch_id=0):
        """research 노드의 실패 NodeResult를 공통 검증한다."""
        node_result = output["node_result"]

        self.assertEqual(node_result["node"], "research")
        self.assertEqual(node_result["dispatch_id"], dispatch_id)
        self.assertEqual(node_result["status"], "failed")
        self.assertTrue(
            node_result["error"].startswith(error_code),
            node_result["error"],
        )

    def test_default_generator_uses_structured_metric_entries(self):
        """LLM 구조화 출력에서 측정값 목록이 dict로 변환되는지 확인한다."""
        chunk = fake_retrieve("retry", 10)[0]

        def fake_structured(prompt, response_model, role):
            self.assertEqual(role, "generator")
            self.assertEqual(
                response_model.model_json_schema()["properties"][
                    "key_metrics"
                ]["type"],
                "array",
            )

            return response_model(
                name="TurboQuant",
                camp="SW",
                approach="논문에서 확인한 방식",
                scope="논문에서 확인한 적용 범위",
                key_metrics=[
                    {
                        "name": "측정 결과",
                        "value": "41.99 tokens/s",
                    }
                ],
                limitations=[],
                evidence=[
                    {
                        "claim": "측정 결과 41.99 tokens/s",
                        "source_id": chunk["source_id"],
                        "stance": "positive",
                    }
                ],
            )

        with (
            patch("llm.structured", side_effect=fake_structured),
            patch("llm.load_prompt", return_value=""),
        ):
            draft = _default_generate(
                "TurboQuant",
                "SW",
                [chunk],
            )

        summary = _validate_summary(
            draft,
            "TurboQuant",
            [chunk],
        )

        self.assertEqual(
            summary["key_metrics"],
            {"측정 결과": "41.99 tokens/s"},
        )
        self.assertEqual(
            summary["evidence"][0]["source_id"],
            chunk["source_id"],
        )

    def test_rewrites_and_returns_shared_state_shape(self):
        """CorrectiveRAG 재작성 후 공유 State 계약 형식을 반환하는지 확인한다."""
        output = research_node(
            make_initial_state(),
            retrieve_fn=fake_retrieve,
            generate_fn=fake_generate,
            grade_fn=lambda _query, _chunk: True,
            rewrite_fn=lambda query, _name, _attempt: query + " retry",
            reference_fn=fake_reference,
        )

        self.assertEqual(
            set(output),
            {"tech_summary", "references", "node_result"},
        )
        self.assertEqual(
            set(output["tech_summary"]),
            set(TECHS),
        )
        self.assertEqual(len(output["references"]), 2)

        self.assertEqual(
            {
                reference["source_id"]
                for reference in output["references"]
            },
            {
                summary["evidence"][0]["source_id"]
                for summary in output["tech_summary"].values()
            },
        )

        self.assertIsInstance(
            output["tech_summary"]["TurboQuant"]["key_metrics"],
            dict,
        )
        self.assertEqual(
            set(output["tech_summary"]["TurboQuant"]),
            set(
                sample_state_after_research()[
                    "tech_summary"
                ]["TurboQuant"]
            ),
        )
        self.assertEqual(
            output["tech_summary"]["InfiniGen"]["evidence"][0]["stance"],
            "positive",
        )

        self.assert_success_node_result(output)

    def test_returns_current_dispatch_id(self):
        """Supervisor가 전달한 dispatch_id를 그대로 반환해야 한다."""
        state = make_initial_state()
        state["control"]["dispatch_id"] = 7

        output = research_node(
            state,
            retrieve_fn=fake_retrieve,
            generate_fn=fake_generate,
            grade_fn=lambda _query, _chunk: True,
            rewrite_fn=lambda query, _name, _attempt: query + " retry",
            reference_fn=fake_reference,
        )

        self.assert_success_node_result(
            output,
            dispatch_id=7,
        )

    def test_references_match_every_cited_page(self):
        """모든 Evidence 페이지에 대응하는 Reference가 생성되는지 확인한다."""

        def two_page_generate(name, camp, chunks):
            draft = fake_generate(name, camp, chunks)
            draft["evidence"] = [
                {
                    "claim": "측정 결과 41.99 tokens/s",
                    "source_id": chunk["source_id"],
                    "stance": "positive",
                }
                for chunk in chunks[:2]
            ]
            return draft

        output = research_node(
            make_initial_state(),
            retrieve_fn=lambda query, k: fake_retrieve(
                query + " retry",
                k,
            ),
            generate_fn=two_page_generate,
            grade_fn=lambda _query, _chunk: True,
            reference_fn=fake_reference,
        )

        evidence_ids = {
            evidence["source_id"]
            for summary in output["tech_summary"].values()
            for evidence in summary["evidence"]
        }
        reference_ids = {
            reference["source_id"]
            for reference in output["references"]
        }

        self.assertEqual(reference_ids, evidence_ids)
        self.assertEqual(len(reference_ids), 4)
        self.assertEqual(
            {
                "arxiv:2504.19874#p2",
                "arxiv:2504.19874#p3",
            },
            {
                source_id
                for source_id in reference_ids
                if "2504.19874" in source_id
            },
        )

        self.assert_success_node_result(output)

    def test_one_technology_can_fail_without_stopping_graph(self):
        """한 기술만 근거를 확보해도 E-1004와 함께 success를 반환한다."""

        def only_turboquant(query, k):
            return [
                chunk
                for chunk in fake_retrieve(query + " retry", k)
                if chunk["arxiv_id"] == "2504.19874"
            ]

        output = research_node(
            make_initial_state(),
            retrieve_fn=only_turboquant,
            generate_fn=fake_generate,
            grade_fn=lambda _query, _chunk: True,
            rewrite_fn=lambda query, _name, attempt: (
                f"{query} retry-{attempt}"
            ),
            reference_fn=fake_reference,
        )

        self.assertEqual(len(output["references"]), 1)
        self.assertTrue(
            output["tech_summary"]["TurboQuant"]["evidence"]
        )
        self.assertEqual(
            output["tech_summary"]["InfiniGen"]["evidence"],
            [],
        )
        self.assertIn(
            "[E-1004]",
            output["tech_summary"]["InfiniGen"]["limitations"][0],
        )

        self.assert_success_node_result(output)

    def test_empty_search_is_success(self):
        """검색 결과 0건은 E-1001이지만 실행 자체는 success이다."""
        output = research_node(
            make_initial_state(),
            retrieve_fn=lambda _query, _k: [],
            grade_fn=lambda _query, _chunk: True,
            rewrite_fn=lambda query, name, attempt: (
                f"{query} {name} retry-{attempt}"
            ),
            reference_fn=fake_reference,
        )

        self.assertEqual(output["references"], [])

        for name in TECHS:
            summary = output["tech_summary"][name]

            self.assertEqual(summary["evidence"], [])
            self.assertTrue(
                summary["limitations"][0].startswith("[E-1001]"),
                summary["limitations"],
            )

        self.assert_success_node_result(output)

    def test_api_failure_returns_failed_node_result(self):
        """API 또는 구조화 출력 장애는 E-1002 failed로 반환한다."""

        def broken_retrieve(_query, _k):
            raise RuntimeError("API unavailable")

        output = research_node(
            make_initial_state(),
            retrieve_fn=broken_retrieve,
            reference_fn=fake_reference,
        )

        self.assertEqual(output["references"], [])

        for name in TECHS:
            self.assertEqual(
                output["tech_summary"][name]["evidence"],
                [],
            )
            self.assertTrue(
                output["tech_summary"][name]["limitations"][0].startswith(
                    "[E-1002]"
                )
            )

        self.assert_failed_node_result(
            output,
            error_code="E-1002",
        )

    def test_paper_load_failure_returns_failed_node_result(self):
        """PaperLoadError는 E-1003 failed로 반환한다."""

        def broken_retrieve(_query, _k):
            raise PaperLoadError("paper unavailable")

        output = research_node(
            make_initial_state(),
            retrieve_fn=broken_retrieve,
            reference_fn=fake_reference,
        )

        self.assertEqual(output["references"], [])

        for name in TECHS:
            self.assertEqual(
                output["tech_summary"][name]["evidence"],
                [],
            )
            self.assertTrue(
                output["tech_summary"][name]["limitations"][0].startswith(
                    "[E-1003]"
                )
            )

        self.assert_failed_node_result(
            output,
            error_code="E-1003",
        )

    def test_generic_os_error_is_api_failure(self):
        """모델·인덱스·검색 준비 중 OSError는 E-1002로 분류한다."""

        def broken_retrieve(_query, _k):
            raise OSError("index unavailable")

        output = research_node(
            make_initial_state(),
            retrieve_fn=broken_retrieve,
            reference_fn=fake_reference,
        )

        for name in TECHS:
            self.assertTrue(
                output["tech_summary"][name]["limitations"][0].startswith(
                    "[E-1002]"
                )
            )

        self.assert_failed_node_result(
            output,
            error_code="E-1002",
        )

    def test_unretrieved_citation_is_rejected_without_crash(self):
        """검색되지 않은 source_id를 사용한 인용은 거부한다."""

        def unsupported(name, camp, chunks):
            draft = fake_generate(name, camp, chunks)
            draft["evidence"][0]["source_id"] = "arxiv:fake#p99"
            return draft

        output = research_node(
            make_initial_state(),
            retrieve_fn=lambda query, k: fake_retrieve(
                query + " retry",
                k,
            ),
            generate_fn=unsupported,
            grade_fn=lambda _query, _chunk: True,
            reference_fn=fake_reference,
        )

        self.assertEqual(output["references"], [])

        for name in TECHS:
            self.assertEqual(
                output["tech_summary"][name]["evidence"],
                [],
            )
            self.assertIn(
                "[E-1002]",
                output["tech_summary"][name]["limitations"][0],
            )

        self.assert_failed_node_result(
            output,
            error_code="E-1002",
        )

    def test_number_absent_from_cited_page_is_dropped(self):
        """인용 페이지에 없는 수치 Evidence만 제외하고 기술 결과는 유지한다."""

        def invented_result(name, camp, chunks):
            draft = fake_generate(name, camp, chunks)
            draft["evidence"][0]["claim"] = "처리량 9999 tokens/s"
            return draft

        output = research_node(
            make_initial_state(),
            retrieve_fn=lambda query, k: fake_retrieve(
                query + " retry",
                k,
            ),
            generate_fn=invented_result,
            grade_fn=lambda _query, _chunk: True,
            reference_fn=fake_reference,
        )

        self.assertEqual(output["references"], [])
        self.assertTrue(
            all(
                not output["tech_summary"][name]["evidence"]
                for name in TECHS
            )
        )

        for name in TECHS:
            self.assertEqual(
                output["tech_summary"][name]["approach"],
                "논문에서 확인한 방식",
            )
            self.assertEqual(
                output["tech_summary"][name]["key_metrics"],
                {"측정 결과": "41.99 tokens/s"},
            )

        self.assert_success_node_result(output)

    def test_numeric_substrings_are_not_accepted_as_evidence(self):
        """2023의 부분 문자열 3과 20을 별도 수치 근거로 인정하지 않는다."""
        chunk = {
            "text": "The baseline was released in 2023.",
            "source_id": "arxiv:2504.19874#p4",
            "arxiv_id": "2504.19874",
            "page": 4,
            "section": "Evaluation",
        }
        draft = {
            "name": "TurboQuant",
            "camp": "SW",
            "approach": "논문에서 확인한 방식",
            "scope": "논문에서 확인한 적용 범위",
            "key_metrics": {
                "연도": "2023",
                "배속": "3x",
                "비율": "20 percent",
            },
            "limitations": [],
            "evidence": [
                {
                    "claim": "The baseline was released in 2023.",
                    "source_id": chunk["source_id"],
                    "stance": "neutral",
                },
                {
                    "claim": "The method achieved 3x speedup.",
                    "source_id": chunk["source_id"],
                    "stance": "positive",
                },
                {
                    "claim": "The method reduced traffic by 20 percent.",
                    "source_id": chunk["source_id"],
                    "stance": "positive",
                },
            ],
        }

        summary = _validate_summary(draft, "TurboQuant", [chunk])

        self.assertEqual(summary["key_metrics"], {"연도": "2023"})
        self.assertEqual(len(summary["evidence"]), 1)
        self.assertIn("2023", summary["evidence"][0]["claim"])

    def test_one_relevant_chunk_is_sufficient(self):
        """기술별 관련 청크가 하나라도 있으면 생성 단계로 진행한다."""

        def one_page_per_paper(query, k):
            return [
                chunk
                for chunk in fake_retrieve(query + " retry", k)
                if chunk["page"] == 2
            ]

        output = research_node(
            make_initial_state(),
            retrieve_fn=one_page_per_paper,
            generate_fn=fake_generate,
            grade_fn=lambda _query, _chunk: True,
            reference_fn=fake_reference,
        )

        self.assertEqual(len(output["references"]), 2)
        self.assertTrue(
            all(output["tech_summary"][name]["evidence"] for name in TECHS)
        )
        self.assert_success_node_result(output)

    def test_unchanged_rewrite_ends_as_empty_search(self):
        """동일 질의 재작성은 예외 없이 E-1001 빈 결과로 종료한다."""
        calls = []

        def empty_retrieve(query, _k):
            calls.append(query)
            return []

        output = research_node(
            make_initial_state(),
            retrieve_fn=empty_retrieve,
            grade_fn=lambda _query, _chunk: True,
            rewrite_fn=lambda query, _name, _attempt: query,
            reference_fn=fake_reference,
        )

        self.assertEqual(len(calls), len(TECHS))
        for name in TECHS:
            self.assertTrue(
                output["tech_summary"][name]["limitations"][0].startswith(
                    "[E-1001]"
                )
            )
        self.assert_success_node_result(output)

    def test_invalid_numeric_evidence_is_dropped_without_losing_valid_evidence(
        self,
    ):
        """근거 없는 수치 Evidence만 제거하고 정상 Evidence는 유지한다."""

        def one_invalid_claim(name, camp, chunks):
            draft = fake_generate(name, camp, chunks)
            draft["evidence"].append(
                {
                    "claim": "처리량 9999 tokens/s",
                    "source_id": chunks[1]["source_id"],
                    "stance": "positive",
                }
            )
            return draft

        output = research_node(
            make_initial_state(),
            retrieve_fn=lambda query, k: fake_retrieve(
                query + " retry",
                k,
            ),
            generate_fn=one_invalid_claim,
            grade_fn=lambda _query, _chunk: True,
            reference_fn=fake_reference,
        )

        self.assertEqual(len(output["references"]), 2)

        for name in TECHS:
            self.assertEqual(
                len(output["tech_summary"][name]["evidence"]),
                1,
            )
            self.assertEqual(
                output["tech_summary"][name]["limitations"],
                ["논문에서 확인한 한계"],
            )

        self.assert_success_node_result(output)

    def test_unexpected_error_does_not_expose_original_message(self):
        """예상하지 못한 오류에서도 외부 예외 원문을 노출하지 않는다."""
        sensitive_message = (
            "Incorrect API key provided: sk-proj-secret-value"
        )

        with patch(
            "agents.research._research_one",
            side_effect=RuntimeError(sensitive_message),
        ):
            output = research_node(make_initial_state())

        self.assertEqual(set(output), {"node_result"})
        self.assert_failed_node_result(
            output,
            error_code="E-1002",
        )
        self.assertEqual(
            output["node_result"]["error"],
            "E-1002 기술 조사 실행 실패: RuntimeError",
        )
        self.assertNotIn(
            sensitive_message,
            output["node_result"]["error"],
        )
        self.assertNotIn(
            "sk-proj",
            output["node_result"]["error"],
        )

    def test_missing_control_uses_default_dispatch_id(self):
        """control이 없는 비정상 State에서도 예외를 밖으로 던지지 않는다."""
        state = make_initial_state()
        state.pop("control")

        output = research_node(
            state,
            retrieve_fn=fake_retrieve,
            generate_fn=fake_generate,
            grade_fn=lambda _query, _chunk: True,
            rewrite_fn=lambda query, _name, _attempt: query + " retry",
            reference_fn=fake_reference,
        )

        self.assert_success_node_result(
            output,
            dispatch_id=0,
        )


if __name__ == "__main__":
    unittest.main()
