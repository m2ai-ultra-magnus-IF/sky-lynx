# CLAUDE.md - Sky-Lynx

## Quick Commands

```bash
# Development
source .venv/bin/activate
pip install -e ".[dev]"

# Run manually
python -m sky_lynx.analyzer --dry-run  # No API calls
python -m sky_lynx.analyzer            # Full run

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
claude_client.py     # Anthropic API calls with persona
report_writer.py     # Markdown report generation
pr_drafter.py        # GitHub PR via gh CLI
```

## Key Decisions

- **Batch processing over real-time**: Weekly analysis is sufficient for improvement cadence
- **Draft PRs over direct commits**: Human review required before changes
- **Persona-driven analysis**: Uses Agent Persona Academy for consistent voice
- **Trend-aware**: Compares current week to previous for context

## Data Sources

- **Input**: `~/.claude/usage-data/facets/*.json`
- **Outcome Records**: Snow-Town JSONL store (via `outcome_reader.py`)
- **IdeaForge Market Signals**: `~/projects/ideaforge/data/ideaforge.db` (via `ideaforge_reader.py`, read-only)
  - Signal type breakdown, idea classifications, score averages, top ideas
  - Override DB path with `IDEAFORGE_DB_PATH` env var
- **Output Report**: `~/documentation/improvements/YYYY-MM-DD-sky-lynx-report.md`
- **Persona**: `~/projects/agent-persona-academy/personas/sky-lynx/persona.yaml`

## Testing Strategy

- Unit tests for insights_parser (parsing, aggregation, trends)
- Unit tests for report_writer (markdown formatting)
- Integration test with fixture JSON files
- Manual verification of PR creation (--dry-run flag)
