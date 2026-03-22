"""Tests for skill_reader module."""

import json
import tempfile
from pathlib import Path

import pytest

from sky_lynx.skill_reader import build_skill_digest, load_skill_data


@pytest.fixture
def skill_env(tmp_path: Path) -> tuple[Path, Path]:
    """Create temp skills directory and telemetry file."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # Create deployed skills
    for name in ["context-fork-guide", "context-hygiene", "l5-sprint"]:
        skill_dir = skills_dir / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"# {name}")

    # Create a dir without SKILL.md (should be ignored)
    (skills_dir / "broken-skill").mkdir()

    # Create telemetry file
    telemetry_path = tmp_path / "telemetry.jsonl"
    events = [
        {"event_type": "tool_used", "tool_name": "context-fork-guide"},
        {"event_type": "tool_used", "tool_name": "context-fork-guide"},
        {"event_type": "tool_used", "tool_name": "l5-sprint"},
        {"event_type": "message_received", "message_type": "text"},  # Not tool_used
    ]
    with open(telemetry_path, "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")

    return skills_dir, telemetry_path


class TestLoadSkillData:
    """Tests for load_skill_data function."""

    def test_load_with_telemetry(self, skill_env: tuple[Path, Path]):
        skills_dir, telemetry_path = skill_env
        data = load_skill_data(skills_dir, telemetry_path)

        assert data["total_deployed"] == 3
        assert "context-fork-guide" in data["deployed_skills"]
        assert "broken-skill" not in data["deployed_skills"]

    def test_usage_counts(self, skill_env: tuple[Path, Path]):
        skills_dir, telemetry_path = skill_env
        data = load_skill_data(skills_dir, telemetry_path)

        assert data["usage_counts"]["context-fork-guide"] == 2
        assert data["usage_counts"]["l5-sprint"] == 1
        assert data["usage_counts"]["context-hygiene"] == 0

    def test_unused_skills(self, skill_env: tuple[Path, Path]):
        skills_dir, telemetry_path = skill_env
        data = load_skill_data(skills_dir, telemetry_path)

        assert "context-hygiene" in data["unused_skills"]
        assert "context-fork-guide" not in data["unused_skills"]

    def test_no_telemetry_file(self, skill_env: tuple[Path, Path]):
        skills_dir, _ = skill_env
        data = load_skill_data(skills_dir, Path("/nonexistent/telemetry.jsonl"))

        # Should still list deployed skills, all with 0 usage
        assert data["total_deployed"] == 3
        assert all(c == 0 for c in data["usage_counts"].values())

    def test_missing_skills_dir(self):
        data = load_skill_data(Path("/nonexistent/skills"))
        assert data == {}

    def test_empty_skills_dir(self, tmp_path: Path):
        skills_dir = tmp_path / "empty_skills"
        skills_dir.mkdir()
        data = load_skill_data(skills_dir)
        assert data == {}


class TestBuildSkillDigest:
    """Tests for build_skill_digest function."""

    def test_digest_populated(self, skill_env: tuple[Path, Path]):
        skills_dir, telemetry_path = skill_env
        data = load_skill_data(skills_dir, telemetry_path)
        digest = build_skill_digest(data)

        assert "**Deployed Skills**: 3" in digest
        assert "context-fork-guide: 2 events" in digest
        assert "**Unused Skills**" in digest
        assert "context-hygiene" in digest

    def test_digest_empty(self):
        assert build_skill_digest({}) == "Skill inventory data not available."

    def test_digest_no_skills(self):
        digest = build_skill_digest({"total_deployed": 0})
        assert "No skills" in digest
