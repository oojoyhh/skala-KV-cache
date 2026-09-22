"""검색 평가는 문서 ID와 1-based 페이지를 함께 비교한다."""

import unittest

from eval.retrieval_eval import evaluate_results, load_dataset


class RetrievalEvaluationTest(unittest.TestCase):
    def test_dataset_contains_both_papers(self):
        dataset = load_dataset()
        self.assertEqual(len(dataset), 12)
        self.assertEqual({item["technology"] for item in dataset}, {"TurboQuant", "InfiniGen"})

    def test_wrong_paper_same_page_is_not_a_hit(self):
        dataset = [{"id": "x", "technology": "TurboQuant",
                    "document_id": "arxiv:2504.19874", "gold_pages": [4]}]
        metrics = evaluate_results(dataset, {"x": ["arxiv:2406.19707#p4"]})
        self.assertEqual(metrics["hit_rate@5"], 0.0)
        self.assertEqual(metrics["mrr@5"], 0.0)

    def test_hit_rate_and_mrr(self):
        dataset = [
            {"id": "a", "technology": "TurboQuant", "document_id": "arxiv:2504.19874", "gold_pages": [4]},
            {"id": "b", "technology": "InfiniGen", "document_id": "arxiv:2406.19707", "gold_pages": [8]},
        ]
        results = {"a": ["arxiv:2504.19874#p4"],
                   "b": ["arxiv:2406.19707#p2", "arxiv:2406.19707#p8"]}
        metrics = evaluate_results(dataset, results)
        self.assertEqual(metrics["hit_rate@5"], 1.0)
        self.assertEqual(metrics["mrr@5"], 0.75)

    def test_unqualified_page_is_rejected(self):
        dataset = [{"id": "x", "technology": "TurboQuant",
                    "document_id": "arxiv:2504.19874", "gold_pages": [4]}]
        with self.assertRaises(ValueError):
            evaluate_results(dataset, {"x": [4]})


if __name__ == "__main__":
    unittest.main()
