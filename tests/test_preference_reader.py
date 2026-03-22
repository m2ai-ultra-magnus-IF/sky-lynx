"""Tests for preference_reader module."""

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from sky_lynx.preference_reader import build_preference_digest, load_preference_data

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS preference_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT,
    dimension TEXT,
    value TEXT,
    confidence REAL,
    evidence_count INTEGER DEFAULT 0,
    source TEXT DEFAULT 'daily_analysis',
    version INTEGER DEFAULT 1,
    created_at INTEGER,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS preference_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    preference_id INTEGER,
    old_value TEXT,
    new_value TEXT,
    old_confidence REAL,
    new_confidence REAL,
    reason TEXT,
    changed_at INTEGER,
    FOREIGN KEY (preference_id) REFERENCES preference_profile(id)
);
"""


@pytest.fixture
def preference_db() -> Path:
    """Create a temp DB with preference data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    now = int(time.time())
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)

    # Insert preferences
    prefs = [
        ("communication", "response_length", "concise, direct", 0.85, 12, "manual", now, now),
        ("communication", "tone", "technical, no fluff", 0.72, 8, "daily_analysis", now, now),
        ("technical", "error_handling", "fix and move on, no apology", 0.90, 15, "manual", now, now),
        ("technical", "code_style", "minimal comments, self-documenting", 0.45, 3, "daily_analysis", now, now),
        ("work_patterns", "task_approach", "ship first, iterate", 0.65, 5, "daily_analysis", now, now),
    ]
    conn.executemany(
        "INSERT INTO preference_profile (category, dimension, value, confidence, evidence_count, source, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        prefs,
    )

    # Insert recent history
    recent = now - 3600  # 1 hour ago
    old = now - (10 * 86400)  # 10 days ago
    history = [
        (1, "brief", "concise, direct", 0.80, 0.85, "reinforced: observed in conversation", recent),
        (2, "casual", "technical, no fluff", 0.65, 0.72, "correction: user prefers technical", recent),
        (4, "verbose comments", "minimal comments", 0.50, 0.45, "decay: unobserved", recent),
        # Old entry (should not appear in 7-day window)
        (3, "apologize first", "fix and move on", 0.85, 0.90, "reinforced", old),
    ]
    conn.executemany(
        "INSERT INTO preference_history (preference_id, old_value, new_value, old_confidence, new_confidence, reason, changed_at) "
        "VALUES (?,?,?,?,?,?,?)",
        history,
    )

    conn.commit()
    conn.close()

    yield db_path
    db_path.unlink(missing_ok=True)


@pytest.fixture
def empty_pref_db() -> Path:
    """Create a temp DB with schema but no data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()

    yield db_path
    db_path.unlink(missing_ok=True)


@pytest.fixture
def no_pref_table_db() -> Path:
    """Create a temp DB with no preference tables at all."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE dummy (id INTEGER)")
    conn.commit()
    conn.close()

    yield db_path
    db_path.unlink(missing_ok=True)


class TestLoadPreferenceData:
    """Tests for load_preference_data function."""

    def test_load_populated_db(self, preference_db: Path):
        data = load_preference_data(preference_db)

        assert data["total"] == 5
        assert data["by_category"]["communication"] == 2
        assert data["by_category"]["technical"] == 2
        assert data["by_category"]["work_patterns"] == 1
        assert data["manual_count"] == 2
        assert data["llm_count"] == 3

    def test_confidence_stats(self, preference_db: Path):
        data = load_preference_data(preference_db)

        assert data["high_confidence_count"] == 2  # 0.85, 0.90
        assert data["low_confidence_count"] == 1  # 0.45
        assert 0.7 < data["avg_confidence"] < 0.75

    def test_recent_changes(self, preference_db: Path):
        data = load_preference_data(preference_db)

        assert data["changes_last_7d"] == 3  # Old entry excluded
        assert len(data["recent_changes"]) == 3

    def test_confidence_trend(self, preference_db: Path):
        data = load_preference_data(preference_db)

        # +0.05, +0.07, -0.05 => avg +0.023
        assert data["confidence_trend"] > 0

    def test_empty_db(self, empty_pref_db: Path):
        data = load_preference_data(empty_pref_db)
        assert data.get("total", 0) == 0

    def test_missing_table(self, no_pref_table_db: Path):
        data = load_preference_data(no_pref_table_db)
        assert data == {}

    def test_missing_db(self):
        data = load_preference_data(Path("/nonexistent/claudeclaw.db"))
        assert data == {}

    def test_env_var_override(self, preference_db: Path, monkeypatch):
        monkeypatch.setenv("CLAUDECLAW_DB_PATH", str(preference_db))
        data = load_preference_data()
        assert data["total"] == 5


class TestBuildPreferenceDigest:
    """Tests for build_preference_digest function."""

    def test_digest_populated(self, preference_db: Path):
        data = load_preference_data(preference_db)
        digest = build_preference_digest(data)

        assert "**Total Preferences**: 5" in digest
        assert "**Average Confidence**:" in digest
        assert "manual" in digest
        assert "**Categories**:" in digest
        assert "**Recent Changes**:" in digest

    def test_digest_empty(self):
        assert build_preference_digest({}) == "ClaudeClaw preference data not available."

    def test_digest_none(self):
        assert build_preference_digest(None) == "ClaudeClaw preference data not available."

    def test_digest_zero_prefs(self):
        digest = build_preference_digest({"total": 0})
        assert "empty" in digest.lower()
