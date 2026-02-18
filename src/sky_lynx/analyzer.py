"""Main analyzer module for Sky-Lynx.

Entry point for weekly analysis of Claude Code usage insights.

Usage:
    python -m sky_lynx.analyzer [--dry-run]
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from . import __version__
from .claude_client import AnalysisResult, analyze_insights
from .insights_parser import (
    TrendAnalysis,
    load_weekly_analysis,
)
from .ideaforge_reader import build_ideaforge_digest, load_ideaforge_data
from .outcome_reader import build_outcome_digest, load_outcome_records
from .pr_drafter import create_draft_pr
from .report_writer import write_weekly_report

# Load environment variables
load_dotenv()

# Also try shared env file
shared_env = Path.home() / ".env.shared"
if shared_env.exists():
    load_dotenv(shared_env)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def format_metrics_summary(analysis: TrendAnalysis) -> str:
    """Format trend analysis into a summary string for Claude.

    Args:
        analysis: TrendAnalysis from insights parser

    Returns:
        Formatted string summary
    """
    current = analysis.current
    lines = [
        f"**Period**: {current.period_start.strftime('%Y-%m-%d')} to {current.period_end.strftime('%Y-%m-%d')}",
        f"**Total Sessions**: {current.total_sessions}",
    ]

    # Add trend if available
    if analysis.previous:
        trend_symbol = "+" if analysis.session_count_change >= 0 else ""
        lines.append(f"**Session Trend**: {trend_symbol}{analysis.session_count_change:.1f}%")

    # Outcomes
    if current.outcomes:
        lines.append("")
        lines.append("**Outcomes**:")
        for outcome, count in current.outcomes.most_common():
            pct = (count / current.total_sessions * 100) if current.total_sessions > 0 else 0
            lines.append(f"  - {outcome}: {count} ({pct:.0f}%)")

    # Satisfaction
    if current.satisfaction:
        lines.append("")
        lines.append("**Satisfaction**:")
        for level, count in current.satisfaction.most_common():
            lines.append(f"  - {level}: {count}")
        if analysis.satisfaction_trend:
            lines.append(f"  - Trend: {analysis.satisfaction_trend}")

    # Helpfulness
    if current.helpfulness:
        lines.append("")
        lines.append("**Claude Helpfulness**:")
        for level, count in current.helpfulness.most_common():
            lines.append(f"  - {level}: {count}")

    # Session types
    if current.session_types:
        lines.append("")
        lines.append("**Session Types**:")
        for stype, count in current.session_types.most_common():
            lines.append(f"  - {stype}: {count}")

    # Friction (most important)
    if current.friction_counts:
        lines.append("")
        lines.append("**Friction Counts**:")
        total_friction = sum(current.friction_counts.values())
        for ftype, count in current.friction_counts.most_common():
            lines.append(f"  - {ftype}: {count}")
        lines.append(f"  - **Total**: {total_friction}")

        if analysis.previous:
            trend_symbol = "+" if analysis.friction_change >= 0 else ""
            lines.append(f"  - Friction Trend: {trend_symbol}{analysis.friction_change:.1f}%")

    # Goal categories
    if current.goal_categories:
        lines.append("")
        lines.append("**Goal Categories**:")
        for category, count in current.goal_categories.most_common(5):
            lines.append(f"  - {category}: {count}")

    # Primary successes
    if current.primary_successes:
        lines.append("")
        lines.append("**Primary Successes**:")
        for success, count in current.primary_successes.most_common(5):
            lines.append(f"  - {success}: {count}")

    return "\n".join(lines)


def run_analysis(dry_run: bool = False) -> tuple[TrendAnalysis, AnalysisResult]:
    """Run the full analysis pipeline.

    Args:
        dry_run: If True, skip API calls

    Returns:
        Tuple of (TrendAnalysis, AnalysisResult)
    """
    logger.info("Loading usage insights...")
    trend_analysis = load_weekly_analysis()

    if trend_analysis.current.total_sessions == 0:
        logger.warning("No sessions found for this week. Using available data.")

    logger.info(f"Found {trend_analysis.current.total_sessions} sessions this week")

    # Format for Claude
    metrics_summary = format_metrics_summary(trend_analysis)
    friction_details = trend_analysis.current.friction_details

    logger.info(f"Friction details: {len(friction_details)}")

    # Load outcome records from Snow-Town
    outcome_digest = None
    try:
        outcome_records = load_outcome_records()
        if outcome_records:
            outcome_digest = build_outcome_digest(outcome_records)
            logger.info(f"Loaded {len(outcome_records)} outcome records for analysis")
        else:
            logger.info("No outcome records available yet")
    except Exception as e:
        logger.warning(f"Could not load outcome records: {e}")

    # Load IdeaForge market signals
    ideaforge_digest = None
    try:
        ideaforge_data = load_ideaforge_data()
        if ideaforge_data:
            ideaforge_digest = build_ideaforge_digest(ideaforge_data)
            logger.info(f"Loaded IdeaForge data: {ideaforge_data.get('total_signals', 0)} signals, {ideaforge_data.get('total_ideas', 0)} ideas")
        else:
            logger.info("No IdeaForge data available")
    except Exception as e:
        logger.warning(f"Could not load IdeaForge data: {e}")

    # Run Claude analysis
    logger.info("Running Claude analysis..." if not dry_run else "Dry run - skipping API call")
    analysis_result = analyze_insights(
        metrics_summary=metrics_summary,
        friction_details=friction_details,
        dry_run=dry_run,
        outcome_digest=outcome_digest,
        ideaforge_digest=ideaforge_digest,
    )

    logger.info(f"Got {len(analysis_result.recommendations)} recommendations")

    return trend_analysis, analysis_result


def main() -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    parser = argparse.ArgumentParser(
        description="Sky-Lynx: Continuous Improvement Agent for Claude Code"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip API calls and PR creation",
    )
    parser.add_argument(
        "--no-pr",
        action="store_true",
        help="Generate report but skip PR creation",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"sky-lynx {__version__}",
    )

    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info(f"Sky-Lynx v{__version__} starting at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    try:
        # Run analysis
        trend_analysis, analysis_result = run_analysis(dry_run=args.dry_run)

        # Write report
        logger.info("Writing weekly report...")
        report_path = write_weekly_report(trend_analysis, analysis_result)
        logger.info(f"Report saved to: {report_path}")

        # Create PR (unless --no-pr or --dry-run)
        if not args.no_pr and not args.dry_run:
            logger.info("Creating draft PR...")
            pr_url = create_draft_pr(analysis_result)
            if pr_url:
                logger.info(f"Draft PR created: {pr_url}")
            else:
                logger.info("No changes to propose - skipping PR creation")
        else:
            logger.info("Skipping PR creation (--dry-run or --no-pr)")

        logger.info("=" * 60)
        logger.info("Sky-Lynx analysis complete")
        logger.info("=" * 60)

        return 0

    except Exception as e:
        logger.exception(f"Analysis failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
