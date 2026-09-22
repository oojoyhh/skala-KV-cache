"""2번 기술 조사 노드의 공유 State 계약·오류 처리 테스트."""

import unittest

from agents.research import research_node
from state import TECHS, make_initial_state
from tests.fixtures import sample_state_after_research


def fake_reference(arxiv_id):
    return {
        "source_id": f"arxiv:{arxiv_id}", "kind": "paper", "author": "Test Author",
        "date": "2025", "title": "Test Paper", "venue": "arXiv",
        "url": f"https://arxiv.org/abs/{arxiv_id}", "used_by": ["research"],
        "stance": "neutral",
    }


def fake_generate(name, camp, chunks):
    return {
        "name": name, "camp": camp, "approach": "논문에서 확인한 방식",
        "scope": "논문에서 확인한 적용 범위", "key_metrics": {"측정 결과": "41.99 tokens/s"},
        "limitations": ["논문에서 확인한 한계"],
        "evidence": [{"claim": "측정 결과 41.99 tokens/s", "source_id": chunks[0]["source_id"],
                      "stance": "positive"}],
    }


def fake_retrieve(query, k):
    if "retry" not in query:
        return []
    return [
        {"text": f"{tech} evaluation 41.99 tokens/s on page {page}",
         "source_id": f"arxiv:{arxiv_id}#p{page}", "arxiv_id": arxiv_id,
         "page": page, "section": "Evaluation"}
        for tech, arxiv_id in (("TurboQuant", "2504.19874"), ("InfiniGen", "2406.19707"))
        for page in (2, 3)
    ][:k]


class ResearchNodeTest(unittest.TestCase):
    def test_rewrites_and_returns_shared_state_shape(self):
        output = research_node(
            make_initial_state(), retrieve_fn=fake_retrieve, generate_fn=fake_generate,
            grade_fn=lambda _query, _chunk: True,
            rewrite_fn=lambda query, _name, _attempt: query + " retry",
            reference_fn=fake_reference,
        )
        self.assertEqual(set(output), {"tech_summary", "references"})
        self.assertEqual(set(output["tech_summary"]), set(TECHS))
        self.assertEqual(len(output["references"]), 2)
        self.assertIsInstance(output["tech_summary"]["TurboQuant"]["key_metrics"], dict)
        self.assertEqual(
            set(output["tech_summary"]["TurboQuant"]),
            set(sample_state_after_research()["tech_summary"]["TurboQuant"]),
        )
        self.assertEqual(output["tech_summary"]["InfiniGen"]["evidence"][0]["stance"], "positive")

    def test_one_technology_can_fail_without_stopping_graph(self):
        def only_turboquant(query, k):
            return [chunk for chunk in fake_retrieve(query + " retry", k)
                    if chunk["arxiv_id"] == "2504.19874"]

        output = research_node(
            make_initial_state(), retrieve_fn=only_turboquant, generate_fn=fake_generate,
            grade_fn=lambda _query, _chunk: True,
            rewrite_fn=lambda query, _name, attempt: f"{query} retry-{attempt}",
            reference_fn=fake_reference,
        )
        self.assertEqual(len(output["references"]), 1)
        self.assertTrue(output["tech_summary"]["TurboQuant"]["evidence"])
        self.assertEqual(output["tech_summary"]["InfiniGen"]["evidence"], [])
        self.assertIn("[E-1004]", output["tech_summary"]["InfiniGen"]["limitations"][0])

    def test_unretrieved_citation_is_rejected_without_crash(self):
        def unsupported(name, camp, chunks):
            draft = fake_generate(name, camp, chunks)
            draft["evidence"][0]["source_id"] = "arxiv:fake#p99"
            return draft

        output = research_node(
            make_initial_state(), retrieve_fn=lambda query, k: fake_retrieve(query + " retry", k),
            generate_fn=unsupported, grade_fn=lambda _query, _chunk: True,
            reference_fn=fake_reference,
        )
        self.assertEqual(output["references"], [])
        for name in TECHS:
            self.assertEqual(output["tech_summary"][name]["evidence"], [])
            self.assertIn("[E-1002]", output["tech_summary"][name]["limitations"][0])

    def test_number_absent_from_cited_page_is_rejected(self):
        def invented_result(name, camp, chunks):
            draft = fake_generate(name, camp, chunks)
            draft["evidence"][0]["claim"] = "처리량 9999 tokens/s"
            return draft

        output = research_node(
            make_initial_state(), retrieve_fn=lambda query, k: fake_retrieve(query + " retry", k),
            generate_fn=invented_result, grade_fn=lambda _query, _chunk: True,
            reference_fn=fake_reference,
        )
        self.assertEqual(output["references"], [])
        self.assertTrue(all(not output["tech_summary"][name]["evidence"] for name in TECHS))


if __name__ == "__main__":
    unittest.main()
