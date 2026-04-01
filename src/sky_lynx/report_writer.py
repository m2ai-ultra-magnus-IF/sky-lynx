"""Weekly report writer for Sky-Lynx.

Generates markdown reports from analysis results.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .claude_client import AnalysisResult, Recommendation
from .insights_parser import TrendAnalysis

if TYPE_CHECKING:
    from .auto_applicator import AutoApplyResult

logger = logging.getLogger(__name__)

# Import st-records contracts via path
_st_records_path = str(Path.home() / "projects" / "st-records")
if _st_records_path not in sys.path:
    sys.path.insert(0, _st_records_path)

from contracts.improvement_recommendation import (
    EvidenceBasis,
    ImprovementRecommendation,
    RecommendationType,
    TargetScope,
)
from contracts.store import ContractStore


def get_reports_dir() -> Path:
    """Get the path to the reports directory.

    Returns:
        Path to ~/documentation/improvements/
    """
    reports_dir = Path.home() / "documentation" / "improvements"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return reports_dir


def format_trend(value: float) -> str:
    """Format a percentage change with symbol.

    Args:
        value: Percentage change

    Returns:
        Formatted string like "+12%" or "-5%"
    """
    symbol = "+" if value >= 0 else ""
    return f"{symbol}{value:.0f}%"


def write_weekly_report(
    trend_analysis: TrendAnalysis,
    analysis_result: AnalysisResult,
    output_dir: Path | None = None,
    auto_apply_results: list[AutoApplyResult] | None = None,
) -> Path:
    """Write the weekly report to markdown file.

    Args:
        trend_analysis: TrendAnalysis from insights parser
        analysis_result: AnalysisResult from Claude
        output_dir: Optional override for output directory
        auto_apply_results: Optional results from auto-apply step

    Returns:
        Path to the written report file
    """
    output_dir = output_dir or get_reports_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    current = trend_analysis.current

    # Build report content
    lines = [
        "# Sky-Lynx Weekly Report",
        "",
        f"**Generated**: {today}",
        f"**Period**: {current.period_start.strftime('%Y-%m-%d')} to {current.period_end.strftime('%Y-%m-%d')}",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        analysis_result.executive_summary or "_No summary generated._",
        "",
        "---",
        "",
        "## Key Metrics",
        "",
        "| Metric | This Week | Trend |",
        "|--------|-----------|-------|",
    ]

    # Sessions row
    session_trend = (
        format_trend(trend_analysis.session_count_change)
        if trend_analysis.previous
        else "baseline"
    )
    lines.append(f"| Sessions | {current.total_sessions} | {session_trend} |")

    # Friction row
    total_friction = sum(current.friction_counts.values())
    friction_trend = (
        format_trend(trend_analysis.friction_change)
        if trend_analysis.previous
        else "baseline"
    )
    lines.append(f"| Friction Events | {total_friction} | {friction_trend} |")

    # Satisfaction row
    satisfied = current.satisfaction.get("likely_satisfied", 0)
    lines.append(
        f"| Satisfaction (likely_satisfied) | {satisfied} | {trend_analysis.satisfaction_trend} |"
    )

    lines.extend(
        [
            "",
            "### Outcomes Breakdown",
            "",
        ]
    )

    if current.outcomes:
        for outcome, count in current.outcomes.most_common():
            pct = (count / current.total_sessions * 100) if current.total_sessions > 0 else 0
            lines.append(f"- **{outcome}**: {count} ({pct:.0f}%)")
    else:
        lines.append("_No outcome data available._")

    lines.extend(
        [
            "",
            "### Friction Breakdown",
            "",
        ]
    )

    if current.friction_counts:
        for ftype, count in current.friction_counts.most_common():
            lines.append(f"- **{ftype}**: {count}")
    else:
        lines.append("_No friction recorded this week._")

    lines.extend(
        [
            "",
            "---",
            "",
            "## Friction Analysis",
            "",
            analysis_result.friction_analysis or "_No friction analysis generated._",
            "",
        ]
    )

    # Add friction details if available
    if current.friction_details:
        lines.extend(
            [
                "### Friction Details",
                "",
            ]
        )
        for detail in current.friction_details:
            lines.append(f"- {detail}")
        lines.append("")

    lines.extend(
        [
            "---",
            "",
            "## Recommendations",
            "",
        ]
    )

    if analysis_result.recommendations:
        # Group by priority
        high_priority = [r for r in analysis_result.recommendations if r.priority == "high"]
        medium_priority = [r for r in analysis_result.recommendations if r.priority == "medium"]
        low_priority = [r for r in analysis_result.recommendations if r.priority == "low"]

        if high_priority:
            lines.append("### High Priority")
            lines.append("")
            for i, rec in enumerate(high_priority, 1):
                lines.extend(_format_recommendation(i, rec))

        if medium_priority:
            lines.append("### Medium Priority")
            lines.append("")
            for i, rec in enumerate(medium_priority, 1):
                lines.extend(_format_recommendation(i, rec))

        if low_priority:
            lines.append("### Low Priority")
            lines.append("")
            for i, rec in enumerate(low_priority, 1):
                lines.extend(_format_recommendation(i, rec))
    else:
        lines.append("_No specific recommendations generated._")
        lines.append("")

    # Auto-applied rules section
    if auto_apply_results:
        applied = [r for r in auto_apply_results if r.applied]
        skipped = [r for r in auto_apply_results if not r.applied]
        if applied or skipped:
            lines.extend([
                "---",
                "",
                "## Auto-Applied Rules",
                "",
            ])
            if applied:
                lines.append("### Applied")
                lines.append("")
                for r in applied:
                    lines.append(f"- **{r.title}**: `{r.rule_text}`")
                lines.append("")
            if skipped:
                lines.append("### Skipped")
                lines.append("")
                for r in skipped:
                    lines.append(f"- **{r.title}**: {r.reason}")
                lines.append("")

    lines.extend(
        [
            "---",
            "",
            "## What's Working Well",
            "",
            analysis_result.whats_working or "_No positive patterns identified._",
            "",
            "---",
            "",
            "*This report was generated by [Sky-Lynx](https://github.com/m2ai-portfolio/sky-lynx), "
            "a continuous improvement agent for Claude Code.*",
        ]
    )

    # Write to file
    report_path = output_dir / f"{today}-sky-lynx-report.md"
    report_path.write_text("\n".join(lines))

    # Write JSON sidecar with structured recommendations
    if analysis_result.recommendations:
        write_recommendations_sidecar(
            analysis_result.recommendations,
            output_dir,
            today,
        )

    return report_path


def _format_recommendation(index: int, rec: Recommendation) -> list[str]:
    """Format a single recommendation for markdown output.

    Args:
        index: Recommendation number
        rec: Recommendation object

    Returns:
        List of markdown lines
    """
    lines = [
        f"{index}. **{rec.title}**",
    ]

    if rec.evidence:
        lines.append(f"   - **Evidence**: {rec.evidence}")

    if rec.suggested_change:
        lines.append(f"   - **Suggested Change**: {rec.suggested_change}")

    if rec.impact:
        lines.append(f"   - **Impact**: {rec.impact}")

    lines.append(f"   - **Reversibility**: {rec.reversibility.title()}")
    lines.append("")

    return lines


# Map Sky-Lynx recommendation_type strings to contract enum values
_RECOMMENDATION_TYPE_MAP = {
    "voice_adjustment": RecommendationType.VOICE_ADJUSTMENT,
    "framework_addition": RecommendationType.FRAMEWORK_ADDITION,
    "framework_refinement": RecommendationType.FRAMEWORK_REFINEMENT,
    "validation_marker_change": RecommendationType.VALIDATION_MARKER_CHANGE,
    "case_study_addition": RecommendationType.CASE_STUDY_ADDITION,
    "constraint_addition": RecommendationType.CONSTRAINT_ADDITION,
    "constraint_removal": RecommendationType.CONSTRAINT_REMOVAL,
    "claude_md_update": RecommendationType.CLAUDE_MD_UPDATE,
    "pipeline_change": RecommendationType.PIPELINE_CHANGE,
}


def _to_contract_recommendation(
    rec: Recommendation,
    session_id: str,
) -> ImprovementRecommendation:
    """Convert a Sky-Lynx Recommendation to a Snow-Town ImprovementRecommendation."""
    rec_type = _RECOMMENDATION_TYPE_MAP.get(
        rec.recommendation_type, RecommendationType.OTHER
    )

    scope = TargetScope.ALL_PERSONAS
    target_ids: list[str] = []

    return ImprovementRecommendation(
        recommendation_id=f"sl-{uuid.uuid4().hex[:8]}",
        session_id=session_id,
        recommendation_type=rec_type,
        target_system=rec.target_system,
        title=rec.title,
        description=rec.evidence,
        suggested_change=rec.suggested_change,
        scope=scope,
        target_persona_ids=target_ids,
        priority=rec.priority,
        impact=rec.impact,
        reversibility=rec.reversibility,
        evidence=EvidenceBasis(
            description=rec.evidence,
            pattern_frequency=1,
            signal_strength=0.7 if rec.priority == "high" else 0.5 if rec.priority == "medium" else 0.3,
        ),
    )


def write_recommendations_sidecar(
    recommendations: list[Recommendation],
    output_dir: Path,
    date_str: str,
) -> Path | None:
    """Write a JSON sidecar file with structured recommendations.

    Also appends each recommendation to st-records JSONL store.

    Args:
        recommendations: List of Recommendation objects
        output_dir: Directory to write the sidecar file
        date_str: Date string for the filename

    Returns:
        Path to the sidecar file, or None if no recommendations
    """
    if not recommendations:
        return None

    session_id = f"sky-lynx-{date_str}"

    # Convert to contract format
    contract_recs = [
        _to_contract_recommendation(rec, session_id) for rec in recommendations
    ]

    # Write JSON sidecar
    sidecar_path = output_dir / f"{date_str}-sky-lynx-recommendations.json"
    sidecar_data = [json.loads(rec.model_dump_json()) for rec in contract_recs]
    sidecar_path.write_text(json.dumps(sidecar_data, indent=2))
    logger.info(f"Wrote {len(contract_recs)} recommendations to {sidecar_path}")

    # Append to st-records JSONL store (with session_id dedup)
    try:
        store = ContractStore()
        existing = store.query_recommendations(limit=10000)
        existing_sessions = {r.session_id for r in existing if r.session_id}
        if session_id in existing_sessions:
            logger.info(
                f"Session '{session_id}' already has recommendations in store, skipping to avoid duplicates"
            )
        else:
            for rec in contract_recs:
                store.write_recommendation(rec)
            logger.info(f"Appended {len(contract_recs)} recommendations to st-records store")
        store.close()
    except Exception as e:
        logger.warning(f"Failed to write to st-records store: {e}")

    return sidecar_path
