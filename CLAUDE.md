# CLAUDE.md - Sky-Lynx

## Quick Commands

```bash
# Development
source .venv/bin/activate
pip install -e ".[dev]"

# Run analysis
python -m sky_lynx.analyzer analyze --dry-run  # No API calls
python -m sky_lynx.analyzer analyze            # Full run
python -m sky_lynx.analyzer analyze --auto-apply  # Auto-apply CLAUDE.md changes

# Pipeline config proposals
python -m sky_lynx.analyzer list-proposals        # View pending proposals
python -m sky_lynx.analyzer apply-proposal 1      # Accept a proposal
python -m sky_lynx.analyzer reject-proposal 1     # Reject a proposal
python -m sky_lynx.analyzer check-proposals       # Squawk about overdue proposals

# Tests
pytest tests/
mypy src/
ruff check src/
```

## Project Purpose

Sky-Lynx is a continuous improvement agent that:
1. Parses Claude Code usage insights weekly
2. Identifies friction patterns and workflow inefficiencies
3. Recommends CLAUDE.md improvements
4. Creates draft PRs with proposed changes

## Architecture

```
analyzer.py          # Main orchestration, CLI entry point
insights_parser.py   # Read/aggregate facets/*.json
claude_client.py     # Shells out to `claude -p` so analysis runs under Max
report_writer.py     # Markdown report generation
pr_drafter.py        # GitHub PR via gh CLI
```

## Key Decisions

- **Semi-weekly analysis**: Runs Wed + Sun at 2 AM for tighter feedback loops (L5 roadmap: event-driven triggers next)
- **Draft PRs over direct commits**: Human review required before changes
- **Persona-driven analysis**: Uses ST Agent Registry for consistent voice
- **Trend-aware**: Compares current week to previous for context

## Data Sources

- **Input**: `~/.claude/usage-data/facets/*.json`
- **Outcome Records**: ST Records JSONL store (via `outcome_reader.py`)
- **IdeaForge Market Signals**: `~/projects/ideaforge/data/ideaforge.db` (via `ideaforge_reader.py`, read-only)
  - Signal type breakdown, idea classifications, score averages, top ideas
  - Override DB path with `IDEAFORGE_DB_PATH` env var
- **Metroplex Pipeline Health**: `~/projects/metroplex/data/metroplex.db` (via `metroplex_reader.py`, read-only)
  - Build success/failure rates, triage decisions, queue throughput, gate health
  - Override DB path with `METROPLEX_DB_PATH` env var
  - Enables pipeline config recommendations (thresholds, caps)
- **Research Signals**: ST Records `persona_metrics.db` (via `research_reader.py`, read-only)
  - Papers, tools, domain trends from research-agents project
  - Signal counts by source/relevance, persona-tagged findings, recent high-relevance signals
  - Override DB path with `ST_RECORDS_DB_PATH` env var
- **Output Report**: `~/documentation/improvements/YYYY-MM-DD-sky-lynx-report.md`
- **Persona**: `~/projects/st-agent-registry/personas/sky-lynx/persona.yaml`
- **Proposals**: `~/projects/sky-lynx/data/proposals.db` (pipeline config proposals with squawk pattern)

## Testing Strategy

- Unit tests for insights_parser (parsing, aggregation, trends)
- Unit tests for report_writer (markdown formatting)
- Integration test with fixture JSON files
- Manual verification of PR creation (--dry-run flag)
