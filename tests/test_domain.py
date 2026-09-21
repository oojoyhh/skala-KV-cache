import unittest

from agents.domain import (
    DomainStructuredResponse,
    DomainValidationError,
    Evidence,
    evaluate_domain,
)


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


if __name__ == "__main__":
    unittest.main()
