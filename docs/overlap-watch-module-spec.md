---
title: Overlap Watch Module Spec
status: DRAFT
author: Sky-Lynx / Matthew
created: 2026-03-24
---

# Overlap Watch Module

## Purpose

Detect when Anthropic ships native features that overlap with custom tooling in the ST Metro ecosystem. Surface actionable recommendations: retire, migrate, or keep the custom system based on an 80% overlap threshold.

## Problem Statement

Anthropic's feature velocity is accelerating. Recent examples:

| Native Feature | Custom System It Overlaps | Overlap % |
|---|---|---|
| Auto-memory | Perceptor (cross-session context) | ~30% |
| Auto-dream | Sky-Lynx memory consolidation slice | ~15-20% |
| Auto mode | Custom permission allowlists in hookify | ~10% |

Without systematic monitoring, custom systems accumulate maintenance cost for capabilities that are now free and better-integrated natively. Conversely, retiring too early loses edge-case coverage that native features don't handle.

## Architecture

### New Files

```
src/sky_lynx/overlap_reader.py    # Data collection + digest
src/sky_lynx/overlap_registry.py  # Custom systems manifest
tests/test_overlap_reader.py      # Tests
data/overlap-snapshots/           # Cached changelog state
```

### Integration Points

Follows the standard Sky-Lynx reader pattern:

```python
# In analyzer.py, alongside other readers:
from sky_lynx.overlap_reader import load_overlap_data, build_overlap_digest

# In run_analysis():
try:
    overlap_data = load_overlap_data()
    digests["overlap"] = build_overlap_digest(overlap_data)
except Exception as e:
    logger.warning(f"Overlap reader failed: {e}")
```

## Custom Systems Registry

`overlap_registry.py` maintains a declarative manifest of every custom system and what capabilities it provides. This is the "what we built" side of the comparison.

```python
@dataclass
class CustomSystem:
    name: str
    description: str
    capabilities: list[str]       # What it does (matchable keywords)
    maintenance_cost: str         # low / medium / high
    edge_cases: list[str]         # What native features likely won't cover
    retirement_blocker: str|None  # Hard dependency that prevents retirement
    last_reviewed: str            # ISO date of last overlap assessment

CUSTOM_SYSTEMS: list[CustomSystem] = [
    CustomSystem(
        name="perceptor",
        description="Cross-tool context sharing (Claude Code <-> Claude Desktop) via GitHub",
        capabilities=[
            "cross-tool-context", "context-sharing", "session-recovery",
            "disaster-recovery", "github-sync"
        ],
        maintenance_cost="low",
        edge_cases=[
            "Claude Desktop context (not just Claude Code)",
            "GitHub-backed DR with full history",
            "Explicit save/load semantics (not auto)"
        ],
        retirement_blocker="No native cross-tool context sharing exists",
        last_reviewed="2026-03-24"
    ),
    CustomSystem(
        name="sky-lynx-memory-consolidation",
        description="Memory dedup, pruning, date normalization within Sky-Lynx analysis",
        capabilities=[
            "memory-consolidation", "memory-pruning", "memory-dedup",
            "stale-entry-removal"
        ],
        maintenance_cost="low",
        edge_cases=[
            "Ecosystem-aware pruning (knows about ST Metro context)",
            "Coordinated with CLAUDE.md rule generation"
        ],
        retirement_blocker=None,
        last_reviewed="2026-03-24"
    ),
    CustomSystem(
        name="sky-lynx-learning-loop",
        description="Cross-system behavioral self-reflection and CLAUDE.md rule generation",
        capabilities=[
            "self-reflection", "behavioral-learning", "rule-generation",
            "cross-project-correlation", "friction-detection",
            "ecosystem-intelligence"
        ],
        maintenance_cost="medium",
        edge_cases=[
            "Reads IdeaForge, Metroplex, ST Records, ClaudeClaw data",
            "Domain-specific optimization (L5 autonomy goals)",
            "Pipeline config proposals",
            "Multi-target routing (personas, skills, schedules)"
        ],
        retirement_blocker="No native cross-system learning exists",
        last_reviewed="2026-03-24"
    ),
    CustomSystem(
        name="claudeclaw-memory",
        description="Per-agent memory system for Galvatron and Data agents",
        capabilities=[
            "agent-memory", "preference-learning", "conversation-history",
            "per-agent-isolation"
        ],
        maintenance_cost="medium",
        edge_cases=[
            "Per-agent isolation (not per-project)",
            "Telegram-native context for Galvatron",
            "Custom memory types beyond Claude Code's schema"
        ],
        retirement_blocker="ClaudeClaw agents are not Claude Code sessions",
        last_reviewed="2026-03-24"
    ),
    CustomSystem(
        name="hookify",
        description="Rule-based hooks to block dangerous or incorrect Claude Code actions",
        capabilities=[
            "permission-control", "action-blocking", "safety-rules",
            "model-version-protection"
        ],
        maintenance_cost="low",
        edge_cases=[
            "Domain-specific rules (verify-api-models, protect-state-machine)",
            "Project-scoped rules",
            "Custom block messages with remediation instructions"
        ],
        retirement_blocker="Auto mode classifier is generic, not domain-aware",
        last_reviewed="2026-03-24"
    ),
    CustomSystem(
        name="vault-second-brain",
        description="Obsidian vault as human-facing knowledge layer and project documentation",
        capabilities=[
            "knowledge-management", "project-documentation",
            "daily-notes", "cross-reference"
        ],
        maintenance_cost="low",
        edge_cases=[
            "Human-facing (not agent-facing)",
            "Obsidian plugin ecosystem",
            "Offline-first, local-first"
        ],
        retirement_blocker="Fundamentally different audience (human vs agent)",
        last_reviewed="2026-03-24"
    ),
]
```

## Data Collection (`overlap_reader.py`)

### Sources

1. **Claude Code changelog** — `claude changelog` CLI output (primary, no network needed)
2. **Anthropic blog** — `https://www.anthropic.com/news` (weekly fetch, cached)
3. **Claude Code `/memory` state** — detect newly enabled features
4. **`claude --help` diff** — new CLI flags between runs

### Collection Flow

```python
def load_overlap_data() -> dict:
    """Collect native feature state and compare against registry."""

    # 1. Get current Claude Code version + changelog diff since last run
    changelog = _get_changelog_diff(since=_last_snapshot_date())

    # 2. Fetch Anthropic blog headlines (cached, weekly)
    blog_entries = _fetch_blog_headlines()

    # 3. Snapshot current CLI flags
    cli_flags = _snapshot_cli_help()

    # 4. Read /memory feature states if accessible
    memory_state = _read_memory_feature_state()

    # 5. Diff against previous snapshot
    new_features = _diff_snapshots(changelog, blog_entries, cli_flags, memory_state)

    # 6. Match against registry
    overlaps = _compute_overlaps(new_features, CUSTOM_SYSTEMS)

    return {
        "new_features": new_features,
        "overlaps": overlaps,
        "registry": CUSTOM_SYSTEMS,
        "snapshot_date": datetime.now().isoformat()
    }
```

### Overlap Scoring

Each overlap is scored on three dimensions:

```python
@dataclass
class OverlapAssessment:
    custom_system: str
    native_feature: str
    capability_overlap_pct: int    # 0-100, keyword match + LLM assessment
    edge_case_coverage: int        # 0-100, how many edge cases native handles
    migration_effort: str          # trivial / moderate / significant
    recommendation: str            # retire / migrate / keep / monitor
    reasoning: str
```

**Decision matrix:**

| Capability Overlap | Edge Case Coverage | Recommendation |
|---|---|---|
| >= 80% | >= 80% | **Retire** — native feature is sufficient |
| >= 80% | < 80% | **Case-by-case** — flag for human review |
| 50-79% | any | **Monitor** — track in next cycles |
| < 50% | any | **Keep** — minimal overlap |

## Digest Format

```python
def build_overlap_digest(data: dict) -> str:
    """Build digest for Claude analysis prompt."""

    lines = ["## Overlap Watch"]

    if not data["overlaps"]:
        lines.append("No new native feature overlaps detected since last run.")
        return "\n".join(lines)

    lines.append(f"Detected {len(data['overlaps'])} overlap(s) since last analysis:\n")

    for overlap in data["overlaps"]:
        lines.append(f"### {overlap.native_feature} vs {overlap.custom_system}")
        lines.append(f"- Capability overlap: {overlap.capability_overlap_pct}%")
        lines.append(f"- Edge case coverage: {overlap.edge_case_coverage}%")
        lines.append(f"- Migration effort: {overlap.migration_effort}")
        lines.append(f"- Recommendation: **{overlap.recommendation}**")
        lines.append(f"- Reasoning: {overlap.reasoning}")
        lines.append("")

    # Surface systems that haven't been reviewed in 60+ days
    stale = [s for s in data["registry"]
             if _days_since(s.last_reviewed) > 60]
    if stale:
        lines.append("### Stale Reviews (>60 days)")
        for s in stale:
            lines.append(f"- **{s.name}**: last reviewed {s.last_reviewed}")

    return "\n".join(lines)
```

## Recommendation Routing

Overlap-watch recommendations use existing Sky-Lynx recommendation types:

| Scenario | recommendation_type | target_system |
|---|---|---|
| Retire a custom system | `constraint_removal` | `claude_md` |
| Migrate capability to native | `pipeline_change` | varies |
| Update registry after review | `other` | `claude_md` |
| Flag for human review | `other` | `claude_md` |

## Cron Cadence

Runs as part of the existing Sky-Lynx semi-weekly analysis (Wed + Sun 2 AM). No separate cron entry needed. The blog fetch is cached with a 7-day TTL so repeated runs within a week don't re-fetch.

## Output Example

In a Sky-Lynx report, this would appear as:

```markdown
## Overlap Watch

### Auto-dream vs sky-lynx-memory-consolidation
- Capability overlap: 85%
- Edge case coverage: 40% (no ecosystem awareness, no CLAUDE.md coordination)
- Migration effort: trivial
- Recommendation: **retire** — let auto-dream handle memory janitor work
- Reasoning: Auto-dream covers dedup, pruning, and date normalization natively.
  Sky-Lynx edge cases (ecosystem-aware pruning) are handled by the learning
  loop module, not the consolidation slice. Clean separation.

### Auto-dream vs sky-lynx-learning-loop
- Capability overlap: 15%
- Edge case coverage: 0% (no cross-system data, no rule generation)
- Migration effort: n/a
- Recommendation: **keep** — zero risk of replacement
- Reasoning: Auto-dream is single-scope housekeeping. The learning loop reads
  8+ data sources and generates behavioral rules. No overlap on core value.
```

## Testing Strategy

```python
# test_overlap_reader.py

def test_no_overlaps_when_no_new_features():
    """Empty changelog = no overlaps."""

def test_keyword_matching_finds_overlap():
    """A feature with 'memory-consolidation' matches the right system."""

def test_overlap_below_threshold_recommends_monitor():
    """50-79% overlap = monitor, not retire."""

def test_overlap_above_threshold_with_edge_cases():
    """>=80% overlap but <80% edge case coverage = case-by-case."""

def test_stale_review_detection():
    """Systems not reviewed in 60+ days appear in digest."""

def test_digest_format_empty():
    """No overlaps produces clean single-line digest."""

def test_registry_completeness():
    """All known custom systems are in the registry."""
```

## Implementation Order

1. **`overlap_registry.py`** — static manifest, no dependencies
2. **`overlap_reader.py`** — `_get_changelog_diff()` and `_snapshot_cli_help()` first (local only, no network)
3. **Blog fetcher** — add `_fetch_blog_headlines()` with caching
4. **`_compute_overlaps()`** — keyword matching + LLM-assisted assessment
5. **`build_overlap_digest()`** — format for analysis prompt
6. **Wire into `analyzer.py`** — standard reader integration
7. **Tests**
8. **First dry-run** — validate against known overlaps (auto-dream, auto-mode)

## Maintenance

The registry is the only part that requires manual updates — when Matthew builds or retires a custom system, add/remove it from `CUSTOM_SYSTEMS`. Sky-Lynx itself can recommend registry updates when it detects a new project via `manifest_refresh.py`.

## Open Questions

1. **LLM-assisted overlap scoring** — should `_compute_overlaps()` call Claude for nuanced assessment, or stick to keyword matching? LLM is more accurate but adds cost per run.
2. **Notification channel** — overlap alerts in the weekly report only, or also push to ClaudeClaw/Slack for high-overlap detections?
3. **Retroactive scan** — should the first run do a full historical scan of the Anthropic blog to establish a baseline, or start fresh from today?
