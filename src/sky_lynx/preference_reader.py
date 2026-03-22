"""ClaudeClaw preference profile reader for Sky-Lynx.

Reads ClaudeClaw's preference_profile and preference_history tables
to produce digests showing what the preference learning system has
discovered, how confidence is trending, and recent changes.

Data source: ~/projects/claudeclaw/store/claudeclaw.db
Override with CLAUDECLAW_DB_PATH environment variable.
"""

import logging
import os
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / "projects" / "claudeclaw" / "store" / "claudeclaw.db"


def load_preference_data(db_path: Path | None = None) -> dict:
    """Load preference profile and recent history from ClaudeClaw's DB.

    Opens the database read-only and queries preference_profile for
    current state plus preference_history for recent changes.

    Args:
        db_path: Path to claudeclaw.db. Defaults to standard location
                 or CLAUDECLAW_DB_PATH env var.

    Returns:
        Dict with preference counts, categories, confidence stats,
        manual/LLM ratio, and recent changes. Empty dict if unavailable.
    """
    if db_path is None:
        db_path = Path(os.environ.get("CLAUDECLAW_DB_PATH", str(DEFAULT_DB_PATH)))

    if not db_path.exists():
        logger.info(f"ClaudeClaw DB not found at {db_path}")
        return {}

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as e:
        logger.warning(f"Could not open ClaudeClaw DB: {e}")
        return {}

    try:
        # Check if preference_profile table exists
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='preference_profile'"
        ).fetchone()
        if not table_check:
            logger.info("preference_profile table does not exist yet")
            return {}

        data: dict = {}

        # All preferences with current state
        rows = conn.execute(
            "SELECT category, dimension, value, confidence, evidence_count, source "
            "FROM preference_profile ORDER BY category, confidence DESC"
        ).fetchall()

        data["total"] = len(rows)
        if data["total"] == 0:
            return data

        # Category breakdown
        categories: dict[str, int] = {}
        manual_count = 0
        llm_count = 0
        confidence_sum = 0.0

        prefs_list = []
        for row in rows:
            cat = row["category"] or "uncategorized"
            categories[cat] = categories.get(cat, 0) + 1
            confidence_sum += row["confidence"] or 0
            if row["source"] == "manual":
                manual_count += 1
            else:
                llm_count += 1
            prefs_list.append({
                "category": cat,
                "dimension": row["dimension"],
                "value": row["value"],
                "confidence": row["confidence"],
                "evidence_count": row["evidence_count"],
                "source": row["source"],
            })

        data["by_category"] = categories
        data["manual_count"] = manual_count
        data["llm_count"] = llm_count
        data["manual_ratio"] = manual_count / data["total"] if data["total"] > 0 else 0
        data["avg_confidence"] = confidence_sum / data["total"] if data["total"] > 0 else 0
        data["preferences"] = prefs_list

        # High confidence preferences (>= 0.8)
        high_conf = [p for p in prefs_list if (p["confidence"] or 0) >= 0.8]
        data["high_confidence_count"] = len(high_conf)

        # Low confidence preferences (< 0.5)
        low_conf = [p for p in prefs_list if (p["confidence"] or 0) < 0.5]
        data["low_confidence_count"] = len(low_conf)

        # Recent changes from preference_history (last 7 days)
        history_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='preference_history'"
        ).fetchone()

        if history_check:
            seven_days_ago = int(time.time()) - (7 * 86400)
            history_rows = conn.execute(
                "SELECT ph.old_value, ph.new_value, ph.old_confidence, ph.new_confidence, "
                "ph.reason, ph.changed_at, pp.dimension, pp.category "
                "FROM preference_history ph "
                "LEFT JOIN preference_profile pp ON ph.preference_id = pp.id "
                "WHERE ph.changed_at >= ? "
                "ORDER BY ph.changed_at DESC",
                (seven_days_ago,),
            ).fetchall()

            data["recent_changes"] = [
                {
                    "dimension": row["dimension"],
                    "category": row["category"],
                    "old_value": row["old_value"],
                    "new_value": row["new_value"],
                    "old_confidence": row["old_confidence"],
                    "new_confidence": row["new_confidence"],
                    "reason": row["reason"],
                }
                for row in history_rows
            ]
            data["changes_last_7d"] = len(data["recent_changes"])

            # Confidence trajectory: average confidence change direction
            if data["recent_changes"]:
                deltas = [
                    (c["new_confidence"] or 0) - (c["old_confidence"] or 0)
                    for c in data["recent_changes"]
                    if c["new_confidence"] is not None and c["old_confidence"] is not None
                ]
                data["confidence_trend"] = sum(deltas) / len(deltas) if deltas else 0
            else:
                data["confidence_trend"] = 0
        else:
            data["recent_changes"] = []
            data["changes_last_7d"] = 0
            data["confidence_trend"] = 0

        return data

    except sqlite3.Error as e:
        logger.warning(f"Error reading ClaudeClaw preference data: {e}")
        return {}
    finally:
        conn.close()


def build_preference_digest(data: dict) -> str:
    """Format preference data into a markdown digest for the analysis prompt.

    Args:
        data: Dict from load_preference_data()

    Returns:
        Formatted markdown digest string.
        Returns fallback message if data is empty/None.
    """
    if not data:
        return "ClaudeClaw preference data not available."

    total = data.get("total", 0)
    if total == 0:
        return "ClaudeClaw preference profile is empty (no preferences learned yet)."

    lines = [
        f"**Total Preferences**: {total}",
        f"**Average Confidence**: {data.get('avg_confidence', 0):.2f}",
        f"**High Confidence (>=0.8)**: {data.get('high_confidence_count', 0)}",
        f"**Low Confidence (<0.5)**: {data.get('low_confidence_count', 0)}",
        f"**Source Mix**: {data.get('manual_count', 0)} manual, "
        f"{data.get('llm_count', 0)} LLM-discovered "
        f"({data.get('manual_ratio', 0):.0%} manual)",
        "",
    ]

    # Category breakdown
    categories = data.get("by_category", {})
    if categories:
        lines.append("**Categories**:")
        for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
            lines.append(f"  - {cat}: {count}")
        lines.append("")

    # Recent changes
    changes = data.get("changes_last_7d", 0)
    trend = data.get("confidence_trend", 0)
    if changes > 0:
        trend_dir = "rising" if trend > 0.005 else "falling" if trend < -0.005 else "stable"
        lines.append(
            f"**Changes (last 7d)**: {changes} "
            f"(confidence trend: {trend_dir}, avg delta: {trend:+.3f})"
        )

        recent = data.get("recent_changes", [])[:5]
        if recent:
            lines.append("**Recent Changes**:")
            for c in recent:
                dim = c.get("dimension", "unknown")
                reason = c.get("reason", "")[:60]
                old_conf = c.get("old_confidence", 0) or 0
                new_conf = c.get("new_confidence", 0) or 0
                lines.append(f"  - {dim}: {old_conf:.2f} -> {new_conf:.2f} ({reason})")
    else:
        lines.append("**Changes (last 7d)**: none")

    return "\n".join(lines)
