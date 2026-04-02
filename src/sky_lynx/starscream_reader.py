"""Starscream LinkedIn analytics reader for Sky-Lynx.

Reads post performance data from starscream_analytics.db to produce
a digest of content health, engagement trends, and topic patterns
for inclusion in the weekly Sky-Lynx analysis.

Data source: ~/projects/claudeclaw/store/starscream_analytics.db
Override with STARSCREAM_ANALYTICS_DB_PATH environment variable.
"""

import logging
import os
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = (
    Path.home() / "projects" / "claudeclaw" / "store" / "starscream_analytics.db"
)

SECONDS_PER_WEEK = 7 * 86400


def load_starscream_data(db_path: Path | None = None) -> dict:
    """Load LinkedIn post performance data from starscream_analytics.db.

    Reads the most recent snapshot per post (post_metrics has multiple
    snapshots per post_id over time), aggregates by topic, and computes
    week-over-week follower trend.

    Args:
        db_path: Path to starscream_analytics.db. Defaults to standard
                 location or STARSCREAM_ANALYTICS_DB_PATH env var.

    Returns:
        Dict with post metrics, topic performance, and follower trends.
        Empty dict if unavailable.
    """
    if db_path is None:
        db_path = Path(
            os.environ.get("STARSCREAM_ANALYTICS_DB_PATH", str(DEFAULT_DB_PATH))
        )

    if not db_path.exists():
        logger.info(f"Starscream analytics DB not found at {db_path}")
        return {}

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as e:
        logger.warning(f"Could not open Starscream analytics DB: {e}")
        return {}

    try:
        # Verify required tables exist
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {"post_metrics", "daily_aggregate", "post_structure"}
        if not required.issubset(tables):
            logger.info(f"Starscream DB missing tables: {required - tables}")
            return {}

        now = time.time()
        week_ago = now - SECONDS_PER_WEEK

        # Latest snapshot per post (post_metrics stores multiple snapshots)
        posts = conn.execute(
            """
            SELECT id, platform, content_preview, published_at,
                   likes, comments, shares, impressions, reach,
                   engagement_rate
            FROM post_metrics
            WHERE (id, collected_at) IN (
                SELECT id, MAX(collected_at) FROM post_metrics GROUP BY id
            )
            ORDER BY published_at DESC
            """
        ).fetchall()

        if not posts:
            logger.info("No post metrics found in Starscream DB")
            return {}

        total_posts = len(posts)
        total_impressions = sum(p["impressions"] for p in posts)
        total_likes = sum(p["likes"] for p in posts)
        total_comments = sum(p["comments"] for p in posts)
        zero_engagement = sum(1 for p in posts if p["engagement_rate"] == 0)

        # Posts this week
        week_posts = [
            p for p in posts if p["published_at"] and _iso_to_epoch(p["published_at"]) >= week_ago
        ]

        # Top 3 and bottom 3 by engagement rate
        sorted_posts = sorted(posts, key=lambda p: p["engagement_rate"], reverse=True)
        top_posts = sorted_posts[:3]
        bottom_posts = [p for p in sorted_posts[-3:] if p["engagement_rate"] == 0]

        # Topic performance from post_structure (claudeclaw DB has more detail,
        # but starscream_analytics.db has its own post_structure copy)
        topic_rows = conn.execute(
            """
            SELECT ps.topic_angle,
                   COUNT(*) as post_count,
                   AVG(pm.engagement_rate) as avg_er,
                   AVG(pm.impressions) as avg_imp,
                   SUM(pm.likes) as total_likes
            FROM post_structure ps
            JOIN post_metrics pm ON ps.post_id = pm.id
            WHERE (pm.id, pm.collected_at) IN (
                SELECT id, MAX(collected_at) FROM post_metrics GROUP BY id
            )
            GROUP BY ps.topic_angle
            ORDER BY avg_er DESC
            """
        ).fetchall()

        # Latest follower count and trend
        follower_rows = conn.execute(
            """
            SELECT total_followers, new_followers_24h, collected_at
            FROM follower_metrics
            ORDER BY collected_at DESC
            LIMIT 8
            """
        ).fetchall()

        current_followers = follower_rows[0]["total_followers"] if follower_rows else 0
        follower_trend = sum(r["new_followers_24h"] for r in follower_rows) if follower_rows else 0

        # Daily aggregate for the past 7 days
        daily_rows = conn.execute(
            """
            SELECT date, total_posts, total_likes, total_impressions,
                   avg_engagement_rate, follower_count
            FROM daily_aggregate
            ORDER BY date DESC
            LIMIT 7
            """
        ).fetchall()

        return {
            "total_posts": total_posts,
            "total_impressions": total_impressions,
            "total_likes": total_likes,
            "total_comments": total_comments,
            "zero_engagement_count": zero_engagement,
            "zero_engagement_pct": round(zero_engagement / total_posts * 100, 1) if total_posts else 0,
            "week_post_count": len(week_posts),
            "current_followers": current_followers,
            "follower_7d_net": follower_trend,
            "top_posts": [_post_to_dict(p) for p in top_posts],
            "bottom_posts": [_post_to_dict(p) for p in bottom_posts],
            "topic_performance": [dict(r) for r in topic_rows],
            "daily_aggregate": [dict(r) for r in daily_rows],
        }

    except sqlite3.Error as e:
        logger.warning(f"Error reading Starscream analytics DB: {e}")
        return {}
    finally:
        conn.close()


def build_starscream_digest(data: dict) -> str:
    """Format Starscream analytics into a digest string for Claude.

    Args:
        data: Dict returned by load_starscream_data()

    Returns:
        Formatted digest string, or empty string if no data.
    """
    if not data:
        return ""

    lines = [
        "## Starscream LinkedIn Performance",
        "",
        "### Account Overview",
        f"- Posts tracked: {data['total_posts']}",
        f"- Followers: {data['current_followers']} ({'+' if data['follower_7d_net'] >= 0 else ''}{data['follower_7d_net']} past 7d)",
        f"- Total impressions: {data['total_impressions']:,}",
        f"- Total likes: {data['total_likes']} | Comments: {data['total_comments']}",
        f"- Zero-engagement posts: {data['zero_engagement_count']} ({data['zero_engagement_pct']}%)",
        f"- Posts this week: {data['week_post_count']}",
        "",
    ]

    if data.get("topic_performance"):
        lines += ["### Topic Performance (by avg engagement rate)", ""]
        for t in data["topic_performance"]:
            topic = t.get("topic_angle") or "unknown"
            lines.append(
                f"- {topic}: {t['post_count']} posts | "
                f"{round(t['avg_er'], 1)}% avg ER | "
                f"{round(t['avg_imp'])} avg imp | "
                f"{t['total_likes']} likes"
            )
        lines.append("")

    if data.get("top_posts"):
        lines += ["### Top Posts (by engagement rate)", ""]
        for p in data["top_posts"]:
            lines.append(
                f"- [{p['engagement_rate']}% ER, {p['impressions']} imp] "
                f"{p['preview']}"
            )
        lines.append("")

    if data.get("bottom_posts"):
        lines += ["### Zero-Engagement Posts (openers to avoid)", ""]
        for p in data["bottom_posts"]:
            lines.append(f"- {p['preview']}")
        lines.append("")

    if data.get("daily_aggregate"):
        lines += ["### 7-Day Trend", ""]
        for d in data["daily_aggregate"]:
            lines.append(
                f"- {d['date']}: {d['total_impressions']} imp | "
                f"{d['avg_engagement_rate']:.1f}% avg ER | "
                f"{d['follower_count']} followers"
            )
        lines.append("")

    return "\n".join(lines)


def _post_to_dict(row: sqlite3.Row) -> dict:
    """Convert a post_metrics row to a summary dict."""
    preview = (row["content_preview"] or "")[:80].replace("\n", " ")
    return {
        "preview": preview,
        "published_at": row["published_at"],
        "likes": row["likes"],
        "comments": row["comments"],
        "impressions": row["impressions"],
        "engagement_rate": round(row["engagement_rate"], 1),
    }


def _iso_to_epoch(iso_str: str) -> float:
    """Convert an ISO 8601 string to a Unix timestamp.

    Handles both Z suffix and +00:00 offset. Returns 0 on parse failure.
    """
    from datetime import datetime, timezone

    iso_str = iso_str.rstrip("Z").split("+")[0].split(".")[0]
    try:
        dt = datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0
