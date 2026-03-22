"""ClaudeClaw mission performance reader for Sky-Lynx.

Reads ClaudeClaw's missions and mission_subtasks tables to produce
digests showing multi-agent orchestration performance: completion
rates, per-agent reliability, and failure modes.

Data source: ~/projects/claudeclaw/store/claudeclaw.db
Override with CLAUDECLAW_DB_PATH environment variable.
"""

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / "projects" / "claudeclaw" / "store" / "claudeclaw.db"


def load_mission_data(db_path: Path | None = None) -> dict:
    """Load mission and subtask performance data from ClaudeClaw's DB.

    Args:
        db_path: Path to claudeclaw.db. Defaults to standard location
                 or CLAUDECLAW_DB_PATH env var.

    Returns:
        Dict with mission status counts, agent performance stats,
        duration metrics, and failure modes. Empty dict if unavailable.
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
        # Check if missions table exists
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='missions'"
        ).fetchone()
        if not table_check:
            logger.info("missions table does not exist yet")
            return {}

        data: dict = {}

        # Mission status counts
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM missions GROUP BY status"
        ).fetchall()
        data["status_counts"] = {row["status"]: row["cnt"] for row in rows}
        data["total_missions"] = sum(data["status_counts"].values())

        if data["total_missions"] == 0:
            return data

        completed = data["status_counts"].get("completed", 0)
        failed = data["status_counts"].get("failed", 0)
        data["completion_rate"] = (
            completed / (completed + failed) * 100
            if (completed + failed) > 0
            else 0
        )

        # Average mission duration (completed only)
        dur_row = conn.execute(
            "SELECT AVG(completed_at - created_at) as avg_duration "
            "FROM missions WHERE status = 'completed' "
            "AND completed_at IS NOT NULL AND created_at IS NOT NULL"
        ).fetchone()
        data["avg_duration_s"] = dur_row["avg_duration"] if dur_row["avg_duration"] else 0

        # Check if mission_subtasks table exists
        subtask_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mission_subtasks'"
        ).fetchone()

        if subtask_check:
            # Per-agent performance
            agent_rows = conn.execute(
                "SELECT agent_type, "
                "COUNT(*) as total, "
                "SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as succeeded, "
                "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed, "
                "AVG(CASE WHEN completed_at IS NOT NULL AND started_at IS NOT NULL "
                "    THEN completed_at - started_at END) as avg_latency "
                "FROM mission_subtasks "
                "WHERE agent_type IS NOT NULL "
                "GROUP BY agent_type"
            ).fetchall()
            data["agent_stats"] = [
                {
                    "agent_type": row["agent_type"],
                    "total": row["total"],
                    "succeeded": row["succeeded"],
                    "failed": row["failed"],
                    "success_rate": (
                        row["succeeded"] / row["total"] * 100 if row["total"] > 0 else 0
                    ),
                    "avg_latency_s": row["avg_latency"] if row["avg_latency"] else 0,
                }
                for row in agent_rows
            ]

            # Subtask count distribution (complexity proxy)
            dist_rows = conn.execute(
                "SELECT mission_id, COUNT(*) as subtask_count "
                "FROM mission_subtasks GROUP BY mission_id"
            ).fetchall()
            counts = [row["subtask_count"] for row in dist_rows]
            data["subtask_distribution"] = {
                "min": min(counts) if counts else 0,
                "max": max(counts) if counts else 0,
                "avg": sum(counts) / len(counts) if counts else 0,
                "total_subtasks": sum(counts),
            }

            # Failure modes (recent, up to 10)
            fail_rows = conn.execute(
                "SELECT agent_type, error FROM mission_subtasks "
                "WHERE status = 'failed' AND error IS NOT NULL "
                "ORDER BY COALESCE(completed_at, started_at) DESC LIMIT 10"
            ).fetchall()
            data["failure_modes"] = [
                {
                    "agent_type": row["agent_type"],
                    "error": (row["error"] or "")[:120],
                }
                for row in fail_rows
            ]
        else:
            data["agent_stats"] = []
            data["subtask_distribution"] = {}
            data["failure_modes"] = []

        return data

    except sqlite3.Error as e:
        logger.warning(f"Error reading ClaudeClaw mission data: {e}")
        return {}
    finally:
        conn.close()


def build_mission_digest(data: dict) -> str:
    """Format mission data into a markdown digest for the analysis prompt.

    Args:
        data: Dict from load_mission_data()

    Returns:
        Formatted markdown digest string.
    """
    if not data:
        return "ClaudeClaw mission data not available."

    total = data.get("total_missions", 0)
    if total == 0:
        return "No missions executed yet."

    lines = [
        f"**Total Missions**: {total}",
        f"**Completion Rate**: {data.get('completion_rate', 0):.0f}%",
    ]

    # Status breakdown
    statuses = data.get("status_counts", {})
    if statuses:
        parts = [f"{s}: {c}" for s, c in sorted(statuses.items(), key=lambda x: -x[1])]
        lines.append(f"**Status**: {', '.join(parts)}")

    # Duration
    avg_dur = data.get("avg_duration_s", 0)
    if avg_dur > 0:
        if avg_dur > 3600:
            lines.append(f"**Avg Duration**: {avg_dur / 3600:.1f}h")
        elif avg_dur > 60:
            lines.append(f"**Avg Duration**: {avg_dur / 60:.1f}min")
        else:
            lines.append(f"**Avg Duration**: {avg_dur:.0f}s")

    lines.append("")

    # Agent performance
    agent_stats = data.get("agent_stats", [])
    if agent_stats:
        lines.append("**Agent Performance**:")
        for agent in sorted(agent_stats, key=lambda x: -x["total"]):
            latency = agent["avg_latency_s"]
            lat_str = f"{latency:.0f}s" if latency < 60 else f"{latency / 60:.1f}min"
            lines.append(
                f"  - {agent['agent_type']}: {agent['total']} tasks, "
                f"{agent['success_rate']:.0f}% success, avg {lat_str}"
            )
        lines.append("")

    # Subtask distribution
    dist = data.get("subtask_distribution", {})
    if dist and dist.get("total_subtasks", 0) > 0:
        lines.append(
            f"**Subtask Complexity**: avg {dist['avg']:.1f} per mission "
            f"(range: {dist['min']}-{dist['max']})"
        )
        lines.append("")

    # Failure modes
    failures = data.get("failure_modes", [])
    if failures:
        lines.append(f"**Recent Failures** ({len(failures)}):")
        for f in failures[:5]:
            lines.append(f"  - [{f['agent_type']}] {f['error']}")

    return "\n".join(lines)
