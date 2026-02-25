"""Telemetry reader for Sky-Lynx.

Reads structured JSONL telemetry data emitted by Data (ClaudeClaw) and
produces summary digests for the weekly analysis prompt.

Data source: ~/projects/claudeclaw/store/telemetry.jsonl
Override with TELEMETRY_JSONL_PATH environment variable.
"""

import json
import logging
import os
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TELEMETRY_PATH = Path.home() / "projects" / "claudeclaw" / "store" / "telemetry.jsonl"


def load_telemetry_data(path: Path | None = None) -> dict:
    """Load and aggregate telemetry data from ClaudeClaw's JSONL file.

    Args:
        path: Path to telemetry.jsonl. Defaults to env var or standard location.

    Returns:
        Dict with aggregated telemetry metrics.
        Empty dict if file is unavailable or empty.
    """
    telemetry_path = path or Path(
        os.environ.get("TELEMETRY_JSONL_PATH", str(DEFAULT_TELEMETRY_PATH))
    )

    if not telemetry_path.exists():
        logger.info(f"Telemetry file not found: {telemetry_path}")
        return {}

    events: list[dict] = []
    try:
        with open(telemetry_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError as e:
        logger.warning(f"Could not read telemetry file: {e}")
        return {}

    if not events:
        return {}

    # Aggregate by event type
    event_counts = Counter(e.get("event_type") for e in events)

    # Message type breakdown
    message_types = Counter(
        e.get("message_type")
        for e in events
        if e.get("event_type") == "message_received"
    )

    # Backend distribution
    backends = Counter(
        e.get("backend")
        for e in events
        if e.get("event_type") == "message_routed"
    )

    # Tool usage frequency
    tools = Counter(
        e.get("tool_name")
        for e in events
        if e.get("event_type") == "tool_used"
    )

    # Latency stats for agent_completed
    latencies = [
        e["latency_ms"]
        for e in events
        if e.get("event_type") == "agent_completed" and "latency_ms" in e
    ]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    max_latency = max(latencies) if latencies else 0

    # Success/failure for agent_completed
    completions = [e for e in events if e.get("event_type") == "agent_completed"]
    successes = sum(1 for e in completions if e.get("success"))
    failures = len(completions) - successes

    # Error breakdown
    errors = [e for e in events if e.get("event_type") == "error"]
    error_sources = Counter(e.get("error_source") for e in errors)

    # Scheduled task stats
    sched_events = [e for e in events if e.get("event_type") == "scheduled_task_executed"]
    sched_success = sum(1 for e in sched_events if e.get("success"))
    sched_failure = len(sched_events) - sched_success

    return {
        "total_events": len(events),
        "event_counts": dict(event_counts),
        "message_types": dict(message_types),
        "backends": dict(backends),
        "tools_top_20": dict(tools.most_common(20)),
        "avg_latency_ms": round(avg_latency),
        "max_latency_ms": max_latency,
        "completions": len(completions),
        "successes": successes,
        "failures": failures,
        "error_count": len(errors),
        "error_sources": dict(error_sources),
        "scheduled_tasks": len(sched_events),
        "scheduled_successes": sched_success,
        "scheduled_failures": sched_failure,
    }


def build_telemetry_digest(data: dict) -> str:
    """Format telemetry data into a markdown digest for the analysis prompt.

    Args:
        data: Dict from load_telemetry_data()

    Returns:
        Formatted markdown digest string
    """
    if not data:
        return "No telemetry data available from Data (ClaudeClaw)."

    lines = [
        f"**Total Events**: {data['total_events']}",
        "",
    ]

    # Message types
    msg_types = data.get("message_types", {})
    if msg_types:
        lines.append("**Message Types**:")
        for mtype, count in sorted(msg_types.items(), key=lambda x: -x[1]):
            lines.append(f"  - {mtype}: {count}")
        lines.append("")

    # Backend routing
    backends = data.get("backends", {})
    if backends:
        lines.append("**Backend Routing**:")
        for backend, count in sorted(backends.items(), key=lambda x: -x[1]):
            lines.append(f"  - {backend}: {count}")
        lines.append("")

    # Completion stats
    completions = data.get("completions", 0)
    if completions > 0:
        success_rate = data["successes"] / completions * 100
        lines.append(f"**Completions**: {completions} (success rate: {success_rate:.0f}%)")
        lines.append(f"**Average Latency**: {data['avg_latency_ms']}ms")
        lines.append(f"**Max Latency**: {data['max_latency_ms']}ms")
        lines.append("")

    # Tool usage
    tools = data.get("tools_top_20", {})
    if tools:
        lines.append("**Top Tools Used**:")
        for tool, count in sorted(tools.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"  - {tool}: {count}")
        lines.append("")

    # Errors
    error_count = data.get("error_count", 0)
    if error_count > 0:
        lines.append(f"**Errors**: {error_count}")
        error_sources = data.get("error_sources", {})
        for source, count in sorted(error_sources.items(), key=lambda x: -x[1]):
            lines.append(f"  - {source}: {count}")
        lines.append("")

    # Scheduled tasks
    sched = data.get("scheduled_tasks", 0)
    if sched > 0:
        lines.append(f"**Scheduled Tasks**: {sched} (success: {data['scheduled_successes']}, failed: {data['scheduled_failures']})")

    return "\n".join(lines)
