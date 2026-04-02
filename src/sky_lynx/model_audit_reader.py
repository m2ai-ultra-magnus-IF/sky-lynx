"""Model audit reader for Sky-Lynx.

Runs the model-audit benchmark runner against current pipeline models
and produces a digest summarizing quality scores and failure modes.

Data source: ~/projects/model-audit/ (benchmark runner)
Models tested: pipeline defaults from Metroplex + IdeaForge + Swindle configs.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

RUNNER_PATH = Path.home() / "projects" / "model-audit" / "runner.py"
RUNNER_VENV = Path.home() / "projects" / "model-audit" / "venv"

# Pipeline models to audit (component -> model ID)
PIPELINE_MODELS = {
    "spec_expansion": "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B",
    "idea_scoring": "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B",
    "listing_copy": "mistralai/Mistral-Small-3.2-24B-Instruct-2506",
    "qa_review": "gpt-4.1",
}

# Map each component to its most relevant benchmark
COMPONENT_BENCHMARKS = {
    "spec_expansion": "spec_expansion",
    "idea_scoring": "idea_scoring",
    "listing_copy": "listing_copy",
    "qa_review": "spec_expansion",  # Tyrest reviews specs, so spec quality matters
}


def _get_python() -> str:
    """Get the Python interpreter path from the model-audit venv."""
    venv_python = RUNNER_VENV / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def load_model_audit_data(
    models: dict[str, str] | None = None,
    timeout_seconds: int = 300,
) -> dict:
    """Run the benchmark runner against pipeline models and collect results.

    Args:
        models: Override dict of component -> model_id. Defaults to PIPELINE_MODELS.
        timeout_seconds: Max time to wait for all benchmarks.

    Returns:
        Dict with audit results per component, or empty dict on failure.
    """
    if not RUNNER_PATH.exists():
        logger.warning("Model audit runner not found at %s", RUNNER_PATH)
        return {}

    models = models or PIPELINE_MODELS
    results = {}

    # Deduplicate: group benchmarks by model to minimize API calls
    model_benchmarks: dict[str, list[str]] = {}
    for component, model in models.items():
        benchmark = COMPONENT_BENCHMARKS.get(component)
        if benchmark:
            model_benchmarks.setdefault(model, []).append(benchmark)

    for model, benchmarks in model_benchmarks.items():
        unique_benchmarks = list(set(benchmarks))
        benchmark_arg = ",".join(unique_benchmarks)

        logger.info("Auditing model %s on benchmarks: %s", model, benchmark_arg)

        try:
            env = os.environ.copy()
            result = subprocess.run(
                [
                    _get_python(),
                    str(RUNNER_PATH),
                    "--model", model,
                    "--benchmark", benchmark_arg,
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
                cwd=str(RUNNER_PATH.parent),
            )

            if result.returncode != 0:
                logger.warning(
                    "Runner failed for %s (exit %d): %s",
                    model, result.returncode, result.stderr[:300],
                )
                continue

            # Parse JSON output (comes after the progress lines)
            stdout = result.stdout
            # Find the JSON array in the output
            json_start = stdout.find("[")
            if json_start == -1:
                logger.warning("No JSON output from runner for %s", model)
                continue

            audit_data = json.loads(stdout[json_start:])
            for entry in audit_data:
                entry["model"] = model
                bid = entry.get("benchmark_id", "unknown")
                results[f"{model}:{bid}"] = entry

        except subprocess.TimeoutExpired:
            logger.warning("Model audit timed out for %s after %ds", model, timeout_seconds)
        except json.JSONDecodeError as e:
            logger.warning("Could not parse runner output for %s: %s", model, e)
        except Exception as e:
            logger.warning("Model audit failed for %s: %s", model, e)

    if not results:
        return {}

    # Compute summary stats
    total_checks = sum(r.get("total_checks", 0) for r in results.values())
    total_pass = sum(r.get("total_pass", 0) for r in results.values())
    critical_failures = [
        r for r in results.values() if not r.get("critical_pass", True)
    ]

    return {
        "results": results,
        "total_benchmarks": len(results),
        "total_checks": total_checks,
        "total_pass": total_pass,
        "pass_rate": total_pass / total_checks if total_checks > 0 else 0,
        "critical_failures": len(critical_failures),
        "models_tested": list(set(r["model"] for r in results.values())),
    }


def build_model_audit_digest(data: dict) -> str:
    """Format model audit results into a digest string for Claude analysis.

    Args:
        data: Output of load_model_audit_data().

    Returns:
        Markdown-formatted digest string.
    """
    if not data or not data.get("results"):
        return ""

    lines = [
        "## Model Audit Results\n",
        f"Models tested: {', '.join(data['models_tested'])}",
        f"Overall: {data['total_pass']}/{data['total_checks']} checks passed "
        f"({data['pass_rate']:.0%}), "
        f"{data['critical_failures']} critical failures\n",
    ]

    # Per-benchmark results
    for key, r in sorted(data["results"].items()):
        model_short = r["model"].split("/")[-1][:30]
        bid = r.get("benchmark_id", "unknown")
        status = "PASS" if r.get("critical_pass", False) else "**FAIL**"
        score = f"{r.get('total_pass', 0)}/{r.get('total_checks', 0)}"
        latency = r.get("latency_ms", 0)

        lines.append(f"### {model_short} x {bid} -- {status} ({score}, {latency}ms)")

        if r.get("error"):
            lines.append(f"  Error: {r['error']}")
            continue

        failed_checks = [
            c for c in r.get("checks", []) if not c.get("passed", True)
        ]
        if failed_checks:
            for c in failed_checks:
                sev = f" [{c['severity']}]" if c.get("severity") else ""
                lines.append(f"  FAIL{sev}: {c.get('id', '?')} -- {c.get('detail', '')}")
        else:
            lines.append("  All checks passed.")

        lines.append("")

    # Recommendations section
    if data["critical_failures"] > 0:
        lines.append("### Action Items")
        lines.append(
            "Critical failures detected. Consider evaluating alternative models "
            "for the affected pipeline components. Run `/model-audit compare` "
            "with candidate models before swapping."
        )

    return "\n".join(lines)
