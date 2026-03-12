"""Taste profile reader for Sky-Lynx.

Reads the latest taste delta report produced by taste_capture.py and
builds a digest for inclusion in the weekly analysis prompt.

Data source: projects/sky-lynx/data/taste-snapshots/taste-delta_*.md
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SNAPSHOTS_DIR = Path(__file__).parent.parent.parent / "data" / "taste-snapshots"


def load_taste_data(snapshots_dir: Path | None = None) -> dict | None:
    """Load the most recent taste delta report.

    Args:
        snapshots_dir: Override path to taste-snapshots directory.

    Returns:
        Dict with keys 'report_text', 'report_date', 'snapshot_path',
        or None if no delta reports exist.
    """
    snap_dir = snapshots_dir or Path(
        os.environ.get("TASTE_SNAPSHOTS_DIR", str(DEFAULT_SNAPSHOTS_DIR))
    )

    if not snap_dir.exists():
        logger.info(f"Taste snapshots directory not found: {snap_dir}")
        return None

    # Find the most recent delta report
    deltas = sorted(snap_dir.glob("taste-delta_*.md"), reverse=True)
    if not deltas:
        logger.info("No taste delta reports found")
        return None

    latest = deltas[0]
    report_text = latest.read_text()

    # Extract date from filename: taste-delta_YYYY-MM-DD.md
    date_str = latest.stem.replace("taste-delta_", "")

    return {
        "report_text": report_text,
        "report_date": date_str,
        "snapshot_path": str(latest),
    }


def build_taste_digest(data: dict) -> str:
    """Format taste data into a markdown digest for the analysis prompt.

    Args:
        data: Dict from load_taste_data()

    Returns:
        Formatted markdown digest string
    """
    if not data:
        return "No taste profile data available."

    lines = [
        f"**Latest Taste Capture**: {data['report_date']}",
        "",
        data["report_text"],
    ]

    return "\n".join(lines)
