"""Parsers on small made-up payloads with the real response shape.

The repo is public, so no real responses are committed as fixtures.
"""

from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from physio_state.garmin import fit, parse

TZ = ZoneInfo("Europe/Bratislava")


def hrv_payload(values=(60, 70, 80, 90)):
    return {
        "userProfilePk": 123,
        "hrvSummary": {
            "calendarDate": "2026-10-26",
            "lastNightAvg": 75,
            "lastNight5MinHigh": 90,
            "weeklyAvg": 70,
            "baseline": {"lowUpper": 50, "balancedLow": 60, "balancedUpper": 80},
            "status": "BALANCED",
        },
        "hrvReadings": [
            {
                "hrvValue": v,
                "readingTimeGMT": f"2026-10-25T23:{i * 5:02d}:00.0",
                "readingTimeLocal": f"2026-10-26T00:{i * 5:02d}:00.0",
            }
            for i, v in enumerate(values)
        ],
        "sleepStartTimestampGMT": "2026-10-25T22:00:00.0",
        "sleepEndTimestampGMT": "2026-10-26T05:00:00.0",
    }


def test_hrv_aggregates_are_recomputed_from_readings():
    readings, nights = parse.parse_hrv([hrv_payload()])
    n = nights.iloc[0]
    assert len(readings) == n["n_readings"] == 4
    assert n["hrv_mean_ms"] == pytest.approx(n["garmin_last_night_avg"])
    assert n["hrv_first_half_ms"] == 65 and n["hrv_second_half_ms"] == 85
    assert readings["utc_offset_min"].eq(60).all()


def test_hrv_status_without_readings_stays_missing():
    _, nights = parse.parse_hrv([hrv_payload(values=())])
    assert nights.iloc[0]["n_readings"] == 0
    assert pd.isna(nights.iloc[0]["hrv_mean_ms"])


def test_sleep_uses_true_utc_and_skips_days_without_a_night():
    end = 1793000000000
    payload = {
        "calendarDate": "2026-10-26",
        "sleepStartTimestampGMT": end - 25200000,
        "sleepEndTimestampGMT": end,
        "sleepEndTimestampLocal": end + 3600000,  # "local" epochs are shifted
        "sleepTimeSeconds": 25200,
        "userProfilePK": 123,
        "sleepScores": {"overall": {"value": 80}},
    }
    df = parse.parse_sleep([payload, {"calendarDate": "2026-10-27"}])
    assert len(df) == 1
    assert df.iloc[0]["sleep_end_utc"] == pd.Timestamp(end, unit="ms", tz="UTC")
    assert df.iloc[0]["utc_offset_min"] == 60
    assert df.iloc[0]["garmin_sleep_score"] == 80


def test_heart_rate_offset_follows_the_clock_change():
    start = pd.Timestamp("2026-10-24T22:00:00", tz="UTC")  # local midnight, 25 Oct
    times = [start + pd.Timedelta(hours=h) for h in (0, 24)]
    payload = {
        "calendarDate": "2026-10-25",
        "heartRateValues": [[int(t.timestamp() * 1000), 60] for t in times] + [[0, None]],
    }
    samples, _ = parse.parse_heart_rate([payload], TZ)
    assert samples["utc_offset_min"].tolist() == [120, 60]


def test_daily_summary_converts_milliseconds():
    payload = {
        "calendarDate": "2026-10-25",
        "stress": {"restDurationInMillis": 60000},
        "wellnessChronology": {"totalDurationInMillis": 25 * 3600 * 1000},
    }
    row = parse.parse_daily_summary([payload]).iloc[0]
    assert row["garmin_stress_rest_s"] == 60
    assert row["day_length_s"] == 25 * 3600


def test_activity_identifiers_never_reach_the_table():
    record = {
        "activityId": 1,
        "activityName": "Morning run in Hometown",
        "activityType": {"typeKey": "running"},
        "startTimeGMT": "2026-10-26 08:00:00",
        "startTimeLocal": "2026-10-26 09:00:00",
        "ownerFullName": "Some Name",
        "ownerDisplayName": "name",
        "ownerId": 123,
        "locationName": "Hometown",
        "startLatitude": 48.1,
        "startLongitude": 17.1,
        "duration": 1800.0,
    }
    df = parse.parse_activities([(record, {"summaryDTO": {"directWorkoutRpe": 70}})])
    text = df.astype(str).to_csv()
    for secret in ("Hometown", "Some Name", "48.1", "17.1"):
        assert secret not in text
    assert df.iloc[0]["garmin_rpe"] == 7
    assert df.iloc[0]["utc_offset_min"] == 60


class FakeFrame:
    def __init__(self, **fields):
        self.fields = fields

    def has_field(self, name):
        return name in self.fields

    def get_value(self, name, fallback=None):
        return self.fields.get(name, fallback)


def test_fit_record_keeps_no_position():
    frame = FakeFrame(
        timestamp=pd.Timestamp("2026-10-26", tz="UTC"),
        heart_rate=150,
        position_lat=123,
        position_long=456,
    )
    row = fit.record_row(frame)
    assert row["hr"] == 150
    assert not {"position_lat", "position_long"} & set(row)


def test_fit_rr_drops_unset_slots():
    assert fit.rr_values(FakeFrame(time=(0.8, 0.75, None))) == [800, 750]
    assert fit.rr_values(FakeFrame()) == []
