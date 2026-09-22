import copy
import re
import unittest

import config
from agents.stakeholder import (
    Evidence,
    StakeholderStructuredResponse,
    StakeholderValidationError,
    evaluate_stakeholders,
    stakeholder_node,
)
from tests.fixtures import fake_search, sample_state_after_research


class FakeStructuredOutputClient:
    def __init__(self, result: StakeholderStructuredResponse) -> None:
        self.result = result
        self.prompt = ""

    def invoke(
        self,
        prompt: str,
        response_model: type[StakeholderStructuredResponse],
    ) -> StakeholderStructuredResponse:
        self.prompt = prompt
        self.response_model = response_model
        return self.result


class SelectingStructuredClient:
    """Selects prompt Evidence without calling an external LLM."""

    def invoke(self, prompt: str, response_model: type[StakeholderStructuredResponse]) -> StakeholderStructuredResponse:
        evidence = []
        for claim, source_id, stance in re.findall(
            r"- claim: (.*?) \| source_id: (.*?) \| stance: (positive|negative|neutral)", prompt
        ):
            evidence.append({"claim": claim, "source_id": source_id, "stance": stance})
        return response_model(
            competitors=evidence[:1],
            adopters_devs=evidence[1:2],
            investors=evidence[2:],
            summary="Ignored by deterministic summary generation.",
        )


class FailingStructuredClient:
    def invoke(self, prompt: str, response_model: type[StakeholderStructuredResponse]) -> StakeholderStructuredResponse:
        raise RuntimeError("LLM unavailable")


class StakeholderEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence: list[Evidence] = [
            {
                "claim": "A competitor announced a comparable method.",
                "source_id": "ref-competitor",
                "stance": "negative",
            },
            {
                "claim": "A provider deployed the method.",
                "source_id": "ref-adopter",
                "stance": "positive",
            },
            {
                "claim": "Funding was announced for the product.",
                "source_id": "ref-investor",
                "stance": "positive",
            },
        ]

    def test_evidence_is_classified_into_all_three_groups(self) -> None:
        response = StakeholderStructuredResponse(
            competitors=[self.evidence[0]],
            adopters_devs=[self.evidence[1]],
            investors=[self.evidence[2]],
            summary="The supplied evidence addresses each stakeholder group.",
        )
        client = FakeStructuredOutputClient(response)

        result = evaluate_stakeholders("KV cache technique", self.evidence, client)

        self.assertEqual(result["competitors"], [self.evidence[0]])
        self.assertEqual(result["adopters_devs"], [self.evidence[1]])
        self.assertEqual(result["investors"], [self.evidence[2]])
        self.assertIn("source_id: ref-competitor", client.prompt)

    def test_missing_investor_evidence_is_a_valid_empty_list(self) -> None:
        response = StakeholderStructuredResponse(
            competitors=[self.evidence[0]],
            adopters_devs=[self.evidence[1]],
            investors=[],
            summary="No supplied evidence concerns investors.",
        )

        result = evaluate_stakeholders(
            "KV cache technique", self.evidence[:2], FakeStructuredOutputClient(response)
        )

        self.assertEqual(result["investors"], [])

    def test_no_evidence_returns_three_empty_groups(self) -> None:
        response = StakeholderStructuredResponse(
            competitors=[],
            adopters_devs=[],
            investors=[],
            summary="No stakeholder claims can be made from supplied evidence.",
        )

        result = evaluate_stakeholders("KV cache technique", [], FakeStructuredOutputClient(response))

        self.assertEqual(result["competitors"], [])
        self.assertEqual(result["adopters_devs"], [])
        self.assertEqual(result["investors"], [])

    def test_unknown_source_id_is_rejected(self) -> None:
        response = StakeholderStructuredResponse(
            competitors=[
                {
                    "claim": "A competitor announced a comparable method.",
                    "source_id": "unknown-reference",
                    "stance": "negative",
                }
            ],
            adopters_devs=[],
            investors=[],
            summary="Invalid output.",
        )

        with self.assertRaisesRegex(StakeholderValidationError, "unknown source_id"):
            evaluate_stakeholders("KV cache technique", self.evidence, FakeStructuredOutputClient(response))

    def test_model_created_evidence_is_rejected(self) -> None:
        response = StakeholderStructuredResponse(
            competitors=[
                {
                    "claim": "A newly invented competitor claim.",
                    "source_id": "ref-competitor",
                    "stance": "negative",
                }
            ],
            adopters_devs=[],
            investors=[],
            summary="Invalid output.",
        )

        with self.assertRaisesRegex(StakeholderValidationError, "not present in the input"):
            evaluate_stakeholders("KV cache technique", self.evidence, FakeStructuredOutputClient(response))

    def test_hallucinated_llm_summary_is_discarded(self) -> None:
        response = StakeholderStructuredResponse(
            competitors=[self.evidence[0]],
            adopters_devs=[],
            investors=[],
            summary="An unsupported stakeholder fact.",
        )

        result = evaluate_stakeholders("KV cache technique", self.evidence, FakeStructuredOutputClient(response))

        self.assertNotIn("unsupported stakeholder fact", result["summary"])
        self.assertEqual(result["summary"], f"competitors: {self.evidence[0]['claim']}")

    def test_summary_uses_only_selected_evidence_claims(self) -> None:
        response = StakeholderStructuredResponse(
            competitors=[self.evidence[0]],
            adopters_devs=[self.evidence[1]],
            investors=[],
            summary="Ignored LLM summary.",
        )

        result = evaluate_stakeholders("KV cache technique", self.evidence, FakeStructuredOutputClient(response))

        self.assertEqual(
            result["summary"],
            f"competitors: {self.evidence[0]['claim']}; adopters_devs: {self.evidence[1]['claim']}",
        )
        self.assertNotIn(self.evidence[2]["claim"], result["summary"])

    def test_empty_evidence_has_a_no_evidence_summary(self) -> None:
        response = StakeholderStructuredResponse(
            competitors=[], adopters_devs=[], investors=[], summary="Ignored LLM summary."
        )

        result = evaluate_stakeholders("KV cache technique", [], FakeStructuredOutputClient(response))

        self.assertEqual(result["summary"], "제공된 Evidence에서 이해관계자 관련 근거를 확인할 수 없음.")


class StakeholderNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = sample_state_after_research()
        self.client = SelectingStructuredClient()

    def _tracking_search(self, calls: list[tuple[str, str]], **overrides):
        def search(query: str, stance: str, **kwargs):
            calls.append((query, stance))
            return fake_search(query, stance, **kwargs, **overrides)

        return search

    def test_node_returns_two_technology_results_and_new_references(self) -> None:
        calls: list[tuple[str, str]] = []
        update = stakeholder_node(self.state, search_fn=self._tracking_search(calls), client=self.client)

        self.assertEqual(set(update), {"stakeholder_result", "references"})
        self.assertEqual(set(update["stakeholder_result"]), {"TurboQuant", "InfiniGen"})
        for result in update["stakeholder_result"].values():
            self.assertEqual(set(result), {"competitors", "adopters_devs", "investors", "summary"})
        self.assertTrue(update["references"])
        self.assertEqual(len(calls), 2 * 2 * config.QUERIES_PER_STANCE)
        self.assertEqual({stance for _, stance in calls}, {"positive", "negative"})

    def test_retry_hint_changes_queries_and_targets_negative_evidence(self) -> None:
        first_calls: list[tuple[str, str]] = []
        stakeholder_node(self.state, search_fn=self._tracking_search(first_calls), client=self.client)
        retry_state = copy.deepcopy(self.state)
        retry_state["sufficiency"] = {
            "trl": True, "market": True, "stakeholder": False, "domain": True,
            "reasons": {"stakeholder": "InfiniGen: 반론 근거 미확인(negative 0건)"},
        }
        retry_calls: list[tuple[str, str]] = []
        stakeholder_node(retry_state, search_fn=self._tracking_search(retry_calls), client=self.client)

        self.assertNotEqual(first_calls, retry_calls)
        self.assertTrue(all("retry focus:" in query for query, _ in retry_calls))
        self.assertTrue(any("negative" in query and "limitations" in query for query, _ in retry_calls))

    def test_no_search_results_returns_e1001_for_both_technologies(self) -> None:
        update = stakeholder_node(self.state, search_fn=lambda *args, **kwargs: [], client=self.client)

        self.assertEqual(set(update["stakeholder_result"]), {"TurboQuant", "InfiniGen"})
        for result in update["stakeholder_result"].values():
            self.assertEqual(result["competitors"], [])
            self.assertTrue(result["summary"].startswith("[E-1001]"))
        self.assertEqual(update["references"], [])

    def test_search_failure_isolated_to_one_technology(self) -> None:
        def partially_failing_search(query: str, stance: str, **kwargs):
            if query.startswith("TurboQuant"):
                raise RuntimeError("search unavailable")
            return fake_search(query, stance, **kwargs)

        update = stakeholder_node(self.state, search_fn=partially_failing_search, client=self.client)

        self.assertTrue(update["stakeholder_result"]["TurboQuant"]["summary"].startswith("[E-1002]"))
        self.assertFalse(update["stakeholder_result"]["InfiniGen"]["summary"].startswith("[E-1002]"))

    def test_structured_client_failure_returns_e1002_without_crashing(self) -> None:
        update = stakeholder_node(self.state, search_fn=fake_search, client=FailingStructuredClient())

        for result in update["stakeholder_result"].values():
            self.assertTrue(result["summary"].startswith("[E-1002]"))
        self.assertEqual(update["references"], [])

    def test_existing_state_references_are_not_copied(self) -> None:
        existing_ids = {reference["source_id"] for reference in self.state["references"]}
        update = stakeholder_node(self.state, search_fn=fake_search, client=self.client)

        self.assertFalse(existing_ids & {reference["source_id"] for reference in update["references"]})


if __name__ == "__main__":
    unittest.main()
