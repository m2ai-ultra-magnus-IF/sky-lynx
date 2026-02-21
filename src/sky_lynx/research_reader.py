"""Research signal reader for Sky-Lynx.

Reads research signals from Snow-Town's SQLite database and produces
summary digests for the analysis prompt. Uses direct SQL queries
to avoid importing research-agents' Python code.
"""

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / "projects" / "st-factory" / "data" / "persona_metrics.db"


def load_research_signals(db_path: Path | None = None) -> dict:
    """Load aggregate data from Snow-Town's research_signals table.

    Opens the database read-only and runs aggregate queries to build
    a summary of research signals by source, relevance, and domain.

    Args:
        db_path: Path to persona_metrics.db. Defaults to Snow-Town data dir
                 or SNOW_TOWN_DB_PATH env var.

    Returns:
        Dict with signal counts, relevance distribution, recent high signals.
        Empty dict if DB is missing or table doesn't exist.
    """
    if db_path is None:
        db_path = Path(os.environ.get("SNOW_TOWN_DB_PATH", str(DEFAULT_DB_PATH)))

    if not db_path.exists():
        logger.warning(f"Snow-Town DB not found at {db_path}")
        return {}

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as e:
        logger.warning(f"Could not open Snow-Town DB: {e}")
        return {}

    try:
        data: dict = {}

        # Check if research_signals table exists
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='research_signals'"
        ).fetchone()
        if not table_check:
            logger.info("research_signals table not found (contract not yet active)")
            return {}

        # Total signal count
        data["total_signals"] = conn.execute(
            "SELECT COUNT(*) FROM research_signals"
        ).fetchone()[0]

        if data["total_signals"] == 0:
            return data

        # Signals by source
        rows = conn.execute(
            "SELECT source, COUNT(*) as cnt FROM research_signals GROUP BY source"
        ).fetchall()
        data["by_source"] = {row["source"]: row["cnt"] for row in rows}

        # Signals by relevance
        rows = conn.execute(
            "SELECT relevance, COUNT(*) as cnt FROM research_signals GROUP BY relevance"
        ).fetchall()
        data["by_relevance"] = {row["relevance"]: row["cnt"] for row in rows}

        # Signals by domain (non-null only)
        rows = conn.execute(
            "SELECT domain, COUNT(*) as cnt FROM research_signals "
            "WHERE domain IS NOT NULL GROUP BY domain ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        data["by_domain"] = {row["domain"]: row["cnt"] for row in rows}

        # Consumed vs unconsumed
        consumed = conn.execute(
            "SELECT COUNT(*) FROM research_signals WHERE consumed_by IS NOT NULL"
        ).fetchone()[0]
        data["consumed"] = consumed
        data["unconsumed"] = data["total_signals"] - consumed

        # Recent high-relevance signals (last 10)
        high_rows = conn.execute(
            "SELECT signal_id, source, title, relevance, relevance_rationale, tags, domain, emitted_at "
            "FROM research_signals WHERE relevance = 'high' "
            "ORDER BY emitted_at DESC LIMIT 10"
        ).fetchall()
        data["recent_high"] = [
            {
                "signal_id": row["signal_id"],
                "source": row["source"],
                "title": row["title"],
                "relevance_rationale": row["relevance_rationale"],
                "tags": row["tags"],
                "domain": row["domain"],
                "emitted_at": row["emitted_at"],
            }
            for row in high_rows
        ]

        # Persona-tagged signals (tags containing "persona:")
        rows = conn.execute(
            "SELECT tags FROM research_signals WHERE tags LIKE '%persona:%'"
        ).fetchall()
        persona_counts: dict[str, int] = {}
        import json
        for row in rows:
            try:
                tags = json.loads(row["tags"])
                for tag in tags:
                    if tag.startswith("persona:"):
                        persona_id = tag.split(":", 1)[1]
                        persona_counts[persona_id] = persona_counts.get(persona_id, 0) + 1
            except (json.JSONDecodeError, TypeError):
                continue
        data["persona_tagged"] = persona_counts

        return data

    except sqlite3.Error as e:
        logger.warning(f"Error reading research signals: {e}")
        return {}
    finally:
        conn.close()


def build_research_digest(data: dict) -> str:
    """Format research signal data into a markdown digest string.

    Args:
        data: Dict from load_research_signals()

    Returns:
        Formatted digest string for inclusion in analysis prompt.
        Returns fallback message if data is empty/None.
    """
    if not data:
        return "Research signal data not available."

    total = data.get("total_signals", 0)
    if total == 0:
        return "No research signals collected yet."

    lines = [
        f"**Total Research Signals**: {total}",
        "",
    ]

    # By source
    by_source = data.get("by_source", {})
    if by_source:
        lines.append("**By Source**:")
        for source, count in sorted(by_source.items(), key=lambda x: -x[1]):
            lines.append(f"  - {source}: {count}")
        lines.append("")

    # By relevance
    by_relevance = data.get("by_relevance", {})
    if by_relevance:
        lines.append("**By Relevance**:")
        for rel, count in sorted(by_relevance.items()):
            lines.append(f"  - {rel}: {count}")
        lines.append("")

    # Consumption status
    consumed = data.get("consumed", 0)
    unconsumed = data.get("unconsumed", 0)
    lines.append(f"**Consumed**: {consumed} | **Unconsumed**: {unconsumed}")
    lines.append("")

    # By domain
    by_domain = data.get("by_domain", {})
    if by_domain:
        lines.append("**Top Domains**:")
        for domain, count in sorted(by_domain.items(), key=lambda x: -x[1]):
            lines.append(f"  - {domain}: {count}")
        lines.append("")

    # Persona-tagged findings
    persona_tagged = data.get("persona_tagged", {})
    if persona_tagged:
        lines.append("**Persona-Relevant Signals**:")
        for persona, count in sorted(persona_tagged.items(), key=lambda x: -x[1]):
            lines.append(f"  - {persona}: {count}")
        lines.append("")

    # Recent high-relevance signals
    recent_high = data.get("recent_high", [])
    if recent_high:
        lines.append("**Recent High-Relevance Signals**:")
        for sig in recent_high[:5]:
            lines.append(f"  - [{sig['source']}] {sig['title']}")
            if sig.get("relevance_rationale"):
                lines.append(f"    Rationale: {sig['relevance_rationale']}")
        lines.append("")

    return "\n".join(lines)
