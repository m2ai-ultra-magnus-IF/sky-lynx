"""Tests for quality score correlation in metroplex_reader and outcome_reader (Phase 14c)."""

import sqlite3
import json
from datetime import datetime

import pytest

from sky_lynx.metroplex_reader import (
    _load_quality_correlation,
    build_pipeline_health_digest,
)
from sky_lynx.outcome_reader import build_outcome_digest


# ---- Metroplex reader tests ----

@pytest.fixture
def metroplex_db(tmp_path):
    """Create an in-memory Metroplex DB with quality scores."""
    db_path = tmp_path / "metroplex.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE build_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idea_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            spec_path TEXT NOT NULL DEFAULT '',
            queue_job_id TEXT NOT NULL,
            status TEXT NOT NULL,
            queued_at TEXT NOT NULL,
            project_dir TEXT,
            review_status TEXT,
            quality_score REAL,
            retry_count INTEGER DEFAULT 0,
            next_retry_at TEXT,
            estimated_cost REAL
        );
        CREATE TABLE publish_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            build_job_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            repo_url TEXT,
            status TEXT NOT NULL,
            error TEXT,
            project_dir TEXT NOT NULL,
            created_at TEXT NOT NULL,
            published_at TEXT
        );
    """)
    return conn


class TestLoadQualityCorrelation:
    """Tests for _load_quality_correlation."""

    def test_no_scored_builds(self, metroplex_db):
        """Empty DB returns 0 scored builds."""
        data = {}
        _load_quality_correlation(metroplex_db, data)
        assert data["quality"]["scored_builds"] == 0
        assert data["quality"]["groups"] == {}

    def test_scored_builds_grouped_by_state(self, metroplex_db):
        """Builds are grouped by terminal state with correct stats."""
        now = datetime.now().isoformat()

        # Published build (score 60)
        metroplex_db.execute(
            "INSERT INTO build_jobs (idea_id, title, queue_job_id, status, queued_at, "
            "review_status, quality_score) VALUES (1, 'Published', 'job-1', 'completed', ?, 'reviewed', 60.0)",
            (now,),
        )
        metroplex_db.execute(
            "INSERT INTO publish_jobs (build_job_id, title, repo_name, repo_url, status, "
            "project_dir, created_at) VALUES ('job-1', 'Published', 'pub', 'https://gh/pub', "
            "'published', '/tmp', ?)",
            (now,),
        )

        # Failed build (score 30)
        metroplex_db.execute(
            "INSERT INTO build_jobs (idea_id, title, queue_job_id, status, queued_at, "
            "quality_score) VALUES (2, 'Failed', 'job-2', 'failed', ?, 30.0)",
            (now,),
        )

        # Review-failed build (score 40)
        metroplex_db.execute(
            "INSERT INTO build_jobs (idea_id, title, queue_job_id, status, queued_at, "
            "review_status, quality_score) VALUES (3, 'ReviewFail', 'job-3', 'completed', ?, 'review_failed', 40.0)",
            (now,),
        )
        metroplex_db.commit()

        data = {}
        _load_quality_correlation(metroplex_db, data)

        assert data["quality"]["scored_builds"] == 3
        assert "published" in data["quality"]["groups"]
        assert "build_failed" in data["quality"]["groups"]
        assert "review_failed" in data["quality"]["groups"]

        assert data["quality"]["groups"]["published"]["avg"] == 60.0
        assert data["quality"]["groups"]["build_failed"]["avg"] == 30.0
        assert data["quality"]["groups"]["review_failed"]["avg"] == 40.0

    def test_suggested_threshold(self, metroplex_db):
        """Threshold is midpoint between published avg and failed avg."""
        now = datetime.now().isoformat()

        # Published: 60
        metroplex_db.execute(
            "INSERT INTO build_jobs (idea_id, title, queue_job_id, status, queued_at, "
            "review_status, quality_score) VALUES (1, 'P1', 'j1', 'completed', ?, 'reviewed', 60.0)",
            (now,),
        )
        metroplex_db.execute(
            "INSERT INTO publish_jobs (build_job_id, title, repo_name, status, "
            "project_dir, created_at) VALUES ('j1', 'P1', 'r', 'published', '/t', ?)",
            (now,),
        )

        # Failed: 30
        metroplex_db.execute(
            "INSERT INTO build_jobs (idea_id, title, queue_job_id, status, queued_at, "
            "quality_score) VALUES (2, 'F1', 'j2', 'failed', ?, 30.0)",
            (now,),
        )
        metroplex_db.commit()

        data = {}
        _load_quality_correlation(metroplex_db, data)

        # Midpoint of 60 and 30 = 45
        assert data["quality"]["suggested_threshold"] == 45.0

    def test_no_threshold_without_both_groups(self, metroplex_db):
        """No threshold suggested if only one outcome group exists."""
        now = datetime.now().isoformat()

        metroplex_db.execute(
            "INSERT INTO build_jobs (idea_id, title, queue_job_id, status, queued_at, "
            "quality_score) VALUES (1, 'F1', 'j1', 'failed', ?, 30.0)",
            (now,),
        )
        metroplex_db.commit()

        data = {}
        _load_quality_correlation(metroplex_db, data)

        assert "suggested_threshold" not in data["quality"]


class TestPipelineHealthDigestQuality:
    """Tests for quality section in pipeline health digest."""

    def test_quality_section_included(self):
        """Quality section appears when scored builds exist."""
        data = {
            "build_total": 5, "build_completed": 3, "build_failed": 2,
            "build_queued": 0, "build_success_rate": 60.0,
            "triage_total": 10, "triage_approved": 5, "triage_rejected": 3,
            "triage_deferred": 2, "triage_approve_rate": 50.0,
            "recent_triage_approved": 2, "recent_triage_rejected": 1,
            "recent_triage_deferred": 0,
            "queue_pending": 0, "queue_dispatched": 0,
            "queue_completed": 5, "queue_failed": 0,
            "published": 2, "publish_failed": 0,
            "gate_status": {}, "recent_cycle_errors": 0, "recent_cycles": 10,
            "quality": {
                "scored_builds": 5,
                "overall_avg": 45.0,
                "groups": {
                    "published": {"count": 2, "avg": 60.0, "min": 55.0, "max": 65.0},
                    "build_failed": {"count": 3, "avg": 35.0, "min": 26.0, "max": 40.0},
                },
                "suggested_threshold": 47.5,
                "threshold_rationale": "Midpoint between published avg (60.0) and failed avg (35.0)",
            },
        }

        digest = build_pipeline_health_digest(data)
        assert "Build Quality Scores" in digest
        assert "Scored builds: 5" in digest
        assert "Published: avg=60.0" in digest
        assert "Suggested quality threshold" in digest
        assert "47.5" in digest

    def test_no_quality_section_when_empty(self):
        """No quality section when no scored builds."""
        data = {
            "build_total": 0, "build_completed": 0, "build_failed": 0,
            "build_queued": 0, "build_success_rate": 0,
            "triage_total": 0, "triage_approved": 0, "triage_rejected": 0,
            "triage_deferred": 0, "triage_approve_rate": 0,
            "recent_triage_approved": 0, "recent_triage_rejected": 0,
            "recent_triage_deferred": 0,
            "queue_pending": 0, "queue_dispatched": 0,
            "queue_completed": 0, "queue_failed": 0,
            "published": 0, "publish_failed": 0,
            "gate_status": {}, "recent_cycle_errors": 0, "recent_cycles": 0,
            "quality": {"scored_builds": 0, "groups": {}},
        }

        digest = build_pipeline_health_digest(data)
        assert "Build Quality Scores" not in digest


# ---- Outcome reader tests ----

class TestOutcomeDigestQuality:
    """Tests for quality score section in outcome digest."""

    def test_quality_scores_in_digest(self):
        """Quality scores by outcome appear in digest."""
        import sys
        from pathlib import Path
        _st = str(Path.home() / "projects" / "st-factory")
        if _st not in sys.path:
            sys.path.insert(0, _st)
        from contracts.outcome_record import OutcomeRecord, TerminalOutcome

        records = [
            OutcomeRecord(
                idea_id=1, idea_title="Good", outcome=TerminalOutcome.PUBLISHED,
                overall_score=60.0,
            ),
            OutcomeRecord(
                idea_id=2, idea_title="Bad", outcome=TerminalOutcome.BUILD_FAILED,
                overall_score=30.0,
            ),
            OutcomeRecord(
                idea_id=3, idea_title="Also Bad", outcome=TerminalOutcome.BUILD_FAILED,
                overall_score=35.0,
            ),
        ]

        digest = build_outcome_digest(records)
        assert "Quality Scores by Outcome" in digest
        assert "published: avg=60.0" in digest
        assert "build_failed: avg=32.5" in digest

    def test_no_quality_section_without_scores(self):
        """No quality section when records have no overall_score."""
        import sys
        from pathlib import Path
        _st = str(Path.home() / "projects" / "st-factory")
        if _st not in sys.path:
            sys.path.insert(0, _st)
        from contracts.outcome_record import OutcomeRecord, TerminalOutcome

        records = [
            OutcomeRecord(
                idea_id=1, idea_title="No Score", outcome=TerminalOutcome.REJECTED,
            ),
        ]

        digest = build_outcome_digest(records)
        assert "Quality Scores by Outcome" not in digest
