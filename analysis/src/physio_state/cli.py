"""Command line entry point: physio-state."""

import argparse
import logging
import sys
from datetime import date, datetime

from .build import rebuild
from .config import load_config
from .garmin import sync as garmin_sync
from .garmin.rawstore import RawStore
from .training import obsidian

log = logging.getLogger("physio_state")


def _print_counts(counts: dict[str, int]) -> None:
    for name, n in counts.items():
        print(f"  {name:18} {n}")


def _api(cfg):
    from .garmin.api import GarminApi

    return GarminApi(cfg.request_delay_s)


def _cmd_sync(cfg, args) -> int:
    today = datetime.now(cfg.tz).date()
    warning = garmin_sync.key_expiry_warning(cfg, today)
    if warning:
        log.warning(warning)
    api = _api(cfg)
    try:
        report = garmin_sync.sync(api, cfg, today)
    finally:
        api.close()
    if not obsidian.snapshot(cfg):
        log.warning("workout CSV not found: %s", cfg.workout_csv)
    print(f"sync {report.status}: {report.calls} calls, {report.changed} new or changed")
    _print_counts(rebuild(cfg))
    return 0


def _cmd_backfill(cfg, args) -> int:
    api = _api(cfg)
    try:
        report = garmin_sync.backfill(api, cfg, args.start, args.end)
    finally:
        api.close()
    print(f"backfill {report.status}: {report.calls} calls, {report.changed} new or changed")
    _print_counts(rebuild(cfg))
    return 0


def _cmd_status(cfg, args) -> int:
    raw = RawStore(cfg.raw_dir)
    for endpoint in ("hrv", "sleep", "heart_rate", "daily_summary", "activities"):
        keys = raw.keys(endpoint)
        span = f"{keys[0]} .. {keys[-1]}" if keys and endpoint != "activities" else ""
        print(f"{endpoint:14} {len(keys):5}  {span}")
    print(f"{'activity_files':14} {len(raw.keys('activity_files', '.zip')):5}")
    runs = cfg.data_dir / "garmin" / "sync_runs.jsonl"
    if runs.exists():
        print("last run:", runs.read_text().splitlines()[-1])
    expires = cfg.garmin_key_expires
    print(f"key expires: {expires or 'not set (garmin_key_expires in config)'}")
    return 0


def _cmd_rebuild(cfg, args) -> int:
    obsidian.snapshot(cfg)
    _print_counts(rebuild(cfg))
    return 0


def _cmd_study_length(cfg, args) -> int:
    from .models.simulate import study_length

    table = study_length(reps=args.reps)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(cfg.data_dir / "study_length.csv", index=False)
    print(table.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="physio-state")
    sub = p.add_subparsers(dest="command", required=True)
    garmin = sub.add_parser("garmin", help="Garmin data").add_subparsers(
        dest="garmin_command", required=True
    )
    garmin.add_parser("sync", help="daily sync, workout snapshot and table rebuild").set_defaults(
        func=_cmd_sync
    )
    b = garmin.add_parser("backfill", help="optional one-off import of a date range")
    b.add_argument("--start", type=date.fromisoformat, required=True)
    b.add_argument("--end", type=date.fromisoformat, required=True)
    b.set_defaults(func=_cmd_backfill)
    garmin.add_parser("status", help="what is stored").set_defaults(func=_cmd_status)
    sub.add_parser("rebuild", help="rebuild all tables from raw").set_defaults(func=_cmd_rebuild)
    model = sub.add_parser("model", help="modeling").add_subparsers(
        dest="model_command", required=True
    )
    s = model.add_parser("study-length", help="simulate how many weeks the study needs")
    s.add_argument("--reps", type=int, default=200)
    s.set_defaults(func=_cmd_study_length)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return args.func(load_config(), args)


if __name__ == "__main__":
    sys.exit(main())
