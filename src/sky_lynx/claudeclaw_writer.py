"""Write Sky-Lynx recommendations as JSON files for ClaudeClaw consumption.

Option A file-based handoff: Sky-Lynx writes recommendation files,
ClaudeClaw's daily-loop reads and applies them.

Output directory: ~/projects/sky-lynx/data/claudeclaw-recommendations/
Format: {timestamp}_{target_system}_{index}.json
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from .claude_client import Recommendation

logger = logging.getLogger(__name__)

CLAUDECLAW_TARGETS = {"preference", "routing", "skill", "schedule"}
DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent.parent / "data" / "claudeclaw-recommendations"


def write_claudeclaw_recommendations(
    recommendations: list[Recommendation],
    output_dir: Path | None = None,
) -> list[Path]:
    """Write ClaudeClaw-targeted recommendations as JSON files.

    Only writes recommendations whose target_system is in CLAUDECLAW_TARGETS.
    Each recommendation becomes one JSON file for ClaudeClaw to consume.

    Args:
        recommendations: All recommendations from analysis.
        output_dir: Override output directory. Defaults to
                    data/claudeclaw-recommendations/ in the sky-lynx project.

    Returns:
        List of written file paths.
    """
    out_dir = output_dir or DEFAULT_OUTPUT_DIR

    claw_recs = [r for r in recommendations if r.target_system in CLAUDECLAW_TARGETS]
    if not claw_recs:
        logger.info("No ClaudeClaw-targeted recommendations to write")
        return []

    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    now = datetime.now(UTC)
    timestamp = now.strftime("%Y-%m-%d_%H%M%S")

    for i, rec in enumerate(claw_recs):
        filename = f"{timestamp}_{rec.target_system}_{i}.json"
        filepath = out_dir / filename
        payload = {
            "source": "sky-lynx",
            "created_at": now.isoformat(),
            "target_system": rec.target_system,
            "title": rec.title,
            "priority": rec.priority,
            "evidence": rec.evidence,
            "suggested_change": rec.suggested_change,
            "impact": rec.impact,
            "reversibility": rec.reversibility,
            "recommendation_type": rec.recommendation_type,
        }
        filepath.write_text(json.dumps(payload, indent=2))
        written.append(filepath)
        logger.info(f"Wrote ClaudeClaw recommendation: {filepath.name}")

    logger.info(f"Wrote {len(written)} ClaudeClaw recommendations to {out_dir}")
    return written
