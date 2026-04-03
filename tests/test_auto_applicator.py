"""Tests for the auto_applicator module."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from sky_lynx.auto_applicator import (
    APPEND_MARKER,
    AutoApplyResult,
    CooldownState,
    MAX_AUTO_CHANGES_PER_WEEK,
    _extract_existing_rules,
    _insert_before_marker,
    _insert_into_subsection,
    apply_rule,
    auto_apply_recommendations,
    check_cooldown,
    create_backup,
    detect_subsection,
    format_rule_for_claude_md,
    is_auto_eligible,
    record_audit,
    rollback,
    update_cooldown,
    validate_rule_text,
)
from sky_lynx.claude_client import Recommendation

# --- Fixtures ---

MOCK_CLAUDE_MD = """\
# CLAUDE.md

## Learned Rules & Patterns

Rules from real incidents.

### Architecture
- All DB access goes through Repository singleton

### Environment
- Load `~/.env.shared` first

### Testing
- Set `asyncio_mode = "auto"` in pyproject.toml

### Build
- Run `npm run build` before using MCP servers

### Git
- Never commit directly to `main`

### Security
- Audit live `.env` files

<!-- New rules will be appended below this line -->

<!--

-->
"""


def _make_rec(**overrides: object) -> Recommendation:
    """Create a recommendation with sensible defaults for auto-apply eligibility."""
    defaults = {
        "title": "Test Rule",
        "priority": "high",
        "evidence": "Observed multiple times in weekly friction analysis across sessions",
        "suggested_change": "Always check lock files before diagnosing broken pipelines",
        "impact": "Reduces debugging time",
        "reversibility": "high",
        "target_system": "claude_md",
        "recommendation_type": "claude_md_update",
    }
    defaults.update(overrides)
    return Recommendation(**defaults)  # type: ignore[arg-type]


# --- Eligibility Gate Tests ---


class TestIsAutoEligible:
    def test_fully_eligible(self) -> None:
        rec = _make_rec()
        eligible, reason = is_auto_eligible(rec, history_count=3)
        assert eligible is True
        assert reason == "eligible"

    def test_wrong_target_system(self) -> None:
        rec = _make_rec(target_system="pipeline")
        eligible, reason = is_auto_eligible(rec)
        assert eligible is False
        assert "target_system" in reason

    def test_wrong_recommendation_type(self) -> None:
        rec = _make_rec(recommendation_type="pipeline_change")
        eligible, reason = is_auto_eligible(rec)
        assert eligible is False
        assert "recommendation_type" in reason

    def test_low_reversibility(self) -> None:
        rec = _make_rec(reversibility="low")
        eligible, reason = is_auto_eligible(rec)
        assert eligible is False
        assert "reversibility" in reason

    def test_medium_priority(self) -> None:
        rec = _make_rec(priority="medium")
        eligible, reason = is_auto_eligible(rec)
        assert eligible is False
        assert "priority" in reason

    def test_short_suggested_change(self) -> None:
        rec = _make_rec(suggested_change="too short")
        eligible, reason = is_auto_eligible(rec)
        assert eligible is False
        assert "too short" in reason

    def test_low_history_weak_evidence(self) -> None:
        rec = _make_rec(evidence="brief", suggested_change="A change that is long enough to pass the length check")
        eligible, reason = is_auto_eligible(rec, history_count=0)
        assert eligible is False
        assert "insufficient history" in reason

    def test_low_history_strong_evidence_passes(self) -> None:
        rec = _make_rec(
            evidence="Observed in 5 sessions over 3 weeks with consistent friction patterns reported",
        )
        eligible, _ = is_auto_eligible(rec, history_count=0)
        assert eligible is True


# --- Cooldown Tests ---


class TestCooldown:
    def test_fresh_state_allows(self, tmp_path: Path) -> None:
        with patch("sky_lynx.auto_applicator.STATE_DIR", tmp_path):
            can, remaining = check_cooldown()
            assert can is True
            assert remaining == MAX_AUTO_CHANGES_PER_WEEK

    def test_budget_exhausted(self, tmp_path: Path) -> None:
        cooldown_path = tmp_path / "cooldown.json"
        with patch("sky_lynx.auto_applicator.STATE_DIR", tmp_path), \
             patch("sky_lynx.auto_applicator._current_iso_week", return_value="2026-W09"):
            state = CooldownState(
                changes_this_week=MAX_AUTO_CHANGES_PER_WEEK,
                week_iso="2026-W09",
                applied_titles=[f"t{i}" for i in range(MAX_AUTO_CHANGES_PER_WEEK)],
            )
            cooldown_path.write_text(state.model_dump_json())

            can, remaining = check_cooldown()
            assert can is False
            assert remaining == 0

    def test_week_rollover_resets(self, tmp_path: Path) -> None:
        cooldown_path = tmp_path / "cooldown.json"
        with patch("sky_lynx.auto_applicator.STATE_DIR", tmp_path), \
             patch("sky_lynx.auto_applicator._current_iso_week", return_value="2026-W10"):
            state = CooldownState(
                changes_this_week=MAX_AUTO_CHANGES_PER_WEEK,
                week_iso="2026-W09",
                applied_titles=[f"t{i}" for i in range(MAX_AUTO_CHANGES_PER_WEEK)],
            )
            cooldown_path.write_text(state.model_dump_json())

            can, remaining = check_cooldown()
            assert can is True
            assert remaining == MAX_AUTO_CHANGES_PER_WEEK


# --- Validation Tests ---


class TestValidateRuleText:
    def test_valid_rule(self) -> None:
        valid, reason = validate_rule_text(
            "- Always run tests before committing changes to avoid regressions",
            [],
        )
        assert valid is True
        assert reason == "valid"

    def test_too_short(self) -> None:
        valid, reason = validate_rule_text("- Short", [])
        assert valid is False
        assert "too short" in reason

    def test_too_long(self) -> None:
        valid, reason = validate_rule_text("- " + "x" * 500, [])
        assert valid is False
        assert "too long" in reason

    def test_missing_bullet_prefix(self) -> None:
        valid, reason = validate_rule_text(
            "Always run tests before committing to the repo",
            [],
        )
        assert valid is False
        assert "start with" in reason

    def test_unclosed_backticks(self) -> None:
        valid, reason = validate_rule_text(
            "- Always run `npm run build before using MCP servers",
            [],
        )
        assert valid is False
        assert "backtick" in reason

    def test_dangerous_pattern_rm_rf(self) -> None:
        valid, reason = validate_rule_text(
            "- Clean up temp files with rm -rf /tmp/sky-lynx-*",
            [],
        )
        assert valid is False
        assert "dangerous" in reason

    def test_duplicate_detection(self) -> None:
        existing = ["- Always run tests before committing changes to avoid regressions"]
        valid, reason = validate_rule_text(
            "- Always run tests before committing changes to avoid regressions",
            existing,
        )
        assert valid is False
        assert "duplicate" in reason


# --- Formatting and Subsection Detection ---


class TestFormatAndDetect:
    def test_format_rule(self) -> None:
        rec = _make_rec(
            suggested_change="Always verify lock files before debugging",
            evidence="Lock files caused 3 false-alarm investigations",
        )
        rule = format_rule_for_claude_md(rec)
        assert rule.startswith("- ")
        assert "Always verify lock files" in rule
        assert "Lock files caused" in rule

    def test_format_strips_existing_bullet(self) -> None:
        rec = _make_rec(suggested_change="- Already has bullet prefix and is long enough")
        rule = format_rule_for_claude_md(rec)
        assert not rule.startswith("- - ")
        assert rule.startswith("- ")

    def test_detect_testing_subsection(self) -> None:
        rec = _make_rec(
            title="Add pytest fixture guidance",
            suggested_change="Always use pytest fixtures for DB setup in integration tests",
            evidence="Tests fail when using manual setup",
        )
        result = detect_subsection(rec, MOCK_CLAUDE_MD)
        assert result == "Testing"

    def test_detect_build_subsection(self) -> None:
        rec = _make_rec(
            title="npm build order",
            suggested_change="Run npm run build before running npm test in CI",
            evidence="Build artifacts missing in CI pipeline",
        )
        result = detect_subsection(rec, MOCK_CLAUDE_MD)
        assert result == "Build"

    def test_detect_returns_none_for_unmatched(self) -> None:
        rec = _make_rec(
            title="Misc advice",
            suggested_change="Something completely unrelated to any category zzzzzz",
            evidence="No matching keywords zzzzzz",
        )
        result = detect_subsection(rec, MOCK_CLAUDE_MD)
        assert result is None


# --- Apply + Verify Tests ---


class TestApplyRule:
    def test_apply_to_subsection(self, tmp_path: Path) -> None:
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(MOCK_CLAUDE_MD)

        rule = "- New architecture rule for testing insertion"
        success = apply_rule(rule, "Architecture", claude_md)

        assert success is True
        content = claude_md.read_text()
        assert rule in content
        assert APPEND_MARKER in content

        # Verify it's between Architecture header and Environment header
        arch_idx = content.index("### Architecture")
        env_idx = content.index("### Environment")
        rule_idx = content.index(rule)
        assert arch_idx < rule_idx < env_idx

    def test_apply_before_marker(self, tmp_path: Path) -> None:
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(MOCK_CLAUDE_MD)

        rule = "- Fallback rule with no subsection match"
        success = apply_rule(rule, None, claude_md)

        assert success is True
        content = claude_md.read_text()
        assert rule in content
        assert content.index(rule) < content.index(APPEND_MARKER)

    def test_apply_fails_without_marker(self, tmp_path: Path) -> None:
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# CLAUDE.md\n\nNo marker here.\n")

        success = apply_rule("- Some rule text for testing", None, claude_md)
        assert success is False


# --- Full Cycle: Apply then Rollback ---


class TestFullCycle:
    def test_apply_and_rollback(self, tmp_path: Path) -> None:
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(MOCK_CLAUDE_MD)
        original_content = MOCK_CLAUDE_MD

        state_dir = tmp_path / ".sky-lynx"

        with patch("sky_lynx.auto_applicator.STATE_DIR", state_dir):
            # Create backup and apply
            backup = create_backup(claude_md)
            assert backup.exists()

            rule = "- Full cycle test rule that should be rolled back"
            apply_rule(rule, None, claude_md)
            assert rule in claude_md.read_text()

            # Rollback
            success = rollback("latest", claude_md)
            assert success is True
            assert claude_md.read_text() == original_content


# --- Dry Run Test ---


class TestDryRun:
    def test_dry_run_no_writes(self, tmp_path: Path) -> None:
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(MOCK_CLAUDE_MD)
        original_content = MOCK_CLAUDE_MD

        state_dir = tmp_path / ".sky-lynx"

        rec = _make_rec()

        with patch("sky_lynx.auto_applicator.STATE_DIR", state_dir), \
             patch("sky_lynx.auto_applicator._current_iso_week", return_value="2026-W09"):
            results = auto_apply_recommendations(
                [rec],
                session_id="test-dry",
                dry_run=True,
                claude_md_path=claude_md,
            )

        assert len(results) == 1
        assert results[0].applied is False
        assert "dry run" in results[0].reason
        # File unchanged
        assert claude_md.read_text() == original_content


# --- Orchestrator Integration Test ---


class TestAutoApplyOrchestrator:
    def test_applies_eligible_rec(self, tmp_path: Path) -> None:
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(MOCK_CLAUDE_MD)
        state_dir = tmp_path / ".sky-lynx"

        rec = _make_rec(
            title="Lock file check",
            suggested_change="Always check for stale lock files at /tmp/*.lock before diagnosing pipeline issues",
            evidence="Lock files from crashed processes caused 3 false-alarm pipeline investigations this month",
        )

        with patch("sky_lynx.auto_applicator.STATE_DIR", state_dir), \
             patch("sky_lynx.auto_applicator._current_iso_week", return_value="2026-W09"):
            results = auto_apply_recommendations(
                [rec],
                session_id="test-session",
                dry_run=False,
                claude_md_path=claude_md,
            )

        assert len(results) == 1
        assert results[0].applied is True
        assert results[0].rule_text in claude_md.read_text()

        # Backup created
        backups = list((state_dir / "backups").glob("CLAUDE.md.*"))
        assert len(backups) == 1

        # Audit recorded
        audit_path = state_dir / "audit.jsonl"
        assert audit_path.exists()
        audit_entry = json.loads(audit_path.read_text().strip())
        assert audit_entry["applied"] is True
        assert audit_entry["session_id"] == "test-session"

        # Cooldown updated
        cooldown = json.loads((state_dir / "cooldown.json").read_text())
        assert cooldown["changes_this_week"] == 1

    def test_skips_ineligible_rec(self, tmp_path: Path) -> None:
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(MOCK_CLAUDE_MD)
        state_dir = tmp_path / ".sky-lynx"

        rec = _make_rec(priority="low")

        with patch("sky_lynx.auto_applicator.STATE_DIR", state_dir):
            results = auto_apply_recommendations(
                [rec],
                session_id="test-skip",
                claude_md_path=claude_md,
            )

        assert len(results) == 1
        assert results[0].applied is False
        # File unchanged
        assert claude_md.read_text() == MOCK_CLAUDE_MD


# --- Audit Trail Test ---


class TestAudit:
    def test_record_audit(self, tmp_path: Path) -> None:
        with patch("sky_lynx.auto_applicator.STATE_DIR", tmp_path):
            result = AutoApplyResult(
                title="Test", applied=True, reason="ok", rule_text="- Test rule"
            )
            record_audit(result, "session-1")

            audit_path = tmp_path / "audit.jsonl"
            assert audit_path.exists()
            entry = json.loads(audit_path.read_text().strip())
            assert entry["title"] == "Test"
            assert entry["session_id"] == "session-1"


# --- Helper Tests ---


class TestExtractExistingRules:
    def test_extracts_rules(self) -> None:
        rules = _extract_existing_rules(MOCK_CLAUDE_MD)
        assert len(rules) == 6
        assert any("Repository singleton" in r for r in rules)

    def test_empty_content(self) -> None:
        rules = _extract_existing_rules("# Nothing here\n")
        assert rules == []


class TestInsertHelpers:
    def test_insert_before_marker(self) -> None:
        content = f"some content\n\n{APPEND_MARKER}\n"
        result = _insert_before_marker(content, "- new rule")
        assert result is not None
        assert "- new rule" in result
        assert result.index("- new rule") < result.index(APPEND_MARKER)

    def test_insert_into_subsection(self) -> None:
        result = _insert_into_subsection(MOCK_CLAUDE_MD, "- new git rule", "Git")
        assert result is not None
        assert "- new git rule" in result
        # Should be between Git and Security
        git_idx = result.index("### Git")
        sec_idx = result.index("### Security")
        rule_idx = result.index("- new git rule")
        assert git_idx < rule_idx < sec_idx
