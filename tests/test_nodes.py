import copy
import unittest

from agents.domain import DomainStructuredResponse
from agents.nodes import (
    domain_node,
    route_after_sufficiency,
    stakeholder_node,
    sufficiency_node,
)
from agents.stakeholder import StakeholderStructuredResponse
from state import Evidence, State, TechName


class FakeEvidenceProvider:
    def __init__(self, evidence: list[Evidence]) -> None:
        self.evidence = evidence

    def get_evidence(self, state: State, technology: TechName) -> list[Evidence]:
        return self.evidence


class FakeStructuredOutputClient:
    def __init__(self, response: object) -> None:
        self.response = response

    def invoke(self, prompt: str, response_model: type[object]) -> object:
        return self.response


def item(source_id: str, stance: str = "neutral") -> Evidence:
    return {"claim": f"Claim from {source_id}", "source_id": source_id, "stance": stance}  # type: ignore[typeddict-item]


def stakeholder_response(evidence: list[Evidence]) -> StakeholderStructuredResponse:
    return StakeholderStructuredResponse(
        competitors=[evidence[0]], adopters_devs=[evidence[1]], investors=[evidence[2]], summary="ignored"
    )


def domain_response(evidence: list[Evidence]) -> DomainStructuredResponse:
    return DomainStructuredResponse(
        cost=[evidence[0]], throughput=[evidence[1]], model_quality=[evidence[2]],
        transfer_overhead=[evidence[3]], deployment_barrier=[evidence[4]], summary="ignored"
    )


def full_state(evidence: list[Evidence]) -> State:
    trl = {"level": 2, "rationale": "Evidence-backed", "evidence": [evidence[0]], "uncertainty": "low"}
    market = {
        "market_size_growth": [evidence[0]], "adoption": [evidence[1]],
        "ecosystem": [evidence[2]], "summary": "Evidence-backed"
    }
    stakeholder = {
        "competitors": [evidence[0]], "adopters_devs": [evidence[1]],
        "investors": [evidence[2]], "summary": "Evidence-backed"
    }
    domain = {
        "domain": "datacenter/cloud serving", "cost": [evidence[0]], "throughput": [evidence[1]],
        "model_quality": [evidence[2]], "transfer_overhead": [evidence[3]],
        "deployment_barrier": [evidence[4]], "summary": "Evidence-backed"
    }
    return {
        "tech_sw": "TurboQuant", "tech_hw": "InfiniGen", "domain": "datacenter/cloud serving",
        "tech_summary": {}, "trl_result": {"TurboQuant": trl, "InfiniGen": copy.deepcopy(trl)},
        "market_result": {"TurboQuant": market, "InfiniGen": copy.deepcopy(market)},
        "stakeholder_result": {"TurboQuant": stakeholder, "InfiniGen": copy.deepcopy(stakeholder)},
        "domain_result": {"TurboQuant": domain, "InfiniGen": copy.deepcopy(domain)},
        "sufficiency": {"trl": True, "market": True, "stakeholder": True, "domain": True, "reasons": {}},
        "retry_count": 0, "synthesis": {"agreements": [], "conflicts": [], "neutrality_note": "", "limitations": []},
        "references": [], "report_path": "",
    }


class NodeAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = [item(f"ref-{index}") for index in range(5)]
        self.provider = FakeEvidenceProvider(self.evidence)

    def test_stakeholder_node_preserves_another_technology_result(self) -> None:
        state = full_state(self.evidence)
        update = stakeholder_node(
            state, technology="InfiniGen", evidence_provider=self.provider,
            structured_output_client=FakeStructuredOutputClient(stakeholder_response(self.evidence)),
        )

        self.assertIn("TurboQuant", update["stakeholder_result"])
        self.assertIn("InfiniGen", update["stakeholder_result"])

    def test_domain_node_preserves_another_technology_result(self) -> None:
        state = full_state(self.evidence)
        update = domain_node(
            state, technology="InfiniGen", evidence_provider=self.provider,
            structured_output_client=FakeStructuredOutputClient(domain_response(self.evidence)),
        )

        self.assertIn("TurboQuant", update["domain_result"])
        self.assertIn("InfiniGen", update["domain_result"])

    def test_sufficiency_node_combines_two_technology_results(self) -> None:
        update = sufficiency_node(full_state(self.evidence))

        self.assertEqual(update["sufficiency"], {"trl": True, "market": True, "stakeholder": True, "domain": True, "reasons": {}})
        self.assertEqual(set(update), {"sufficiency"})

    def test_low_trl_with_evidence_is_sufficient(self) -> None:
        update = sufficiency_node(full_state(self.evidence))

        self.assertTrue(update["sufficiency"]["trl"])

    def test_negative_market_evidence_is_sufficient(self) -> None:
        state = full_state(self.evidence)
        state["market_result"]["TurboQuant"]["adoption"][0]["stance"] = "negative"

        update = sufficiency_node(state)

        self.assertTrue(update["sufficiency"]["market"])

    def test_route_all_sufficient_to_synthesis(self) -> None:
        check = sufficiency_node(full_state(self.evidence))["sufficiency"]
        self.assertEqual(route_after_sufficiency(check, 0, 2), "synthesis")

    def test_route_missing_trl_to_market(self) -> None:
        check = {"trl": False, "market": True, "stakeholder": True, "domain": True, "reasons": {"trl": "missing"}}
        self.assertEqual(route_after_sufficiency(check, 0, 2), "market")

    def test_route_missing_market_to_market(self) -> None:
        check = {"trl": True, "market": False, "stakeholder": True, "domain": True, "reasons": {"market": "missing"}}
        self.assertEqual(route_after_sufficiency(check, 0, 2), "market")

    def test_route_missing_stakeholder_to_stakeholder(self) -> None:
        check = {"trl": True, "market": True, "stakeholder": False, "domain": True, "reasons": {"stakeholder": "missing"}}
        self.assertEqual(route_after_sufficiency(check, 0, 2), "stakeholder")

    def test_route_missing_domain_to_domain(self) -> None:
        check = {"trl": True, "market": True, "stakeholder": True, "domain": False, "reasons": {"domain": "missing"}}
        self.assertEqual(route_after_sufficiency(check, 0, 2), "domain")

    def test_route_at_iteration_limit_to_synthesis(self) -> None:
        check = {"trl": False, "market": True, "stakeholder": True, "domain": True, "reasons": {"trl": "missing"}}
        self.assertEqual(route_after_sufficiency(check, 2, 2), "synthesis")

    def test_two_technology_updates_do_not_overwrite_each_other(self) -> None:
        state = full_state(self.evidence)
        first = stakeholder_node(
            state, technology="InfiniGen", evidence_provider=self.provider,
            structured_output_client=FakeStructuredOutputClient(stakeholder_response(self.evidence)),
        )
        state["stakeholder_result"] = first["stakeholder_result"]
        second = stakeholder_node(
            state, technology="TurboQuant", evidence_provider=self.provider,
            structured_output_client=FakeStructuredOutputClient(stakeholder_response(self.evidence)),
        )

        self.assertEqual(set(second["stakeholder_result"]), {"TurboQuant", "InfiniGen"})

    def test_missing_infinigen_investors_makes_combined_stakeholder_false(self) -> None:
        state = full_state(self.evidence)
        state["stakeholder_result"]["InfiniGen"] = copy.deepcopy(state["stakeholder_result"]["TurboQuant"])
        state["stakeholder_result"]["InfiniGen"]["investors"] = []

        update = sufficiency_node(state)

        self.assertFalse(update["sufficiency"]["stakeholder"])
        self.assertIn("InfiniGen", update["sufficiency"]["reasons"]["stakeholder"])
        self.assertIn("investors", update["sufficiency"]["reasons"]["stakeholder"])

    def test_missing_turboquant_transfer_overhead_makes_combined_domain_false(self) -> None:
        state = full_state(self.evidence)
        state["domain_result"]["TurboQuant"]["transfer_overhead"] = []

        update = sufficiency_node(state)

        self.assertFalse(update["sufficiency"]["domain"])
        self.assertIn("TurboQuant", update["sufficiency"]["reasons"]["domain"])
        self.assertIn("transfer_overhead", update["sufficiency"]["reasons"]["domain"])

    def test_missing_one_technology_trl_evidence_makes_combined_trl_false(self) -> None:
        state = full_state(self.evidence)
        state["trl_result"]["InfiniGen"] = copy.deepcopy(state["trl_result"]["InfiniGen"])
        state["trl_result"]["InfiniGen"]["evidence"] = []

        update = sufficiency_node(state)

        self.assertFalse(update["sufficiency"]["trl"])
        self.assertIn("InfiniGen", update["sufficiency"]["reasons"]["trl"])

    def test_missing_one_technology_market_evidence_makes_combined_market_false(self) -> None:
        state = full_state(self.evidence)
        state["market_result"]["InfiniGen"] = copy.deepcopy(state["market_result"]["InfiniGen"])
        state["market_result"]["InfiniGen"]["market_size_growth"] = []
        state["market_result"]["InfiniGen"]["adoption"] = []
        state["market_result"]["InfiniGen"]["ecosystem"] = []

        update = sufficiency_node(state)

        self.assertFalse(update["sufficiency"]["market"])
        self.assertIn("InfiniGen", update["sufficiency"]["reasons"]["market"])

    def test_different_technology_gaps_remain_in_their_perspective_reasons(self) -> None:
        state = full_state(self.evidence)
        state["stakeholder_result"]["TurboQuant"]["investors"] = []
        state["domain_result"]["InfiniGen"] = copy.deepcopy(state["domain_result"]["TurboQuant"])
        state["domain_result"]["InfiniGen"]["deployment_barrier"] = []

        update = sufficiency_node(state)

        self.assertFalse(update["sufficiency"]["stakeholder"])
        self.assertFalse(update["sufficiency"]["domain"])
        self.assertIn("TurboQuant", update["sufficiency"]["reasons"]["stakeholder"])
        self.assertIn("InfiniGen", update["sufficiency"]["reasons"]["domain"])


if __name__ == "__main__":
    unittest.main()
