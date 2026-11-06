"""Rebuild every table from the raw layer. Same raw input gives the same tables."""

import json
import logging
from datetime import UTC, datetime

import pandas as pd

from .config import Config
from .garmin import fit, parse
from .garmin.rawstore import RawStore
from .store import refresh_views, write_table
from .training import load, obsidian

log = logging.getLogger(__name__)


def _dates(df: pd.DataFrame) -> pd.DataFrame:
    """Store `day` as a date; rows sort by it for stable output."""
    df = df.copy()
    df["day"] = pd.to_datetime(df["day"]).dt.date
    return df.sort_values(list(df.columns[:2]), ignore_index=True)


def _payloads(raw: RawStore, endpoint: str) -> list:
    return [raw.load_json(endpoint, k) for k in raw.keys(endpoint)]


def _zone_versions(raw: RawStore) -> list:
    versions = []
    for path in sorted((raw.root / "hr_zones").glob("*.json")):
        stamp = path.stem.rsplit("_", 1)[1]
        t = datetime.strptime(stamp, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC)
        versions.append((t, json.loads(path.read_text())))
    return versions


def _fit_tables(raw: RawStore) -> tuple[pd.DataFrame, pd.DataFrame]:
    samples, rr = [], []
    for key in raw.keys("activity_files", ".zip"):
        data = fit.parse_activity_zip(
            raw.latest("activity_files", key, ".zip").read_bytes(), int(key)
        )
        samples.append(data.samples)
        rr.append(data.rr)
    if not samples:
        return pd.DataFrame(columns=fit.SAMPLE_COLS), pd.DataFrame(columns=fit.RR_COLS)
    return pd.concat(samples, ignore_index=True), pd.concat(rr, ignore_index=True)


def _sync_runs(cfg: Config) -> pd.DataFrame:
    path = cfg.data_dir / "garmin" / "sync_runs.jsonl"
    if not path.exists():
        return pd.DataFrame()
    return pd.DataFrame([json.loads(line) for line in path.read_text().splitlines()])


def rebuild(cfg: Config) -> dict[str, int]:
    """Write all tables and views. Returns row counts by table."""
    raw = RawStore(cfg.raw_dir)
    tables: dict[str, pd.DataFrame] = {}

    readings, nights = parse.parse_hrv(_payloads(raw, "hrv"))
    tables["hrv_readings"], tables["hrv_nightly"] = _dates(readings), _dates(nights)
    tables["sleep_nightly"] = _dates(parse.parse_sleep(_payloads(raw, "sleep")))
    hr, hr_daily = parse.parse_heart_rate(_payloads(raw, "heart_rate"), cfg.tz)
    tables["hr_2min"], tables["hr_daily"] = _dates(hr), _dates(hr_daily)
    summaries = _payloads(raw, "daily_summary")
    if summaries:
        tables["daily_summary"] = _dates(parse.parse_daily_summary(summaries))
    items = [
        (raw.load_json("activities", k), raw.load_json("activity_detail", k))
        for k in raw.keys("activities")
    ]
    activities = parse.parse_activities(items)
    tables["activities"] = activities
    tables["hr_zones"] = parse.parse_hr_zones(_zone_versions(raw))
    tables["activity_samples"], tables["activity_rr"] = _fit_tables(raw)

    result = obsidian.load_latest(cfg)
    for error in result.errors:
        log.warning("workout CSV %s", error)
    workouts = load.build_workouts(result.sets)
    wakes = tables["sleep_nightly"][["day", "sleep_end_utc"]]
    events = load.build_load_events(workouts, activities, cfg.tz, wakes)
    tables["workout_sets"], tables["workouts"] = result.sets, workouts
    tables["load_events"], tables["load_daily"] = events, load.build_load_daily(events)

    runs = _sync_runs(cfg)
    if not runs.empty:
        tables["sync_runs"] = runs

    for name, df in tables.items():
        write_table(df, cfg.tables_dir, name)
    refresh_views(cfg.tables_dir, cfg.db_path)
    return {name: len(df) for name, df in tables.items()}
