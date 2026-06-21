"""Per-site clickbait statistics tracking — running totals and daily averages."""

import json
import logging
import os
from collections import defaultdict
from datetime import date, datetime, timezone

log = logging.getLogger(__name__)

STATS_FILENAME = "stats.json"

SiteStats = dict[str, int | float | dict[str, int]]


def _stats_path(output_dir: str) -> str:
    return os.path.join(output_dir, STATS_FILENAME)


def load_stats(output_dir: str) -> dict[str, SiteStats]:
    """Load existing stats from output dir, or return empty structure."""
    path = _stats_path(output_dir)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load stats.json: %s — starting fresh", e)
    return {}


def save_stats(output_dir: str, stats: dict[str, SiteStats]) -> None:
    """Save stats to output dir."""
    path = _stats_path(output_dir)
    try:
        with open(path, "w") as f:
            json.dump(stats, f, indent=2)
        log.info("Stats saved to %s", path)
    except OSError as e:
        log.warning("Failed to save stats.json: %s", e)


def update_stats(
    stats: dict[str, SiteStats],
    per_site_counts: dict[str, int],
    today: date | None = None,
) -> dict[str, SiteStats]:
    """Update running stats with today's per-site clickbait counts.

    Args:
        stats: Existing stats dict (may be empty).
        per_site_counts: Mapping {site_name: clickbait_count} for this run.
        today: The date for this run (defaults to today UTC).

    Returns:
        Updated stats dict.
    """
    if today is None:
        today = date.today()
    today_key = today.isoformat()

    for site_name, clickbait_count in per_site_counts.items():
        if site_name not in stats:
            stats[site_name] = {
                "total_clickbait": 0,
                "total_runs": 0,
                "runs": {},
                "last_updated": "",
            }

        site = stats[site_name]
        site["total_clickbait"] = site["total_clickbait"] + clickbait_count
        site["total_runs"] = site["total_runs"] + 1
        site["runs"][today_key] = clickbait_count
        site["average_daily"] = round(site["total_clickbait"] / site["total_runs"], 1)
        site["last_updated"] = datetime.now(timezone.utc).isoformat()

    return stats


def get_site_stats(
    stats: dict[str, SiteStats], site_name: str
) -> dict:
    """Get formatted stats for a single site for template rendering.

    Returns dict with keys: total_clickbait, total_runs, average_daily, last_updated
    or None values if site not in stats yet.
    """
    site = stats.get(site_name)
    if not site:
        return {
            "total_clickbait": 0,
            "total_runs": 0,
            "average_daily": 0,
            "last_updated": None,
        }
    return {
        "total_clickbait": site["total_clickbait"],
        "total_runs": site["total_runs"],
        "average_daily": site.get("average_daily", 0),
        "last_updated": site.get("last_updated", ""),
    }