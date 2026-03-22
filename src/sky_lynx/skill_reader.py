"""Skill inventory and usage reader for Sky-Lynx.

Cross-references deployed Claude Code skills (~/.claude/skills/) with
ClaudeClaw telemetry (tool_used events) to identify which skills are
actively used and which are dead weight.

This closes the Forge feedback loop: Forge produces skills,
skill_reader measures usage, Sky-Lynx recommends improvements.

Data sources:
  - ~/.claude/skills/ (filesystem)
  - ~/projects/claudeclaw/store/telemetry.jsonl (JSONL)
Override with SKILLS_DIR and TELEMETRY_JSONL_PATH environment variables.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SKILLS_DIR = Path.home() / ".claude" / "skills"
DEFAULT_TELEMETRY_PATH = Path.home() / "projects" / "claudeclaw" / "store" / "telemetry.jsonl"


def load_skill_data(
    skills_dir: Path | None = None,
    telemetry_path: Path | None = None,
) -> dict:
    """Load deployed skills and cross-reference with telemetry usage.

    Args:
        skills_dir: Path to skills directory. Defaults to ~/.claude/skills/
                    or SKILLS_DIR env var.
        telemetry_path: Path to telemetry.jsonl. Defaults to standard
                        location or TELEMETRY_JSONL_PATH env var.

    Returns:
        Dict with deployed skills, usage counts, and unused skills.
        Empty dict if skills directory doesn't exist.
    """
    skills_dir = skills_dir or Path(
        os.environ.get("SKILLS_DIR", str(DEFAULT_SKILLS_DIR))
    )
    telemetry_path = telemetry_path or Path(
        os.environ.get("TELEMETRY_JSONL_PATH", str(DEFAULT_TELEMETRY_PATH))
    )

    if not skills_dir.exists():
        logger.info(f"Skills directory not found: {skills_dir}")
        return {}

    # Discover deployed skills (directories with SKILL.md)
    deployed = []
    for entry in sorted(skills_dir.iterdir()):
        if entry.is_dir() and (entry / "SKILL.md").exists():
            deployed.append(entry.name)

    if not deployed:
        logger.info("No deployed skills found")
        return {}

    data: dict = {
        "deployed_skills": deployed,
        "total_deployed": len(deployed),
        "usage_counts": {},
        "unused_skills": [],
        "total_tool_events": 0,
    }

    # Load telemetry tool_used events
    tool_counts: dict[str, int] = {}
    if telemetry_path.exists():
        try:
            with open(telemetry_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("event_type") == "tool_used":
                        tool_name = event.get("tool_name", "")
                        if tool_name:
                            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
                            data["total_tool_events"] += 1
        except OSError as e:
            logger.warning(f"Could not read telemetry for skill usage: {e}")

    # Cross-reference: match skill names against tool_used names
    # Skill names are kebab-case (e.g., "context-fork-guide")
    # Tool names might use the skill name directly or as substring
    for skill in deployed:
        count = 0
        skill_lower = skill.lower()
        for tool_name, tool_count in tool_counts.items():
            if skill_lower in tool_name.lower() or tool_name.lower() in skill_lower:
                count += tool_count
        data["usage_counts"][skill] = count

    data["unused_skills"] = [
        s for s in deployed if data["usage_counts"].get(s, 0) == 0
    ]
    data["used_skills"] = [
        s for s in deployed if data["usage_counts"].get(s, 0) > 0
    ]

    return data


def build_skill_digest(data: dict) -> str:
    """Format skill data into a markdown digest for the analysis prompt.

    Args:
        data: Dict from load_skill_data()

    Returns:
        Formatted markdown digest string.
    """
    if not data:
        return "Skill inventory data not available."

    total = data.get("total_deployed", 0)
    if total == 0:
        return "No skills deployed."

    unused = data.get("unused_skills", [])
    used = data.get("used_skills", [])
    usage = data.get("usage_counts", {})

    lines = [
        f"**Deployed Skills**: {total}",
        f"**Used**: {len(used)} | **Unused**: {len(unused)}",
        "",
    ]

    # Used skills with counts
    if used:
        lines.append("**Skill Usage**:")
        for skill in sorted(used, key=lambda s: -usage.get(s, 0)):
            lines.append(f"  - {skill}: {usage[skill]} events")
        lines.append("")

    # Unused skills
    if unused:
        lines.append("**Unused Skills** (candidates for improvement or removal):")
        for skill in unused:
            lines.append(f"  - {skill}")

    return "\n".join(lines)
