"""Agent effectiveness tracker for Sky-Lynx.

DISABLED 2026-05-28 (see fix/agent-effectiveness-tracker-successor):
This module used to measure whether applied agent patches improved
agent-specific metrics, reading the ST Records ``agent_patches`` table and
calling ``ContractStore`` methods. That table was DEPRECATED 2026-05-12
(CLEANUP-A Scope 2c; see ``st-records/DEPRECATED_TABLES.md``) and the
``ContractStore`` methods it relied on
(``get_applied_agent_patches_for_evaluation``,
``update_agent_patch_effectiveness``, ``get_agent_effectiveness_summary``,
``get_recent_agent_patches_with_scores``) were removed in the same cleanup.

The declared successor — the R-B ``agent-promote`` script + ClaudeClaw
skill-registry extension — is deferred to Pass 6 of the pivot and does NOT
exist yet, so there is no live source of "applied agent patch + outcome"
data to read. Rather than invent a data source, the public entry points
(:func:`run_agent_effectiveness_evaluation` and
:func:`build_agent_effectiveness_digest`) are short-circuited to a safe,
no-op result. Both callers (``analyzer.py``, ``claude_client.py``) already
guard on a falsy/empty digest, so this degrades gracefully.

The per-agent metric readers and scoring helpers below are retained intact
(they reference live DBs: metroplex.db build_jobs, claudeclaw.db
mission_tasks, starscream_analytics.db engagement_snapshots) so that if/when
a successor patch-tracking source lands, re-wiring the two entry points to a
real list of applied patches is all that's required. They are currently
unreachable from the public API.

Scores -1.0 to 1.0, matching the existing effectiveness_tracker scale.
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Reason surfaced when the disabled entry points are invoked.
_DISABLED_MSG = (
    "agent effectiveness tracking disabled: agent_patches deprecated "
    "2026-05-12, no successor source"
)

# Minimum days after application before evaluating
MIN_DAYS_AFTER = 7
# Comparison window (days before/after patch application)
COMPARISON_WINDOW_DAYS = 14

# DB paths
METROPLEX_DB = Path.home() / "projects" / "metroplex" / "data" / "metroplex.db"
CLAUDECLAW_DB = Path.home() / "projects" / "claudeclaw" / "store" / "claudeclaw.db"
STARSCREAM_DB = Path.home() / "projects" / "claudeclaw" / "store" / "starscream_analytics.db"


class AgentEffectivenessResult(BaseModel):
    """Result of evaluating an agent patch's effectiveness."""

    patch_id: str
    agent_id: str
    effectiveness: str  # effective, neutral, harmful
    effectiveness_score: float = Field(ge=-1.0, le=1.0)
    reasoning: str
    metrics_before: dict = Field(default_factory=dict)
    metrics_after: dict = Field(default_factory=dict)


def _safe_connect(db_path: Path) -> sqlite3.Connection | None:
    """Connect to a SQLite DB if it exists."""
    if not db_path.exists():
        logger.warning(f"DB not found: {db_path}")
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _score_change(before: float, after: float, higher_is_better: bool) -> float:
    """Score a metric change as -1.0 to 1.0."""
    if before == 0 and after == 0:
        return 0.0
    delta = (1.0 if after > 0 else 0.0) if before == 0 else (after - before) / max(before, 0.01)
    delta = max(-1.0, min(1.0, delta))
    if not higher_is_better:
        delta = -delta
    return delta


def _get_kup_metrics(
    before_start: datetime, before_end: datetime,
    after_start: datetime, after_end: datetime,
) -> tuple[dict, dict] | None:
    """Get Kup metrics from metroplex.db: build success rate."""
    conn = _safe_connect(METROPLEX_DB)
    if conn is None:
        return None

    try:
        def _query_period(start: datetime, end: datetime) -> dict:
            start_str = start.isoformat()
            end_str = end.isoformat()
            rows = conn.execute(
                """SELECT status, COUNT(*) as cnt
                FROM build_jobs
                WHERE queued_at BETWEEN ? AND ?
                GROUP BY status""",
                (start_str, end_str),
            ).fetchall()
            counts = {row["status"]: row["cnt"] for row in rows}
            total = sum(counts.values())
            completed = counts.get("completed", 0)
            failed = counts.get("failed", 0)
            success_rate = completed / total if total > 0 else 0.0
            return {
                "total_builds": total,
                "completed": completed,
                "failed": failed,
                "success_rate": success_rate,
            }

        before = _query_period(before_start, before_end)
        after = _query_period(after_start, after_end)

        if before["total_builds"] < 2 or after["total_builds"] < 2:
            logger.info("Kup: insufficient builds for comparison")
            return None

        return before, after
    finally:
        conn.close()


def _get_starscream_metrics(
    before_start: datetime, before_end: datetime,
    after_start: datetime, after_end: datetime,
) -> tuple[dict, dict] | None:
    """Get Starscream metrics from starscream_analytics.db: engagement rate."""
    conn = _safe_connect(STARSCREAM_DB)
    if conn is None:
        return None

    try:
        def _query_period(start: datetime, end: datetime) -> dict:
            start_str = start.strftime("%Y-%m-%d")
            end_str = end.strftime("%Y-%m-%d")
            rows = conn.execute(
                """SELECT AVG(engagement_rate) as avg_engagement,
                          COUNT(DISTINCT post_id) as post_count,
                          AVG(impressions) as avg_impressions
                FROM engagement_snapshots
                WHERE collected_at BETWEEN ? AND ?""",
                (start_str, end_str),
            ).fetchall()
            row = rows[0] if rows else None
            return {
                "avg_engagement": float(row["avg_engagement"] or 0.0) if row else 0.0,
                "post_count": int(row["post_count"] or 0) if row else 0,
                "avg_impressions": float(row["avg_impressions"] or 0.0) if row else 0.0,
            }

        before = _query_period(before_start, before_end)
        after = _query_period(after_start, after_end)

        if before["post_count"] < 1 or after["post_count"] < 1:
            logger.info("Starscream: insufficient posts for comparison")
            return None

        return before, after
    finally:
        conn.close()


def _get_generic_agent_metrics(
    agent_id: str,
    before_start: datetime, before_end: datetime,
    after_start: datetime, after_end: datetime,
) -> tuple[dict, dict] | None:
    """Get generic agent metrics from claudeclaw.db: mission task completion rate."""
    conn = _safe_connect(CLAUDECLAW_DB)
    if conn is None:
        return None

    try:
        def _query_period(start: datetime, end: datetime) -> dict:
            start_ts = int(start.timestamp())
            end_ts = int(end.timestamp())
            rows = conn.execute(
                """SELECT status, COUNT(*) as cnt
                FROM mission_tasks
                WHERE assigned_agent = ?
                  AND created_at BETWEEN ? AND ?
                GROUP BY status""",
                (agent_id, start_ts, end_ts),
            ).fetchall()
            counts = {row["status"]: row["cnt"] for row in rows}
            total = sum(counts.values())
            completed = counts.get("completed", 0) + counts.get("done", 0)
            failed = counts.get("failed", 0) + counts.get("error", 0)
            completion_rate = completed / total if total > 0 else 0.0
            return {
                "total_tasks": total,
                "completed": completed,
                "failed": failed,
                "completion_rate": completion_rate,
            }

        before = _query_period(before_start, before_end)
        after = _query_period(after_start, after_end)

        if before["total_tasks"] < 2 or after["total_tasks"] < 2:
            logger.info(f"{agent_id}: insufficient tasks for comparison")
            return None

        return before, after
    finally:
        conn.close()


def _score_kup(before: dict, after: dict) -> tuple[float, str]:
    """Score Kup patch based on build success rate change."""
    score = _score_change(before["success_rate"], after["success_rate"], higher_is_better=True)
    score = round(score, 3)
    parts = []
    if abs(score) > 0.05:
        direction = "improved" if score > 0 else "declined"
        parts.append(
            f"build success rate {direction} "
            f"({before['success_rate']:.0%} -> {after['success_rate']:.0%})"
        )
    if not parts:
        parts.append("no significant change in build success rate")
    return score, "; ".join(parts)


def _score_starscream(before: dict, after: dict) -> tuple[float, str]:
    """Score Starscream patch based on engagement changes."""
    engagement_score = _score_change(
        before["avg_engagement"], after["avg_engagement"], higher_is_better=True
    )
    impressions_score = _score_change(
        before["avg_impressions"], after["avg_impressions"], higher_is_better=True
    )
    # Engagement weighted more heavily
    score = round(engagement_score * 0.7 + impressions_score * 0.3, 3)
    score = max(-1.0, min(1.0, score))
    parts = []
    if abs(engagement_score) > 0.05:
        direction = "improved" if engagement_score > 0 else "declined"
        parts.append(
            f"engagement {direction} "
            f"({before['avg_engagement']:.3f} -> {after['avg_engagement']:.3f})"
        )
    if abs(impressions_score) > 0.05:
        direction = "increased" if impressions_score > 0 else "decreased"
        parts.append(
            f"impressions {direction} "
            f"({before['avg_impressions']:.0f} -> {after['avg_impressions']:.0f})"
        )
    if not parts:
        parts.append("no significant engagement changes")
    return score, "; ".join(parts)


def _score_generic(before: dict, after: dict) -> tuple[float, str]:
    """Score generic agent patch based on task completion rate change."""
    score = _score_change(
        before["completion_rate"], after["completion_rate"], higher_is_better=True
    )
    score = round(score, 3)
    parts = []
    if abs(score) > 0.05:
        direction = "improved" if score > 0 else "declined"
        parts.append(
            f"task completion rate {direction} "
            f"({before['completion_rate']:.0%} -> {after['completion_rate']:.0%})"
        )
    if not parts:
        parts.append("no significant change in task completion rate")
    return score, "; ".join(parts)


def evaluate_agent_patch(patch: dict) -> AgentEffectivenessResult | None:
    """Evaluate a single agent patch's effectiveness.

    Routes to agent-specific metric sources based on agent_id.
    """
    emitted_at_str = patch.get("emitted_at", "")
    if not emitted_at_str:
        return None

    try:
        applied_date = datetime.fromisoformat(emitted_at_str)
    except ValueError:
        logger.warning(f"Cannot parse emitted_at for {patch['patch_id']}: {emitted_at_str}")
        return None

    now = datetime.now()
    days_since = (now - applied_date).days
    if days_since < MIN_DAYS_AFTER:
        logger.info(
            f"Skipping {patch['patch_id']}: only {days_since} days since application "
            f"(need {MIN_DAYS_AFTER})"
        )
        return None

    before_start = applied_date - timedelta(days=COMPARISON_WINDOW_DAYS)
    before_end = applied_date
    after_start = applied_date
    after_end = min(applied_date + timedelta(days=COMPARISON_WINDOW_DAYS), now)

    agent_id = patch["agent_id"]

    # Route to agent-specific metrics
    if agent_id == "kup":
        result = _get_kup_metrics(before_start, before_end, after_start, after_end)
        if result is None:
            return None
        before, after = result
        score, reasoning = _score_kup(before, after)
    elif agent_id == "starscream":
        result = _get_starscream_metrics(before_start, before_end, after_start, after_end)
        if result is None:
            return None
        before, after = result
        score, reasoning = _score_starscream(before, after)
    else:
        # Generic: ravage, soundwave, scourge, or any future agent
        result = _get_generic_agent_metrics(
            agent_id, before_start, before_end, after_start, after_end
        )
        if result is None:
            return None
        before, after = result
        score, reasoning = _score_generic(before, after)

    # Classify
    if score >= 0.1:
        effectiveness = "effective"
    elif score <= -0.1:
        effectiveness = "harmful"
    else:
        effectiveness = "neutral"

    return AgentEffectivenessResult(
        patch_id=patch["patch_id"],
        agent_id=agent_id,
        effectiveness=effectiveness,
        effectiveness_score=score,
        reasoning=reasoning,
        metrics_before=before,
        metrics_after=after,
    )


def run_agent_effectiveness_evaluation() -> list[AgentEffectivenessResult]:
    """Evaluate all applied agent patches that haven't been scored yet.

    DISABLED: the ``agent_patches`` source table was deprecated 2026-05-12 and
    no successor patch-tracking source exists yet. Returns an empty list
    instead of calling removed ``ContractStore`` methods (which would raise
    ``AttributeError``). The caller (``analyzer.py``) already treats an empty
    result as "nothing to evaluate".
    """
    logger.info(_DISABLED_MSG)
    return []


def build_agent_effectiveness_digest() -> str | None:
    """Build a digest of agent patch effectiveness for inclusion in analysis.

    DISABLED: see :func:`run_agent_effectiveness_evaluation`. Returns ``None``
    so the digest is omitted from the analysis prompt. Both callers
    (``analyzer.py`` and ``claude_client.py``) guard on a truthy digest, so a
    ``None`` return degrades gracefully.
    """
    logger.info(_DISABLED_MSG)
    return None
