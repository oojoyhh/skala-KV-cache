import copy
import re
import unittest

from agents.stakeholder import Evidence, StakeholderStructuredResponse, evaluate_stakeholders, stakeholder_node
from tests.fixtures import fake_search, sample_state_after_research


class SearchFailureResult(list):
    error_code = "E-1002"


class FakeClient:
    def __init__(self, response: StakeholderStructuredResponse):
        self.response, self.prompt = response, ""

    def invoke(self, prompt: str, response_model: type[StakeholderStructuredResponse]) -> StakeholderStructuredResponse:
        self.prompt = prompt
        return self.response


class SelectingClient:
    def invoke(self, prompt: str, response_model: type[StakeholderStructuredResponse]) -> StakeholderStructuredResponse:
        selections = [{"id": key, "stance": "neutral"} for key in re.findall(r"- id: (E\d+) \| claim:", prompt)]
        return response_model(competitors=selections[:1], adopters_devs=selections[1:2], investors=selections[2:], summary="ignored")


class FailingClient:
    def invoke(self, *_: object) -> StakeholderStructuredResponse:
        raise RuntimeError("LLM unavailable")


class ContentClient:
    def invoke(self, prompt: str, response_model: type[StakeholderStructuredResponse]) -> StakeholderStructuredResponse:
        return response_model(competitors=[{"id": "E3", "stance": "positive"}], adopters_devs=[{"id": "E1", "stance": "negative"}], investors=[{"id": "E2", "stance": "neutral"}], summary="ignored")


def content_mismatch_search(_: str, stance: str, **__: object) -> list[dict]:
    records = {"negative": [("web:support", "The technique reduces serving memory use.")], "positive": [("web:limit", "The technique adds deployment complexity."), ("web:neutral", "The vendor published implementation details.")]}
    return [{"source_id": sid, "kind": "web", "author": "Test", "date": "2026", "title": text, "venue": "test", "url": f"https://test/{sid}", "used_by": ["stakeholder"], "stance": stance, "content": text} for sid, text in records[stance]]


class StakeholderEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence: list[Evidence] = [
            {"claim": "Competitor claim.", "source_id": "ref-1", "stance": "negative"},
            {"claim": "Adopter claim.", "source_id": "ref-2", "stance": "positive"},
            {"claim": "Investor claim.", "source_id": "ref-3", "stance": "neutral"},
        ]

    def response(self, **groups: object) -> StakeholderStructuredResponse:
        return StakeholderStructuredResponse(summary="ignored", **groups)

    def test_id_selection_restores_exact_candidate_without_exposing_source_id(self) -> None:
        client = FakeClient(self.response(competitors=[{"id": "E1", "stance": "negative"}], adopters_devs=[{"id": "E2", "stance": "positive"}], investors=[{"id": "E3", "stance": "neutral"}]))
        result = evaluate_stakeholders("KV", self.evidence, client, classify_stance=True)
        self.assertEqual(result["competitors"], [self.evidence[0]])
        self.assertEqual(result["adopters_devs"], [self.evidence[1]])
        self.assertEqual(result["investors"], [self.evidence[2]])
        self.assertIn("id: E1", client.prompt)
        self.assertNotIn("source_id: ref-1", client.prompt)

    def test_unknown_id_is_dropped_not_an_e1002_error(self) -> None:
        result = evaluate_stakeholders("KV", self.evidence, FakeClient(self.response(competitors=[{"id": "E99", "stance": "negative"}, {"id": "E1", "stance": "negative"}], adopters_devs=[], investors=[])), classify_stance=True)
        self.assertEqual(result["competitors"], [self.evidence[0]])

    def test_reuse_and_conflict_keep_only_valid_selection(self) -> None:
        result = evaluate_stakeholders("KV", self.evidence, FakeClient(self.response(competitors=[{"id": "E1", "stance": "negative"}], adopters_devs=[{"id": "E1", "stance": "negative"}], investors=[{"id": "E1", "stance": "positive"}])), classify_stance=True)
        self.assertEqual(result["competitors"], [self.evidence[0]])
        self.assertEqual(result["adopters_devs"], [self.evidence[0]])
        self.assertEqual(result["investors"], [])

    def test_supplied_evidence_path_preserves_original_stance(self) -> None:
        result = evaluate_stakeholders("KV", self.evidence, FakeClient(self.response(competitors=[{"id": "E1", "stance": "positive"}], adopters_devs=[], investors=[])))
        self.assertEqual(result["competitors"], [self.evidence[0]])


class StakeholderNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state, self.client = sample_state_after_research(), SelectingClient()

    def test_node_returns_two_keys_and_new_references(self) -> None:
        update = stakeholder_node(self.state, search_fn=fake_search, client=self.client)
        self.assertEqual(set(update["stakeholder_result"]), {"TurboQuant", "InfiniGen"})
        self.assertTrue(update["references"])

    def test_retry_and_search_error_contracts_are_preserved(self) -> None:
        first, retry = [], []
        def track(calls):
            def search(query: str, stance: str, **kwargs):
                calls.append(query)
                return fake_search(query, stance, **kwargs)
            return search
        stakeholder_node(self.state, search_fn=track(first), client=self.client)
        state = copy.deepcopy(self.state)
        state["sufficiency"] = {"trl": True, "market": True, "stakeholder": False, "domain": True, "reasons": {"stakeholder": "negative 부족"}}
        stakeholder_node(state, search_fn=track(retry), client=self.client)
        self.assertNotEqual(first, retry)
        self.assertTrue(all("retry focus:" in query for query in retry))
        empty = stakeholder_node(self.state, search_fn=lambda *_a, **_k: [], client=self.client)
        failed = stakeholder_node(self.state, search_fn=lambda *_a, **_k: SearchFailureResult(), client=self.client)
        self.assertTrue(all(x["summary"].startswith("[E-1001]") for x in empty["stakeholder_result"].values()))
        self.assertTrue(all(x["summary"].startswith("[E-1002]") for x in failed["stakeholder_result"].values()))

    def test_partial_search_failure_recovers_with_later_results(self) -> None:
        calls = 0

        def partially_failed_search(query: str, stance: str, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return SearchFailureResult()
            return fake_search(query, stance, **kwargs)

        update = stakeholder_node(self.state, search_fn=partially_failed_search, client=self.client)

        self.assertGreater(calls, 1)
        self.assertTrue(update["references"])
        for result in update["stakeholder_result"].values():
            self.assertFalse(result["summary"].startswith("[E-1002]"))
            self.assertTrue(any(result[group] for group in ("competitors", "adopters_devs", "investors")))

    def test_content_stance_is_not_search_intent(self) -> None:
        update = stakeholder_node(self.state, search_fn=content_mismatch_search, client=ContentClient())
        result = update["stakeholder_result"]["TurboQuant"]
        self.assertEqual([(x["source_id"], x["stance"]) for x in [*result["competitors"], *result["adopters_devs"], *result["investors"]]], [("web:support", "positive"), ("web:limit", "negative"), ("web:neutral", "neutral")])

    def test_llm_failure_still_returns_e1002(self) -> None:
        update = stakeholder_node(self.state, search_fn=fake_search, client=FailingClient())
        self.assertTrue(all(x["summary"].startswith("[E-1002]") for x in update["stakeholder_result"].values()))


if __name__ == "__main__":
    unittest.main()
