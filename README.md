# Sky-Lynx

Continuous improvement agent that analyzes Claude Code usage insights and recommends `~/CLAUDE.md` updates.

## Overview

Sky-Lynx runs weekly (Sundays 2 AM via cron) to:
1. Parse Claude Code insights data from `~/.claude/usage-data/facets/`
2. Analyze friction patterns, tool usage, and workflow efficiency
3. Generate a recommendations report
4. Create a draft PR with proposed `~/CLAUDE.md` changes

## Installation

```bash
cd /home/ubuntu/projects/sky-lynx
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Configuration

Create a `.env` file with:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GITHUB_TOKEN=ghp_...  # For PR creation
```

Or use the shared environment file at `~/.env.shared`.

## Usage

### Manual Run

```bash
# Dry run (no API calls, no PR creation)
python -m sky_lynx.analyzer --dry-run

# Full run
python -m sky_lynx.analyzer
```

### Automated (Cron)

Install the cron file:

```bash
sudo cp cron/sky-lynx /etc/cron.d/
sudo systemctl restart cron
```

## Output

### Weekly Report

Reports are saved to:
```
~/documentation/improvements/YYYY-MM-DD-sky-lynx-report.md
```

### Draft PRs

Draft PRs are created on the branch `sky-lynx/YYYY-MM-DD` with proposed CLAUDE.md changes.

## Architecture

```
src/sky_lynx/
├── __init__.py
├── analyzer.py          # Main entry point and orchestration
├── insights_parser.py   # Parse facets/*.json files
├── claude_client.py     # Anthropic API wrapper
├── report_writer.py     # Markdown report generation
└── pr_drafter.py        # GitHub PR creation via gh CLI
```

## Agent Persona

Sky-Lynx uses the Kaizen-inspired analyst persona defined in:
```
~/projects/st-agent-registry/personas/sky-lynx/persona.yaml
```

### Frameworks

- **Kaizen Analysis** - Small, incremental improvements; waste elimination (muda)
- **Feedback Loop Detection** - Recurring patterns vs one-time issues
- **Cognitive Load Management** - Automation candidates; context switching costs
- **Risk-Adjusted Prioritization** - Impact vs effort; reversibility assessment

## Development

```bash
# Run tests
pytest

# Type checking
mypy src/

# Linting
ruff check src/
```

## Related Projects

- **Agent Persona Academy**: Persona definition system
- **Ultra Magnus / idea-catcher**: Similar batch processing pattern
- **Perceptor**: Context sharing across conversations
