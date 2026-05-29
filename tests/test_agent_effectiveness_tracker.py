"""Tests for the (disabled) agent effectiveness tracker.

The ``agent_patches`` source table was deprecated 2026-05-12 and the
``ContractStore`` methods this module relied on were removed. The public
entry points are short-circuited to safe no-ops until a successor
patch-tracking source exists. These tests pin that contract: invocation must
NOT raise (previously raised ``AttributeError`` against the removed methods),
and must return empty/None so downstream callers omit the digest.
"""

import logging

from sky_lynx.agent_effectiveness_tracker import (
    build_agent_effectiveness_digest,
    run_agent_effectiveness_evaluation,
)


def test_run_agent_effectiveness_evaluation_returns_empty_without_raising():
    # Must not raise AttributeError on removed ContractStore methods.
    result = run_agent_effectiveness_evaluation()
    assert result == []


def test_build_agent_effectiveness_digest_returns_none_without_raising():
    # None signals downstream callers to omit the digest from the prompt.
    assert build_agent_effectiveness_digest() is None


def test_disabled_message_is_logged(caplog):
    with caplog.at_level(logging.INFO):
        run_agent_effectiveness_evaluation()
        build_agent_effectiveness_digest()
    assert any(
        "agent effectiveness tracking disabled" in rec.message
        and "no successor source" in rec.message
        for rec in caplog.records
    )
