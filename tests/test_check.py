import unittest

from agents.check import can_retry, evaluate_sufficiency, needs_retry
from agents.domain import DomainResult, Evidence as DomainEvidence
from agents.stakeholder import Evidence as StakeholderEvidence, StakeholderResult


def evidence(source_id: str) -> StakeholderEvidence:
    return {"claim": f"Claim from {source_id}", "source_id": source_id, "stance": "neutral"}


def complete_stakeholder_result() -> StakeholderResult:
    return {
        "competitors": [evidence("stakeholder-competitors")],
        "adopters_devs": [evidence("stakeholder-adopters")],
        "investors": [evidence("stakeholder-investors")],
        "summary": "Evidence exists for each stakeholder group.",
    }


def complete_domain_result() -> DomainResult:
    domain_evidence: DomainEvidence = evidence("domain")
    return {
        "domain": "datacenter/cloud serving",
        "cost": [domain_evidence],
        "throughput": [domain_evidence],
        "model_quality": [domain_evidence],
        "transfer_overhead": [domain_evidence],
        "deployment_barrier": [domain_evidence],
        "summary": "Evidence exists for each domain axis.",
    }


class SufficiencyCheckTests(unittest.TestCase):
    def evaluate(
        self,
        stakeholder_result: StakeholderResult | None = None,
        domain_result: DomainResult | None = None,
        *,
        trl_estimate: object | None = 5,
        trl_evidence: list[object] | None = None,
        market_result: object | None = {"outlook": "neutral"},
        market_evidence: list[object] | None = None,
    ):
        return evaluate_sufficiency(
            stakeholder_result or complete_stakeholder_result(),
            domain_result or complete_domain_result(),
            trl_estimate=trl_estimate,
            trl_evidence=trl_evidence if trl_evidence is not None else [evidence("trl")],
            market_result=market_result,
            market_evidence=market_evidence if market_evidence is not None else [evidence("market")],
        )

    def test_all_four_perspectives_are_sufficient(self) -> None:
        check = self.evaluate()

        self.assertEqual(check, {"trl": True, "market": True, "stakeholder": True, "domain": True, "reasons": {}})

    def test_missing_investor_evidence_makes_stakeholder_insufficient(self) -> None:
        stakeholder = complete_stakeholder_result()
        stakeholder["investors"] = []

        check = self.evaluate(stakeholder_result=stakeholder)

        self.assertFalse(check["stakeholder"])
        self.assertIn("investors", check["reasons"]["stakeholder"])

    def test_missing_transfer_overhead_makes_domain_insufficient(self) -> None:
        domain = complete_domain_result()
        domain["transfer_overhead"] = []

        check = self.evaluate(domain_result=domain)

        self.assertFalse(check["domain"])
        self.assertIn("transfer_overhead", check["reasons"]["domain"])

    def test_stakeholder_and_domain_can_both_be_insufficient(self) -> None:
        stakeholder = complete_stakeholder_result()
        stakeholder["investors"] = []
        domain = complete_domain_result()
        domain["deployment_barrier"] = []

        check = self.evaluate(stakeholder, domain)

        self.assertFalse(check["stakeholder"])
        self.assertFalse(check["domain"])
        self.assertEqual(set(check["reasons"]), {"stakeholder", "domain"})

    def test_low_trl_with_estimate_and_evidence_is_sufficient(self) -> None:
        check = self.evaluate(trl_estimate=2, trl_evidence=[evidence("trl-low")])

        self.assertTrue(check["trl"])
        self.assertNotIn("trl", check["reasons"])

    def test_negative_market_with_evidence_is_sufficient(self) -> None:
        check = self.evaluate(
            market_result={"outlook": "negative"},
            market_evidence=[evidence("market-negative")],
        )

        self.assertTrue(check["market"])
        self.assertNotIn("market", check["reasons"])

    def test_needs_retry_depends_on_any_false_perspective(self) -> None:
        self.assertFalse(needs_retry(self.evaluate()))
        stakeholder = complete_stakeholder_result()
        stakeholder["competitors"] = []
        self.assertTrue(needs_retry(self.evaluate(stakeholder_result=stakeholder)))

    def test_can_retry_obeys_the_iteration_limit(self) -> None:
        self.assertTrue(can_retry(retry_count=1, max_iterations=2))
        self.assertFalse(can_retry(retry_count=2, max_iterations=2))
        self.assertFalse(can_retry(retry_count=3, max_iterations=2))


if __name__ == "__main__":
    unittest.main()
