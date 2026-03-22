"""Tests for mission_reader module."""

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from sky_lynx.mission_reader import build_mission_digest, load_mission_data

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS missions (
    id TEXT PRIMARY KEY,
    chat_id TEXT,
    topic_id TEXT,
    goal TEXT,
    plan_json TEXT,
    status TEXT DEFAULT 'pending',
    result_summary TEXT,
    created_at INTEGER,
    approved_at INTEGER,
    completed_at INTEGER
);

CREATE TABLE IF NOT EXISTS mission_subtasks (
    id TEXT PRIMARY KEY,
    mission_id TEXT,
    agent_id TEXT,
    agent_type TEXT,
    prompt TEXT,
    verification_criteria TEXT,
    depends_on TEXT,
    status TEXT DEFAULT 'pending',
    result TEXT,
    error TEXT,
    started_at INTEGER,
    completed_at INTEGER,
    cost_usd REAL,
    FOREIGN KEY (mission_id) REFERENCES missions(id)
);
"""


@pytest.fixture
def mission_db() -> Path:
    """Create a temp DB with mission data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    now = int(time.time())
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)

    # Insert missions
    missions = [
        ("m1", "chat1", None, "Deploy feature X", "[]", "completed", "Done", now - 7200, now - 7100, now - 3600),
        ("m2", "chat1", None, "Fix bug Y", "[]", "completed", "Fixed", now - 5000, now - 4900, now - 3000),
        ("m3", "chat1", None, "Research Z", "[]", "failed", None, now - 2000, now - 1900, now - 1000),
        ("m4", "chat1", None, "Build widget", "[]", "working", None, now - 500, now - 400, None),
    ]
    conn.executemany(
        "INSERT INTO missions (id, chat_id, topic_id, goal, plan_json, status, result_summary, created_at, approved_at, completed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        missions,
    )

    # Insert subtasks
    subtasks = [
        ("s1", "m1", "ravage", "worker", "Code the feature", None, None, "completed", "Done", None, now - 7000, now - 5000, 0.05),
        ("s2", "m1", "soundwave", "worker", "Research context", None, None, "completed", "Found it", None, now - 7000, now - 6500, 0.02),
        ("s3", "m2", "ravage", "worker", "Fix the bug", None, None, "completed", "Fixed", None, now - 4800, now - 3200, 0.03),
        ("s4", "m3", "soundwave", "worker", "Research topic", None, None, "failed", None, "Timeout after 120s", now - 1800, now - 1200, 0.01),
        ("s5", "m3", "ravage", "worker", "Implement fix", None, '["s4"]', "canceled", None, None, None, None, None),
        ("s6", "m4", "ravage", "worker", "Build it", None, None, "working", None, None, now - 300, None, None),
    ]
    conn.executemany(
        "INSERT INTO mission_subtasks (id, mission_id, agent_id, agent_type, prompt, verification_criteria, depends_on, status, result, error, started_at, completed_at, cost_usd) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        subtasks,
    )

    conn.commit()
    conn.close()

    yield db_path
    db_path.unlink(missing_ok=True)


@pytest.fixture
def empty_mission_db() -> Path:
    """Create a temp DB with schema but no data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()

    yield db_path
    db_path.unlink(missing_ok=True)


class TestLoadMissionData:
    """Tests for load_mission_data function."""

    def test_load_populated_db(self, mission_db: Path):
        data = load_mission_data(mission_db)

        assert data["total_missions"] == 4
        assert data["status_counts"]["completed"] == 2
        assert data["status_counts"]["failed"] == 1
        assert data["status_counts"]["working"] == 1

    def test_completion_rate(self, mission_db: Path):
        data = load_mission_data(mission_db)
        # 2 completed / (2 completed + 1 failed) = 66.7%
        assert 66 <= data["completion_rate"] <= 67

    def test_agent_stats(self, mission_db: Path):
        data = load_mission_data(mission_db)

        agent_map = {a["agent_type"]: a for a in data["agent_stats"]}
        assert "worker" in agent_map
        worker = agent_map["worker"]
        assert worker["total"] == 6
        assert worker["succeeded"] == 3
        assert worker["failed"] == 1

    def test_subtask_distribution(self, mission_db: Path):
        data = load_mission_data(mission_db)
        dist = data["subtask_distribution"]
        assert dist["min"] == 1
        assert dist["max"] == 2
        assert dist["total_subtasks"] == 6

    def test_failure_modes(self, mission_db: Path):
        data = load_mission_data(mission_db)
        assert len(data["failure_modes"]) == 1
        assert "Timeout" in data["failure_modes"][0]["error"]

    def test_empty_db(self, empty_mission_db: Path):
        data = load_mission_data(empty_mission_db)
        assert data.get("total_missions", 0) == 0

    def test_missing_db(self):
        data = load_mission_data(Path("/nonexistent/claudeclaw.db"))
        assert data == {}


class TestBuildMissionDigest:
    """Tests for build_mission_digest function."""

    def test_digest_populated(self, mission_db: Path):
        data = load_mission_data(mission_db)
        digest = build_mission_digest(data)

        assert "**Total Missions**: 4" in digest
        assert "**Completion Rate**:" in digest
        assert "**Agent Performance**:" in digest
        assert "worker" in digest

    def test_digest_empty(self):
        assert build_mission_digest({}) == "ClaudeClaw mission data not available."

    def test_digest_no_missions(self):
        digest = build_mission_digest({"total_missions": 0})
        assert "No missions" in digest
