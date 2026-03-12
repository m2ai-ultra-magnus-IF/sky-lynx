"""Tests for the Linear issue writer module."""

from unittest.mock import MagicMock, patch

import pytest

from sky_lynx.claude_client import Recommendation
from sky_lynx.linear_writer import (
    PRIORITY_MAP,
    _format_issue_body,
    create_linear_issues,
)


# --- Fixtures ---


def _make_rec(**overrides) -> Recommendation:
    """Create a test Recommendation with defaults."""
    defaults = {
        "title": "Reduce friction in testing",
        "priority": "high",
        "evidence": "Testing friction increased 20%",
        "suggested_change": "Add pytest fixtures for common patterns",
        "impact": "Reduce testing friction by 30%",
        "reversibility": "high",
        "target_system": "claude_md",
        "recommendation_type": "claude_md_update",
        "recommendation_id": "sl-test-001",
    }
    defaults.update(overrides)
    return Recommendation(**defaults)


# --- Tests ---


class TestCreateLinearIssues:
    """Test the create_linear_issues function."""

    def test_empty_recommendations_returns_empty(self):
        result = create_linear_issues([], session_id="test-session")
        assert result == []

    @patch.dict("os.environ", {"ARCADE_API_KEY": "", "SKYLYNX_LINEAR_TEAM": "M2AI"})
    def test_no_api_key_returns_empty(self):
        recs = [_make_rec()]
        result = create_linear_issues(recs, session_id="test")
        assert result == []

    @patch.dict(
        "os.environ",
        {"ARCADE_API_KEY": "arc_test", "SKYLYNX_LINEAR_TEAM": ""},
        clear=False,
    )
    def test_no_team_returns_empty(self):
        recs = [_make_rec()]
        with patch("sky_lynx.linear_writer.Arcade"):
            result = create_linear_issues(recs, session_id="test")
        assert result == []

    @patch.dict(
        "os.environ",
        {
            "ARCADE_API_KEY": "arc_test",
            "ARCADE_USER_ID": "agent@local",
            "SKYLYNX_LINEAR_TEAM": "M2AI",
            "SKYLYNX_LINEAR_LABEL": "sky-lynx",
        },
    )
    def test_dry_run_returns_ids_without_api_call(self):
        recs = [_make_rec(), _make_rec(title="Second rec", recommendation_id="sl-002")]
        with patch("sky_lynx.linear_writer.Arcade"):
            result = create_linear_issues(recs, session_id="test", dry_run=True)
        assert len(result) == 2
        assert all(r.startswith("DRY-") for r in result)

    @patch.dict(
        "os.environ",
        {
            "ARCADE_API_KEY": "arc_test",
            "ARCADE_USER_ID": "agent@local",
            "SKYLYNX_LINEAR_TEAM": "M2AI",
            "SKYLYNX_LINEAR_LABEL": "sky-lynx",
        },
    )
    def test_creates_issues_via_arcade(self):
        recs = [_make_rec()]
        mock_client = MagicMock()
        mock_output = MagicMock()
        mock_output.output.value = {"issue": {"identifier": "M2A-42"}}
        mock_client.tools.execute.return_value = mock_output

        with patch("sky_lynx.linear_writer.Arcade", return_value=mock_client):
            result = create_linear_issues(recs, session_id="test")

        assert result == ["M2A-42"]
        mock_client.tools.execute.assert_called_once()
        call_kwargs = mock_client.tools.execute.call_args
        assert call_kwargs.kwargs["tool_name"] == "Linear_CreateIssue"
        params = call_kwargs.kwargs["input"]
        assert params["title"] == "[Sky-Lynx] Reduce friction in testing"
        assert params["priority"] == 2  # high
        assert params["team"] == "M2AI"
        assert params["label"] == "sky-lynx"

    @patch.dict(
        "os.environ",
        {
            "ARCADE_API_KEY": "arc_test",
            "ARCADE_USER_ID": "agent@local",
            "SKYLYNX_LINEAR_TEAM": "M2AI",
        },
    )
    def test_handles_api_error_gracefully(self):
        recs = [_make_rec()]
        mock_client = MagicMock()
        mock_client.tools.execute.side_effect = Exception("API error")

        with patch("sky_lynx.linear_writer.Arcade", return_value=mock_client):
            result = create_linear_issues(recs, session_id="test")

        assert result == []

    @patch.dict(
        "os.environ",
        {
            "ARCADE_API_KEY": "arc_test",
            "ARCADE_USER_ID": "agent@local",
            "SKYLYNX_LINEAR_TEAM": "M2AI",
        },
    )
    def test_multiple_recs_partial_failure(self):
        """One succeeds, one fails — returns the successful one."""
        recs = [_make_rec(), _make_rec(title="Failing rec")]
        mock_client = MagicMock()

        good_output = MagicMock()
        good_output.output.value = {"issue": {"identifier": "M2A-10"}}
        bad_output = Exception("timeout")

        mock_client.tools.execute.side_effect = [good_output, bad_output]

        with patch("sky_lynx.linear_writer.Arcade", return_value=mock_client):
            result = create_linear_issues(recs, session_id="test")

        assert result == ["M2A-10"]

    def test_no_arcade_installed(self):
        recs = [_make_rec()]
        with patch("sky_lynx.linear_writer.Arcade", None):
            result = create_linear_issues(recs, session_id="test")
        assert result == []


class TestPriorityMap:
    """Test priority mapping."""

    def test_high_maps_to_2(self):
        assert PRIORITY_MAP["high"] == 2

    def test_medium_maps_to_3(self):
        assert PRIORITY_MAP["medium"] == 3

    def test_low_maps_to_4(self):
        assert PRIORITY_MAP["low"] == 4


class TestFormatIssueBody:
    """Test issue body formatting."""

    def test_includes_all_fields(self):
        rec = _make_rec()
        body = _format_issue_body(rec, "sky-lynx-2026-03-06")

        assert "sky-lynx-2026-03-06" in body
        assert "high" in body
        assert "claude_md_update" in body
        assert "Evidence" in body
        assert "Suggested Change" in body
        assert "Expected Impact" in body
        assert "sl-test-001" in body

    def test_handles_empty_fields(self):
        rec = _make_rec(evidence="", suggested_change="", impact="")
        body = _format_issue_body(rec, "test-session")

        assert "test-session" in body
        # Should not have Evidence/Suggested Change headers for empty fields
        assert "## Evidence" not in body
        assert "## Suggested Change" not in body

    def test_recommendation_id_in_footer(self):
        rec = _make_rec(recommendation_id="sl-custom-id")
        body = _format_issue_body(rec, "test")
        assert "sl-custom-id" in body
