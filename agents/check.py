"""Deterministic sufficiency checks for four completed evaluation perspectives."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from state import DomainResult, StakeholderResult, SufficiencyCheck


class SufficiencyValidationError(ValueError):
    """Raised when a SufficiencyCheck does not match its fixed contract."""


def is_trl_sufficient(trl_estimate: object | None, evidence: Sequence[object] | None) -> bool:
    """TRL is sufficient when an estimate and at least one supporting item exist.

    The estimate value is intentionally not scored: a low TRL can still be a
    well-supported assessment.
    """

    return trl_estimate is not None and bool(evidence)


def is_market_sufficient(
    market_result: object | None,
    evidence: Sequence[object] | None,
) -> bool:
    """Market is sufficient when an evaluation result and support both exist."""

    return market_result is not None and bool(evidence)


def _missing_stakeholder_groups(result: StakeholderResult) -> list[str]:
    return [
        group
        for group in ("competitors", "adopters_devs", "investors")
        if not result[group]
    ]


def _missing_domain_axes(result: DomainResult) -> list[str]:
    return [
        axis
        for axis in (
            "cost",
            "throughput",
            "model_quality",
            "transfer_overhead",
            "deployment_barrier",
        )
        if not result[axis]
    ]


def validate_sufficiency_check(check: Mapping[str, object]) -> SufficiencyCheck:
    """Validate the exact fixed output contract and reason/bool consistency."""

    required_fields = {"trl", "market", "stakeholder", "domain", "reasons"}
    if set(check) != required_fields:
        raise SufficiencyValidationError(
            f"SufficiencyCheck fields must be exactly {sorted(required_fields)}."
        )

    reasons = check["reasons"]
    if not isinstance(reasons, dict) or not all(
        isinstance(key, str) and isinstance(value, str) and value
        for key, value in reasons.items()
    ):
        raise SufficiencyValidationError("reasons must be a dict[str, non-empty str].")

    for perspective in ("trl", "market", "stakeholder", "domain"):
        value = check[perspective]
        if type(value) is not bool:
            raise SufficiencyValidationError(f"{perspective} must be a bool.")
        has_reason = perspective in reasons
        if not value and not has_reason:
            raise SufficiencyValidationError(f"{perspective}=False requires a reason.")
        if value and has_reason:
            raise SufficiencyValidationError(f"{perspective}=True must not have a reason.")

    unknown_reasons = set(reasons) - {"trl", "market", "stakeholder", "domain"}
    if unknown_reasons:
        raise SufficiencyValidationError(
            f"reasons has unknown perspectives: {sorted(unknown_reasons)}"
        )

    return {
        "trl": check["trl"],
        "market": check["market"],
        "stakeholder": check["stakeholder"],
        "domain": check["domain"],
        "reasons": reasons,
    }


def evaluate_sufficiency(
    stakeholder_result: StakeholderResult,
    domain_result: DomainResult,
    *,
    trl_estimate: object | None,
    trl_evidence: Sequence[object] | None,
    market_result: object | None,
    market_evidence: Sequence[object] | None,
) -> SufficiencyCheck:
    """Create a deterministic SufficiencyCheck without routing or side effects."""

    trl = is_trl_sufficient(trl_estimate, trl_evidence)
    market = is_market_sufficient(market_result, market_evidence)
    missing_groups = _missing_stakeholder_groups(stakeholder_result)
    missing_axes = _missing_domain_axes(domain_result)
    stakeholder = not missing_groups
    domain = not missing_axes

    reasons: dict[str, str] = {}
    if not trl:
        reasons["trl"] = "TRL 추정값 또는 이를 뒷받침하는 Evidence가 없음"
    if not market:
        reasons["market"] = "시장 평가 결과 또는 이를 뒷받침하는 Evidence가 없음"
    if not stakeholder:
        reasons["stakeholder"] = f"{', '.join(missing_groups)} Evidence가 없음"
    if not domain:
        reasons["domain"] = f"{', '.join(missing_axes)} Evidence가 없음"

    return validate_sufficiency_check(
        {
            "trl": trl,
            "market": market,
            "stakeholder": stakeholder,
            "domain": domain,
            "reasons": reasons,
        }
    )


def needs_retry(check: SufficiencyCheck) -> bool:
    """Return whether any perspective remains insufficient; do not mutate state."""

    return not all(check[perspective] for perspective in ("trl", "market", "stakeholder", "domain"))


def can_retry(retry_count: int, max_iterations: int) -> bool:
    """Return whether the caller may try again; do not increment retry_count."""

    return retry_count < max_iterations
