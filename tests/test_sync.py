import json
from datetime import date

import pytest
from garmin_personal_api.errors import HttpError
from physio_state.config import Config, load_config
from physio_state.garmin import sync as s
from physio_state.garmin.api import MAX_TRIES, GarminApi
from physio_state.garmin.rawstore import RawStore


def http_error(status):
    return HttpError(status_code=status, method="GET", url="/x", response_text="")


class FakeApi:
    """Returns stable data; `responses` can be changed between runs."""

    def __init__(self):
        self.calls = []
        self.hrv = {"hrvSummary": {"calendarDate": "x"}}

    def call(self, method, *args, **kwargs):
        self.calls.append(method)
        if method == "get_daily_sleep":
            raise http_error(404)
        if method == "get_daily_summaries":
            return {"data": [{"calendarDate": "2026-11-06"}]}
        if method == "search_activities":
            return [
                {"activityId": 7, "startTimeGMT": "2026-11-05 08:00:00", "manualActivity": False}
            ]
        if method == "download_activity":
            return b"zip"
        return {
            "get_daily_hrv": self.hrv,
            "get_daily_heart_rate": {"heartRateValues": []},
            "get_activity": {"summaryDTO": {}},
            "get_heart_rate_zones": [{"sport": "DEFAULT"}],
            "get_weight_measurements": {
                "dailyWeightSummaries": [{"summaryDate": "2026-11-05", "allWeightMetrics": []}]
            },
        }[method]


def test_second_run_adds_nothing_and_changes_are_kept(tmp_path):
    cfg = Config(data_dir=tmp_path)
    api = FakeApi()
    first = s.sync(api, cfg, date(2026, 11, 6))
    assert first.status == "ok" and first.changed > 0
    assert s.sync(api, cfg, date(2026, 11, 6)).changed == 0
    assert api.calls.count("download_activity") == 1  # files are fetched once

    api.hrv = {"hrvSummary": {"calendarDate": "y"}}
    assert s.sync(api, cfg, date(2026, 11, 6)).changed == 4  # the 4 re-pulled days
    store = RawStore(cfg.raw_dir)
    assert len(list((store.root / "hrv").glob("2026-11-06_*"))) == 2
    assert store.keys("weight") == ["2026-11-05"]
    assert store.load_json("hrv", "2026-11-06")["hrvSummary"]["calendarDate"] == "y"


def test_failed_run_is_logged_and_raised(tmp_path):
    cfg = Config(data_dir=tmp_path)

    class Broken(FakeApi):
        def call(self, method, *a, **k):
            raise http_error(500)

    with pytest.raises(HttpError):
        s.sync(Broken(), cfg, date(2026, 11, 6))
    run = json.loads((tmp_path / "garmin" / "sync_runs.jsonl").read_text())
    assert run["status"] == "failed" and "HTTP 500" in run["error"]


def test_manifest_hash_matches_file(tmp_path):
    import hashlib

    store = RawStore(tmp_path)
    store.save("hrv", "2026-11-06", b"{}")
    entry = json.loads(store.manifest.read_text())
    assert entry["sha256"] == hashlib.sha256((tmp_path / entry["path"]).read_bytes()).hexdigest()


def test_date_chunks_cover_range_without_overlap():
    chunks = s.date_chunks(date(2026, 1, 1), date(2026, 3, 1), 28)
    assert chunks[0] == (date(2026, 1, 1), date(2026, 1, 28))
    assert chunks[-1][1] == date(2026, 3, 1)
    assert all((b - a).days < 28 for a, b in chunks)


def test_key_expiry_warning_window():
    cfg = Config(garmin_key_expires=date(2026, 11, 20))
    assert s.key_expiry_warning(cfg, date(2026, 11, 6)) is not None
    assert s.key_expiry_warning(cfg, date(2026, 11, 5)) is None
    assert s.key_expiry_warning(Config(), date(2026, 11, 6)) is None


def make_api(client, auth=None):
    api = GarminApi.__new__(GarminApi)
    api.delay_s, api._sleep, api._key = 0, lambda s: None, "k"
    api._client, api._auth = client, auth
    return api


def test_api_renews_token_once_on_401():
    class Token:
        access_token = "new"

    class Auth:
        renewals = 0

        def obtain_token(self, key, force_refresh=False):
            Auth.renewals += 1
            return Token

    class Client:
        def update_bearer_token(self, token):
            self.token = token

        def get_x(self):
            raise http_error(401)

    with pytest.raises(HttpError):
        make_api(Client(), Auth()).call("get_x")
    assert Auth.renewals == 1


def test_api_stops_retrying_on_429():
    class Client:
        tries = 0

        def get_x(self):
            Client.tries += 1
            raise http_error(429)

    with pytest.raises(HttpError):
        make_api(Client()).call("get_x")
    assert Client.tries == MAX_TRIES


def test_config_rejects_unknown_settings(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("subject_id = 'S02'\ngarmin_key_expires = 2027-01-01\n")
    cfg = load_config(path)
    assert cfg.subject_id == "S02" and cfg.garmin_key_expires == date(2027, 1, 1)
    path.write_text("typo = 1\n")
    with pytest.raises(ValueError, match="typo"):
        load_config(path)
