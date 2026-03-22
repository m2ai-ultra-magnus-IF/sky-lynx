"""Tests for claudeclaw_writer module."""

import json
from pathlib import Path

import pytest

from sky_lynx.claude_client import Recommendation
from sky_lynx.claudeclaw_writer import write_claudeclaw_recommendations


def _make_rec(target_system: str = "claude_md", **kwargs) -> Recommendation:
    defaults = {
        "title": "Test rec",
        "priority": "high",
        "evidence": "Test evidence",
        "suggested_change": "Test change",
        "impact": "Test impact",
        "reversibility": "high",
        "target_system": target_system,
    }
    defaults.update(kwargs)
    return Recommendation(**defaults)


class TestWriteClaudeclawRecommendations:
    """Tests for write_claudeclaw_recommendations function."""

    def test_writes_claudeclaw_targets(self, tmp_path: Path):
        recs = [
            _make_rec("preference", title="Adjust tone pref"),
            _make_rec("routing", title="Route more to Ravage"),
            _make_rec("claude_md", title="Add rule"),  # Should NOT be written
        ]
        written = write_claudeclaw_recommendations(recs, tmp_path)

        assert len(written) == 2
        assert all(f.exists() for f in written)

        # Verify JSON content
        data = json.loads(written[0].read_text())
        assert data["source"] == "sky-lynx"
        assert data["target_system"] == "preference"
        assert data["title"] == "Adjust tone pref"

    def test_skips_non_claudeclaw_targets(self, tmp_path: Path):
        recs = [
            _make_rec("claude_md"),
            _make_rec("persona"),
            _make_rec("pipeline"),
        ]
        written = write_claudeclaw_recommendations(recs, tmp_path)
        assert len(written) == 0

    def test_empty_recommendations(self, tmp_path: Path):
        written = write_claudeclaw_recommendations([], tmp_path)
        assert written == []

    def test_all_target_types(self, tmp_path: Path):
        recs = [
            _make_rec("preference"),
            _make_rec("routing"),
            _make_rec("skill"),
            _make_rec("schedule"),
        ]
        written = write_claudeclaw_recommendations(recs, tmp_path)
        assert len(written) == 4

        targets = {json.loads(f.read_text())["target_system"] for f in written}
        assert targets == {"preference", "routing", "skill", "schedule"}

    def test_creates_output_dir(self, tmp_path: Path):
        out_dir = tmp_path / "nested" / "deep" / "dir"
        recs = [_make_rec("preference")]
        written = write_claudeclaw_recommendations(recs, out_dir)
        assert len(written) == 1
        assert out_dir.exists()

    def test_json_format(self, tmp_path: Path):
        recs = [_make_rec("skill", title="Improve l5-sprint", evidence="Low usage")]
        written = write_claudeclaw_recommendations(recs, tmp_path)

        data = json.loads(written[0].read_text())
        assert "created_at" in data
        assert data["evidence"] == "Low usage"
        assert data["recommendation_type"] == "other"
