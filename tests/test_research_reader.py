"""Tests for research_reader module."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from sky_lynx.research_reader import build_research_digest, load_research_signals

# Schema matching Snow-Town's research_signals table
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    url TEXT,
    relevance TEXT NOT NULL,
    relevance_rationale TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    domain TEXT,
    consumed_by TEXT,
    emitted_at TEXT NOT NULL,
    raw_json TEXT NOT NULL
);
"""


@pytest.fixture
def research_db() -> Path:
    """Create a temp DB with research signal test data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)

    signals = [
        ("arxiv-2401.001", "arxiv_hf", "Tool-Augmented LLMs", "New approach to tool use",
         "http://arxiv.org/abs/2401.001", "high", "Directly relevant to MCP work",
         json.dumps(["mcp", "agents", "persona:carmack"]), "ai-agents", None,
         "2026-02-20T05:00:00", json.dumps({"signal_id": "arxiv-2401.001"})),
        ("arxiv-2401.002", "arxiv_hf", "Prompt Engineering Survey", "Survey of techniques",
         "http://arxiv.org/abs/2401.002", "medium", "Background context",
         json.dumps(["prompting"]), "ai-agents", "sky-lynx",
         "2026-02-19T05:00:00", json.dumps({"signal_id": "arxiv-2401.002"})),
        ("tool-mcp-server", "tool_monitor", "New MCP Server Framework", "Framework for building MCP servers",
         "https://github.com/example/mcp", "high", "Core tooling",
         json.dumps(["mcp", "framework", "persona:hopper"]), "developer-tools", None,
         "2026-02-20T05:00:00", json.dumps({"signal_id": "tool-mcp-server"})),
        ("domain-health-ai", "domain_watch", "AI in Home Health", "Rising adoption trends",
         "https://example.com/health-ai", "high", "Healthcare focus area",
         json.dumps(["healthcare", "ai"]), "healthcare-ai", None,
         "2026-02-18T05:00:00", json.dumps({"signal_id": "domain-health-ai"})),
    ]
    conn.executemany(
        "INSERT INTO research_signals (signal_id, source, title, summary, url, relevance, "
        "relevance_rationale, tags, domain, consumed_by, emitted_at, raw_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        signals,
    )

    conn.commit()
    conn.close()

    yield db_path
    db_path.unlink(missing_ok=True)


@pytest.fixture
def empty_db() -> Path:
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
def no_table_db() -> Path:
    """Create a temp DB without the research_signals table."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS other_table (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    yield db_path
    db_path.unlink(missing_ok=True)


class TestLoadResearchSignals:

    def test_load_populated_db(self, research_db: Path):
        data = load_research_signals(research_db)

        assert data["total_signals"] == 4

        assert data["by_source"]["arxiv_hf"] == 2
        assert data["by_source"]["tool_monitor"] == 1
        assert data["by_source"]["domain_watch"] == 1

        assert data["by_relevance"]["high"] == 3
        assert data["by_relevance"]["medium"] == 1

        assert data["by_domain"]["ai-agents"] == 2
        assert data["by_domain"]["developer-tools"] == 1
        assert data["by_domain"]["healthcare-ai"] == 1

        assert data["consumed"] == 1
        assert data["unconsumed"] == 3

    def test_load_recent_high_signals(self, research_db: Path):
        data = load_research_signals(research_db)

        recent = data["recent_high"]
        assert len(recent) == 3  # 3 high-relevance signals
        assert recent[0]["title"] in ["Tool-Augmented LLMs", "New MCP Server Framework"]

    def test_load_persona_tagged(self, research_db: Path):
        data = load_research_signals(research_db)

        persona = data["persona_tagged"]
        assert persona["carmack"] == 1
        assert persona["hopper"] == 1

    def test_load_empty_db(self, empty_db: Path):
        data = load_research_signals(empty_db)
        assert data.get("total_signals", 0) == 0

    def test_load_missing_db(self):
        data = load_research_signals(Path("/nonexistent/db.db"))
        assert data == {}

    def test_load_no_table(self, no_table_db: Path):
        data = load_research_signals(no_table_db)
        assert data == {}

    def test_load_env_var_override(self, research_db: Path, monkeypatch):
        monkeypatch.setenv("ST_RECORDS_DB_PATH", str(research_db))
        data = load_research_signals()
        assert data["total_signals"] == 4


class TestBuildResearchDigest:

    def test_digest_with_data(self, research_db: Path):
        data = load_research_signals(research_db)
        digest = build_research_digest(data)

        assert "**Total Research Signals**: 4" in digest
        assert "arxiv_hf: 2" in digest
        assert "tool_monitor: 1" in digest
        assert "high: 3" in digest
        assert "**Consumed**: 1" in digest
        assert "ai-agents" in digest
        assert "carmack" in digest
        assert "Tool-Augmented LLMs" in digest

    def test_digest_empty_data(self):
        assert build_research_digest({}) == "Research signal data not available."

    def test_digest_none_data(self):
        assert build_research_digest(None) == "Research signal data not available."

    def test_digest_zero_signals(self):
        assert build_research_digest({"total_signals": 0}) == "No research signals collected yet."
