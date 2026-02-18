"""Tests for ideaforge_reader module."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from sky_lynx.ideaforge_reader import build_ideaforge_digest, load_ideaforge_data

# Minimal schema matching IdeaForge's production DB (from db.py)
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT UNIQUE NOT NULL,
    subreddit TEXT NOT NULL,
    post_id TEXT NOT NULL,
    title TEXT NOT NULL,
    selftext TEXT DEFAULT '',
    author TEXT DEFAULT '',
    url TEXT DEFAULT '',
    score INTEGER DEFAULT 0,
    num_comments INTEGER DEFAULT 0,
    created_utc REAL DEFAULT 0,
    signal_type TEXT NOT NULL,
    matched_keywords TEXT DEFAULT '[]',
    harvested_at TIMESTAMP NOT NULL,
    processed INTEGER DEFAULT 0,
    batch_id TEXT
);

CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    problem_statement TEXT DEFAULT '',
    target_audience TEXT DEFAULT '',
    source_signals TEXT DEFAULT '[]',
    source_subreddits TEXT DEFAULT '[]',
    signal_count INTEGER DEFAULT 0,
    opportunity_score REAL,
    problem_score REAL,
    feasibility_score REAL,
    why_now_score REAL,
    competition_score REAL,
    weighted_score REAL,
    score_rationale TEXT,
    artifact_type TEXT,
    route_rationale TEXT,
    route_confidence REAL,
    struggling_user TEXT,
    classified_at TIMESTAMP,
    status TEXT DEFAULT 'unscored',
    synthesized_at TIMESTAMP,
    scored_at TIMESTAMP,
    exported_at TIMESTAMP,
    ultra_magnus_id INTEGER
);
"""


@pytest.fixture
def ideaforge_db() -> Path:
    """Create a temp IdeaForge DB with test data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)

    # Insert test signals
    signals = [
        ("sig_hn_1", "hackernews", "1", "AI costs too much", "", "", "", 150, 45, 0, "pain_point", "[]", "2026-02-17T00:00:00", 1, "batch1"),
        ("sig_hn_2", "hackernews", "2", "Need better testing tools", "", "", "", 80, 20, 0, "solution_request", "[]", "2026-02-17T00:00:00", 1, "batch1"),
        ("sig_hn_3", "hackernews", "3", "I wish AI was faster", "", "", "", 200, 60, 0, "wish", "[]", "2026-02-17T00:00:00", 1, "batch1"),
        ("sig_hn_4", "hackernews", "4", "Broken CI pipeline", "", "", "", 50, 10, 0, "complaint", "[]", "2026-02-17T00:00:00", 0, None),
    ]
    conn.executemany(
        "INSERT INTO signals (signal_id, subreddit, post_id, title, selftext, author, url, score, num_comments, created_utc, signal_type, matched_keywords, harvested_at, processed, batch_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        signals,
    )

    # Insert test ideas
    ideas = [
        ("AI Cost Optimizer", "Reduce AI API costs", "High costs", "Devs", "[]", "[]", 2, 8.0, 7.0, 6.0, 9.0, 6.0, 7.2, None, "product", "Strong market signal", 0.85, "Sarah the SaaS engineer", "2026-02-17", "classified", "2026-02-16", "2026-02-17", None, None),
        ("Test Automator", "Automated testing tool", "Flaky tests", "QA teams", "[]", "[]", 1, 6.0, 8.0, 7.0, 7.0, 5.0, 6.6, None, "tool", "Good fit for tool", 0.80, "Mike the QA lead", "2026-02-17", "classified", "2026-02-16", "2026-02-17", None, None),
        ("Dismissed Idea", "Not viable", "No problem", "Nobody", "[]", "[]", 1, 3.0, 2.0, 2.0, 3.0, 2.0, 2.4, None, "dismiss", "Low scores", 0.90, "", "2026-02-17", "dismissed", "2026-02-16", "2026-02-17", None, None),
    ]
    conn.executemany(
        "INSERT INTO ideas (title, description, problem_statement, target_audience, source_signals, source_subreddits, signal_count, opportunity_score, problem_score, feasibility_score, why_now_score, competition_score, weighted_score, score_rationale, artifact_type, route_rationale, route_confidence, struggling_user, classified_at, status, synthesized_at, scored_at, exported_at, ultra_magnus_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ideas,
    )

    conn.commit()
    conn.close()

    yield db_path
    db_path.unlink(missing_ok=True)


@pytest.fixture
def empty_db() -> Path:
    """Create a temp IdeaForge DB with schema but no data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()

    yield db_path
    db_path.unlink(missing_ok=True)


class TestLoadIdeaforgeData:
    """Tests for load_ideaforge_data function."""

    def test_load_populated_db(self, ideaforge_db: Path):
        """Should load all aggregate data from a populated DB."""
        data = load_ideaforge_data(ideaforge_db)

        assert data["total_signals"] == 4
        assert data["signal_types"]["pain_point"] == 1
        assert data["signal_types"]["wish"] == 1
        assert data["signal_types"]["solution_request"] == 1
        assert data["signal_types"]["complaint"] == 1

        assert data["total_ideas"] == 3
        assert data["artifact_types"]["product"] == 1
        assert data["artifact_types"]["tool"] == 1
        assert data["artifact_types"]["dismiss"] == 1

        assert data["dismissed"] == 1
        assert 33 <= data["dismiss_rate"] <= 34  # 1/3

        scores = data["scores"]
        assert scores["scored_count"] == 3
        assert scores["min_weighted"] == 2.4
        assert scores["max_weighted"] == 7.2

    def test_load_top_ideas(self, ideaforge_db: Path):
        """Should return top classified ideas sorted by score."""
        data = load_ideaforge_data(ideaforge_db)

        top = data["top_ideas"]
        assert len(top) == 2  # Only classified, not dismissed
        assert top[0]["title"] == "AI Cost Optimizer"
        assert top[0]["weighted_score"] == 7.2
        assert top[0]["artifact_type"] == "product"
        assert top[1]["title"] == "Test Automator"

    def test_load_signal_engagement(self, ideaforge_db: Path):
        """Should calculate signal engagement averages."""
        data = load_ideaforge_data(ideaforge_db)

        engagement = data["signal_engagement"]
        assert engagement["total"] == 4
        assert engagement["avg_score"] == 120.0  # (150+80+200+50)/4
        assert engagement["avg_comments"] == 33.75  # (45+20+60+10)/4

    def test_load_empty_db(self, empty_db: Path):
        """Should return valid dict with zeros for empty DB."""
        data = load_ideaforge_data(empty_db)

        assert data["total_signals"] == 0
        assert data["total_ideas"] == 0
        assert data["dismiss_rate"] == 0
        assert data["top_ideas"] == []

    def test_load_missing_db(self):
        """Should return empty dict for missing DB."""
        data = load_ideaforge_data(Path("/nonexistent/ideaforge.db"))
        assert data == {}

    def test_load_env_var_override(self, ideaforge_db: Path, monkeypatch):
        """Should respect IDEAFORGE_DB_PATH env var."""
        monkeypatch.setenv("IDEAFORGE_DB_PATH", str(ideaforge_db))
        data = load_ideaforge_data()  # No explicit path
        assert data["total_signals"] == 4


class TestBuildIdeaforgeDigest:
    """Tests for build_ideaforge_digest function."""

    def test_digest_with_populated_data(self, ideaforge_db: Path):
        """Should produce a complete digest with all sections."""
        data = load_ideaforge_data(ideaforge_db)
        digest = build_ideaforge_digest(data)

        assert "**Total Signals**: 4" in digest
        assert "**Total Ideas**: 3" in digest
        assert "pain_point: 1" in digest
        assert "product: 1" in digest
        assert "**Dismiss Rate**:" in digest
        assert "**Score Averages**" in digest
        assert "AI Cost Optimizer" in digest
        assert "**Signal Engagement**" in digest

    def test_digest_empty_data(self):
        """Should return fallback for empty dict."""
        assert build_ideaforge_digest({}) == "IdeaForge data not available."

    def test_digest_none_data(self):
        """Should return fallback for None."""
        assert build_ideaforge_digest(None) == "IdeaForge data not available."

    def test_digest_contains_top_ideas(self, ideaforge_db: Path):
        """Should include top ideas with type and score."""
        data = load_ideaforge_data(ideaforge_db)
        digest = build_ideaforge_digest(data)

        assert "[product] AI Cost Optimizer" in digest
        assert "score: 7.2" in digest
        assert "[tool] Test Automator" in digest
