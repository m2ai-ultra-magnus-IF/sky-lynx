"""Outcome reader for Sky-Lynx.

Reads OutcomeRecords from Snow-Town's JSONL store and produces
summary digests for the analysis prompt.
"""

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Import st-records contracts via path
_st_records_path = str(Path.home() / "projects" / "st-records")
if _st_records_path not in sys.path:
    sys.path.insert(0, _st_records_path)

from contracts.outcome_record import OutcomeRecord, TerminalOutcome
from contracts.store import ContractStore


def load_outcome_records(limit: int = 100) -> list[OutcomeRecord]:
    """Load OutcomeRecords from st-records JSONL store.

    Args:
        limit: Maximum records to load

    Returns:
        List of OutcomeRecord objects
    """
    store = ContractStore()
    try:
        records = store.read_outcomes(limit=limit)
        logger.info(f"Loaded {len(records)} outcome records")
        return records
    finally:
        store.close()


def build_outcome_digest(records: list[OutcomeRecord]) -> str:
    """Build a summary digest of outcome records for the analysis prompt.

    Calculates:
    - Total ideas completed
    - Outcome distribution
    - Failure rate
    - Common tech stacks
    - Score distribution

    Args:
        records: List of OutcomeRecord objects

    Returns:
        Formatted digest string
    """
    if not records:
        return "No outcome records available yet."

    total = len(records)
    outcomes = {}
    scores: list[float] = []
    tech_stacks: dict[str, int] = {}
    build_outcomes: dict[str, int] = {}

    for r in records:
        outcomes[r.outcome.value] = outcomes.get(r.outcome.value, 0) + 1
        if r.overall_score is not None:
            scores.append(r.overall_score)
        for tech in r.tech_stack:
            tech_stacks[tech] = tech_stacks.get(tech, 0) + 1
        if r.build_outcome:
            build_outcomes[r.build_outcome] = build_outcomes.get(r.build_outcome, 0) + 1

    lines = [
        f"**Total Ideas Completed**: {total}",
        "",
        "**Outcome Distribution**:",
    ]
    for outcome, count in sorted(outcomes.items(), key=lambda x: -x[1]):
        pct = (count / total * 100) if total > 0 else 0
        lines.append(f"  - {outcome}: {count} ({pct:.0f}%)")

    # Failure rate
    failures = outcomes.get("rejected", 0) + outcomes.get("build_failed", 0)
    failure_rate = (failures / total * 100) if total > 0 else 0
    lines.append(f"\n**Failure Rate**: {failure_rate:.0f}% ({failures}/{total})")

    # Score distribution
    if scores:
        avg_score = sum(scores) / len(scores)
        min_score = min(scores)
        max_score = max(scores)
        lines.append(f"\n**Score Distribution**:")
        lines.append(f"  - Average: {avg_score:.1f}")
        lines.append(f"  - Range: {min_score:.0f} - {max_score:.0f}")

    # Tech stacks
    if tech_stacks:
        lines.append(f"\n**Common Tech Stacks**:")
        for tech, count in sorted(tech_stacks.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  - {tech}: {count}")

    # Build outcomes
    if build_outcomes:
        lines.append(f"\n**Build Outcomes**:")
        for bo, count in sorted(build_outcomes.items(), key=lambda x: -x[1]):
            lines.append(f"  - {bo}: {count}")

    # Quality scores by outcome (Phase 14c)
    # overall_score now contains quality_score for published builds
    scored_by_outcome: dict[str, list[float]] = {}
    for r in records:
        if r.overall_score is not None:
            scored_by_outcome.setdefault(r.outcome.value, []).append(r.overall_score)

    if scored_by_outcome:
        lines.append(f"\n**Quality Scores by Outcome**:")
        for outcome, outcome_scores in sorted(scored_by_outcome.items()):
            avg = sum(outcome_scores) / len(outcome_scores)
            lines.append(f"  - {outcome}: avg={avg:.1f}, n={len(outcome_scores)}")

    return "\n".join(lines)
