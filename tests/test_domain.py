import copy
import re
import unittest

import config
from agents.domain import (
    DomainStructuredResponse,
    DomainValidationError,
    Evidence,
    domain_node,
    evaluate_domain,
)
from tests.fixtures import fake_search, sample_state_after_research


class FakeStructuredOutputClient:
    def __init__(self, result: DomainStructuredResponse) -> None:
        self.result = result
        self.prompt = ""

    def invoke(
        self,
        prompt: str,
        response_model: type[DomainStructuredResponse],
    ) -> DomainStructuredResponse:
        self.prompt = prompt
        self.response_model = response_model
        return self.result


class SelectingStructuredClient:
    """Selects prompt Evidence without calling an external LLM."""

    def invoke(self, prompt: str, response_model: type[DomainStructuredResponse]) -> DomainStructuredResponse:
        evidence = []
        for claim, source_id, stance in re.findall(
            r"- claim: (.*?) \| source_id: (.*?) \| stance: (positive|negative|neutral)", prompt
        ):
            evidence.append({"claim": claim, "source_id": source_id, "stance": stance})
        return response_model(
            cost=evidence[:1], throughput=evidence[1:2], model_quality=evidence[2:3],
            transfer_overhead=evidence[3:4], deployment_barrier=evidence[4:], summary="Ignored by Python.",
        )


class CostOnlyStructuredClient:
    def invoke(self, prompt: str, response_model: type[DomainStructuredResponse]) -> DomainStructuredResponse:
        match = re.search(r"- claim: (.*?) \| source_id: (.*?) \| stance: (positive|negative|neutral)", prompt)
        evidence = [] if match is None else [{"claim": match.group(1), "source_id": match.group(2), "stance": match.group(3)}]
        return response_model(
            cost=evidence, throughput=[], model_quality=[], transfer_overhead=[], deployment_barrier=[], summary="Ignored by Python."
        )


class FailingStructuredClient:
    def invoke(self, prompt: str, response_model: type[DomainStructuredResponse]) -> DomainStructuredResponse:
        raise RuntimeError("LLM unavailable")


class DomainEvaluationTests(unittest.TestCase):
    domain = "datacenter/cloud serving"

    def setUp(self) -> None:
        self.evidence: list[Evidence] = [
            {"claim": "The method reduces memory cost.", "source_id": "ref-cost", "stance": "positive"},
            {"claim": "The method improves serving throughput.", "source_id": "ref-throughput", "stance": "positive"},
            {"claim": "Quality is retained after compression.", "source_id": "ref-quality", "stance": "neutral"},
            {"claim": "GPU-host transfers add bandwidth overhead.", "source_id": "ref-transfer", "stance": "negative"},
            {"claim": "Deployment requires infrastructure changes.", "source_id": "ref-deploy", "stance": "negative"},
        ]

    def test_evidence_is_classified_into_all_five_axes(self) -> None:
        response = DomainStructuredResponse(
            cost=[self.evidence[0]],
            throughput=[self.evidence[1]],
            model_quality=[self.evidence[2]],
            transfer_overhead=[self.evidence[3]],
            deployment_barrier=[self.evidence[4]],
            summary="The supplied evidence covers all five domain axes.",
        )
        client = FakeStructuredOutputClient(response)

        result = evaluate_domain(self.domain, self.evidence, client)

        self.assertEqual(result["domain"], self.domain)
        self.assertEqual(result["cost"], [self.evidence[0]])
        self.assertEqual(result["deployment_barrier"], [self.evidence[4]])
        self.assertIn("source_id: ref-transfer", client.prompt)

    def test_empty_axis_is_a_valid_result(self) -> None:
        response = DomainStructuredResponse(
            cost=[self.evidence[0]],
            throughput=[self.evidence[1]],
            model_quality=[],
            transfer_overhead=[],
            deployment_barrier=[],
            summary="Only cost and throughput evidence was supplied.",
        )

        result = evaluate_domain(self.domain, self.evidence[:2], FakeStructuredOutputClient(response))

        self.assertEqual(result["transfer_overhead"], [])

    def test_no_evidence_returns_empty_axes(self) -> None:
        response = DomainStructuredResponse(
            cost=[],
            throughput=[],
            model_quality=[],
            transfer_overhead=[],
            deployment_barrier=[],
            summary="No domain claims can be made from supplied evidence.",
        )

        result = evaluate_domain(self.domain, [], FakeStructuredOutputClient(response))

        for axis in ("cost", "throughput", "model_quality", "transfer_overhead", "deployment_barrier"):
            self.assertEqual(result[axis], [])

    def test_unknown_source_id_is_rejected(self) -> None:
        response = DomainStructuredResponse(
            cost=[{"claim": "Unknown source claim.", "source_id": "unknown", "stance": "positive"}],
            throughput=[], model_quality=[], transfer_overhead=[], deployment_barrier=[], summary="Invalid.",
        )

        with self.assertRaisesRegex(DomainValidationError, "unknown source_id"):
            evaluate_domain(self.domain, self.evidence, FakeStructuredOutputClient(response))

    def test_model_created_evidence_is_rejected(self) -> None:
        response = DomainStructuredResponse(
            cost=[{"claim": "Invented cost claim.", "source_id": "ref-cost", "stance": "positive"}],
            throughput=[], model_quality=[], transfer_overhead=[], deployment_barrier=[], summary="Invalid.",
        )

        with self.assertRaisesRegex(DomainValidationError, "not present in the input"):
            evaluate_domain(self.domain, self.evidence, FakeStructuredOutputClient(response))

    def test_same_evidence_can_be_reused_across_axes(self) -> None:
        response = DomainStructuredResponse(
            cost=[self.evidence[0]],
            throughput=[self.evidence[0]],
            model_quality=[], transfer_overhead=[], deployment_barrier=[],
            summary="One supplied claim is relevant to both selected axes.",
        )

        result = evaluate_domain(self.domain, [self.evidence[0]], FakeStructuredOutputClient(response))

        self.assertEqual(result["cost"], [self.evidence[0]])
        self.assertEqual(result["throughput"], [self.evidence[0]])

    def test_hallucinated_llm_summary_is_discarded(self) -> None:
        response = DomainStructuredResponse(
            cost=[self.evidence[0]],
            throughput=[], model_quality=[], transfer_overhead=[], deployment_barrier=[],
            summary="An unsupported domain fact.",
        )

        result = evaluate_domain(self.domain, self.evidence, FakeStructuredOutputClient(response))

        self.assertNotIn("unsupported domain fact", result["summary"])
        self.assertEqual(result["summary"], f"cost: {self.evidence[0]['claim']}")

    def test_summary_uses_only_selected_axis_claims(self) -> None:
        response = DomainStructuredResponse(
            cost=[self.evidence[0]],
            throughput=[self.evidence[1]],
            model_quality=[], transfer_overhead=[], deployment_barrier=[],
            summary="Ignored LLM summary.",
        )

        result = evaluate_domain(self.domain, self.evidence, FakeStructuredOutputClient(response))

        self.assertEqual(
            result["summary"],
            f"cost: {self.evidence[0]['claim']}; throughput: {self.evidence[1]['claim']}",
        )
        self.assertNotIn(self.evidence[2]["claim"], result["summary"])

    def test_empty_axes_have_a_no_evidence_summary(self) -> None:
        response = DomainStructuredResponse(
            cost=[], throughput=[], model_quality=[], transfer_overhead=[], deployment_barrier=[],
            summary="Ignored LLM summary.",
        )

        result = evaluate_domain(self.domain, [], FakeStructuredOutputClient(response))

        self.assertEqual(result["summary"], "제공된 Evidence에서 도메인 평가 근거를 확인할 수 없음.")


class DomainNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = sample_state_after_research()
        self.client = SelectingStructuredClient()

    def _tracking_search(self, calls: list[tuple[str, str]]):
        def search(query: str, stance: str, **kwargs):
            calls.append((query, stance))
            return fake_search(query, stance, **kwargs)

        return search

    def test_node_returns_two_domain_results_and_new_references(self) -> None:
        calls: list[tuple[str, str]] = []
        update = domain_node(self.state, search_fn=self._tracking_search(calls), client=self.client)

        self.assertEqual(set(update), {"domain_result", "references"})
        self.assertEqual(set(update["domain_result"]), {"TurboQuant", "InfiniGen"})
        for result in update["domain_result"].values():
            self.assertEqual(
                set(result),
                {"domain", "cost", "throughput", "model_quality", "transfer_overhead", "deployment_barrier", "summary"},
            )
            self.assertEqual(result["domain"], self.state["domain"])
        self.assertTrue(update["references"])
        self.assertEqual(len(calls), 2 * 2 * config.QUERIES_PER_STANCE)
        self.assertEqual({stance for _, stance in calls}, {"positive", "negative"})
        self.assertTrue(all(self.state["domain"] in query for query, _ in calls))

    def test_retry_hint_changes_queries_and_targets_negative_evidence(self) -> None:
        first_calls: list[tuple[str, str]] = []
        domain_node(self.state, search_fn=self._tracking_search(first_calls), client=self.client)
        retry_state = copy.deepcopy(self.state)
        retry_state["sufficiency"] = {
            "trl": True, "market": True, "stakeholder": True, "domain": False,
            "reasons": {"domain": "InfiniGen: 반론 근거 미확인(negative 0건)"},
        }
        retry_calls: list[tuple[str, str]] = []
        domain_node(retry_state, search_fn=self._tracking_search(retry_calls), client=self.client)

        self.assertNotEqual(first_calls, retry_calls)
        self.assertTrue(all("retry focus:" in query for query, _ in retry_calls))
        self.assertTrue(any("negative" in query and "limitations" in query for query, _ in retry_calls))

    def test_no_search_results_returns_e1001_with_empty_axes(self) -> None:
        update = domain_node(self.state, search_fn=lambda *args, **kwargs: [], client=self.client)

        self.assertEqual(set(update["domain_result"]), {"TurboQuant", "InfiniGen"})
        for result in update["domain_result"].values():
            self.assertTrue(result["summary"].startswith("[E-1001]"))
            for axis in ("cost", "throughput", "model_quality", "transfer_overhead", "deployment_barrier"):
                self.assertEqual(result[axis], [])

    def test_search_failure_isolated_to_one_technology(self) -> None:
        def partially_failing_search(query: str, stance: str, **kwargs):
            if query.startswith("TurboQuant"):
                raise RuntimeError("search unavailable")
            return fake_search(query, stance, **kwargs)

        update = domain_node(self.state, search_fn=partially_failing_search, client=self.client)

        self.assertTrue(update["domain_result"]["TurboQuant"]["summary"].startswith("[E-1002]"))
        self.assertFalse(update["domain_result"]["InfiniGen"]["summary"].startswith("[E-1002]"))

    def test_structured_client_failure_returns_e1002_without_crashing(self) -> None:
        update = domain_node(self.state, search_fn=fake_search, client=FailingStructuredClient())

        for result in update["domain_result"].values():
            self.assertTrue(result["summary"].startswith("[E-1002]"))
        self.assertEqual(update["references"], [])

    def test_empty_axes_are_not_forced_to_be_filled(self) -> None:
        update = domain_node(self.state, search_fn=fake_search, client=CostOnlyStructuredClient())

        for result in update["domain_result"].values():
            self.assertTrue(result["cost"])
            self.assertEqual(result["transfer_overhead"], [])
            self.assertEqual(result["deployment_barrier"], [])

    def test_existing_state_references_are_not_copied(self) -> None:
        existing_ids = {reference["source_id"] for reference in self.state["references"]}
        update = domain_node(self.state, search_fn=fake_search, client=self.client)

        self.assertFalse(existing_ids & {reference["source_id"] for reference in update["references"]})


if __name__ == "__main__":
    unittest.main()
