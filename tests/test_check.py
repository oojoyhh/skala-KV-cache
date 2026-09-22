"""Unit tests for the deterministic, State-based sufficiency node."""

from __future__ import annotations

import copy
import unittest

import config
from agents.check import check_node, evaluate_sufficiency, needs_retry
from state import Evidence, State, TECHS
from tests.fixtures import expected_sufficiency, sample_state_after_eval


def general_evidence(
    count: int | None = None,
    *,
    stances: list[str] | None = None,
    source_ids: list[str] | None = None,
) -> list[Evidence]:
    """Build test-only Evidence with no dependency on an external service."""

    total = count if count is not None else config.MIN_EVIDENCE
    if stances is None:
        stances = (
            ["positive"] * config.MIN_POSITIVE
            + ["negative"] * config.MIN_NEGATIVE
            + ["neutral"] * max(0, total - config.MIN_POSITIVE - config.MIN_NEGATIVE)
        )
    return [
        {
            "claim": f"test claim {index}",
            "source_id": source_ids[index] if source_ids else f"web:test-{index}",
            "stance": stance,
        }
        for index, stance in enumerate(stances[:total])
    ]


def set_perspective_evidence(
    state: State,
    perspective: str,
    tech: str,
    evidence: list[Evidence],
) -> None:
    """Replace all Evidence for one selected perspective and technology."""

    if perspective == "trl":
        state["trl_result"][tech]["evidence"] = evidence
    elif perspective == "market":
        result = state["market_result"][tech]
        result["market_size_growth"], result["adoption"], result["ecosystem"] = evidence, [], []
    elif perspective == "stakeholder":
        result = state["stakeholder_result"][tech]
        result["competitors"], result["adopters_devs"], result["investors"] = evidence, [], []
    elif perspective == "domain":
        result = state["domain_result"][tech]
        result["cost"], result["throughput"], result["model_quality"] = evidence, [], []
        result["transfer_overhead"], result["deployment_barrier"] = [], []
    else:  # pragma: no cover - guards test helper misuse.
        raise ValueError(f"Unknown perspective: {perspective}")


class CheckNodeTests(unittest.TestCase):
    def state(self, sufficient: bool = True) -> State:
        return copy.deepcopy(sample_state_after_eval(sufficient=sufficient))

    def test_sufficient_fixture_matches_oracle(self) -> None:
        state = self.state(True)
        self.assertEqual(check_node(state)["sufficiency"], expected_sufficiency(state))

    def test_insufficient_fixture_matches_oracle(self) -> None:
        state = self.state(False)
        self.assertEqual(check_node(state)["sufficiency"], expected_sufficiency(state))

    def test_sufficient_state_does_not_increment_retry_count(self) -> None:
        state = self.state(True)
        state["retry_count"] = 4
        self.assertEqual(check_node(state)["retry_count"], 4)

    def test_insufficient_state_increments_retry_count_once(self) -> None:
        state = self.state(False)
        self.assertEqual(check_node(state)["retry_count"], 2)

    def test_retry_count_one_becomes_two_when_still_insufficient(self) -> None:
        state = self.state(False)
        state["retry_count"] = 1
        self.assertEqual(check_node(state)["retry_count"], 2)

    def test_retry_count_two_becomes_three_without_ending_workflow(self) -> None:
        state = self.state(False)
        state["retry_count"] = config.MAX_RETRY
        update = check_node(state)
        self.assertEqual(update["retry_count"], config.MAX_RETRY + 1)
        self.assertTrue(needs_retry(update["sufficiency"]))

    def test_trl_with_too_few_evidence_is_insufficient(self) -> None:
        state = self.state(True)
        set_perspective_evidence(state, "trl", "TurboQuant", general_evidence(config.MIN_TRL_EVIDENCE - 1))
        self.assertFalse(check_node(state)["sufficiency"]["trl"])

    def test_low_trl_level_is_irrelevant_when_evidence_is_sufficient(self) -> None:
        state = self.state(True)
        state["trl_result"]["TurboQuant"]["level"] = 1
        set_perspective_evidence(state, "trl", "TurboQuant", general_evidence(config.MIN_TRL_EVIDENCE))
        self.assertTrue(check_node(state)["sufficiency"]["trl"])

    def test_general_perspective_with_too_few_evidence_is_insufficient(self) -> None:
        state = self.state(True)
        set_perspective_evidence(state, "market", "TurboQuant", general_evidence(config.MIN_EVIDENCE - 1))
        self.assertFalse(check_node(state)["sufficiency"]["market"])

    def test_missing_positive_evidence_is_insufficient(self) -> None:
        state = self.state(True)
        set_perspective_evidence(
            state,
            "market",
            "TurboQuant",
            general_evidence(stances=["negative", "neutral"] * (config.MIN_EVIDENCE // 2)),
        )
        self.assertFalse(check_node(state)["sufficiency"]["market"])

    def test_missing_negative_evidence_is_insufficient(self) -> None:
        state = self.state(True)
        set_perspective_evidence(
            state,
            "market",
            "TurboQuant",
            general_evidence(stances=["positive", "neutral"] * (config.MIN_EVIDENCE // 2)),
        )
        self.assertFalse(check_node(state)["sufficiency"]["market"])

    def test_source_share_above_cap_is_insufficient(self) -> None:
        state = self.state(True)
        total = config.MIN_EVIDENCE
        source_ids = ["web:repeated"] * (total - 1) + ["web:other"]
        set_perspective_evidence(state, "domain", "TurboQuant", general_evidence(total, source_ids=source_ids))
        self.assertFalse(check_node(state)["sufficiency"]["domain"])

    def test_source_share_equal_to_cap_is_allowed(self) -> None:
        state = self.state(True)
        total = config.MIN_EVIDENCE
        source_ids = ["web:left"] * (total // 2) + ["web:right"] * (total // 2)
        set_perspective_evidence(state, "domain", "TurboQuant", general_evidence(total, source_ids=source_ids))
        self.assertTrue(check_node(state)["sufficiency"]["domain"])

    def test_empty_stakeholder_group_does_not_fail_aggregate_rubric(self) -> None:
        state = self.state(True)
        evidence = general_evidence()
        result = state["stakeholder_result"]["TurboQuant"]
        result["competitors"], result["adopters_devs"], result["investors"] = [], evidence[:2], evidence[2:]
        self.assertTrue(check_node(state)["sufficiency"]["stakeholder"])

    def test_empty_domain_axis_does_not_fail_aggregate_rubric(self) -> None:
        state = self.state(True)
        evidence = general_evidence()
        result = state["domain_result"]["TurboQuant"]
        result["cost"], result["throughput"], result["model_quality"] = evidence[:2], evidence[2:], []
        result["transfer_overhead"], result["deployment_barrier"] = [], []
        self.assertTrue(check_node(state)["sufficiency"]["domain"])

    def test_duplicate_domain_evidence_across_axes_is_not_counted_twice(self) -> None:
        state = self.state(True)
        evidence = general_evidence(2)
        result = state["domain_result"]["TurboQuant"]
        result["cost"], result["deployment_barrier"] = evidence, evidence
        result["throughput"], result["model_quality"], result["transfer_overhead"] = [], [], []

        check = check_node(state)["sufficiency"]

        self.assertFalse(check["domain"])
        self.assertIn("Evidence 2개", check["reasons"]["domain"])

    def test_four_unique_domain_evidence_remains_sufficient_when_reused(self) -> None:
        state = self.state(True)
        evidence = general_evidence()
        result = state["domain_result"]["TurboQuant"]
        result["cost"], result["deployment_barrier"] = evidence, evidence
        result["throughput"], result["model_quality"], result["transfer_overhead"] = [], [], []

        self.assertTrue(check_node(state)["sufficiency"]["domain"])

    def test_duplicate_stakeholder_evidence_across_groups_is_not_counted_twice(self) -> None:
        state = self.state(True)
        evidence = general_evidence(2)
        result = state["stakeholder_result"]["TurboQuant"]
        result["competitors"], result["adopters_devs"], result["investors"] = evidence, evidence, []

        self.assertFalse(check_node(state)["sufficiency"]["stakeholder"])

    def test_same_source_with_different_claims_counts_as_distinct_evidence(self) -> None:
        state = self.state(True)
        evidence = general_evidence(source_ids=["web:a", "web:a", "web:b", "web:b"])
        set_perspective_evidence(state, "domain", "TurboQuant", evidence)

        self.assertTrue(check_node(state)["sufficiency"]["domain"])

    def test_duplicate_claim_with_same_source_counts_once_even_when_stance_differs(self) -> None:
        state = self.state(True)
        duplicate = {"claim": "same claim", "source_id": "web:one", "stance": "positive"}
        conflicting_duplicate = {**duplicate, "stance": "negative"}
        result = state["domain_result"]["TurboQuant"]
        result["cost"], result["throughput"] = [duplicate], [conflicting_duplicate]
        result["model_quality"], result["transfer_overhead"], result["deployment_barrier"] = [], [], []

        self.assertIn("Evidence 1개", check_node(state)["sufficiency"]["reasons"]["domain"])

    def test_source_cap_uses_deduplicated_domain_evidence(self) -> None:
        state = self.state(True)
        evidence = general_evidence(source_ids=["web:a", "web:a", "web:b", "web:b"])
        result = state["domain_result"]["TurboQuant"]
        result["cost"], result["deployment_barrier"] = evidence, evidence[:2]
        result["throughput"], result["model_quality"], result["transfer_overhead"] = [], [], []

        self.assertTrue(check_node(state)["sufficiency"]["domain"])

    def test_one_technology_insufficient_makes_perspective_false(self) -> None:
        state = self.state(True)
        set_perspective_evidence(state, "stakeholder", "InfiniGen", general_evidence(config.MIN_EVIDENCE - 1))
        result = check_node(state)["sufficiency"]
        self.assertFalse(result["stakeholder"])
        self.assertIn("InfiniGen", result["reasons"]["stakeholder"])

    def test_two_technologies_insufficient_are_both_named_in_reason(self) -> None:
        state = self.state(True)
        for tech in TECHS:
            set_perspective_evidence(state, "market", tech, general_evidence(config.MIN_EVIDENCE - 1))
        reason = check_node(state)["sufficiency"]["reasons"]["market"]
        self.assertIn("TurboQuant", reason)
        self.assertIn("InfiniGen", reason)

    def test_different_technology_gaps_remain_in_their_perspective_reasons(self) -> None:
        state = self.state(True)
        set_perspective_evidence(
            state,
            "stakeholder",
            "TurboQuant",
            general_evidence(config.MIN_EVIDENCE - 1),
        )
        set_perspective_evidence(
            state,
            "domain",
            "InfiniGen",
            general_evidence(config.MIN_EVIDENCE - 1),
        )

        result = check_node(state)["sufficiency"]

        self.assertFalse(result["stakeholder"])
        self.assertFalse(result["domain"])
        self.assertIn("TurboQuant", result["reasons"]["stakeholder"])
        self.assertIn("InfiniGen", result["reasons"]["domain"])

    def test_missing_result_key_is_insufficient_not_a_crash(self) -> None:
        state = self.state(True)
        del state["domain_result"]
        result = check_node(state)["sufficiency"]
        self.assertFalse(result["domain"])

    def test_reasons_contain_only_false_perspectives(self) -> None:
        result = evaluate_sufficiency(self.state(False))
        self.assertEqual(
            set(result["reasons"]),
            {perspective for perspective in ("trl", "market", "stakeholder", "domain") if not result[perspective]},
        )

    def test_node_returns_exactly_its_own_state_keys(self) -> None:
        self.assertEqual(set(check_node(self.state(True))), {"sufficiency", "retry_count"})


if __name__ == "__main__":
    unittest.main()
