"""Metroplex pipeline health reader for Sky-Lynx.

Reads Metroplex's SQLite database (read-only) to extract pipeline
health metrics: build success/failure rates, triage decisions,
queue throughput, and timing data.

Used by the analyzer to build a pipeline health digest for Claude's
weekly analysis, enabling pipeline config recommendations.
"""

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# Default path to Metroplex's database
DEFAULT_METROPLEX_DB = Path.home() / "projects" / "metroplex" / "data" / "metroplex.db"


def _get_db_path() -> Path:
    """Get Metroplex DB path from env var or default."""
    return Path(os.environ.get("METROPLEX_DB_PATH", str(DEFAULT_METROPLEX_DB)))


def load_metroplex_data() -> dict | None:
    """Load pipeline health metrics from Metroplex's database.

    Returns:
        Dict of metrics, or None if DB not found.
    """
    db_path = _get_db_path()
    if not db_path.exists():
        logger.info("Metroplex DB not found at %s", db_path)
        return None

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        data = {}

        # Build job stats
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM build_jobs GROUP BY status"
        ).fetchall()
        build_stats = {r["status"]: r["cnt"] for r in rows}
        data["build_total"] = sum(build_stats.values())
        data["build_completed"] = build_stats.get("completed", 0)
        data["build_failed"] = build_stats.get("failed", 0)
        data["build_queued"] = build_stats.get("queued", 0)
        data["build_success_rate"] = (
            round(data["build_completed"] / data["build_total"] * 100, 1)
            if data["build_total"] > 0 else 0
        )

        # Triage decision stats
        rows = conn.execute(
            "SELECT decision, COUNT(*) as cnt FROM triage_decisions GROUP BY decision"
        ).fetchall()
        triage_stats = {r["decision"]: r["cnt"] for r in rows}
        data["triage_total"] = sum(triage_stats.values())
        data["triage_approved"] = triage_stats.get("approve", 0)
        data["triage_rejected"] = triage_stats.get("reject", 0)
        data["triage_deferred"] = triage_stats.get("defer", 0)
        data["triage_approve_rate"] = (
            round(data["triage_approved"] / data["triage_total"] * 100, 1)
            if data["triage_total"] > 0 else 0
        )

        # Recent triage decisions (last 7 days)
        rows = conn.execute(
            "SELECT decision, COUNT(*) as cnt FROM triage_decisions "
            "WHERE decided_at >= datetime('now', '-7 days') GROUP BY decision"
        ).fetchall()
        recent_triage = {r["decision"]: r["cnt"] for r in rows}
        data["recent_triage_approved"] = recent_triage.get("approve", 0)
        data["recent_triage_rejected"] = recent_triage.get("reject", 0)
        data["recent_triage_deferred"] = recent_triage.get("defer", 0)

        # Priority queue stats
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM priority_queue GROUP BY status"
        ).fetchall()
        queue_stats = {r["status"]: r["cnt"] for r in rows}
        data["queue_pending"] = queue_stats.get("pending", 0)
        data["queue_dispatched"] = queue_stats.get("dispatched", 0)
        data["queue_completed"] = queue_stats.get("completed", 0)
        data["queue_failed"] = queue_stats.get("failed", 0)

        # Publish stats
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM publish_jobs GROUP BY status"
        ).fetchall()
        publish_stats = {r["status"]: r["cnt"] for r in rows}
        data["published"] = publish_stats.get("published", 0)
        data["publish_failed"] = publish_stats.get("failed", 0)

        # Gate status (circuit breaker)
        rows = conn.execute(
            "SELECT gate, consecutive_failures, halted FROM gate_status"
        ).fetchall()
        data["gate_status"] = {
            r["gate"]: {"failures": r["consecutive_failures"], "halted": bool(r["halted"])}
            for r in rows
        }

        # Recent cycle errors (last 7 days)
        rows = conn.execute(
            "SELECT errors FROM cycles WHERE started_at >= datetime('now', '-7 days') AND errors != '[]'"
        ).fetchall()
        import json
        error_count = 0
        for r in rows:
            try:
                errs = json.loads(r["errors"])
                error_count += len(errs)
            except (json.JSONDecodeError, TypeError):
                pass
        data["recent_cycle_errors"] = error_count

        # Cycle count (last 7 days)
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM cycles WHERE started_at >= datetime('now', '-7 days')"
        ).fetchone()
        data["recent_cycles"] = row["cnt"]

        # Quality score correlation (Phase 14c)
        # Get quality scores grouped by terminal state
        _load_quality_correlation(conn, data)

        conn.close()
        return data

    except Exception as e:
        logger.warning("Could not read Metroplex DB: %s", e)
        return None


def _load_quality_correlation(conn: sqlite3.Connection, data: dict) -> None:
    """Load quality score correlation data into the metrics dict.

    Groups builds by terminal state (completed+published, completed+review_failed,
    failed) and computes quality score stats for each group.
    """
    quality = {"scored_builds": 0, "groups": {}}

    # All scored builds with their terminal state
    rows = conn.execute("""
        SELECT
            b.queue_job_id,
            b.title,
            b.status,
            b.review_status,
            b.quality_score,
            CASE
                WHEN p.status = 'published' THEN 'published'
                WHEN b.review_status = 'review_failed' THEN 'review_failed'
                WHEN b.review_status = 'tyrest_rejected' THEN 'tyrest_rejected'
                WHEN b.status = 'failed' THEN 'build_failed'
                WHEN b.review_status = 'reviewed' THEN 'reviewed_unpublished'
                ELSE 'other'
            END as terminal_state
        FROM build_jobs b
        LEFT JOIN publish_jobs p ON p.build_job_id = b.queue_job_id AND p.status = 'published'
        WHERE b.quality_score IS NOT NULL
        AND b.id IN (SELECT MAX(id) FROM build_jobs GROUP BY queue_job_id)
    """).fetchall()

    if not rows:
        data["quality"] = quality
        return

    quality["scored_builds"] = len(rows)

    # Group by terminal state
    groups: dict[str, list[float]] = {}
    for r in rows:
        state = r["terminal_state"]
        score = r["quality_score"]
        groups.setdefault(state, []).append(score)

    for state, scores in groups.items():
        quality["groups"][state] = {
            "count": len(scores),
            "avg": round(sum(scores) / len(scores), 1),
            "min": round(min(scores), 1),
            "max": round(max(scores), 1),
        }

    # Compute suggested threshold (midpoint between published avg and failed avg)
    all_scores = [r["quality_score"] for r in rows]
    quality["overall_avg"] = round(sum(all_scores) / len(all_scores), 1)

    pub_scores = groups.get("published", [])
    fail_scores = (
        groups.get("build_failed", []) +
        groups.get("review_failed", []) +
        groups.get("tyrest_rejected", [])
    )

    if pub_scores and fail_scores:
        pub_avg = sum(pub_scores) / len(pub_scores)
        fail_avg = sum(fail_scores) / len(fail_scores)
        quality["suggested_threshold"] = round((pub_avg + fail_avg) / 2, 1)
        quality["threshold_rationale"] = (
            f"Midpoint between published avg ({pub_avg:.1f}) "
            f"and failed avg ({fail_avg:.1f})"
        )

    data["quality"] = quality


def build_pipeline_health_digest(data: dict) -> str:
    """Build a markdown digest of pipeline health metrics.

    Args:
        data: Dict from load_metroplex_data()

    Returns:
        Formatted markdown string for inclusion in the analysis prompt.
    """
    lines = []

    # Build health
    lines.append("### Build Pipeline")
    lines.append(f"- Total builds: {data['build_total']}")
    lines.append(f"- Completed: {data['build_completed']} ({data['build_success_rate']}% success rate)")
    lines.append(f"- Failed: {data['build_failed']}")
    if data["build_queued"]:
        lines.append(f"- Queued (waiting): {data['build_queued']}")

    # Triage
    lines.append("")
    lines.append("### Triage Decisions")
    lines.append(f"- Total decisions: {data['triage_total']}")
    lines.append(f"- Approved: {data['triage_approved']} ({data['triage_approve_rate']}%)")
    lines.append(f"- Rejected: {data['triage_rejected']}")
    lines.append(f"- Deferred: {data['triage_deferred']}")
    lines.append(f"- Last 7 days: {data['recent_triage_approved']} approved, "
                 f"{data['recent_triage_rejected']} rejected, "
                 f"{data['recent_triage_deferred']} deferred")

    # Queue
    lines.append("")
    lines.append("### Priority Queue")
    lines.append(f"- Pending: {data['queue_pending']}")
    lines.append(f"- Dispatched: {data['queue_dispatched']}")
    lines.append(f"- Completed: {data['queue_completed']}")
    lines.append(f"- Failed: {data['queue_failed']}")

    # Publish
    lines.append("")
    lines.append("### Publishing")
    lines.append(f"- Published to GitHub: {data['published']}")
    if data["publish_failed"]:
        lines.append(f"- Publish failures: {data['publish_failed']}")

    # Gate health
    halted_gates = [g for g, s in data["gate_status"].items() if s["halted"]]
    if halted_gates:
        lines.append("")
        lines.append(f"### Circuit Breaker ALERT")
        lines.append(f"- Halted gates: {', '.join(halted_gates)}")

    # Operational stats
    lines.append("")
    lines.append("### Operations (last 7 days)")
    lines.append(f"- Cycles run: {data['recent_cycles']}")
    if data["recent_cycle_errors"]:
        lines.append(f"- Cycle errors: {data['recent_cycle_errors']}")

    # Quality correlation (Phase 14c)
    quality = data.get("quality", {})
    if quality.get("scored_builds", 0) > 0:
        lines.append("")
        lines.append("### Build Quality Scores (0-100)")
        lines.append(f"- Scored builds: {quality['scored_builds']}")
        lines.append(f"- Overall average: {quality.get('overall_avg', 0)}")

        for state, stats in quality.get("groups", {}).items():
            label = state.replace("_", " ").title()
            lines.append(f"- {label}: avg={stats['avg']}, "
                         f"range={stats['min']}-{stats['max']}, "
                         f"n={stats['count']}")

        if "suggested_threshold" in quality:
            lines.append(f"- **Suggested quality threshold**: {quality['suggested_threshold']}")
            lines.append(f"  ({quality.get('threshold_rationale', '')})")

    return "\n".join(lines)
