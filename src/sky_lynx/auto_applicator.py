"""Autonomous CLAUDE.md rule applicator for Sky-Lynx.

Applies high-confidence, low-risk recommendations directly to ~/CLAUDE.md
without creating a PR. Keeps audit trail, backups, and cooldown state.
"""

import json
import logging
import shutil
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path

from pydantic import BaseModel

from .claude_client import Recommendation

logger = logging.getLogger(__name__)

# --- Constants ---

CLAUDE_MD_PATH = Path.home() / "CLAUDE.md"
STATE_DIR = Path.home() / ".sky-lynx"
MAX_AUTO_CHANGES_PER_WEEK = 5
APPEND_MARKER = "<!-- New rules will be appended below this line -->"

ALLOWED_TYPES = {
    "claude_md_update",
    "constraint_addition",
    "case_study_addition",
    "framework_refinement",
}

# Patterns that must never appear in auto-applied rules
DANGEROUS_PATTERNS = [
    "rm -rf",
    "sudo ",
    "--no-verify",
    "--force",
    "password",
    "secret",
    "token=",
    "api_key=",
    "credentials",
]

# Keywords for routing rules to subsections under "Learned Rules & Patterns"
SUBSECTION_KEYWORDS: dict[str, list[str]] = {
    "Architecture": [
        "db access", "repository", "singleton", "state_machine", "transition",
        "import", "sys.path", "pipeline", "handler", "middleware", "pattern",
    ],
    "Environment": [
        "env", ".env", "path", "home", "cron", "variable", "config",
    ],
    "Testing": [
        "test", "pytest", "asyncio", "fixture", "mock", "assert", "coverage",
    ],
    "Build": [
        "build", "npm", "compile", "dist", "lock", "deploy", "ci", "cd",
    ],
    "Git": [
        "git", "commit", "branch", "merge", "push", "pull", "rebase", "pr",
    ],
    "Security": [
        "security", "auth", "credential", "secret", "permission", "audit",
        "vulnerability", "xss", "injection",
    ],
}

DUPLICATE_THRESHOLD = 0.8


# --- Data Models ---


class AutoApplyResult(BaseModel):
    """Result of attempting to auto-apply a single recommendation."""

    title: str
    applied: bool
    reason: str
    rule_text: str = ""
    backup_path: str = ""


class CooldownState(BaseModel):
    """Tracks auto-apply budget per ISO week."""

    changes_this_week: int = 0
    week_iso: str = ""  # e.g. "2026-W09"
    last_applied_at: str = ""
    applied_titles: list[str] = []


# --- Functions ---


def _current_iso_week() -> str:
    """Return current ISO week string like '2026-W09'."""
    now = datetime.now(UTC)
    return f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"


def is_auto_eligible(
    rec: Recommendation,
    history_count: int = 0,
) -> tuple[bool, str]:
    """Check whether a recommendation qualifies for auto-application.

    Args:
        rec: The recommendation to evaluate.
        history_count: How many times similar evidence has appeared before.

    Returns:
        (eligible, reason) tuple.
    """
    if rec.target_system != "claude_md":
        return False, f"target_system is '{rec.target_system}', not 'claude_md'"

    if rec.recommendation_type not in ALLOWED_TYPES:
        return False, f"recommendation_type '{rec.recommendation_type}' not in allowed types"

    if rec.reversibility != "high":
        return False, f"reversibility is '{rec.reversibility}', not 'high'"

    if rec.priority != "high":
        return False, f"priority is '{rec.priority}', not 'high'"

    if not rec.suggested_change or len(rec.suggested_change) < 20:
        return False, "suggested_change is missing or too short (< 20 chars)"

    if history_count < 2 and not _has_strong_evidence(rec):
        return False, "insufficient history (< 2) and evidence not strong enough"

    return True, "eligible"


def _has_strong_evidence(rec: Recommendation) -> bool:
    """Heuristic: evidence is 'strong' if it's detailed enough."""
    return bool(rec.evidence) and len(rec.evidence) >= 40


def check_cooldown() -> tuple[bool, int]:
    """Check whether the cooldown budget allows another auto-apply.

    Returns:
        (can_apply, budget_remaining) tuple.
    """
    state = _load_cooldown_state()
    current_week = _current_iso_week()

    if state.week_iso != current_week:
        # New week — reset
        return True, MAX_AUTO_CHANGES_PER_WEEK

    remaining = MAX_AUTO_CHANGES_PER_WEEK - state.changes_this_week
    return remaining > 0, remaining


def _load_cooldown_state() -> CooldownState:
    """Load cooldown state from disk."""
    cooldown_path = STATE_DIR / "cooldown.json"
    if not cooldown_path.exists():
        return CooldownState()
    try:
        data = json.loads(cooldown_path.read_text())
        return CooldownState(**data)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Corrupted cooldown.json, resetting")
        return CooldownState()


def _save_cooldown_state(state: CooldownState) -> None:
    """Persist cooldown state to disk."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cooldown_path = STATE_DIR / "cooldown.json"
    cooldown_path.write_text(state.model_dump_json(indent=2))


def validate_rule_text(text: str, existing_rules: list[str]) -> tuple[bool, str]:
    """Validate a rule before applying it.

    Checks:
        - Length 20-500 chars
        - Starts with '- '
        - No unclosed backticks
        - No dangerous patterns
        - Not a duplicate of existing rules

    Args:
        text: The formatted rule text.
        existing_rules: List of existing rule lines from CLAUDE.md.

    Returns:
        (valid, reason) tuple.
    """
    if len(text) < 20:
        return False, f"rule too short ({len(text)} chars, min 20)"

    if len(text) > 500:
        return False, f"rule too long ({len(text)} chars, max 500)"

    if not text.startswith("- "):
        return False, "rule must start with '- '"

    # Check for unclosed backticks
    backtick_count = text.count("`")
    if backtick_count % 2 != 0:
        return False, "rule has unclosed backticks"

    # Check for dangerous patterns
    text_lower = text.lower()
    for pattern in DANGEROUS_PATTERNS:
        if pattern.lower() in text_lower:
            return False, f"rule contains dangerous pattern: '{pattern}'"

    # Fuzzy dedup against existing rules
    for existing in existing_rules:
        ratio = SequenceMatcher(None, text.lower(), existing.lower()).ratio()
        if ratio > DUPLICATE_THRESHOLD:
            return False, f"duplicate of existing rule (similarity {ratio:.0%})"

    return True, "valid"


def format_rule_for_claude_md(rec: Recommendation) -> str:
    """Convert a recommendation's suggested_change into bullet format.

    Args:
        rec: Recommendation with suggested_change and evidence.

    Returns:
        Formatted rule string like '- {change} -- {evidence_summary}'.
    """
    change = rec.suggested_change.strip()

    # Ensure the change text doesn't already start with '- '
    if change.startswith("- "):
        change = change[2:]

    # Build evidence summary (first sentence or truncated)
    evidence_summary = ""
    if rec.evidence:
        first_sentence = rec.evidence.split(".")[0].strip()
        if len(first_sentence) > 80:
            first_sentence = first_sentence[:77] + "..."
        evidence_summary = first_sentence

    rule = f"- {change}"
    if evidence_summary:
        rule += f" — {evidence_summary}"

    return rule


def detect_subsection(rec: Recommendation, existing_content: str) -> str | None:
    """Determine which subsection under 'Learned Rules & Patterns' to insert into.

    Args:
        rec: The recommendation.
        existing_content: Current CLAUDE.md content.

    Returns:
        Subsection name (e.g. 'Architecture') or None if no match.
    """
    # Build search text from the recommendation
    search_text = (
        f"{rec.title} {rec.suggested_change} {rec.evidence}"
    ).lower()

    best_section = None
    best_score = 0

    for section, keywords in SUBSECTION_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in search_text)
        if score > best_score:
            best_score = score
            best_section = section

    # Only return if there's a meaningful match and the subsection exists
    if best_section and best_score >= 1 and f"### {best_section}" in existing_content:
        return best_section

    return None


def create_backup(claude_md_path: Path | None = None) -> Path:
    """Create a timestamped backup of CLAUDE.md.

    Also prunes backups older than 12 weeks.

    Args:
        claude_md_path: Path to CLAUDE.md (defaults to CLAUDE_MD_PATH).

    Returns:
        Path to the backup file.
    """
    claude_md_path = claude_md_path or CLAUDE_MD_PATH
    backup_dir = STATE_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"CLAUDE.md.{timestamp}"
    shutil.copy2(claude_md_path, backup_path)
    logger.info(f"Backup created: {backup_path}")

    # Prune old backups (> 12 weeks = 84 days)
    _prune_old_backups(backup_dir, max_age_days=84)

    return backup_path


def _prune_old_backups(backup_dir: Path, max_age_days: int = 84) -> None:
    """Remove backups older than max_age_days."""
    now = datetime.now(UTC)
    for backup_file in backup_dir.glob("CLAUDE.md.*"):
        try:
            # Parse timestamp from filename
            ts_str = backup_file.name.replace("CLAUDE.md.", "")
            file_time = datetime.strptime(ts_str, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=UTC
            )
            age_days = (now - file_time).days
            if age_days > max_age_days:
                backup_file.unlink()
                logger.info(f"Pruned old backup: {backup_file}")
        except (ValueError, OSError):
            continue


def apply_rule(
    rule_text: str,
    subsection: str | None,
    claude_md_path: Path | None = None,
) -> bool:
    """Insert a rule into CLAUDE.md at the appropriate location.

    If subsection is given, appends at end of that subsection.
    Otherwise, inserts before the APPEND_MARKER.

    Post-write: re-reads file to verify insertion and marker integrity.

    Args:
        rule_text: The formatted rule line.
        subsection: Target subsection name (e.g. 'Architecture') or None.
        claude_md_path: Path to CLAUDE.md (defaults to CLAUDE_MD_PATH).

    Returns:
        True if successfully applied and verified.
    """
    claude_md_path = claude_md_path or CLAUDE_MD_PATH
    content = claude_md_path.read_text()

    if subsection:
        new_content = _insert_into_subsection(content, rule_text, subsection)
    else:
        new_content = _insert_before_marker(content, rule_text)

    if new_content is None:
        logger.error("Failed to determine insertion point")
        return False

    claude_md_path.write_text(new_content)

    # Post-write verification
    verification = claude_md_path.read_text()
    if rule_text not in verification:
        logger.error("Post-write verification failed: rule not found in file")
        return False

    if APPEND_MARKER not in verification:
        logger.error("Post-write verification failed: append marker missing")
        return False

    target = f"subsection {subsection}" if subsection else "default location"
    logger.info(f"Rule applied successfully to {target}")
    return True


def _insert_into_subsection(content: str, rule_text: str, subsection: str) -> str | None:
    """Insert rule at end of a ### subsection under Learned Rules & Patterns."""
    header = f"### {subsection}"
    lines = content.split("\n")
    insert_idx = None
    in_section = False

    for i, line in enumerate(lines):
        if line.strip() == header:
            in_section = True
            continue
        if in_section and (
            line.startswith("### ") or line.startswith("## ") or line.strip() == APPEND_MARKER
        ):
                insert_idx = i
                break

    if insert_idx is None and in_section:
        # Subsection goes to the end of file — insert at end
        insert_idx = len(lines)

    if insert_idx is None:
        return None

    lines.insert(insert_idx, rule_text)
    return "\n".join(lines)


def _insert_before_marker(content: str, rule_text: str) -> str | None:
    """Insert rule just before the APPEND_MARKER."""
    if APPEND_MARKER not in content:
        return None

    # Insert before the marker, with a blank line if needed
    return content.replace(
        APPEND_MARKER,
        f"{rule_text}\n\n{APPEND_MARKER}",
    )


def record_audit(
    result: AutoApplyResult,
    session_id: str,
) -> None:
    """Append an audit entry to the JSONL audit trail.

    Args:
        result: The auto-apply result.
        session_id: Analysis session identifier.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    audit_path = STATE_DIR / "audit.jsonl"

    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "session_id": session_id,
        "title": result.title,
        "applied": result.applied,
        "reason": result.reason,
        "rule_text": result.rule_text,
        "backup_path": result.backup_path,
    }

    with open(audit_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def update_cooldown(title: str) -> None:
    """Increment the cooldown counter and record the applied title.

    Args:
        title: Title of the applied recommendation.
    """
    state = _load_cooldown_state()
    current_week = _current_iso_week()

    if state.week_iso != current_week:
        state = CooldownState(
            week_iso=current_week,
            changes_this_week=0,
            applied_titles=[],
        )

    state.changes_this_week += 1
    state.last_applied_at = datetime.now(UTC).isoformat()
    state.applied_titles.append(title)
    _save_cooldown_state(state)


def rollback(backup_path_or_latest: str = "latest", claude_md_path: Path | None = None) -> bool:
    """Restore CLAUDE.md from a backup.

    Args:
        backup_path_or_latest: Specific backup path, or 'latest' for most recent.
        claude_md_path: Path to CLAUDE.md (defaults to CLAUDE_MD_PATH).

    Returns:
        True if rollback succeeded.
    """
    claude_md_path = claude_md_path or CLAUDE_MD_PATH
    backup_dir = STATE_DIR / "backups"

    if backup_path_or_latest == "latest":
        backups = sorted(backup_dir.glob("CLAUDE.md.*"))
        if not backups:
            logger.error("No backups found for rollback")
            return False
        backup_path = backups[-1]
    else:
        backup_path = Path(backup_path_or_latest)

    if not backup_path.exists():
        logger.error(f"Backup not found: {backup_path}")
        return False

    shutil.copy2(backup_path, claude_md_path)
    logger.info(f"Rolled back CLAUDE.md from {backup_path}")

    # Audit the rollback
    record_audit(
        AutoApplyResult(
            title="[ROLLBACK]",
            applied=True,
            reason=f"Restored from {backup_path.name}",
            backup_path=str(backup_path),
        ),
        session_id="rollback",
    )

    return True


def _extract_existing_rules(content: str) -> list[str]:
    """Extract all bullet-point rules from the Learned Rules & Patterns section."""
    rules: list[str] = []
    in_section = False

    for line in content.split("\n"):
        if "## Learned Rules & Patterns" in line:
            in_section = True
            continue
        if in_section:
            if line.startswith("## ") and "Learned Rules" not in line:
                break
            if line.startswith("- "):
                rules.append(line.strip())

    return rules


def auto_apply_recommendations(
    recommendations: list[Recommendation],
    session_id: str,
    dry_run: bool = False,
    claude_md_path: Path | None = None,
) -> list[AutoApplyResult]:
    """Top-level orchestrator: evaluate and apply eligible recommendations.

    Args:
        recommendations: List of recommendations from analysis.
        session_id: Analysis session identifier.
        dry_run: If True, evaluate but don't write files.
        claude_md_path: Path to CLAUDE.md (defaults to CLAUDE_MD_PATH).

    Returns:
        List of AutoApplyResult for each recommendation evaluated.
    """
    claude_md_path = claude_md_path or CLAUDE_MD_PATH
    results: list[AutoApplyResult] = []
    backup_created = False
    backup_path_str = ""

    if not claude_md_path.exists():
        logger.error(f"CLAUDE.md not found at {claude_md_path}")
        return results

    content = claude_md_path.read_text()
    existing_rules = _extract_existing_rules(content)

    for rec in recommendations:
        # Gate 1: eligibility
        eligible, reason = is_auto_eligible(rec)
        if not eligible:
            results.append(AutoApplyResult(
                title=rec.title, applied=False, reason=reason,
            ))
            logger.debug(f"Skipped '{rec.title}': {reason}")
            continue

        # Gate 2: cooldown
        can_apply, remaining = check_cooldown()
        if not can_apply:
            results.append(AutoApplyResult(
                title=rec.title, applied=False,
                reason=f"cooldown budget exhausted ({remaining} remaining)",
            ))
            logger.info(f"Skipped '{rec.title}': cooldown budget exhausted")
            continue

        # Gate 3: format and validate
        rule_text = format_rule_for_claude_md(rec)
        valid, val_reason = validate_rule_text(rule_text, existing_rules)
        if not valid:
            results.append(AutoApplyResult(
                title=rec.title, applied=False, reason=f"validation failed: {val_reason}",
                rule_text=rule_text,
            ))
            logger.info(f"Skipped '{rec.title}': {val_reason}")
            continue

        # Detect subsection
        subsection = detect_subsection(rec, content)

        if dry_run:
            target = f"subsection '{subsection}'" if subsection else "before append marker"
            results.append(AutoApplyResult(
                title=rec.title, applied=False,
                reason=f"dry run — would apply to {target}",
                rule_text=rule_text,
            ))
            logger.info(f"[DRY RUN] Would apply '{rec.title}' to {target}: {rule_text}")
            continue

        # Backup (once per session)
        if not backup_created:
            bp = create_backup(claude_md_path)
            backup_path_str = str(bp)
            backup_created = True

        # Apply
        success = apply_rule(rule_text, subsection, claude_md_path)
        if success:
            result = AutoApplyResult(
                title=rec.title, applied=True, reason="auto-applied",
                rule_text=rule_text, backup_path=backup_path_str,
            )
            update_cooldown(rec.title)
            # Refresh content and existing rules for subsequent iterations
            content = claude_md_path.read_text()
            existing_rules = _extract_existing_rules(content)
        else:
            result = AutoApplyResult(
                title=rec.title, applied=False, reason="apply_rule failed",
                rule_text=rule_text, backup_path=backup_path_str,
            )

        record_audit(result, session_id)
        results.append(result)

    # Summary log
    applied_count = sum(1 for r in results if r.applied)
    total = len(results)
    logger.info(f"Auto-apply complete: {applied_count}/{total} recommendations applied")

    return results
