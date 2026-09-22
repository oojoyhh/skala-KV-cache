"""Deterministic, config-based sufficiency checks for evaluation evidence."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

import config
from state import Evidence, PERSPECTIVES, State, SufficiencyCheck, TECHS, perspective_evidence


class SufficiencyValidationError(ValueError):
    """Raised when a SufficiencyCheck does not match the State contract."""


def validate_sufficiency_check(check: Mapping[str, object]) -> SufficiencyCheck:
    """Validate the fixed output shape and its bool/reason consistency."""

    required_fields = {*PERSPECTIVES, "reasons"}
    if set(check) != required_fields:
        raise SufficiencyValidationError(
            f"SufficiencyCheck fields must be exactly {sorted(required_fields)}."
        )

    reasons = check["reasons"]
    if not isinstance(reasons, dict) or not all(
        isinstance(key, str) and key in PERSPECTIVES and isinstance(value, str) and value
        for key, value in reasons.items()
    ):
        raise SufficiencyValidationError("reasons must be a dict of non-empty perspective reasons.")

    for perspective in PERSPECTIVES:
        value = check[perspective]
        if type(value) is not bool:
            raise SufficiencyValidationError(f"{perspective} must be a bool.")
        if value and perspective in reasons:
            raise SufficiencyValidationError(f"{perspective}=True must not have a reason.")
        if not value and perspective not in reasons:
            raise SufficiencyValidationError(f"{perspective}=False requires a reason.")

    return {
        "trl": check["trl"],
        "market": check["market"],
        "stakeholder": check["stakeholder"],
        "domain": check["domain"],
        "reasons": reasons,
    }


def _evidence_status(evidence: Sequence[Evidence], perspective: str) -> tuple[bool, str]:
    """Apply the shared rubric to one technology in one perspective."""

    if perspective == "trl":
        count = len(evidence)
        return count >= config.MIN_TRL_EVIDENCE, f"TRL 근거 {count}개"

    stance_counts = Counter(item["stance"] for item in evidence)
    source_counts = Counter(item["source_id"] for item in evidence)
    total = len(evidence)
    top_share = max(source_counts.values(), default=0) / max(total, 1)

    problems: list[str] = []
    if total < config.MIN_EVIDENCE:
        problems.append(f"Evidence {total}개")
    if stance_counts["positive"] < config.MIN_POSITIVE:
        problems.append("지지(positive) 근거 미확인")
    if stance_counts["negative"] < config.MIN_NEGATIVE:
        problems.append("반론 근거 미확인(negative 0건)")
    if top_share > config.SAME_SOURCE_CAP:
        problems.append(f"한 출처 비율 {top_share:.0%}")
    return not problems, ", ".join(problems)


def evaluate_sufficiency(state: State) -> SufficiencyCheck:
    """Evaluate all perspectives without routing, retries, or State mutation."""

    values: dict[str, bool] = {}
    reasons: dict[str, str] = {}
    for perspective in PERSPECTIVES:
        insufficient: list[str] = []
        for tech in TECHS:
            ok, reason = _evidence_status(perspective_evidence(state, perspective, tech), perspective)
            if not ok:
                insufficient.append(f"{tech}: {reason}")
        values[perspective] = not insufficient
        if insufficient:
            # Keep the fixture's separator as the public query-hint format.
            reasons[perspective] = " / ".join(insufficient)

    return validate_sufficiency_check({**values, "reasons": reasons})


def needs_retry(check: SufficiencyCheck) -> bool:
    """Return whether any perspective is insufficient; do not mutate State."""

    return not all(check[perspective] for perspective in PERSPECTIVES)


def check_node(state: State) -> dict:
    """Return the sufficiency result and increment retry_count only when needed."""

    sufficiency = evaluate_sufficiency(state)
    retry_count = state.get("retry_count", 0) + (1 if needs_retry(sufficiency) else 0)
    return {"sufficiency": sufficiency, "retry_count": retry_count}
