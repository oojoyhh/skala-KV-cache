import unittest

from agents.stakeholder import (
    Evidence,
    StakeholderStructuredResponse,
    StakeholderValidationError,
    evaluate_stakeholders,
)


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


if __name__ == "__main__":
    unittest.main()
