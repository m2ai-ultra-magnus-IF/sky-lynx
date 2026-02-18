"""IdeaForge reader for Sky-Lynx.

Reads IdeaForge's SQLite database (signals + ideas) and produces
summary digests for the analysis prompt. Uses direct SQL queries
to avoid importing IdeaForge's Python code.
"""

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / "projects" / "ideaforge" / "data" / "ideaforge.db"


def load_ideaforge_data(db_path: Path | None = None) -> dict:
    """Load aggregate data from IdeaForge's SQLite database.

    Opens the database read-only and runs aggregate queries to build
    a summary of signals, ideas, scores, and classifications.

    Args:
        db_path: Path to ideaforge.db. Defaults to ~/projects/ideaforge/data/ideaforge.db
                 or IDEAFORGE_DB_PATH env var.

    Returns:
        Dict with signal counts, idea breakdown, scores, top ideas.
        Empty dict if DB is missing or unreadable.
    """
    if db_path is None:
        db_path = Path(os.environ.get("IDEAFORGE_DB_PATH", str(DEFAULT_DB_PATH)))

    if not db_path.exists():
        logger.warning(f"IdeaForge DB not found at {db_path}")
        return {}

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as e:
        logger.warning(f"Could not open IdeaForge DB: {e}")
        return {}

    try:
        data: dict = {}

        # Signal counts by type
        rows = conn.execute(
            "SELECT signal_type, COUNT(*) as cnt FROM signals GROUP BY signal_type"
        ).fetchall()
        data["signal_types"] = {row["signal_type"]: row["cnt"] for row in rows}
        data["total_signals"] = sum(data["signal_types"].values())

        # Idea classification breakdown
        rows = conn.execute(
            "SELECT artifact_type, COUNT(*) as cnt FROM ideas "
            "WHERE artifact_type IS NOT NULL GROUP BY artifact_type"
        ).fetchall()
        data["artifact_types"] = {row["artifact_type"]: row["cnt"] for row in rows}

        # Total ideas
        data["total_ideas"] = conn.execute("SELECT COUNT(*) FROM ideas").fetchone()[0]

        # Dismiss rate
        dismissed = conn.execute(
            "SELECT COUNT(*) FROM ideas WHERE status = 'dismissed'"
        ).fetchone()[0]
        data["dismissed"] = dismissed
        data["dismiss_rate"] = (
            (dismissed / data["total_ideas"] * 100) if data["total_ideas"] > 0 else 0
        )

        # Score dimension averages (non-NULL scores only)
        score_row = conn.execute(
            "SELECT AVG(weighted_score) as avg_weighted, "
            "AVG(opportunity_score) as avg_opportunity, "
            "AVG(problem_score) as avg_problem, "
            "AVG(feasibility_score) as avg_feasibility, "
            "AVG(why_now_score) as avg_why_now, "
            "AVG(competition_score) as avg_competition, "
            "MIN(weighted_score) as min_weighted, "
            "MAX(weighted_score) as max_weighted, "
            "COUNT(weighted_score) as scored_count "
            "FROM ideas WHERE weighted_score IS NOT NULL"
        ).fetchone()
        data["scores"] = {
            "avg_weighted": score_row["avg_weighted"],
            "avg_opportunity": score_row["avg_opportunity"],
            "avg_problem": score_row["avg_problem"],
            "avg_feasibility": score_row["avg_feasibility"],
            "avg_why_now": score_row["avg_why_now"],
            "avg_competition": score_row["avg_competition"],
            "min_weighted": score_row["min_weighted"],
            "max_weighted": score_row["max_weighted"],
            "scored_count": score_row["scored_count"],
        }

        # Top 5 classified ideas
        top_rows = conn.execute(
            "SELECT id, title, weighted_score, artifact_type, route_confidence, struggling_user "
            "FROM ideas WHERE status = 'classified' "
            "ORDER BY weighted_score DESC LIMIT 5"
        ).fetchall()
        data["top_ideas"] = [
            {
                "id": row["id"],
                "title": row["title"],
                "weighted_score": row["weighted_score"],
                "artifact_type": row["artifact_type"],
                "route_confidence": row["route_confidence"],
                "struggling_user": row["struggling_user"],
            }
            for row in top_rows
        ]

        # Signal engagement stats
        engagement = conn.execute(
            "SELECT AVG(score) as avg_score, AVG(num_comments) as avg_comments, "
            "COUNT(*) as total FROM signals"
        ).fetchone()
        data["signal_engagement"] = {
            "avg_score": engagement["avg_score"],
            "avg_comments": engagement["avg_comments"],
            "total": engagement["total"],
        }

        return data

    except sqlite3.Error as e:
        logger.warning(f"Error reading IdeaForge DB: {e}")
        return {}
    finally:
        conn.close()


def build_ideaforge_digest(data: dict) -> str:
    """Format IdeaForge data dict into a markdown digest string.

    Args:
        data: Dict from load_ideaforge_data()

    Returns:
        Formatted digest string for inclusion in analysis prompt.
        Returns fallback message if data is empty/None.
    """
    if not data:
        return "IdeaForge data not available."

    lines = [
        f"**Total Signals**: {data.get('total_signals', 0)}",
        f"**Total Ideas**: {data.get('total_ideas', 0)}",
        "",
    ]

    # Signal type breakdown
    signal_types = data.get("signal_types", {})
    if signal_types:
        lines.append("**Signal Types**:")
        for stype, count in sorted(signal_types.items(), key=lambda x: -x[1]):
            lines.append(f"  - {stype}: {count}")
        lines.append("")

    # Classification breakdown
    artifact_types = data.get("artifact_types", {})
    if artifact_types:
        lines.append("**Idea Classifications**:")
        for atype, count in sorted(artifact_types.items(), key=lambda x: -x[1]):
            lines.append(f"  - {atype}: {count}")
        lines.append("")

    # Dismiss rate
    dismiss_rate = data.get("dismiss_rate", 0)
    dismissed = data.get("dismissed", 0)
    total = data.get("total_ideas", 0)
    lines.append(f"**Dismiss Rate**: {dismiss_rate:.0f}% ({dismissed}/{total})")
    lines.append("")

    # Score averages
    scores = data.get("scores", {})
    if scores.get("scored_count", 0) > 0:
        lines.append("**Score Averages** (scored ideas):")
        lines.append(f"  - Weighted: {scores['avg_weighted']:.1f} (range: {scores['min_weighted']:.1f} - {scores['max_weighted']:.1f})")
        lines.append(f"  - Opportunity: {scores['avg_opportunity']:.1f}")
        lines.append(f"  - Problem: {scores['avg_problem']:.1f}")
        lines.append(f"  - Feasibility: {scores['avg_feasibility']:.1f}")
        lines.append(f"  - Why Now: {scores['avg_why_now']:.1f}")
        lines.append(f"  - Competition: {scores['avg_competition']:.1f}")
        lines.append("")

    # Top classified ideas
    top_ideas = data.get("top_ideas", [])
    if top_ideas:
        lines.append("**Top Classified Ideas**:")
        for idea in top_ideas:
            confidence_pct = int((idea["route_confidence"] or 0) * 100)
            lines.append(
                f"  - [{idea['artifact_type']}] {idea['title']} "
                f"(score: {idea['weighted_score']:.1f}, confidence: {confidence_pct}%)"
            )
        lines.append("")

    # Signal engagement
    engagement = data.get("signal_engagement", {})
    if engagement.get("total", 0) > 0:
        avg_score = engagement.get("avg_score", 0) or 0
        avg_comments = engagement.get("avg_comments", 0) or 0
        lines.append("**Signal Engagement** (HN):")
        lines.append(f"  - Average score: {avg_score:.0f}")
        lines.append(f"  - Average comments: {avg_comments:.0f}")

    return "\n".join(lines)
