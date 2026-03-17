"""Manifest refresh module for Sky-Lynx.

Lightweight daily pulse that scans all ST Metro projects with project.json
files, reads git metadata, computes health scores, and updates manifests.

Designed to run as a daily cron job separate from the full analysis cycle.
Sky-Lynx is the sole writer of project manifests.
"""

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("sky_lynx.manifest_refresh")

PROJECTS_ROOT = Path.home() / "projects"

# Health score weights (must sum to 100)
WEIGHT_RECENCY = 40
WEIGHT_BUILD = 30
WEIGHT_TESTS = 20
WEIGHT_DEPS = 10


def discover_projects(projects_root: Path | None = None) -> list[Path]:
    """Find all project directories containing a project.json manifest."""
    root = projects_root or PROJECTS_ROOT
    projects = []
    for entry in sorted(root.iterdir()):
        manifest = entry / "project.json"
        if entry.is_dir() and manifest.exists():
            projects.append(entry)
    return projects


def load_manifest(project_path: Path) -> dict | None:
    """Read and parse a project.json file."""
    manifest_path = project_path / "project.json"
    try:
        return json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read %s: %s", manifest_path, e)
        return None


def _run_git(project_path: Path, *args: str) -> str | None:
    """Run a git command in the project directory, return stdout or None."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_path), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def get_last_commit_date(project_path: Path) -> str | None:
    """Get ISO 8601 timestamp of the most recent non-auto-snapshot commit."""
    # Try to skip auto-snapshot commits
    output = _run_git(
        project_path,
        "log", "-1", "--format=%aI",
        "--grep=auto-snapshot", "--invert-grep",
    )
    if output:
        return output

    # Fallback: any commit
    return _run_git(project_path, "log", "-1", "--format=%aI")


def get_commits_30d(project_path: Path) -> int:
    """Count meaningful commits in the last 30 days (excluding auto-snapshots)."""
    output = _run_git(
        project_path,
        "rev-list", "--count", "HEAD", "--since=30 days ago",
        "--grep=auto-snapshot", "--invert-grep",
    )
    if output:
        try:
            return int(output)
        except ValueError:
            pass
    return 0


def compute_recency_score(last_commit: str | None, stale_threshold_days: int) -> int:
    """Compute 0-100 recency factor based on days since last commit."""
    if not last_commit:
        return 0

    try:
        last = datetime.fromisoformat(last_commit)
    except ValueError:
        return 0

    now = datetime.now(timezone.utc)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)

    days_old = (now - last).days

    if days_old <= stale_threshold_days:
        return 100
    elif days_old >= stale_threshold_days * 3:
        return 0
    else:
        # Linear decay from 100 to 0 over 2x threshold
        return max(0, 100 - (days_old - stale_threshold_days) * 100 // (stale_threshold_days * 2))


def compute_health_score(factors: dict[str, int]) -> int:
    """Compute weighted composite health score from individual factors."""
    return (
        factors.get("recency", 0) * WEIGHT_RECENCY
        + factors.get("build_pass", 0) * WEIGHT_BUILD
        + factors.get("test_coverage", 0) * WEIGHT_TESTS
        + factors.get("dependency_freshness", 0) * WEIGHT_DEPS
    ) // 100


def refresh_manifest(project_path: Path, manifest: dict) -> dict:
    """Update activity and health fields in a manifest dict."""
    activity = manifest.get("activity", {})
    stale_threshold = activity.get("stale_threshold_days", 14)

    # Update git metadata
    last_commit = get_last_commit_date(project_path)
    if last_commit:
        activity["last_commit"] = last_commit

    activity["commits_30d"] = get_commits_30d(project_path)
    manifest["activity"] = activity

    # Compute health factors
    factors = manifest.get("health", {}).get("factors", {})
    factors["recency"] = compute_recency_score(last_commit, stale_threshold)
    # build_pass, test_coverage, dependency_freshness: preserve existing values
    # (these require running tests / checking deps, which is the full analysis job)

    health_score = compute_health_score(factors)
    manifest["health"] = {
        "score": health_score,
        "factors": factors,
        "last_computed": datetime.now(timezone.utc).isoformat(),
    }

    # Auto-update status based on staleness
    if last_commit:
        try:
            last = datetime.fromisoformat(last_commit)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            days_old = (datetime.now(timezone.utc) - last).days
            if days_old > stale_threshold and manifest.get("status") == "active":
                manifest["status"] = "stale"
                logger.info("%s marked as stale (%d days inactive)", manifest.get("name"), days_old)
            elif days_old <= stale_threshold and manifest.get("status") == "stale":
                manifest["status"] = "active"
                logger.info("%s reactivated (recent commit)", manifest.get("name"))
        except ValueError:
            pass

    return manifest


def write_manifest(project_path: Path, manifest: dict) -> bool:
    """Write updated manifest back to project.json."""
    manifest_path = project_path / "project.json"
    try:
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        return True
    except OSError as e:
        logger.error("Failed to write %s: %s", manifest_path, e)
        return False


def run_refresh(
    projects_root: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Main entry point: scan, refresh, and update all project manifests.

    Returns summary dict with total, updated, errors, and stale counts.
    """
    projects = discover_projects(projects_root)
    summary: dict = {"total": len(projects), "updated": 0, "errors": [], "stale": []}

    for project_path in projects:
        manifest = load_manifest(project_path)
        if manifest is None:
            summary["errors"].append(str(project_path))
            continue

        name = manifest.get("name", project_path.name)
        old_score = manifest.get("health", {}).get("score", 0)

        manifest = refresh_manifest(project_path, manifest)

        new_score = manifest.get("health", {}).get("score", 0)
        status = manifest.get("status", "unknown")

        if status == "stale":
            summary["stale"].append(name)

        if dry_run:
            logger.info(
                "[DRY RUN] %s: health %d→%d, status=%s",
                name, old_score, new_score, status,
            )
        else:
            if write_manifest(project_path, manifest):
                summary["updated"] += 1
                logger.info(
                    "Updated %s: health %d→%d, status=%s",
                    name, old_score, new_score, status,
                )
            else:
                summary["errors"].append(name)

    return summary
