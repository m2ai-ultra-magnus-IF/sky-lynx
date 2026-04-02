"""Tests for the Sky-Lynx trigger listener (Phase F)."""
import json
import time
from datetime import datetime, timezone, timedelta

from sky_lynx.trigger_listener import (
    evaluate_triggers,
    check_cooldown,
    record_trigger,
    cleanup_events,
    CONSECUTIVE_FAILURES_THRESHOLD,
    SUCCESS_RATE_WINDOW,
)


def _write_event(events_dir, event_type, details=None, timestamp=None):
    """Helper to write a fake event file."""
    events_dir.mkdir(parents=True, exist_ok=True)
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    event = {
        "event_type": event_type,
        "timestamp": timestamp,
        "source": "metroplex",
        "details": details or {},
    }
    filename = f"{time.time_ns()}.json"
    (events_dir / filename).write_text(json.dumps(event))
    time.sleep(0.001)  # ensure unique timestamps


# --- evaluate_triggers ---

def test_no_events_no_trigger(tmp_path):
    assert evaluate_triggers(tmp_path) is None


def test_empty_dir_no_trigger(tmp_path):
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    assert evaluate_triggers(events_dir) is None


def test_three_consecutive_failures_triggers(tmp_path):
    for i in range(3):
        _write_event(tmp_path, "build_failed", {"job_id": f"job-{i}", "title": f"Build {i}"})
    result = evaluate_triggers(tmp_path)
    assert result is not None
    assert result.event_type == "build_failed"
    assert "3 consecutive" in result.reason


def test_two_failures_no_trigger(tmp_path):
    for i in range(2):
        _write_event(tmp_path, "build_failed", {"job_id": f"job-{i}"})
    assert evaluate_triggers(tmp_path) is None


def test_failure_then_success_resets_streak(tmp_path):
    _write_event(tmp_path, "build_failed", {"job_id": "job-1"})
    _write_event(tmp_path, "build_failed", {"job_id": "job-2"})
    _write_event(tmp_path, "build_completed", {"job_id": "job-3"})
    _write_event(tmp_path, "build_failed", {"job_id": "job-4"})
    _write_event(tmp_path, "build_failed", {"job_id": "job-5"})
    # Only 2 consecutive failures at the end, not 3
    assert evaluate_triggers(tmp_path) is None


def test_ratchet_tightened_triggers_immediately(tmp_path):
    _write_event(tmp_path, "ratchet_tightened", {
        "previous": 45.0, "new": 47.0, "reason": "test reason"
    })
    result = evaluate_triggers(tmp_path)
    assert result is not None
    assert result.event_type == "ratchet_tightened"
    assert "test reason" in result.reason


def test_ratchet_takes_priority_over_failures(tmp_path):
    for i in range(5):
        _write_event(tmp_path, "build_failed", {"job_id": f"job-{i}"})
    _write_event(tmp_path, "ratchet_tightened", {"reason": "quality improved"})
    result = evaluate_triggers(tmp_path)
    assert result is not None
    assert result.event_type == "ratchet_tightened"


def test_success_rate_below_floor_triggers(tmp_path):
    # 7 failures, 13 successes out of 20 -> 65% success rate (above floor)
    # Let's do 13 failures, 7 successes -> 35% success rate (below 40%)
    for i in range(13):
        _write_event(tmp_path, "build_failed", {"job_id": f"fail-{i}"})
    for i in range(7):
        _write_event(tmp_path, "build_completed", {"job_id": f"pass-{i}"})
    result = evaluate_triggers(tmp_path)
    assert result is not None
    assert "35%" in result.reason or "below" in result.reason


def test_success_rate_above_floor_no_trigger(tmp_path):
    # 8 failures, 12 successes -> 60% (above 40%)
    for i in range(8):
        _write_event(tmp_path, "build_failed", {"job_id": f"fail-{i}"})
    for i in range(12):
        _write_event(tmp_path, "build_completed", {"job_id": f"pass-{i}"})
    # No consecutive failures at end and rate is fine
    assert evaluate_triggers(tmp_path) is None


def test_malformed_event_file_skipped(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "bad.json").write_text("not json")
    _write_event(tmp_path, "build_failed", {"job_id": "job-1"})
    # Should not crash, just skip the bad file
    result = evaluate_triggers(tmp_path)
    assert result is None  # only 1 failure, not enough


# --- check_cooldown ---

def test_cooldown_no_marker_returns_true(tmp_path):
    assert check_cooldown(tmp_path) is True


def test_cooldown_recent_marker_returns_false(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    marker = tmp_path / ".last_triggered"
    marker.write_text(datetime.now(timezone.utc).isoformat())
    assert check_cooldown(tmp_path, cooldown_hours=12.0) is False


def test_cooldown_expired_marker_returns_true(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    marker = tmp_path / ".last_triggered"
    old_time = datetime.now(timezone.utc) - timedelta(hours=13)
    marker.write_text(old_time.isoformat())
    assert check_cooldown(tmp_path, cooldown_hours=12.0) is True


def test_cooldown_corrupt_marker_returns_true(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    marker = tmp_path / ".last_triggered"
    marker.write_text("not a date")
    assert check_cooldown(tmp_path) is True


# --- record_trigger ---

def test_record_trigger_writes_marker(tmp_path):
    record_trigger(tmp_path)
    marker = tmp_path / ".last_triggered"
    assert marker.exists()
    ts = datetime.fromisoformat(marker.read_text().strip())
    assert (datetime.now(timezone.utc) - ts).total_seconds() < 5


# --- cleanup_events ---

def test_cleanup_removes_old_events(tmp_path):
    import os
    tmp_path.mkdir(exist_ok=True)
    # Create an "old" event file
    old_file = tmp_path / "old.json"
    old_file.write_text(json.dumps({"event_type": "build_failed"}))
    old_mtime = time.time() - (8 * 86400)  # 8 days ago
    os.utime(old_file, (old_mtime, old_mtime))

    # Create a "recent" event file
    new_file = tmp_path / "new.json"
    new_file.write_text(json.dumps({"event_type": "build_completed"}))

    removed = cleanup_events(tmp_path, max_age_days=7)
    assert removed == 1
    assert not old_file.exists()
    assert new_file.exists()


def test_cleanup_empty_dir(tmp_path):
    assert cleanup_events(tmp_path) == 0
