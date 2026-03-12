"""
Taste Profile Capture — mines ClaudeClaw conversation history for preference signals.

Runs every 2 weeks. Produces:
1. A taste delta report (what changed since last snapshot)
2. A timestamped snapshot in data/taste-snapshots/
3. Updated metadata in the active taste profile

Data sources:
- ClaudeClaw conversation_log: correction/rejection patterns in user messages
- ClaudeClaw christensen_log: idea rejections and overrides
- Hookify rules: new enforcement rules = new taste signals
- CLAUDE.md learned mistakes: new entries since last capture
"""

import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Paths
CLAUDECLAW_DB = Path.home() / "projects/claudeclaw/store/claudeclaw.db"
TASTE_PROFILE = Path.home() / ".claude/taste-profile.md"
SNAPSHOTS_DIR = Path(__file__).parent.parent.parent / "data" / "taste-snapshots"
HOOKIFY_DIR = Path.home() / ".claude"
PROJECT_HOOKIFY_DIRS = [
    Path.home() / "projects/ultra-magnus/.claude",
]

# Correction patterns to search in user messages
REJECTION_PATTERNS = [
    "not quite", "too %", "less %", "redo", "rewrite", "try again",
    "no,", "nope", "wrong", "that's not", "don't like", "change it",
    "fix this", "not what I", "too formal", "too casual", "too long",
    "too short", "too wordy", "too generic", "too vague",
    "simpler", "shorter", "tighter", "cleaner",
    "overcomplicat", "overthink", "just do", "just build",
    "don't need", "unnecessary", "overkill", "over-engineer",
    "skip the", "drop the", "lose the", "cut the", "remove the",
    "I said", "I already", "I told you", "already told",
]

# Map correction patterns to taste preferences
PREFERENCE_SIGNALS = {
    "enforce_it": [
        "remember to", "make sure to", "don't forget",
        "should have", "was supposed to", "ignored",
    ],
    "lead_with_verdict": [
        "too long", "too wordy", "get to the point", "just tell me",
        "skip the", "tldr", "bottom line",
    ],
    "name_the_pattern": [
        "what do you call", "name for", "term for",
    ],
    "concrete_over_abstract": [
        "too vague", "too generic", "be specific", "example",
        "like what", "such as",
    ],
    "kill_the_class": [
        "again", "keeps happening", "same issue", "every time",
        "still", "recurring",
    ],
    "earn_complexity": [
        "overcomplicat", "overthink", "simpler", "overkill",
        "over-engineer", "unnecessary", "don't need", "just do",
    ],
    "distrust_self_report": [
        "actually run", "did it actually", "verify", "check if",
        "are you sure", "prove it",
    ],
}


def get_last_capture_date() -> datetime | None:
    """Read the last capture date from the taste profile metadata."""
    if not TASTE_PROFILE.exists():
        return None
    content = TASTE_PROFILE.read_text()
    match = re.search(r"^# Last updated: (\d{4}-\d{2}-\d{2})", content, re.MULTILINE)
    if match:
        return datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return None


def mine_conversation_corrections(since_epoch: int | None = None) -> dict:
    """Search ClaudeClaw conversation_log for correction signals."""
    if not CLAUDECLAW_DB.exists():
        return {"total_corrections": 0, "by_preference": {}, "raw_matches": []}

    conn = sqlite3.connect(str(CLAUDECLAW_DB))
    cur = conn.cursor()

    results = {"total_corrections": 0, "by_preference": {}, "raw_matches": []}

    for pref_name, patterns in PREFERENCE_SIGNALS.items():
        count = 0
        for pattern in patterns:
            where = "WHERE role='user' AND content LIKE ?"
            params = [f"%{pattern}%"]
            if since_epoch:
                where += " AND created_at > ?"
                params.append(since_epoch)

            cur.execute(
                f"SELECT content, created_at FROM conversation_log {where} "
                f"ORDER BY created_at DESC LIMIT 5",
                params,
            )
            rows = cur.fetchall()
            count += len(rows)
            for content, ts in rows:
                results["raw_matches"].append({
                    "preference": pref_name,
                    "pattern": pattern,
                    "snippet": content[:200],
                    "timestamp": ts,
                })

        results["by_preference"][pref_name] = count
        results["total_corrections"] += count

    # Also get general rejection patterns
    general_count = 0
    for pattern in REJECTION_PATTERNS:
        where = "WHERE role='user' AND content LIKE ?"
        params = [f"%{pattern}%"]
        if since_epoch:
            where += " AND created_at > ?"
            params.append(since_epoch)

        cur.execute(f"SELECT COUNT(*) FROM conversation_log {where}", params)
        general_count += cur.fetchone()[0]

    results["general_rejection_signals"] = general_count

    conn.close()
    return results


def mine_christensen_log(since_epoch: int | None = None) -> dict:
    """Get idea rejections from Christensen filter."""
    if not CLAUDECLAW_DB.exists():
        return {"total": 0, "pass": 0, "fail": 0, "override": 0, "entries": []}

    conn = sqlite3.connect(str(CLAUDECLAW_DB))
    cur = conn.cursor()

    where = ""
    params = []
    if since_epoch:
        where = "WHERE created_at > ?"
        params = [since_epoch]

    cur.execute(
        f"SELECT idea, outcome, reasoning, created_at FROM christensen_log {where} "
        f"ORDER BY created_at DESC",
        params,
    )
    rows = cur.fetchall()

    results = {"total": len(rows), "pass": 0, "fail": 0, "override": 0, "entries": []}
    for idea, outcome, reasoning, ts in rows:
        results[outcome] = results.get(outcome, 0) + 1
        results["entries"].append({
            "idea": idea[:100],
            "outcome": outcome,
            "reasoning": reasoning[:200] if reasoning else "",
            "timestamp": ts,
        })

    conn.close()
    return results


STALENESS_THRESHOLD_DAYS = 30


def detect_stale_preferences() -> list[dict]:
    """Find preferences with 0 signals in the last STALENESS_THRESHOLD_DAYS days.

    Searches the full conversation history (not just since last capture) to find
    preferences that haven't triggered any correction signals recently.

    Returns:
        List of dicts with 'preference', 'last_signal_date', 'days_silent'.
    """
    if not CLAUDECLAW_DB.exists():
        return []

    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=STALENESS_THRESHOLD_DAYS)
    cutoff_epoch = int(cutoff.timestamp())

    conn = sqlite3.connect(str(CLAUDECLAW_DB))
    cur = conn.cursor()

    stale = []
    for pref_name, patterns in PREFERENCE_SIGNALS.items():
        # Check for any signals in the last 30 days
        recent_count = 0
        last_signal_ts = None

        for pattern in patterns:
            # Count recent signals
            cur.execute(
                "SELECT COUNT(*) FROM conversation_log "
                "WHERE role='user' AND content LIKE ? AND created_at > ?",
                [f"%{pattern}%", cutoff_epoch],
            )
            recent_count += cur.fetchone()[0]

            # Find most recent signal ever
            cur.execute(
                "SELECT MAX(created_at) FROM conversation_log "
                "WHERE role='user' AND content LIKE ?",
                [f"%{pattern}%"],
            )
            row = cur.fetchone()
            if row[0] is not None:
                ts = row[0]
                if last_signal_ts is None or ts > last_signal_ts:
                    last_signal_ts = ts

        if recent_count == 0:
            if last_signal_ts:
                last_date = datetime.fromtimestamp(last_signal_ts, tz=timezone.utc)
                days_silent = (datetime.now(timezone.utc) - last_date).days
            else:
                last_date = None
                days_silent = STALENESS_THRESHOLD_DAYS + 1  # never seen

            stale.append({
                "preference": pref_name,
                "last_signal_date": last_date.strftime("%Y-%m-%d") if last_date else "never",
                "days_silent": days_silent,
            })

    conn.close()
    return stale


def count_hookify_rules() -> dict:
    """Count current hookify rules across global and project dirs."""
    rules = {"global": [], "project": []}

    for f in HOOKIFY_DIR.glob("hookify.*.local.md"):
        rules["global"].append(f.name)

    for proj_dir in PROJECT_HOOKIFY_DIRS:
        if proj_dir.exists():
            for f in proj_dir.glob("hookify.*.local.md"):
                rules["project"].append(f"{proj_dir.parent.name}/{f.name}")

    return rules


def count_learned_mistakes() -> int:
    """Count entries in CLAUDE.md Learned Mistakes section."""
    claude_md = Path.home() / ".claude/CLAUDE.md"
    if not claude_md.exists():
        return 0

    content = claude_md.read_text()
    in_section = False
    count = 0
    for line in content.split("\n"):
        if "## Learned Mistakes" in line:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and line.strip().startswith("- "):
            count += 1

    return count


def generate_delta_report(
    corrections: dict,
    christensen: dict,
    hookify: dict,
    learned_mistakes: int,
    last_capture: str | None,
    stale_preferences: list[dict] | None = None,
) -> str:
    """Generate a human-readable taste delta report."""
    lines = [
        f"# Taste Capture Report — {datetime.now().strftime('%Y-%m-%d')}",
        f"Previous capture: {last_capture or 'None (first run)'}",
        "",
        "## Correction Signal Summary",
        f"Total correction-pattern matches: {corrections['total_corrections']}",
        f"General rejection signals: {corrections.get('general_rejection_signals', 0)}",
        "",
        "### Signals by Preference",
    ]

    for pref, count in sorted(
        corrections["by_preference"].items(), key=lambda x: x[1], reverse=True
    ):
        status = "ACTIVE" if count > 0 else "QUIET"
        lines.append(f"- {pref}: {count} signals [{status}]")

    lines.extend([
        "",
        "## Christensen Filter",
        f"Total evaluations: {christensen['total']}",
        f"  Pass: {christensen['pass']} | Fail: {christensen['fail']} | Override: {christensen['override']}",
    ])
    if christensen["entries"]:
        lines.append("")
        for entry in christensen["entries"][:5]:
            lines.append(f"- [{entry['outcome'].upper()}] {entry['idea']}")
            if entry["reasoning"]:
                lines.append(f"  Reason: {entry['reasoning']}")

    lines.extend([
        "",
        "## Enforcement Infrastructure",
        f"Hookify rules (global): {len(hookify['global'])}",
        f"Hookify rules (project): {len(hookify['project'])}",
        f"Learned mistakes in CLAUDE.md: {learned_mistakes}",
        "",
    ])

    # Stale preferences section
    if stale_preferences:
        lines.extend([
            "## Stale Preferences (0 signals for 30+ days)",
            "",
            "These preferences had no correction signals recently. "
            "Either they're well-enforced (good) or no longer relevant (review/remove).",
            "",
        ])
        for sp in stale_preferences:
            lines.append(
                f"- **{sp['preference']}**: last signal {sp['last_signal_date']}, "
                f"silent {sp['days_silent']} days"
            )
        lines.append("")

    lines.extend([
        "## Recommendations",
        "",
    ])

    # Generate recommendations based on signals
    quiet_prefs = [p for p, c in corrections["by_preference"].items() if c == 0]
    active_prefs = [
        p for p, c in sorted(
            corrections["by_preference"].items(), key=lambda x: x[1], reverse=True
        )
        if c > 2
    ]

    if quiet_prefs:
        lines.append(
            f"Quiet preferences (0 signals, may be stale or well-enforced): "
            f"{', '.join(quiet_prefs)}"
        )
    if active_prefs:
        lines.append(
            f"Most active preferences (frequent corrections, may need stronger enforcement): "
            f"{', '.join(active_prefs)}"
        )
    if not quiet_prefs and not active_prefs:
        lines.append("No notable patterns in this capture period.")

    return "\n".join(lines)


def run_capture(dry_run: bool = False) -> str:
    """Run full taste capture pipeline."""
    last_capture = get_last_capture_date()
    since_epoch = int(last_capture.timestamp()) if last_capture else None

    # Mine all sources
    corrections = mine_conversation_corrections(since_epoch)
    christensen = mine_christensen_log(since_epoch)
    hookify = count_hookify_rules()
    learned_mistakes = count_learned_mistakes()
    stale_preferences = detect_stale_preferences()

    # Generate report
    report = generate_delta_report(
        corrections,
        christensen,
        hookify,
        learned_mistakes,
        last_capture.strftime("%Y-%m-%d") if last_capture else None,
        stale_preferences=stale_preferences,
    )

    if dry_run:
        print(report)
        return report

    # Save snapshot
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d")
    snapshot_path = SNAPSHOTS_DIR / f"taste-profile_{timestamp}.md"
    shutil.copy2(TASTE_PROFILE, snapshot_path)

    # Save delta report
    report_path = SNAPSHOTS_DIR / f"taste-delta_{timestamp}.md"
    report_path.write_text(report)

    # Update capture metadata in taste profile
    if TASTE_PROFILE.exists():
        content = TASTE_PROFILE.read_text()
        next_date = datetime.now().strftime("%Y-%m-%d")
        # Update next capture date (2 weeks out)
        from datetime import timedelta
        next_capture = (datetime.now() + timedelta(weeks=2)).strftime("%Y-%m-%d")
        content = re.sub(
            r"^# Next capture: .+$",
            f"# Next capture: {next_capture}",
            content,
            flags=re.MULTILINE,
        )
        TASTE_PROFILE.write_text(content)

    print(f"Snapshot saved: {snapshot_path}")
    print(f"Delta report saved: {report_path}")
    print(report)
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Taste Profile Capture")
    parser.add_argument("--dry-run", action="store_true", help="Print report without saving")
    args = parser.parse_args()
    run_capture(dry_run=args.dry_run)
