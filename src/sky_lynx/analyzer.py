"""Main analyzer module for Sky-Lynx.

Entry point for weekly analysis of Claude Code usage insights.

Usage:
    python -m sky_lynx.analyzer [--dry-run]
    python -m sky_lynx.analyzer --auto-apply
    python -m sky_lynx.analyzer --rollback latest
"""

import argparse
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from . import __version__
from .auto_applicator import auto_apply_recommendations, rollback
from .claude_client import AnalysisResult, analyze_insights
from .effectiveness_tracker import (
    build_effectiveness_digest,
    run_effectiveness_evaluation,
)
from .ideaforge_reader import build_ideaforge_digest, load_ideaforge_data
from .metroplex_reader import build_pipeline_health_digest, load_metroplex_data
from .proposal_tracker import ProposalTracker
from .insights_parser import (
    TrendAnalysis,
    load_weekly_analysis,
)
from .linear_writer import create_linear_issues
from .outcome_reader import build_outcome_digest, load_outcome_records
from .pr_drafter import create_draft_pr
from .report_writer import write_weekly_report
from .research_reader import build_research_digest, load_research_signals
from .taste_reader import build_taste_digest, load_taste_data
from .telemetry_reader import build_telemetry_digest, load_telemetry_data

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

    # Load research signals from Snow-Town
    research_digest = None
    try:
        research_data = load_research_signals()
        if research_data and research_data.get("total_signals", 0) > 0:
            research_digest = build_research_digest(research_data)
            logger.info(f"Loaded research signals: {research_data.get('total_signals', 0)} total")
        else:
            logger.info("No research signals available yet")
    except Exception as e:
        logger.warning(f"Could not load research signals: {e}")

    # Load taste profile delta
    taste_digest = None
    try:
        taste_data = load_taste_data()
        if taste_data:
            taste_digest = build_taste_digest(taste_data)
            logger.info(f"Loaded taste delta from {taste_data['report_date']}")
        else:
            logger.info("No taste profile data available yet")
    except Exception as e:
        logger.warning(f"Could not load taste data: {e}")

    # Load Data (ClaudeClaw) telemetry
    telemetry_digest = None
    try:
        telemetry_data = load_telemetry_data()
        if telemetry_data:
            telemetry_digest = build_telemetry_digest(telemetry_data)
            logger.info(f"Loaded telemetry data: {telemetry_data.get('total_events', 0)} events")
        else:
            logger.info("No telemetry data available from Data")
    except Exception as e:
        logger.warning(f"Could not load telemetry data: {e}")

    # Load Metroplex pipeline health metrics
    pipeline_health_digest = None
    try:
        metroplex_data = load_metroplex_data()
        if metroplex_data:
            pipeline_health_digest = build_pipeline_health_digest(metroplex_data)
            logger.info(
                "Loaded Metroplex data: %d builds, %d triage decisions",
                metroplex_data.get("build_total", 0),
                metroplex_data.get("triage_total", 0),
            )
        else:
            logger.info("No Metroplex data available")
    except Exception as e:
        logger.warning(f"Could not load Metroplex data: {e}")

    # Evaluate effectiveness of past recommendations (before analysis, so results inform it)
    effectiveness_digest = None
    if not dry_run:
        try:
            eval_results = run_effectiveness_evaluation()
            if eval_results:
                logger.info(f"Evaluated {len(eval_results)} past recommendations")
        except Exception as e:
            logger.warning(f"Could not run effectiveness evaluation: {e}")

    try:
        effectiveness_digest = build_effectiveness_digest()
        if effectiveness_digest:
            logger.info("Loaded effectiveness digest for analysis context")
        else:
            logger.info("No past effectiveness data available yet")
    except Exception as e:
        logger.warning(f"Could not build effectiveness digest: {e}")

    # Run Claude analysis
    logger.info("Running Claude analysis..." if not dry_run else "Dry run - skipping API call")
    analysis_result = analyze_insights(
        metrics_summary=metrics_summary,
        friction_details=friction_details,
        dry_run=dry_run,
        outcome_digest=outcome_digest,
        ideaforge_digest=ideaforge_digest,
        research_digest=research_digest,
        telemetry_digest=telemetry_digest,
        taste_digest=taste_digest,
        effectiveness_digest=effectiveness_digest,
        pipeline_health_digest=pipeline_health_digest,
    )

    logger.info(f"Got {len(analysis_result.recommendations)} recommendations")

    return trend_analysis, analysis_result


def _run_persona_upgrader() -> None:
    """Trigger ST Factory persona upgrader for pending persona recs.

    Runs the upgrader as a subprocess to keep systems decoupled.
    """
    upgrader_path = (
        Path.home() / "projects" / "st-factory" / "scripts" / "persona_upgrader.py"
    )
    if not upgrader_path.exists():
        logger.warning(f"Persona upgrader not found at {upgrader_path}")
        return

    logger.info("Triggering persona upgrader for persona-targeted recommendations...")
    try:
        result = subprocess.run(
            [sys.executable, str(upgrader_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info("Persona upgrader completed successfully")
        else:
            logger.warning(
                f"Persona upgrader exited with code {result.returncode}: "
                f"{result.stderr[:200]}"
            )
    except subprocess.TimeoutExpired:
        logger.warning("Persona upgrader timed out after 120s")
    except Exception as e:
        logger.warning(f"Could not run persona upgrader: {e}")


def main() -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    parser = argparse.ArgumentParser(
        description="Sky-Lynx: Continuous Improvement Agent for Claude Code"
    )
    subparsers = parser.add_subparsers(dest="command")

    # Default analyze command (also runs with no subcommand)
    analyze_parser = subparsers.add_parser("analyze", help="Run weekly analysis (default)")
    analyze_parser.add_argument("--dry-run", action="store_true", help="Skip API calls and PR creation")
    analyze_parser.add_argument("--no-pr", action="store_true", help="Generate report but skip PR creation")
    analyze_parser.add_argument("--auto-apply", action="store_true", help="Auto-apply eligible high-confidence rules")
    analyze_parser.add_argument("--rollback", nargs="?", const="latest", default=None, metavar="BACKUP",
                                help="Rollback ~/CLAUDE.md to a backup (default: latest)")

    # Proposal management commands
    subparsers.add_parser("check-proposals", help="Check for unresolved proposals and squawk if overdue")
    subparsers.add_parser("list-proposals", help="List pending pipeline config proposals")

    apply_parser = subparsers.add_parser("apply-proposal", help="Accept a pipeline config proposal")
    apply_parser.add_argument("proposal_id", type=int, help="Proposal ID to accept")

    reject_parser = subparsers.add_parser("reject-proposal", help="Reject a pipeline config proposal")
    reject_parser.add_argument("proposal_id", type=int, help="Proposal ID to reject")

    # Legacy flags (for backward compatibility when no subcommand given)
    parser.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-pr", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--auto-apply", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--rollback", nargs="?", const="latest", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"sky-lynx {__version__}")

    args = parser.parse_args()

    # Handle proposal subcommands (no analysis needed)
    if args.command == "check-proposals":
        tracker = ProposalTracker()
        squawked = tracker.check_and_squawk()
        tracker.close()
        logger.info("Squawked about %d overdue proposals", squawked)
        return 0

    if args.command == "list-proposals":
        tracker = ProposalTracker()
        pending = tracker.get_pending()
        tracker.close()
        if not pending:
            print("No pending proposals.")
            return 0
        print(f"{'ID':>4}  {'Status':>10}  {'Squawks':>7}  Parameter")
        print("-" * 60)
        for p in pending:
            print(f"{p['id']:>4}  {p['status']:>10}  {p['squawk_count']:>7}  {p['parameter']}")
            print(f"      Current: {p['current_value']}  →  Proposed: {p['proposed_value']}")
            print(f"      Rationale: {p['rationale'][:80]}")
            print()
        return 0

    if args.command == "apply-proposal":
        tracker = ProposalTracker()
        if tracker.accept(args.proposal_id):
            print(f"Proposal #{args.proposal_id} accepted.")
        else:
            print(f"Proposal #{args.proposal_id} not found or already resolved.")
        tracker.close()
        return 0

    if args.command == "reject-proposal":
        tracker = ProposalTracker()
        if tracker.reject(args.proposal_id):
            print(f"Proposal #{args.proposal_id} rejected.")
        else:
            print(f"Proposal #{args.proposal_id} not found or already resolved.")
        tracker.close()
        return 0

    logger.info("=" * 60)
    logger.info(f"Sky-Lynx v{__version__} starting at {datetime.now().isoformat()}")
    logger.info("=" * 60)

    try:
        # Handle --rollback mode (standalone, no analysis needed)
        if args.rollback is not None:
            logger.info(f"Rolling back CLAUDE.md to: {args.rollback}")
            success = rollback(args.rollback)
            if success:
                logger.info("Rollback successful")
                return 0
            else:
                logger.error("Rollback failed")
                return 1

        # Run analysis
        trend_analysis, analysis_result = run_analysis(dry_run=args.dry_run)

        # Auto-apply eligible recommendations (before report/PR so report can include results)
        auto_apply_results = None
        auto_applied_titles: set[str] = set()
        if args.auto_apply:
            session_id = f"sky-lynx-{datetime.now().strftime('%Y-%m-%d')}"
            logger.info("Running auto-apply for eligible recommendations...")
            auto_apply_results = auto_apply_recommendations(
                analysis_result.recommendations,
                session_id=session_id,
                dry_run=args.dry_run,
            )
            auto_applied_titles = {
                r.title for r in auto_apply_results if r.applied
            }
            if auto_applied_titles:
                logger.info(f"Auto-applied {len(auto_applied_titles)} rules to ~/CLAUDE.md")

        # Create proposals for pipeline config recommendations
        pipeline_recs = [
            r for r in analysis_result.recommendations
            if r.target_system == "pipeline"
        ]
        if pipeline_recs and not args.dry_run:
            tracker = ProposalTracker()
            for rec in pipeline_recs:
                # Extract parameter and values from the recommendation
                tracker.propose(
                    parameter=rec.title,
                    current_value="(current)",
                    proposed_value=rec.suggested_change,
                    rationale=rec.evidence,
                )
            tracker.close()
            logger.info("Created %d pipeline config proposals", len(pipeline_recs))

        # Write report (pass auto-apply results for report section)
        logger.info("Writing weekly report...")
        report_path = write_weekly_report(
            trend_analysis,
            analysis_result,
            auto_apply_results=auto_apply_results,
        )
        logger.info(f"Report saved to: {report_path}")

        # Create PR (unless --no-pr or --dry-run), excluding auto-applied recs
        if not args.no_pr and not args.dry_run:
            logger.info("Creating draft PR...")
            pr_url = create_draft_pr(
                analysis_result,
                exclude_titles=auto_applied_titles,
            )
            if pr_url:
                logger.info(f"Draft PR created: {pr_url}")
            else:
                logger.info("No changes to propose - skipping PR creation")
        else:
            logger.info("Skipping PR creation (--dry-run or --no-pr)")

        # Create Linear issues for visibility (10a)
        session_id = f"sky-lynx-{datetime.now().strftime('%Y-%m-%d')}"
        try:
            created_issues = create_linear_issues(
                analysis_result.recommendations,
                session_id=session_id,
                dry_run=args.dry_run,
            )
            if created_issues:
                logger.info(
                    f"Created {len(created_issues)} Linear issues: "
                    f"{', '.join(created_issues)}"
                )
        except Exception as e:
            logger.warning(f"Could not create Linear issues: {e}")

        # Trigger persona upgrader for persona-targeted recs (10b)
        persona_recs = [
            r for r in analysis_result.recommendations
            if r.target_system == "persona"
        ]
        if persona_recs and not args.dry_run:
            _run_persona_upgrader()

        logger.info("=" * 60)
        logger.info("Sky-Lynx analysis complete")
        logger.info("=" * 60)

        return 0

    except Exception as e:
        logger.exception(f"Analysis failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
