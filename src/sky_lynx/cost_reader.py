"""ClaudeClaw token cost reader for Sky-Lynx.

Reads ClaudeClaw's token_usage table to produce digests showing
token consumption, cost per agent, context compaction frequency,
and week-over-week trends.

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

SECONDS_PER_WEEK = 7 * 86400


def load_cost_data(db_path: Path | None = None) -> dict:
    """Load token usage and cost data from ClaudeClaw's DB.

    Aggregates the last 7 days of token_usage by agent, computes
    compaction rates, and compares to the prior 7-day window for trends.

    Args:
        db_path: Path to claudeclaw.db. Defaults to standard location
                 or CLAUDECLAW_DB_PATH env var.

    Returns:
        Dict with cost breakdowns, compaction stats, and trends.
        Empty dict if unavailable.
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
        # Check if token_usage table exists
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='token_usage'"
        ).fetchone()
        if not table_check:
            logger.info("token_usage table does not exist yet")
            return {}

        now = int(time.time())
        week_ago = now - SECONDS_PER_WEEK
        two_weeks_ago = now - (2 * SECONDS_PER_WEEK)

        data: dict = {}

        # Current week: by agent
        agent_rows = conn.execute(
            "SELECT COALESCE(agent_id, 'main') as agent, "
            "SUM(COALESCE(cost_usd, 0)) as total_cost, "
            "SUM(COALESCE(input_tokens, 0)) as total_input, "
            "SUM(COALESCE(output_tokens, 0)) as total_output, "
            "COUNT(*) as turns "
            "FROM token_usage WHERE created_at >= ? "
            "GROUP BY COALESCE(agent_id, 'main')",
            (week_ago,),
        ).fetchall()

        data["by_agent"] = [
            {
                "agent": row["agent"],
                "cost": row["total_cost"],
                "input_tokens": row["total_input"],
                "output_tokens": row["total_output"],
                "turns": row["turns"],
            }
            for row in agent_rows
        ]

        data["total_cost"] = sum(a["cost"] for a in data["by_agent"])
        data["total_input_tokens"] = sum(a["input_tokens"] for a in data["by_agent"])
        data["total_output_tokens"] = sum(a["output_tokens"] for a in data["by_agent"])
        data["total_turns"] = sum(a["turns"] for a in data["by_agent"])

        if data["total_turns"] == 0:
            return data

        # Compaction stats
        compact_row = conn.execute(
            "SELECT SUM(CASE WHEN did_compact = 1 THEN 1 ELSE 0 END) as compactions, "
            "COUNT(*) as total "
            "FROM token_usage WHERE created_at >= ?",
            (week_ago,),
        ).fetchone()
        data["compactions"] = compact_row["compactions"] or 0
        data["compaction_rate"] = (
            data["compactions"] / compact_row["total"] * 100
            if compact_row["total"] > 0
            else 0
        )

        # Top expensive sessions (by total cost)
        session_rows = conn.execute(
            "SELECT session_id, "
            "SUM(COALESCE(cost_usd, 0)) as cost, "
            "COUNT(*) as turns, "
            "MAX(context_tokens) as peak_context "
            "FROM token_usage WHERE created_at >= ? "
            "GROUP BY session_id "
            "ORDER BY cost DESC LIMIT 5",
            (week_ago,),
        ).fetchall()
        data["top_sessions"] = [
            {
                "session_id": (row["session_id"] or "unknown")[:12],
                "cost": row["cost"],
                "turns": row["turns"],
                "peak_context": row["peak_context"] or 0,
            }
            for row in session_rows
        ]

        # Prior week for trend comparison
        prior_row = conn.execute(
            "SELECT SUM(COALESCE(cost_usd, 0)) as cost, COUNT(*) as turns "
            "FROM token_usage WHERE created_at >= ? AND created_at < ?",
            (two_weeks_ago, week_ago),
        ).fetchone()
        prior_cost = prior_row["cost"] or 0
        if prior_cost > 0:
            data["cost_trend_pct"] = (
                (data["total_cost"] - prior_cost) / prior_cost * 100
            )
        else:
            data["cost_trend_pct"] = None

        return data

    except sqlite3.Error as e:
        logger.warning(f"Error reading ClaudeClaw cost data: {e}")
        return {}
    finally:
        conn.close()


def build_cost_digest(data: dict) -> str:
    """Format cost data into a markdown digest for the analysis prompt.

    Args:
        data: Dict from load_cost_data()

    Returns:
        Formatted markdown digest string.
    """
    if not data:
        return "ClaudeClaw cost data not available."

    total_turns = data.get("total_turns", 0)
    if total_turns == 0:
        return "No token usage recorded this week."

    total_cost = data.get("total_cost", 0)
    lines = [
        f"**Total Cost (7d)**: ${total_cost:.2f}",
        f"**Total Turns**: {total_turns}",
        f"**Input Tokens**: {data.get('total_input_tokens', 0):,}",
        f"**Output Tokens**: {data.get('total_output_tokens', 0):,}",
        f"**Compactions**: {data.get('compactions', 0)} "
        f"({data.get('compaction_rate', 0):.1f}% of turns)",
    ]

    # Cost trend
    trend = data.get("cost_trend_pct")
    if trend is not None:
        direction = "up" if trend > 5 else "down" if trend < -5 else "flat"
        lines.append(f"**Cost Trend vs Prior Week**: {trend:+.0f}% ({direction})")
    else:
        lines.append("**Cost Trend**: no prior week data")

    lines.append("")

    # Per-agent breakdown
    by_agent = data.get("by_agent", [])
    if by_agent:
        lines.append("**Cost by Agent**:")
        for agent in sorted(by_agent, key=lambda x: -x["cost"]):
            lines.append(
                f"  - {agent['agent']}: ${agent['cost']:.2f} "
                f"({agent['turns']} turns)"
            )
        lines.append("")

    # Top sessions
    top = data.get("top_sessions", [])
    if top:
        lines.append("**Most Expensive Sessions**:")
        for s in top:
            ctx_k = s["peak_context"] / 1000
            lines.append(
                f"  - {s['session_id']}: ${s['cost']:.2f} "
                f"({s['turns']} turns, peak {ctx_k:.0f}k context)"
            )

    return "\n".join(lines)
