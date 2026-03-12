"""Parse Claude Code usage insights from facets JSON files.

Reads ~/.claude/usage-data/facets/*.json and aggregates session data
for weekly analysis.
"""

import json
import os
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SessionInsight(BaseModel):
    """Parsed insight from a single Claude Code session."""

    session_id: str
    underlying_goal: str = ""
    goal_categories: dict[str, int] = Field(default_factory=dict)
    outcome: str = ""  # mostly_achieved, partially_achieved, not_achieved
    user_satisfaction_counts: dict[str, int] = Field(default_factory=dict)
    claude_helpfulness: str = ""  # essential, helpful, neutral, unhelpful
    session_type: str = ""  # iterative_refinement, single_task, exploration, etc.
    friction_counts: dict[str, int] = Field(default_factory=dict)
    friction_detail: str = ""
    primary_success: str = ""
    brief_summary: str = ""


class WeeklyMetrics(BaseModel):
    """Aggregated metrics for a week of sessions."""

    period_start: datetime
    period_end: datetime
    total_sessions: int = 0

    # Outcomes
    outcomes: Counter[str] = Field(default_factory=Counter)

    # Satisfaction
    satisfaction: Counter[str] = Field(default_factory=Counter)

    # Helpfulness
    helpfulness: Counter[str] = Field(default_factory=Counter)

    # Session types
    session_types: Counter[str] = Field(default_factory=Counter)

    # Goal categories (aggregated)
    goal_categories: Counter[str] = Field(default_factory=Counter)

    # Friction (the key insight source)
    friction_counts: Counter[str] = Field(default_factory=Counter)
    friction_details: list[str] = Field(default_factory=list)

    # Primary successes
    primary_successes: Counter[str] = Field(default_factory=Counter)

    # Raw sessions for detailed analysis
    sessions: list[SessionInsight] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class TrendAnalysis(BaseModel):
    """Comparison between two periods."""

    current: WeeklyMetrics
    previous: WeeklyMetrics | None = None

    # Computed trends
    session_count_change: float = 0.0  # Percentage change
    friction_change: float = 0.0  # Percentage change in total friction events
    satisfaction_trend: str = ""  # improving, stable, declining

    model_config = ConfigDict(arbitrary_types_allowed=True)


def parse_session_file(file_path: Path) -> SessionInsight | None:
    """Parse a single facets JSON file into a SessionInsight.

    Args:
        file_path: Path to the JSON file

    Returns:
        SessionInsight if parsing succeeds, None otherwise
    """
    try:
        with open(file_path) as f:
            data = json.load(f)

        return SessionInsight(
            session_id=data.get("session_id", file_path.stem),
            underlying_goal=data.get("underlying_goal", ""),
            goal_categories=data.get("goal_categories", {}),
            outcome=data.get("outcome", ""),
            user_satisfaction_counts=data.get("user_satisfaction_counts", {}),
            claude_helpfulness=data.get("claude_helpfulness", ""),
            session_type=data.get("session_type", ""),
            friction_counts=data.get("friction_counts", {}),
            friction_detail=data.get("friction_detail", ""),
            primary_success=data.get("primary_success", ""),
            brief_summary=data.get("brief_summary", ""),
        )
    except (json.JSONDecodeError, OSError) as e:
        # Log but don't fail on individual file errors
        print(f"Warning: Could not parse {file_path}: {e}")
        return None


def get_facets_dir() -> Path:
    """Get the path to the Claude Code facets directory.

    Returns:
        Path to ~/.claude/usage-data/facets/
    """
    return Path.home() / ".claude" / "usage-data" / "facets"


def get_file_modification_date(file_path: Path) -> datetime:
    """Get the modification date of a file.

    Args:
        file_path: Path to the file

    Returns:
        Datetime of last modification
    """
    stat = file_path.stat()
    return datetime.fromtimestamp(stat.st_mtime)


def load_sessions_for_period(
    start_date: datetime, end_date: datetime, facets_dir: Path | None = None
) -> list[SessionInsight]:
    """Load all session insights within a date range.

    Args:
        start_date: Start of period (inclusive)
        end_date: End of period (inclusive)
        facets_dir: Optional override for facets directory

    Returns:
        List of SessionInsight objects
    """
    facets_dir = facets_dir or get_facets_dir()

    if not facets_dir.exists():
        return []

    sessions = []
    for json_file in facets_dir.glob("*.json"):
        mod_date = get_file_modification_date(json_file)
        if start_date <= mod_date <= end_date:
            session = parse_session_file(json_file)
            if session:
                sessions.append(session)

    return sessions


def aggregate_weekly_metrics(
    sessions: list[SessionInsight], period_start: datetime, period_end: datetime
) -> WeeklyMetrics:
    """Aggregate session insights into weekly metrics.

    Args:
        sessions: List of session insights
        period_start: Start of the week
        period_end: End of the week

    Returns:
        WeeklyMetrics with aggregated data
    """
    metrics = WeeklyMetrics(period_start=period_start, period_end=period_end)
    metrics.total_sessions = len(sessions)
    metrics.sessions = sessions

    for session in sessions:
        # Outcomes
        if session.outcome:
            metrics.outcomes[session.outcome] += 1

        # Satisfaction
        for satisfaction, count in session.user_satisfaction_counts.items():
            metrics.satisfaction[satisfaction] += count

        # Helpfulness
        if session.claude_helpfulness:
            metrics.helpfulness[session.claude_helpfulness] += 1

        # Session types
        if session.session_type:
            metrics.session_types[session.session_type] += 1

        # Goal categories
        for category, count in session.goal_categories.items():
            metrics.goal_categories[category] += count

        # Friction - the key data source for improvements
        for friction_type, count in session.friction_counts.items():
            metrics.friction_counts[friction_type] += count
        if session.friction_detail:
            metrics.friction_details.append(session.friction_detail)

        # Primary successes
        if session.primary_success:
            metrics.primary_successes[session.primary_success] += 1

    return metrics


def calculate_percentage_change(current: int, previous: int) -> float:
    """Calculate percentage change between two values.

    Args:
        current: Current period value
        previous: Previous period value

    Returns:
        Percentage change (positive = increase, negative = decrease)
    """
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return ((current - previous) / previous) * 100


def calculate_satisfaction_trend(
    current: Counter[str], previous: Counter[str] | None
) -> str:
    """Determine if satisfaction is improving, stable, or declining.

    Args:
        current: Current satisfaction counts
        previous: Previous satisfaction counts (or None)

    Returns:
        "improving", "stable", or "declining"
    """
    if previous is None:
        return "baseline"

    # Weight satisfaction categories
    weights = {"likely_satisfied": 1, "neutral": 0, "likely_unsatisfied": -1}

    def weighted_score(counts: Counter[str]) -> float:
        total = sum(counts.values())
        if total == 0:
            return 0.0
        score = sum(weights.get(k, 0) * v for k, v in counts.items())
        return score / total

    current_score = weighted_score(current)
    previous_score = weighted_score(previous)

    diff = current_score - previous_score
    if diff > 0.1:
        return "improving"
    elif diff < -0.1:
        return "declining"
    return "stable"


def analyze_trends(
    current_metrics: WeeklyMetrics, previous_metrics: WeeklyMetrics | None
) -> TrendAnalysis:
    """Compare current week to previous week.

    Args:
        current_metrics: This week's aggregated metrics
        previous_metrics: Last week's metrics (or None for first run)

    Returns:
        TrendAnalysis with computed trends
    """
    analysis = TrendAnalysis(current=current_metrics, previous=previous_metrics)

    if previous_metrics:
        # Session count change
        analysis.session_count_change = calculate_percentage_change(
            current_metrics.total_sessions, previous_metrics.total_sessions
        )

        # Friction change (total friction events)
        current_friction = sum(current_metrics.friction_counts.values())
        previous_friction = sum(previous_metrics.friction_counts.values())
        analysis.friction_change = calculate_percentage_change(
            current_friction, previous_friction
        )

        # Satisfaction trend
        analysis.satisfaction_trend = calculate_satisfaction_trend(
            current_metrics.satisfaction, previous_metrics.satisfaction
        )
    else:
        analysis.satisfaction_trend = "baseline"

    return analysis


def get_current_week_bounds() -> tuple[datetime, datetime]:
    """Get the start and end of the current week (Sunday-Saturday).

    Returns:
        Tuple of (start_date, end_date)
    """
    today = datetime.now()
    # Find the most recent Sunday
    days_since_sunday = (today.weekday() + 1) % 7
    week_start = today - timedelta(days=days_since_sunday)
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return week_start, week_end


def get_previous_week_bounds() -> tuple[datetime, datetime]:
    """Get the start and end of the previous week.

    Returns:
        Tuple of (start_date, end_date)
    """
    current_start, _ = get_current_week_bounds()
    prev_end = current_start - timedelta(seconds=1)
    prev_start = prev_end - timedelta(days=6, hours=23, minutes=59, seconds=59)
    return prev_start, prev_end


def load_weekly_analysis(facets_dir: Path | None = None) -> TrendAnalysis:
    """Load and analyze the current week's data with trend comparison.

    Args:
        facets_dir: Optional override for facets directory

    Returns:
        TrendAnalysis with current week metrics and trends
    """
    current_start, current_end = get_current_week_bounds()
    prev_start, prev_end = get_previous_week_bounds()

    # Load current week
    current_sessions = load_sessions_for_period(current_start, current_end, facets_dir)
    current_metrics = aggregate_weekly_metrics(
        current_sessions, current_start, current_end
    )

    # Load previous week
    prev_sessions = load_sessions_for_period(prev_start, prev_end, facets_dir)
    prev_metrics = (
        aggregate_weekly_metrics(prev_sessions, prev_start, prev_end)
        if prev_sessions
        else None
    )

    return analyze_trends(current_metrics, prev_metrics)


def parse_facets_in_range(
    start_date: datetime, end_date: datetime, facets_dir: Path | None = None
) -> WeeklyMetrics | None:
    """Load and aggregate usage metrics for an arbitrary date range.

    Used by the effectiveness tracker to compare before/after metrics.

    Args:
        start_date: Start of range (inclusive)
        end_date: End of range (inclusive)
        facets_dir: Optional override for facets directory

    Returns:
        WeeklyMetrics for the range, or None if no data
    """
    sessions = load_sessions_for_period(start_date, end_date, facets_dir)
    if not sessions:
        return None
    return aggregate_weekly_metrics(sessions, start_date, end_date)
