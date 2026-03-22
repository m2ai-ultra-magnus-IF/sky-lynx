"""Tests for cost_reader module."""

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from sky_lynx.cost_reader import build_cost_digest, load_cost_data

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT,
    session_id TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read INTEGER DEFAULT 0,
    context_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    did_compact INTEGER DEFAULT 0,
    created_at INTEGER,
    agent_id TEXT,
    topic_id TEXT
);
"""


@pytest.fixture
def cost_db() -> Path:
    """Create a temp DB with token_usage data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    now = int(time.time())
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)

    # Current week entries
    entries = [
        ("chat1", "sess1", 5000, 1000, 0, 50000, 0.05, 0, now - 3600, "main", None),
        ("chat1", "sess1", 8000, 2000, 0, 80000, 0.10, 1, now - 3000, "main", None),
        ("chat1", "sess2", 3000, 500, 0, 30000, 0.03, 0, now - 2000, "forge", None),
        ("chat1", "sess3", 10000, 3000, 0, 120000, 0.15, 1, now - 1000, "soundwave", None),
    ]
    # Prior week entries
    prior = now - (8 * 86400)
    entries.append(("chat1", "sess0", 4000, 800, 0, 40000, 0.20, 0, prior, "main", None))

    conn.executemany(
        "INSERT INTO token_usage (chat_id, session_id, input_tokens, output_tokens, cache_read, context_tokens, cost_usd, did_compact, created_at, agent_id, topic_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        entries,
    )

    conn.commit()
    conn.close()

    yield db_path
    db_path.unlink(missing_ok=True)


@pytest.fixture
def empty_cost_db() -> Path:
    """Create a temp DB with schema but no data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()

    yield db_path
    db_path.unlink(missing_ok=True)


class TestLoadCostData:
    """Tests for load_cost_data function."""

    def test_load_populated_db(self, cost_db: Path):
        data = load_cost_data(cost_db)

        assert data["total_turns"] == 4  # Only current week
        assert abs(data["total_cost"] - 0.33) < 0.01

    def test_by_agent(self, cost_db: Path):
        data = load_cost_data(cost_db)

        agent_map = {a["agent"]: a for a in data["by_agent"]}
        assert "main" in agent_map
        assert agent_map["main"]["turns"] == 2
        assert "forge" in agent_map
        assert "soundwave" in agent_map

    def test_compaction_stats(self, cost_db: Path):
        data = load_cost_data(cost_db)
        assert data["compactions"] == 2
        assert data["compaction_rate"] == 50.0

    def test_cost_trend(self, cost_db: Path):
        data = load_cost_data(cost_db)
        # Current: $0.33, prior: $0.20 => +65%
        assert data["cost_trend_pct"] is not None
        assert data["cost_trend_pct"] > 50

    def test_top_sessions(self, cost_db: Path):
        data = load_cost_data(cost_db)
        assert len(data["top_sessions"]) > 0
        # Most expensive first
        assert data["top_sessions"][0]["cost"] >= data["top_sessions"][-1]["cost"]

    def test_empty_db(self, empty_cost_db: Path):
        data = load_cost_data(empty_cost_db)
        assert data.get("total_turns", 0) == 0

    def test_missing_db(self):
        data = load_cost_data(Path("/nonexistent/claudeclaw.db"))
        assert data == {}


class TestBuildCostDigest:
    """Tests for build_cost_digest function."""

    def test_digest_populated(self, cost_db: Path):
        data = load_cost_data(cost_db)
        digest = build_cost_digest(data)

        assert "**Total Cost (7d)**:" in digest
        assert "**Total Turns**: 4" in digest
        assert "**Cost by Agent**:" in digest
        assert "main" in digest
        assert "**Compactions**:" in digest

    def test_digest_empty(self):
        assert build_cost_digest({}) == "ClaudeClaw cost data not available."

    def test_digest_no_turns(self):
        digest = build_cost_digest({"total_turns": 0})
        assert "No token usage" in digest
