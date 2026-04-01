"""Tests for skill_reader module."""

import json
import tempfile
from pathlib import Path

import pytest

from sky_lynx.skill_reader import (
    QUALITY_THRESHOLD,
    _load_latest_audit_report,
    build_skill_digest,
    load_skill_data,
)


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


def _make_audit_report(skills: list[dict], timestamp: str = "2026-03-31T00:00:00Z") -> dict:
    """Helper to build a minimal audit report."""
    return {
        "timestamp": timestamp,
        "schema_version": "1.0",
        "total_skills": len(skills),
        "results": skills,
    }


class TestQualityScores:
    """Tests for quality score integration from audit reports."""

    def _setup_report(self, skills_dir: Path, report_data: dict, filename: str = "audit-2026-03-31.json"):
        """Write an audit report into the skill-maintenance/reports/ dir."""
        reports_dir = skills_dir / "skill-maintenance" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / filename).write_text(json.dumps(report_data))

    def test_quality_scores_loaded(self, skill_env: tuple[Path, Path]):
        skills_dir, telemetry_path = skill_env
        report = _make_audit_report([
            {"name": "context-fork-guide", "score": 85},
            {"name": "context-hygiene", "score": 30},
        ])
        self._setup_report(skills_dir, report)

        data = load_skill_data(skills_dir, telemetry_path)
        assert data["quality_scores"]["context-fork-guide"] == 85
        assert data["quality_scores"]["context-hygiene"] == 30
        assert data["quality_report_date"] == "2026-03-31T00:00:00Z"

    def test_quality_scores_missing(self, skill_env: tuple[Path, Path]):
        skills_dir, telemetry_path = skill_env
        # No reports dir created
        data = load_skill_data(skills_dir, telemetry_path)
        assert data["quality_scores"] == {}
        assert data["quality_report_date"] is None

    def test_low_quality_used_flagged(self, skill_env: tuple[Path, Path]):
        skills_dir, telemetry_path = skill_env
        report = _make_audit_report([
            {"name": "context-fork-guide", "score": 25},  # used (2 events) + low quality
            {"name": "l5-sprint", "score": 35},            # used (1 event) + low quality
        ])
        self._setup_report(skills_dir, report)

        data = load_skill_data(skills_dir, telemetry_path)
        assert "context-fork-guide" in data["low_quality_used"]
        assert "l5-sprint" in data["low_quality_used"]

    def test_low_quality_unused_not_flagged(self, skill_env: tuple[Path, Path]):
        skills_dir, telemetry_path = skill_env
        report = _make_audit_report([
            {"name": "context-hygiene", "score": 10},  # unused + low quality
        ])
        self._setup_report(skills_dir, report)

        data = load_skill_data(skills_dir, telemetry_path)
        assert "context-hygiene" not in data["low_quality_used"]

    def test_digest_includes_quality(self, skill_env: tuple[Path, Path]):
        skills_dir, telemetry_path = skill_env
        report = _make_audit_report([
            {"name": "context-fork-guide", "score": 85},
        ])
        self._setup_report(skills_dir, report)

        data = load_skill_data(skills_dir, telemetry_path)
        digest = build_skill_digest(data)
        assert "quality: 85/100" in digest
        assert "**Content Quality**" in digest

    def test_digest_omits_quality_when_missing(self, skill_env: tuple[Path, Path]):
        skills_dir, telemetry_path = skill_env
        data = load_skill_data(skills_dir, telemetry_path)
        digest = build_skill_digest(data)
        assert "Content Quality" not in digest
        assert "quality:" not in digest

    def test_latest_report_selection(self, skill_env: tuple[Path, Path]):
        skills_dir, telemetry_path = skill_env
        old_report = _make_audit_report(
            [{"name": "context-fork-guide", "score": 10}],
            timestamp="2026-03-29T00:00:00Z",
        )
        new_report = _make_audit_report(
            [{"name": "context-fork-guide", "score": 90}],
            timestamp="2026-03-31T00:00:00Z",
        )
        self._setup_report(skills_dir, old_report, "audit-2026-03-29.json")
        self._setup_report(skills_dir, new_report, "audit-2026-03-31.json")

        data = load_skill_data(skills_dir, telemetry_path)
        assert data["quality_scores"]["context-fork-guide"] == 90
        assert data["quality_report_date"] == "2026-03-31T00:00:00Z"

    def test_malformed_report_handled(self, skill_env: tuple[Path, Path]):
        skills_dir, telemetry_path = skill_env
        reports_dir = skills_dir / "skill-maintenance" / "reports"
        reports_dir.mkdir(parents=True)
        (reports_dir / "audit-2026-03-31.json").write_text("NOT VALID JSON {{{")

        data = load_skill_data(skills_dir, telemetry_path)
        assert data["quality_scores"] == {}
        assert data["quality_report_date"] is None
