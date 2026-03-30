"""Tests for analyzer integration features (10a/10b)."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sky_lynx.analyzer import _run_persona_upgrader


class TestRunPersonaUpgrader:
    """Test the persona upgrader subprocess invocation."""

    @patch("sky_lynx.analyzer.subprocess.run")
    def test_calls_upgrader_script(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        upgrader_path = (
            Path.home() / "projects" / "st-records" / "scripts" / "persona_upgrader.py"
        )

        if upgrader_path.exists():
            _run_persona_upgrader()
            mock_run.assert_called_once()
            args = mock_run.call_args
            assert "persona_upgrader.py" in str(args)
        else:
            # If st-records not present, should log warning and return
            _run_persona_upgrader()

    @patch("sky_lynx.analyzer.subprocess.run")
    def test_handles_nonzero_exit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="error details")
        upgrader_path = (
            Path.home() / "projects" / "st-records" / "scripts" / "persona_upgrader.py"
        )
        if upgrader_path.exists():
            # Should not raise
            _run_persona_upgrader()

    @patch("sky_lynx.analyzer.subprocess.run")
    def test_handles_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=120)
        upgrader_path = (
            Path.home() / "projects" / "st-records" / "scripts" / "persona_upgrader.py"
        )
        if upgrader_path.exists():
            # Should not raise
            _run_persona_upgrader()

    @patch("sky_lynx.analyzer.subprocess.run")
    def test_handles_missing_script(self, mock_run):
        with patch("sky_lynx.analyzer.Path.home") as mock_home:
            mock_home.return_value = Path("/nonexistent")
            _run_persona_upgrader()
            mock_run.assert_not_called()
