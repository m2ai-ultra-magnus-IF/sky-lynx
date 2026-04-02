"""
Sky-Lynx Trigger Listener - Phase F
Reads pipeline event files from Metroplex, evaluates trigger conditions,
and determines whether to fire a reactive analysis.
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_EVENTS_DIR = Path.home() / ".local" / "share" / "skylynx-events"
DEFAULT_COOLDOWN_HOURS = 12.0
CONSECUTIVE_FAILURES_THRESHOLD = 3
SUCCESS_RATE_WINDOW = 20
SUCCESS_RATE_FLOOR = 0.40


@dataclass
class TriggerResult:
    """Result of trigger evaluation."""

    reason: str
    event_type: str
    scope: str = "pipeline"
    events: list[dict] = field(default_factory=list)


def _get_events_dir() -> Path:
    return Path(os.environ.get("SKYLYNX_EVENTS_DIR", str(DEFAULT_EVENTS_DIR)))


def _load_events(events_dir: Path) -> list[dict]:
    """Load all event JSON files, sorted by timestamp ascending."""
    events = []
    if not events_dir.exists():
        return events
    for f in events_dir.glob("*.json"):
        try:
            event = json.loads(f.read_text())
            event["_file"] = str(f)
            events.append(event)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping malformed event file %s: %s", f.name, e)
    events.sort(key=lambda e: e.get("timestamp", ""))
    return events


def evaluate_triggers(events_dir: Path | None = None) -> TriggerResult | None:
    """Read pending events and evaluate trigger conditions.

    Returns a TriggerResult if conditions are met, None otherwise.
    """
    events_dir = events_dir or _get_events_dir()
    events = _load_events(events_dir)
    if not events:
        return None

    # Check for ratchet tightened (immediate trigger)
    ratchet_events = [e for e in events if e["event_type"] == "ratchet_tightened"]
    if ratchet_events:
        return TriggerResult(
            reason=f"Quality ratchet tightened: {ratchet_events[-1].get('details', {}).get('reason', 'unknown')}",
            event_type="ratchet_tightened",
            events=ratchet_events,
        )

    # Check for consecutive build failures
    build_events = [e for e in events if e["event_type"] in ("build_completed", "build_failed")]
    if build_events:
        # Count consecutive failures from the end
        consecutive_failures = 0
        for e in reversed(build_events):
            if e["event_type"] == "build_failed":
                consecutive_failures += 1
            else:
                break

        if consecutive_failures >= CONSECUTIVE_FAILURES_THRESHOLD:
            failing = [e for e in build_events[-consecutive_failures:]]
            titles = [e.get("details", {}).get("title", "unknown") for e in failing]
            return TriggerResult(
                reason=f"{consecutive_failures} consecutive build failures: {', '.join(titles[:3])}",
                event_type="build_failed",
                events=failing,
            )

    # Check success rate in rolling window
    if len(build_events) >= SUCCESS_RATE_WINDOW:
        recent = build_events[-SUCCESS_RATE_WINDOW:]
        completed = sum(1 for e in recent if e["event_type"] == "build_completed")
        rate = completed / len(recent)
        if rate < SUCCESS_RATE_FLOOR:
            return TriggerResult(
                reason=f"Success rate {rate:.0%} below {SUCCESS_RATE_FLOOR:.0%} floor ({completed}/{len(recent)} builds)",
                event_type="build_failed",
                events=recent,
            )

    return None


def check_cooldown(events_dir: Path | None = None, cooldown_hours: float = DEFAULT_COOLDOWN_HOURS) -> bool:
    """Return True if cooldown has elapsed (i.e., OK to trigger)."""
    events_dir = events_dir or _get_events_dir()
    marker = events_dir / ".last_triggered"
    if not marker.exists():
        return True
    try:
        last = datetime.fromisoformat(marker.read_text().strip())
        elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        return elapsed >= cooldown_hours
    except (ValueError, OSError):
        return True


def record_trigger(events_dir: Path | None = None) -> None:
    """Write the current timestamp to the cooldown marker."""
    events_dir = events_dir or _get_events_dir()
    events_dir.mkdir(parents=True, exist_ok=True)
    marker = events_dir / ".last_triggered"
    marker.write_text(datetime.now(timezone.utc).isoformat())


def cleanup_events(events_dir: Path | None = None, max_age_days: int = 7) -> int:
    """Remove event files older than max_age_days. Returns count removed."""
    events_dir = events_dir or _get_events_dir()
    if not events_dir.exists():
        return 0
    cutoff = time.time() - (max_age_days * 86400)
    removed = 0
    for f in events_dir.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed
