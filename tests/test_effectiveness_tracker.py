"""Tests for the effectiveness tracker module."""

import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sky_lynx.effectiveness_tracker import (
    EffectivenessResult,
    _compute_friction_rate,
    _compute_outcome_rate,
    _compute_satisfaction_rate,
    _score_change,
    build_effectiveness_digest,
    evaluate_recommendation,
    run_effectiveness_evaluation,
)
from sky_lynx.insights_parser import WeeklyMetrics


# --- Fixtures ---


@pytest.fixture
def sample_metrics_good():
    """Metrics representing a good period (low friction, high satisfaction)."""
    return WeeklyMetrics(
        period_start=datetime(2026, 2, 1),
        period_end=datetime(2026, 2, 14),
        total_sessions=20,
        friction_counts=Counter({"wrong_output": 2, "slow_response": 1}),
        satisfaction=Counter({"high": 12, "very_high": 4, "medium": 3, "low": 1}),
        outcomes=Counter({"mostly_achieved": 15, "partially_achieved": 4, "not_achieved": 1}),
    )


@pytest.fixture
def sample_metrics_bad():
    """Metrics representing a bad period (high friction, low satisfaction)."""
    return WeeklyMetrics(
        period_start=datetime(2026, 1, 18),
        period_end=datetime(2026, 1, 31),
        total_sessions=20,
        friction_counts=Counter({"wrong_output": 8, "slow_response": 5, "context_lost": 3}),
        satisfaction=Counter({"high": 4, "very_high": 1, "medium": 8, "low": 7}),
        outcomes=Counter({"mostly_achieved": 8, "partially_achieved": 8, "not_achieved": 4}),
    )


@pytest.fixture
def mock_store(tmp_path):
    """Create a mock ContractStore with an in-memory DB."""
    # Patch the ContractStore import path
    db_path = tmp_path / "test_metrics.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS improvement_recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id TEXT NOT NULL UNIQUE,
            session_id TEXT,
            recommendation_type TEXT NOT NULL,
            target_system TEXT DEFAULT 'persona',
            title TEXT NOT NULL,
            priority TEXT DEFAULT 'medium',
            scope TEXT,
            target_department TEXT,
            status TEXT DEFAULT 'pending',
            emitted_at TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            effectiveness TEXT,
            effectiveness_score REAL,
            effectiveness_evaluated_at TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db_path


# --- Metric computation tests ---


class TestComputeMetrics:
    """Test individual metric computation functions."""

    def test_friction_rate_normal(self, sample_metrics_good):
        rate = _compute_friction_rate(sample_metrics_good)
        assert rate == 3 / 20  # (2 + 1) / 20

    def test_friction_rate_zero_sessions(self):
        m = WeeklyMetrics(period_start=datetime.now(), period_end=datetime.now(), total_sessions=0)
        assert _compute_friction_rate(m) == 0.0

    def test_satisfaction_rate_normal(self, sample_metrics_good):
        rate = _compute_satisfaction_rate(sample_metrics_good)
        assert rate == 16 / 20  # (12 + 4) / 20

    def test_satisfaction_rate_no_data(self):
        m = WeeklyMetrics(period_start=datetime.now(), period_end=datetime.now(), total_sessions=5)
        assert _compute_satisfaction_rate(m) == 0.0

    def test_outcome_rate_normal(self, sample_metrics_good):
        rate = _compute_outcome_rate(sample_metrics_good)
        assert rate == 15 / 20

    def test_outcome_rate_no_outcomes(self):
        m = WeeklyMetrics(period_start=datetime.now(), period_end=datetime.now(), total_sessions=5)
        assert _compute_outcome_rate(m) == 0.0


class TestScoreChange:
    """Test the _score_change function."""

    def test_improvement_higher_is_better(self):
        score = _score_change(0.5, 0.8, higher_is_better=True)
        assert score > 0  # 60% improvement

    def test_decline_higher_is_better(self):
        score = _score_change(0.8, 0.5, higher_is_better=True)
        assert score < 0  # 37.5% decline

    def test_improvement_lower_is_better(self):
        # Friction: lower is better
        score = _score_change(0.8, 0.5, higher_is_better=False)
        assert score > 0  # Friction decreased = good

    def test_decline_lower_is_better(self):
        score = _score_change(0.5, 0.8, higher_is_better=False)
        assert score < 0  # Friction increased = bad

    def test_no_change(self):
        score = _score_change(0.5, 0.5, higher_is_better=True)
        assert score == 0.0

    def test_both_zero(self):
        score = _score_change(0.0, 0.0, higher_is_better=True)
        assert score == 0.0

    def test_from_zero(self):
        score = _score_change(0.0, 0.5, higher_is_better=True)
        assert score == 1.0

    def test_clamp_to_range(self):
        # Extreme improvement (1000%) should clamp to 1.0
        score = _score_change(0.01, 1.0, higher_is_better=True)
        assert score == 1.0


class TestEvaluateRecommendation:
    """Test evaluate_recommendation with mocked insights."""

    def test_effective_recommendation(self, sample_metrics_bad, sample_metrics_good):
        """Recommendation that improved things scores as effective."""
        rec = {
            "recommendation_id": "sl-test-001",
            "title": "Reduce friction in testing",
            "emitted_at": (datetime.now() - timedelta(days=30)).isoformat(),
        }

        with patch("sky_lynx.effectiveness_tracker.parse_facets_in_range") as mock_parse:
            # Before: bad metrics, After: good metrics
            mock_parse.side_effect = [sample_metrics_bad, sample_metrics_good]

            result = evaluate_recommendation(rec, MagicMock())

        assert result is not None
        assert result.effectiveness == "effective"
        assert result.effectiveness_score > 0

    def test_harmful_recommendation(self, sample_metrics_good, sample_metrics_bad):
        """Recommendation that worsened things scores as harmful."""
        rec = {
            "recommendation_id": "sl-test-002",
            "title": "Bad change",
            "emitted_at": (datetime.now() - timedelta(days=30)).isoformat(),
        }

        with patch("sky_lynx.effectiveness_tracker.parse_facets_in_range") as mock_parse:
            # Before: good metrics, After: bad metrics
            mock_parse.side_effect = [sample_metrics_good, sample_metrics_bad]

            result = evaluate_recommendation(rec, MagicMock())

        assert result is not None
        assert result.effectiveness == "harmful"
        assert result.effectiveness_score < 0

    def test_neutral_recommendation(self, sample_metrics_good):
        """Recommendation with no significant change scores as neutral."""
        rec = {
            "recommendation_id": "sl-test-003",
            "title": "No effect change",
            "emitted_at": (datetime.now() - timedelta(days=30)).isoformat(),
        }

        with patch("sky_lynx.effectiveness_tracker.parse_facets_in_range") as mock_parse:
            # Before and after are the same
            mock_parse.side_effect = [sample_metrics_good, sample_metrics_good]

            result = evaluate_recommendation(rec, MagicMock())

        assert result is not None
        assert result.effectiveness == "neutral"
        assert abs(result.effectiveness_score) <= 0.1

    def test_too_recent_skipped(self):
        """Recommendations applied less than MIN_WEEKS_AFTER ago are skipped."""
        rec = {
            "recommendation_id": "sl-test-004",
            "title": "Too recent",
            "emitted_at": (datetime.now() - timedelta(days=3)).isoformat(),
        }

        result = evaluate_recommendation(rec, MagicMock())
        assert result is None

    def test_no_data_skipped(self):
        """Recommendations with no before/after data are skipped."""
        rec = {
            "recommendation_id": "sl-test-005",
            "title": "No data",
            "emitted_at": (datetime.now() - timedelta(days=30)).isoformat(),
        }

        with patch("sky_lynx.effectiveness_tracker.parse_facets_in_range") as mock_parse:
            mock_parse.return_value = None

            result = evaluate_recommendation(rec, MagicMock())

        assert result is None

    def test_insufficient_sessions_skipped(self):
        """Recommendations with too few sessions are skipped."""
        rec = {
            "recommendation_id": "sl-test-006",
            "title": "Few sessions",
            "emitted_at": (datetime.now() - timedelta(days=30)).isoformat(),
        }

        sparse_metrics = WeeklyMetrics(
            period_start=datetime.now() - timedelta(days=30),
            period_end=datetime.now(),
            total_sessions=2,  # < 3 minimum
        )

        with patch("sky_lynx.effectiveness_tracker.parse_facets_in_range") as mock_parse:
            mock_parse.return_value = sparse_metrics

            result = evaluate_recommendation(rec, MagicMock())

        assert result is None

    def test_missing_emitted_at(self):
        """Recommendations without emitted_at are skipped."""
        rec = {
            "recommendation_id": "sl-test-007",
            "title": "No date",
            "emitted_at": "",
        }
        result = evaluate_recommendation(rec, MagicMock())
        assert result is None

    def test_result_has_reasoning(self, sample_metrics_bad, sample_metrics_good):
        """Result includes human-readable reasoning."""
        rec = {
            "recommendation_id": "sl-test-008",
            "title": "Test reasoning",
            "emitted_at": (datetime.now() - timedelta(days=30)).isoformat(),
        }

        with patch("sky_lynx.effectiveness_tracker.parse_facets_in_range") as mock_parse:
            mock_parse.side_effect = [sample_metrics_bad, sample_metrics_good]

            result = evaluate_recommendation(rec, MagicMock())

        assert result is not None
        assert len(result.reasoning) > 0
        assert "friction" in result.reasoning.lower() or "satisfaction" in result.reasoning.lower()


class TestRunEffectivenessEvaluation:
    """Test the full evaluation run."""

    def test_no_pending_returns_empty(self):
        with patch("sky_lynx.effectiveness_tracker.ContractStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_applied_recommendations_for_evaluation.return_value = []
            MockStore.return_value = mock_store

            results = run_effectiveness_evaluation()

        assert results == []

    def test_evaluates_and_writes_back(self, sample_metrics_bad, sample_metrics_good):
        with patch("sky_lynx.effectiveness_tracker.ContractStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_applied_recommendations_for_evaluation.return_value = [
                {
                    "recommendation_id": "sl-eval-001",
                    "title": "Test eval",
                    "session_id": "sky-lynx-2026-02-01",
                    "recommendation_type": "claude_md_update",
                    "target_system": "claude_md",
                    "priority": "high",
                    "emitted_at": (datetime.now() - timedelta(days=30)).isoformat(),
                    "raw_json": "{}",
                }
            ]
            MockStore.return_value = mock_store

            with patch("sky_lynx.effectiveness_tracker.parse_facets_in_range") as mock_parse:
                mock_parse.side_effect = [sample_metrics_bad, sample_metrics_good]

                results = run_effectiveness_evaluation()

        assert len(results) == 1
        assert results[0].effectiveness == "effective"
        # Should have written back to store
        mock_store.update_recommendation_effectiveness.assert_called_once()
        call_args = mock_store.update_recommendation_effectiveness.call_args
        assert call_args.kwargs["recommendation_id"] == "sl-eval-001"
        assert call_args.kwargs["effectiveness"] == "effective"


class TestBuildEffectivenessDigest:
    """Test digest generation for Claude analysis context."""

    def test_no_data_returns_none(self):
        with patch("sky_lynx.effectiveness_tracker.ContractStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_effectiveness_summary.return_value = {}
            MockStore.return_value = mock_store

            digest = build_effectiveness_digest()

        assert digest is None

    def test_with_data_returns_formatted_digest(self):
        with patch("sky_lynx.effectiveness_tracker.ContractStore") as MockStore:
            mock_store = MagicMock()
            mock_store.get_effectiveness_summary.return_value = {
                "effective": {"count": 3, "avg_score": 0.45},
                "neutral": {"count": 2, "avg_score": 0.02},
                "harmful": {"count": 1, "avg_score": -0.3},
            }

            # Mock the conn for raw SQL query
            mock_conn = MagicMock()
            mock_row = {
                "recommendation_id": "sl-001",
                "title": "Test rec",
                "recommendation_type": "claude_md_update",
                "effectiveness": "effective",
                "effectiveness_score": 0.45,
                "effectiveness_evaluated_at": "2026-02-15T10:00:00",
            }
            mock_conn.execute.return_value.fetchall.return_value = [mock_row]
            mock_store._get_conn.return_value = mock_conn
            MockStore.return_value = mock_store

            digest = build_effectiveness_digest()

        assert digest is not None
        assert "Past Recommendation Effectiveness" in digest
        assert "effective" in digest
        assert "harmful" in digest
        assert "Total evaluated" in digest


class TestEffectivenessResult:
    """Test the EffectivenessResult model."""

    def test_valid_result(self):
        result = EffectivenessResult(
            recommendation_id="sl-001",
            title="Test",
            effectiveness="effective",
            effectiveness_score=0.5,
            reasoning="friction decreased",
        )
        assert result.effectiveness_score == 0.5

    def test_score_bounds(self):
        with pytest.raises(Exception):
            EffectivenessResult(
                recommendation_id="sl-001",
                title="Test",
                effectiveness="effective",
                effectiveness_score=1.5,  # > 1.0
                reasoning="test",
            )


class TestStoreEffectivenessColumns:
    """Test ST Records store effectiveness columns (integration)."""

    def test_update_and_query_effectiveness(self, mock_store):
        """Test writing and reading effectiveness data."""
        db_path = mock_store
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Insert a recommendation
        conn.execute(
            """INSERT INTO improvement_recommendations
            (recommendation_id, recommendation_type, title, status, emitted_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)""",
            ("sl-eff-001", "claude_md_update", "Test Rec", "applied",
             datetime.now().isoformat(), "{}"),
        )
        conn.commit()

        # Update effectiveness
        conn.execute(
            """UPDATE improvement_recommendations
            SET effectiveness = ?, effectiveness_score = ?, effectiveness_evaluated_at = ?
            WHERE recommendation_id = ?""",
            ("effective", 0.45, datetime.now().isoformat(), "sl-eff-001"),
        )
        conn.commit()

        # Read back
        row = conn.execute(
            "SELECT effectiveness, effectiveness_score FROM improvement_recommendations WHERE recommendation_id = ?",
            ("sl-eff-001",),
        ).fetchone()

        assert row["effectiveness"] == "effective"
        assert row["effectiveness_score"] == 0.45

        # Query unevaluated
        unevaluated = conn.execute(
            "SELECT * FROM improvement_recommendations WHERE status = 'applied' AND effectiveness IS NULL"
        ).fetchall()
        assert len(unevaluated) == 0  # Already evaluated

        conn.close()

    def test_get_applied_without_evaluation(self, mock_store):
        """Test querying applied but not-yet-evaluated recommendations."""
        db_path = mock_store
        conn = sqlite3.connect(str(db_path))

        # Insert applied recommendation without effectiveness
        conn.execute(
            """INSERT INTO improvement_recommendations
            (recommendation_id, recommendation_type, title, status, emitted_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)""",
            ("sl-pending-001", "claude_md_update", "Pending Eval", "applied",
             datetime.now().isoformat(), "{}"),
        )
        conn.commit()

        # Query
        rows = conn.execute(
            """SELECT * FROM improvement_recommendations
            WHERE status = 'applied' AND effectiveness IS NULL"""
        ).fetchall()
        assert len(rows) == 1

        conn.close()
