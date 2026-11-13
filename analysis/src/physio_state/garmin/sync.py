"""Fetch Garmin data into the raw store. Safe to repeat: unchanged data adds nothing."""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta

from garmin_personal_api.errors import HttpError

from ..config import Config
from .rawstore import RawStore

log = logging.getLogger(__name__)
DAILY_SUMMARY_CHUNK_DAYS = 28
ACTIVITY_PAGE = 45
HRV_SUMMARY_CHUNK_DAYS = 365
WEIGHT_CHUNK_DAYS = 90
EXPIRY_WARNING_DAYS = 14


@dataclass
class RunReport:
    kind: str
    started_utc: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )
    finished_utc: str | None = None
    status: str = "running"
    start_date: str | None = None
    end_date: str | None = None
    calls: int = 0
    changed: int = 0
    error: str | None = None


def key_expiry_warning(cfg: Config, today: date) -> str | None:
    if cfg.garmin_key_expires is None:
        return None
    left = (cfg.garmin_key_expires - today).days
    if left <= EXPIRY_WARNING_DAYS:
        return f"Garmin API key expires in {left} days ({cfg.garmin_key_expires})"
    return None


def date_chunks(start: date, end: date, days: int) -> list[tuple[date, date]]:
    chunks = []
    while start <= end:
        stop = min(start + timedelta(days=days - 1), end)
        chunks.append((start, stop))
        start = stop + timedelta(days=1)
    return chunks


class Syncer:
    def __init__(self, api, store: RawStore, report: RunReport) -> None:
        self.api, self.store, self.report = api, store, report

    def _get(self, method: str, *args, **kwargs):
        """Call the API; a 404 means no data for that request."""
        self.report.calls += 1
        try:
            return self.api.call(method, *args, **kwargs)
        except HttpError as e:
            if e.status_code == 404:
                return None
            raise

    def _save(self, endpoint: str, key: str, payload) -> None:
        if payload:
            self.report.changed += self.store.save_json(endpoint, key, payload)

    def fetch_day(self, day: date) -> None:
        key = day.isoformat()
        self._save("hrv", key, self._get("get_daily_hrv", day))
        self._save("sleep", key, self._get("get_daily_sleep", day))
        self._save("heart_rate", key, self._get("get_daily_heart_rate", day))

    def fetch_daily_summaries(self, start: date, end: date) -> None:
        for a, b in date_chunks(start, end, DAILY_SUMMARY_CHUNK_DAYS):
            payload = self._get("get_daily_summaries", a, b) or {}
            for item in payload.get("data", []):
                self._save("daily_summary", item["calendarDate"], item)

    def fetch_weight(self, start: date, end: date) -> None:
        """One raw file per day that has weigh-ins (all measurements of that day)."""
        for a, b in date_chunks(start, end, WEIGHT_CHUNK_DAYS):
            payload = self._get("get_weight_measurements", a, b) or {}
            for item in payload.get("dailyWeightSummaries") or []:
                self._save("weight", item["summaryDate"], item)

    def fetch_activities(self, start: date, end: date) -> None:
        offset = 0
        while True:
            page = self._get(
                "search_activities",
                start=offset,
                limit=ACTIVITY_PAGE,
                start_date=start,
                end_date=end,
            )
            for item in page or []:
                self._fetch_activity(item)
            if not page or len(page) < ACTIVITY_PAGE:
                return
            offset += ACTIVITY_PAGE

    def _fetch_activity(self, item: dict) -> None:
        key = str(item["activityId"])
        self._save("activities", key, item)
        self._save("activity_detail", key, self._get("get_activity", key))
        if self.store.latest("activity_files", key, ".zip") is None and not item.get(
            "manualActivity"
        ):
            content = self._get("download_activity", key)
            if content:
                self.report.changed += self.store.save("activity_files", key, content, ".zip")

    def fetch_zones(self) -> None:
        self._save("hr_zones", "current", self._get("get_heart_rate_zones"))


def _newest_activity_date(store: RawStore) -> date | None:
    newest = None
    for key in store.keys("activities"):
        text = store.load_json("activities", key).get("startTimeGMT")
        if text:
            d = date.fromisoformat(text[:10])
            newest = d if newest is None or d > newest else newest
    return newest


def _finish(report: RunReport, cfg: Config) -> None:
    report.finished_utc = datetime.now(UTC).isoformat(timespec="seconds")
    path = cfg.data_dir / "garmin" / "sync_runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(asdict(report)) + "\n")


def _run(report: RunReport, cfg: Config, work) -> RunReport:
    try:
        work()
        report.status = "ok"
    except Exception as e:  # recorded in the run log, then raised for the exit code
        report.status, report.error = "failed", f"{type(e).__name__}: {e}"
        raise
    finally:
        _finish(report, cfg)
    return report


def sync(api, cfg: Config, today: date) -> RunReport:
    """Daily job: re-pull recent days, because values change after the first sync."""
    store = RawStore(cfg.raw_dir)
    start = today - timedelta(days=cfg.repull_days)
    report = RunReport("sync", start_date=start.isoformat(), end_date=today.isoformat())
    syncer = Syncer(api, store, report)

    def work() -> None:
        for i in range((today - start).days + 1):
            syncer.fetch_day(start + timedelta(days=i))
        syncer.fetch_daily_summaries(start, today)
        syncer.fetch_weight(start, today)
        newest = _newest_activity_date(store)
        since = start if newest is None else min(start, newest - timedelta(days=cfg.repull_days))
        syncer.fetch_activities(since, today)
        syncer.fetch_zones()

    return _run(report, cfg, work)


def backfill(api, cfg: Config, start: date, end: date) -> RunReport:
    """Optional one-off import. Only nights known to have HRV get per-night calls."""
    store = RawStore(cfg.raw_dir)
    report = RunReport("backfill", start_date=start.isoformat(), end_date=end.isoformat())
    syncer = Syncer(api, store, report)

    def work() -> None:
        for a, b in date_chunks(start, end, HRV_SUMMARY_CHUNK_DAYS):
            payload = syncer._get("get_daily_hrv_summaries", a, b) or {}
            for item in payload.get("hrvSummaries", []):
                if item.get("lastNightAvg") is None:
                    continue
                day = date.fromisoformat(item["calendarDate"])
                if store.latest("hrv", day.isoformat()) is None:
                    syncer.fetch_day(day)
        syncer.fetch_daily_summaries(start, end)
        syncer.fetch_weight(start, end)
        syncer.fetch_activities(start, end)

    return _run(report, cfg, work)
