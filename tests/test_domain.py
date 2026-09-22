import copy
import re
import unittest

from agents.domain import DomainStructuredResponse, Evidence, build_domain_prompt, domain_node, evaluate_domain
from tests.fixtures import fake_search, sample_state_after_research


class SearchFailureResult(list):
    error_code = "E-1002"


class FakeClient:
    def __init__(self, response: DomainStructuredResponse):
        self.response, self.prompt = response, ""
    def invoke(self, prompt: str, response_model: type[DomainStructuredResponse]) -> DomainStructuredResponse:
        self.prompt = prompt
        return self.response


class SelectingClient:
    def invoke(self, prompt: str, response_model: type[DomainStructuredResponse]) -> DomainStructuredResponse:
        selected = [{"id": key, "stance": "neutral"} for key in re.findall(r"- id: (E\d+) \| claim:", prompt)]
        return response_model(cost=selected[:1], throughput=selected[1:2], model_quality=selected[2:3], transfer_overhead=selected[3:4], deployment_barrier=selected[4:], summary="ignored")


class FailingClient:
    def invoke(self, *_: object) -> DomainStructuredResponse:
        raise RuntimeError("LLM unavailable")


class ContentClient:
    def invoke(self, prompt: str, response_model: type[DomainStructuredResponse]) -> DomainStructuredResponse:
        return response_model(cost=[{"id": "E3", "stance": "positive"}], throughput=[{"id": "E1", "stance": "negative"}], model_quality=[{"id": "E2", "stance": "neutral"}], transfer_overhead=[], deployment_barrier=[], summary="ignored")


def content_mismatch_search(_: str, stance: str, **__: object) -> list[dict]:
    records = {"negative": [("web:support", "The technique reduces serving memory use.")], "positive": [("web:limit", "The technique adds deployment complexity."), ("web:neutral", "The vendor published implementation details.")]}
    return [{"source_id": sid, "kind": "web", "author": "Test", "date": "2026", "title": text, "venue": "test", "url": f"https://test/{sid}", "used_by": ["domain"], "stance": stance, "content": text} for sid, text in records[stance]]


class DomainEvaluationTests(unittest.TestCase):
    domain = "datacenter"
    def setUp(self) -> None:
        self.evidence: list[Evidence] = [
            {"claim": "Cost claim.", "source_id": "ref-1", "stance": "positive"},
            {"claim": "Throughput claim.", "source_id": "ref-2", "stance": "neutral"},
        ]
    def response(self, **axes: object) -> DomainStructuredResponse:
        return DomainStructuredResponse(summary="ignored", **axes)
    def test_id_selection_restores_exact_candidate_without_source_copy(self) -> None:
        client = FakeClient(self.response(cost=[{"id": "E1", "stance": "positive"}], throughput=[{"id": "E2", "stance": "neutral"}], model_quality=[], transfer_overhead=[], deployment_barrier=[]))
        result = evaluate_domain(self.domain, self.evidence, client, classify_stance=True)
        self.assertEqual(result["cost"], [self.evidence[0]])
        self.assertEqual(result["throughput"], [self.evidence[1]])
        self.assertIn("id: E1", client.prompt)
        self.assertNotIn("source_id: ref-1", client.prompt)
    def test_prompt_and_supplied_path_contract(self) -> None:
        self.assertIn("id만 선택", build_domain_prompt(self.domain, self.evidence, classify_stance=True))
        result = evaluate_domain(self.domain, self.evidence, FakeClient(self.response(cost=[{"id": "E1", "stance": "negative"}], throughput=[], model_quality=[], transfer_overhead=[], deployment_barrier=[])))
        self.assertEqual(result["cost"], [self.evidence[0]])
    def test_unknown_id_reuse_and_conflict_are_item_local(self) -> None:
        result = evaluate_domain(self.domain, self.evidence, FakeClient(self.response(cost=[{"id": "E99", "stance": "positive"}, {"id": "E1", "stance": "positive"}], throughput=[{"id": "E1", "stance": "positive"}], model_quality=[{"id": "E1", "stance": "negative"}], transfer_overhead=[], deployment_barrier=[])), classify_stance=True)
        self.assertEqual(result["cost"], [self.evidence[0]])
        self.assertEqual(result["throughput"], [self.evidence[0]])
        self.assertEqual(result["model_quality"], [])


class DomainNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state, self.client = sample_state_after_research(), SelectingClient()
    def test_node_returns_two_keys_and_new_references(self) -> None:
        update = domain_node(self.state, search_fn=fake_search, client=self.client)
        self.assertEqual(set(update["domain_result"]), {"TurboQuant", "InfiniGen"})
        self.assertTrue(update["references"])
    def test_retry_and_search_error_contracts_are_preserved(self) -> None:
        first, retry = [], []
        def track(calls):
            def search(query: str, stance: str, **kwargs):
                calls.append(query); return fake_search(query, stance, **kwargs)
            return search
        domain_node(self.state, search_fn=track(first), client=self.client)
        state = copy.deepcopy(self.state); state["sufficiency"] = {"trl": True, "market": True, "stakeholder": True, "domain": False, "reasons": {"domain": "negative 부족"}}
        domain_node(state, search_fn=track(retry), client=self.client)
        self.assertNotEqual(first, retry); self.assertTrue(all("retry focus:" in query for query in retry))
        empty = domain_node(self.state, search_fn=lambda *_a, **_k: [], client=self.client)
        failed = domain_node(self.state, search_fn=lambda *_a, **_k: SearchFailureResult(), client=self.client)
        self.assertTrue(all(x["summary"].startswith("[E-1001]") for x in empty["domain_result"].values()))
        self.assertTrue(all(x["summary"].startswith("[E-1002]") for x in failed["domain_result"].values()))
    def test_partial_search_failure_recovers_with_later_results(self) -> None:
        calls = 0
        def partially_failed_search(query: str, stance: str, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return SearchFailureResult()
            return fake_search(query, stance, **kwargs)

        update = domain_node(self.state, search_fn=partially_failed_search, client=self.client)

        self.assertGreater(calls, 1)
        self.assertTrue(update["references"])
        for result in update["domain_result"].values():
            self.assertFalse(result["summary"].startswith("[E-1002]"))
            self.assertTrue(any(result[axis] for axis in (
                "cost", "throughput", "model_quality", "transfer_overhead", "deployment_barrier",
            )))
    def test_content_stance_is_not_search_intent(self) -> None:
        update = domain_node(self.state, search_fn=content_mismatch_search, client=ContentClient())
        result = update["domain_result"]["TurboQuant"]
        self.assertEqual([(x["source_id"], x["stance"]) for x in [*result["cost"], *result["throughput"], *result["model_quality"]]], [("web:support", "positive"), ("web:limit", "negative"), ("web:neutral", "neutral")])
    def test_llm_failure_still_returns_e1002(self) -> None:
        update = domain_node(self.state, search_fn=fake_search, client=FailingClient())
        self.assertTrue(all(x["summary"].startswith("[E-1002]") for x in update["domain_result"].values()))


if __name__ == "__main__":
    unittest.main()
