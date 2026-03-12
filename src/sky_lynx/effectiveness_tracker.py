"""Effectiveness tracker for Sky-Lynx recommendations.

Measures whether applied recommendations actually improved metrics.
Compares before/after usage insights around each recommendation's
application date to score it as effective, neutral, or harmful.

This closes the self-improvement loop: apply -> measure -> learn.
"""

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from .insights_parser import WeeklyMetrics, parse_facets_in_range

logger = logging.getLogger(__name__)

# Import st-factory contracts via path
_snow_town_path = str(Path.home() / "projects" / "st-factory")
if _snow_town_path not in sys.path:
    sys.path.insert(0, _snow_town_path)

from contracts.store import ContractStore  # noqa: E402, I001


# Minimum weeks of data needed after application to evaluate
MIN_WEEKS_AFTER = 1
# How many weeks before/after to compare
COMPARISON_WINDOW_DAYS = 14


class EffectivenessResult(BaseModel):
    """Result of evaluating a single recommendation's effectiveness."""

    recommendation_id: str
    title: str
    effectiveness: str  # effective, neutral, harmful
    effectiveness_score: float = Field(ge=-1.0, le=1.0)
    reasoning: str
    before_friction: float = 0.0
    after_friction: float = 0.0
    before_satisfaction_rate: float = 0.0
    after_satisfaction_rate: float = 0.0
    before_outcome_rate: float = 0.0
    after_outcome_rate: float = 0.0
    sessions_before: int = 0
    sessions_after: int = 0


def _compute_friction_rate(metrics: WeeklyMetrics) -> float:
    """Compute friction events per session."""
    if metrics.total_sessions == 0:
        return 0.0
    total_friction = sum(metrics.friction_counts.values())
    return total_friction / metrics.total_sessions


def _compute_satisfaction_rate(metrics: WeeklyMetrics) -> float:
    """Compute positive satisfaction rate (high + very_high / total)."""
    total = sum(metrics.satisfaction.values())
    if total == 0:
        return 0.0
    positive = metrics.satisfaction.get("high", 0) + metrics.satisfaction.get("very_high", 0)
    return positive / total


def _compute_outcome_rate(metrics: WeeklyMetrics) -> float:
    """Compute successful outcome rate (mostly_achieved / total)."""
    total = sum(metrics.outcomes.values())
    if total == 0:
        return 0.0
    achieved = metrics.outcomes.get("mostly_achieved", 0)
    return achieved / total


def _score_change(before: float, after: float, higher_is_better: bool) -> float:
    """Score a metric change as -1.0 to 1.0.

    Args:
        before: Metric value before the change
        after: Metric value after the change
        higher_is_better: Whether higher values are desirable

    Returns:
        Score from -1.0 (harmful) to 1.0 (effective)
    """
    if before == 0 and after == 0:
        return 0.0

    # Compute relative change
    if before == 0:
        delta = 1.0 if after > 0 else 0.0
    else:
        delta = (after - before) / max(before, 0.01)

    # Clamp to [-1, 1]
    delta = max(-1.0, min(1.0, delta))

    if not higher_is_better:
        delta = -delta  # Invert for metrics where lower is better (friction)

    return delta


def evaluate_recommendation(
    rec: dict[str, str],
    store: ContractStore,
) -> EffectivenessResult | None:
    """Evaluate a single recommendation's effectiveness.

    Compares usage metrics from the 2 weeks before vs 2 weeks after
    the recommendation was applied.

    Args:
        rec: Recommendation dict from store
        store: ContractStore instance

    Returns:
        EffectivenessResult or None if insufficient data
    """
    # Determine when it was applied
    emitted_at_str = rec.get("emitted_at", "")
    if not emitted_at_str:
        return None

    try:
        applied_date = datetime.fromisoformat(emitted_at_str)
    except ValueError:
        logger.warning(f"Cannot parse emitted_at for {rec['recommendation_id']}: {emitted_at_str}")
        return None

    # Check if enough time has passed
    now = datetime.now()
    days_since = (now - applied_date).days
    if days_since < MIN_WEEKS_AFTER * 7:
        logger.info(
            f"Skipping {rec['recommendation_id']}: only {days_since} days since application "
            f"(need {MIN_WEEKS_AFTER * 7})"
        )
        return None

    # Load before/after metrics from usage insights
    before_start = applied_date - timedelta(days=COMPARISON_WINDOW_DAYS)
    before_end = applied_date
    after_start = applied_date
    after_end = min(applied_date + timedelta(days=COMPARISON_WINDOW_DAYS), now)

    before_metrics = parse_facets_in_range(before_start, before_end)
    after_metrics = parse_facets_in_range(after_start, after_end)

    if before_metrics is None or after_metrics is None:
        logger.info(f"Skipping {rec['recommendation_id']}: no usage data for comparison window")
        return None

    if before_metrics.total_sessions < 3 or after_metrics.total_sessions < 3:
        logger.info(
            f"Skipping {rec['recommendation_id']}: insufficient sessions "
            f"(before={before_metrics.total_sessions}, after={after_metrics.total_sessions})"
        )
        return None

    # Compute metrics
    before_friction = _compute_friction_rate(before_metrics)
    after_friction = _compute_friction_rate(after_metrics)
    before_satisfaction = _compute_satisfaction_rate(before_metrics)
    after_satisfaction = _compute_satisfaction_rate(after_metrics)
    before_outcome = _compute_outcome_rate(before_metrics)
    after_outcome = _compute_outcome_rate(after_metrics)

    # Score each dimension
    friction_score = _score_change(before_friction, after_friction, higher_is_better=False)
    satisfaction_score = _score_change(
        before_satisfaction, after_satisfaction, higher_is_better=True
    )
    outcome_score = _score_change(before_outcome, after_outcome, higher_is_better=True)

    # Weighted average (friction is most important for CLAUDE.md changes)
    overall_score = (friction_score * 0.5 + satisfaction_score * 0.3 + outcome_score * 0.2)
    overall_score = round(max(-1.0, min(1.0, overall_score)), 3)

    # Classify
    if overall_score >= 0.1:
        effectiveness = "effective"
    elif overall_score <= -0.1:
        effectiveness = "harmful"
    else:
        effectiveness = "neutral"

    # Build reasoning
    parts = []
    if abs(friction_score) > 0.05:
        direction = "decreased" if friction_score > 0 else "increased"
        parts.append(
            f"friction {direction} ({before_friction:.2f} -> {after_friction:.2f}/session)"
        )
    if abs(satisfaction_score) > 0.05:
        direction = "improved" if satisfaction_score > 0 else "declined"
        parts.append(
            f"satisfaction {direction} ({before_satisfaction:.0%} -> {after_satisfaction:.0%})"
        )
    if abs(outcome_score) > 0.05:
        direction = "improved" if outcome_score > 0 else "declined"
        parts.append(f"outcomes {direction} ({before_outcome:.0%} -> {after_outcome:.0%})")
    if not parts:
        parts.append("no significant metric changes observed")

    reasoning = "; ".join(parts)

    return EffectivenessResult(
        recommendation_id=rec["recommendation_id"],
        title=rec["title"],
        effectiveness=effectiveness,
        effectiveness_score=overall_score,
        reasoning=reasoning,
        before_friction=before_friction,
        after_friction=after_friction,
        before_satisfaction_rate=before_satisfaction,
        after_satisfaction_rate=after_satisfaction,
        before_outcome_rate=before_outcome,
        after_outcome_rate=after_outcome,
        sessions_before=before_metrics.total_sessions,
        sessions_after=after_metrics.total_sessions,
    )


def run_effectiveness_evaluation() -> list[EffectivenessResult]:
    """Evaluate all applied recommendations that haven't been scored yet.

    Returns:
        List of EffectivenessResult for evaluated recommendations
    """
    store = ContractStore()
    try:
        pending = store.get_applied_recommendations_for_evaluation()
        if not pending:
            logger.info("No applied recommendations pending evaluation")
            return []

        logger.info(f"Evaluating effectiveness of {len(pending)} applied recommendations")
        results = []

        for rec in pending:
            result = evaluate_recommendation(rec, store)
            if result is None:
                continue

            # Write back to ST Factory
            store.update_recommendation_effectiveness(
                recommendation_id=result.recommendation_id,
                effectiveness=result.effectiveness,
                effectiveness_score=result.effectiveness_score,
                evaluated_at=datetime.now().isoformat(),
            )
            logger.info(
                f"  {result.recommendation_id}: {result.effectiveness} "
                f"(score={result.effectiveness_score:.3f}) — {result.reasoning}"
            )
            results.append(result)

        return results
    finally:
        store.close()


def build_effectiveness_digest() -> str | None:
    """Build a digest of past effectiveness results for inclusion in Sky-Lynx analysis.

    This tells Claude which past recommendations worked and which didn't,
    so it can adjust future recommendation quality.

    Returns:
        Formatted digest string, or None if no data
    """
    store = ContractStore()
    try:
        summary = store.get_effectiveness_summary()
        if not summary:
            return None

        # Get recent evaluated recommendations for detail
        conn = store._get_conn()
        rows = conn.execute(
            """SELECT recommendation_id, title, recommendation_type,
                      effectiveness, effectiveness_score, effectiveness_evaluated_at
            FROM improvement_recommendations
            WHERE effectiveness IS NOT NULL
            ORDER BY effectiveness_evaluated_at DESC
            LIMIT 20"""
        ).fetchall()

        if not rows:
            return None

        lines = ["## Past Recommendation Effectiveness", ""]

        # Summary stats
        total_evaluated = sum(v["count"] for v in summary.values())
        lines.append(f"**Total evaluated**: {total_evaluated}")
        for eff_type in ["effective", "neutral", "harmful"]:
            if eff_type in summary:
                count = summary[eff_type]["count"]
                avg = summary[eff_type]["avg_score"]
                pct = count / total_evaluated * 100
                lines.append(f"  - {eff_type}: {count} ({pct:.0f}%, avg score: {avg:.3f})")

        # Recent details
        lines.append("")
        lines.append("**Recent evaluations**:")
        for row in rows[:10]:
            score = row["effectiveness_score"]
            indicator = "+" if score > 0 else ""
            lines.append(
                f"  - [{row['effectiveness']}] {row['title']} "
                f"({indicator}{score:.3f}, type: {row['recommendation_type']})"
            )

        # Guidance
        lines.append("")
        harmful_count = summary.get("harmful", {}).get("count", 0)
        if harmful_count > 0:
            lines.append(
                f"**Note**: {harmful_count} recommendation(s) were scored as harmful. "
                "Avoid similar recommendation types/patterns in future analysis."
            )

        return "\n".join(lines)
    finally:
        store.close()
