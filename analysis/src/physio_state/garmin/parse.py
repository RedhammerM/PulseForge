"""Raw Garmin JSON to tables.

Every field is named explicitly: new vendor fields, names, profile IDs, GPS and
activity titles cannot reach a table unless they are added here on purpose.
Composite vendor scores carry a `garmin_` prefix; they are benchmarks only.
"""

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from ..keys import utc_from_ms, utc_offset_min


def _gmt(text: str | None) -> pd.Timestamp | None:
    return None if text is None else pd.Timestamp(text, tz="UTC")


def _offset_from_strings(gmt: str | None, local: str | None) -> int | None:
    if gmt is None or local is None:
        return None
    return int((pd.Timestamp(local) - pd.Timestamp(gmt)).total_seconds() // 60)


def _frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns)


# ------------------------------------------------------------------- HRV
HRV_READING_COLS = ["day", "time_utc", "utc_offset_min", "hrv_ms"]
HRV_NIGHT_COLS = [
    "day", "n_readings", "hrv_mean_ms", "hrv_median_ms", "hrv_ln_mean", "hrv_sd_ms",
    "hrv_first_half_ms", "hrv_second_half_ms", "sleep_start_utc", "sleep_end_utc",
    "garmin_last_night_avg", "garmin_last_night_5min_high", "garmin_weekly_avg",
    "garmin_baseline_low_upper", "garmin_baseline_balanced_low", "garmin_baseline_balanced_upper",
    "garmin_status",
]  # fmt: skip


def parse_hrv(payloads: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    readings, nights = [], []
    for p in payloads:
        s = p.get("hrvSummary") or {}
        day = s.get("calendarDate")
        rows = [
            {
                "day": day,
                "time_utc": _gmt(r["readingTimeGMT"]),
                "utc_offset_min": _offset_from_strings(
                    r["readingTimeGMT"], r.get("readingTimeLocal")
                ),
                "hrv_ms": r["hrvValue"],
            }
            for r in p.get("hrvReadings") or []
            if r.get("hrvValue") is not None
        ]
        rows.sort(key=lambda r: r["time_utc"])
        readings += rows
        v = np.array([r["hrv_ms"] for r in rows], dtype=float)
        half = len(v) // 2
        base = s.get("baseline") or {}
        nights.append(
            {
                "day": day,
                "n_readings": len(v),
                "hrv_mean_ms": v.mean() if len(v) else None,
                "hrv_median_ms": float(np.median(v)) if len(v) else None,
                "hrv_ln_mean": float(np.log(v[v > 0]).mean()) if (v > 0).any() else None,
                "hrv_sd_ms": v.std(ddof=1) if len(v) > 1 else None,
                "hrv_first_half_ms": v[:half].mean() if half else None,
                "hrv_second_half_ms": v[half:].mean() if half else None,
                "sleep_start_utc": _gmt(p.get("sleepStartTimestampGMT")),
                "sleep_end_utc": _gmt(p.get("sleepEndTimestampGMT")),
                "garmin_last_night_avg": s.get("lastNightAvg"),
                "garmin_last_night_5min_high": s.get("lastNight5MinHigh"),
                "garmin_weekly_avg": s.get("weeklyAvg"),
                "garmin_baseline_low_upper": base.get("lowUpper"),
                "garmin_baseline_balanced_low": base.get("balancedLow"),
                "garmin_baseline_balanced_upper": base.get("balancedUpper"),
                "garmin_status": s.get("status"),
            }
        )
    return _frame(readings, HRV_READING_COLS), _frame(nights, HRV_NIGHT_COLS)


# ----------------------------------------------------------------- sleep
SLEEP_COLS = [
    "day", "sleep_start_utc", "sleep_end_utc", "utc_offset_min", "sleep_s", "nap_s",
    "deep_s", "light_s", "rem_s", "awake_s", "unmeasurable_s", "awake_count",
    "avg_hr", "avg_respiration", "lowest_respiration", "highest_respiration",
    "sleep_window_confirmed", "garmin_avg_sleep_stress", "garmin_sleep_score",
    "garmin_sleep_need_min",
]  # fmt: skip


def parse_sleep(payloads: list[dict]) -> pd.DataFrame:
    rows = []
    for p in payloads:
        p = p.get("dailySleepDTO", p)
        start, end = p.get("sleepStartTimestampGMT"), p.get("sleepEndTimestampGMT")
        if start is None or end is None:
            continue  # a day record without a detected night
        local = p.get("sleepEndTimestampLocal")
        rows.append(
            {
                "day": p["calendarDate"],
                "sleep_start_utc": pd.Timestamp(start, unit="ms", tz="UTC"),
                "sleep_end_utc": pd.Timestamp(end, unit="ms", tz="UTC"),
                "utc_offset_min": None if local is None else (local - end) // 60000,
                "sleep_s": p.get("sleepTimeSeconds"),
                "nap_s": p.get("napTimeSeconds"),
                "deep_s": p.get("deepSleepSeconds"),
                "light_s": p.get("lightSleepSeconds"),
                "rem_s": p.get("remSleepSeconds"),
                "awake_s": p.get("awakeSleepSeconds"),
                "unmeasurable_s": p.get("unmeasurableSleepSeconds"),
                "awake_count": p.get("awakeCount"),
                "avg_hr": p.get("avgHeartRate"),
                "avg_respiration": p.get("averageRespirationValue"),
                "lowest_respiration": p.get("lowestRespirationValue"),
                "highest_respiration": p.get("highestRespirationValue"),
                "sleep_window_confirmed": p.get("sleepWindowConfirmed"),
                "garmin_avg_sleep_stress": p.get("avgSleepStress"),
                "garmin_sleep_score": ((p.get("sleepScores") or {}).get("overall") or {}).get(
                    "value"
                ),
                "garmin_sleep_need_min": (p.get("sleepNeed") or {}).get("actual"),
            }
        )
    return _frame(rows, SLEEP_COLS)


# ------------------------------------------------------------ heart rate
def parse_heart_rate(payloads: list[dict], tz) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return the 2-minute series and one row per day of resting/min/max HR."""
    samples, daily = [], []
    for p in payloads:
        day = p.get("calendarDate")
        samples += [
            {"day": day, "t_ms": t, "hr": hr}
            for t, hr in p.get("heartRateValues") or []
            if hr is not None
        ]
        daily.append(
            {
                "day": day,
                "resting_hr": p.get("restingHeartRate"),
                "min_hr": p.get("minHeartRate"),
                "max_hr": p.get("maxHeartRate"),
                "resting_hr_7d": p.get("lastSevenDaysAvgRestingHeartRate"),
            }
        )
    df = _frame(samples, ["day", "t_ms", "hr"])
    df["time_utc"] = utc_from_ms(df.pop("t_ms"))
    df["utc_offset_min"] = utc_offset_min(df["time_utc"], tz)
    df = df[["day", "time_utc", "utc_offset_min", "hr"]]
    return df, _frame(daily, ["day", "resting_hr", "min_hr", "max_hr", "resting_hr_7d"])


# --------------------------------------------------------- daily summary
def _s(ms: float | None) -> float | None:
    return None if ms is None else ms / 1000


def parse_daily_summary(payloads: list[dict]) -> pd.DataFrame:
    rows = []
    for p in payloads:
        hr, st = p.get("heartRate") or {}, p.get("stress") or {}
        bb, rs = p.get("bodyBattery") or {}, p.get("respiration") or {}
        mv, im = p.get("movement") or {}, p.get("intensityMinutes") or {}
        cal, chron = p.get("calories") or {}, p.get("wellnessChronology") or {}
        rows.append(
            {
                "day": p["calendarDate"],
                "day_length_s": _s(chron.get("totalDurationInMillis")),
                "resting_hr": hr.get("restingValue"),
                "min_hr": hr.get("minValue"),
                "max_hr": hr.get("maxValue"),
                "respiration_avg": rs.get("avgValue"),
                "respiration_min": rs.get("minValue"),
                "respiration_max": rs.get("maxValue"),
                "steps": mv.get("steps"),
                "intensity_moderate_min": im.get("moderate"),
                "intensity_vigorous_min": im.get("vigorous"),
                "calories_active": cal.get("burnedActive"),
                "calories_total": cal.get("burnedTotal"),
                "garmin_stress_avg": st.get("avgLevel"),
                "garmin_stress_rest_s": _s(st.get("restDurationInMillis")),
                "garmin_stress_low_s": _s(st.get("lowStressDurationInMillis")),
                "garmin_stress_medium_s": _s(st.get("mediumStressDurationInMillis")),
                "garmin_stress_high_s": _s(st.get("highStressDurationInMillis")),
                "garmin_stress_uncategorized_share": st.get("uncategorizedProportion"),
                "garmin_bb_charged": bb.get("chargedValue"),
                "garmin_bb_drained": bb.get("drainedValue"),
                "garmin_bb_min": bb.get("minValue"),
                "garmin_bb_max": bb.get("maxValue"),
                "garmin_bb_latest": bb.get("latestValue"),
            }
        )
    return pd.DataFrame(rows)


# ------------------------------------------------------------ activities
ACTIVITY_FIELDS = {
    "duration_s": "duration",
    "elapsed_s": "elapsedDuration",
    "moving_s": "movingDuration",
    "distance_m": "distance",
    "avg_hr": "averageHR",
    "max_hr": "maxHR",
    "hr_zone1_s": "hrTimeInZone_1",
    "hr_zone2_s": "hrTimeInZone_2",
    "hr_zone3_s": "hrTimeInZone_3",
    "hr_zone4_s": "hrTimeInZone_4",
    "hr_zone5_s": "hrTimeInZone_5",
    "avg_speed_mps": "averageSpeed",
    "avg_power_w": "avgPower",
    "norm_power_w": "normPower",
    "max_power_w": "maxPower",
    "avg_run_cadence": "averageRunningCadenceInStepsPerMinute",
    "elevation_gain_m": "elevationGain",
    "total_sets": "totalSets",
    "active_sets": "activeSets",
    "total_reps": "totalReps",
    "manual_activity": "manualActivity",
    "garmin_training_load": "activityTrainingLoad",
    "garmin_aerobic_te": "aerobicTrainingEffect",
    "garmin_anaerobic_te": "anaerobicTrainingEffect",
    "garmin_bb_change": "differenceBodyBattery",
}
ACTIVITY_COLS = [
    "activity_id", "activity_type", "start_utc", "utc_offset_min", *ACTIVITY_FIELDS, "garmin_rpe",
]  # fmt: skip


def parse_activities(items: list[tuple[dict, dict | None]]) -> pd.DataFrame:
    """`items` holds (list record, detail record or None) per activity.

    `garmin_rpe` is the watch's post-activity perceived effort on a 0-10 scale,
    when the detail record has it (Garmin stores 10-100).
    """
    rows = []
    for a, detail in items:
        summary = (detail or {}).get("summaryDTO") or {}
        rpe = summary.get("directWorkoutRpe")
        row = {
            "activity_id": a["activityId"],
            "activity_type": (a.get("activityType") or {}).get("typeKey"),
            "start_utc": _gmt(a.get("startTimeGMT")),
            "utc_offset_min": _offset_from_strings(a.get("startTimeGMT"), a.get("startTimeLocal")),
            "garmin_rpe": None if rpe is None else rpe / 10,
        }
        row.update({col: a.get(src) for col, src in ACTIVITY_FIELDS.items()})
        rows.append(row)
    return _frame(rows, ACTIVITY_COLS).sort_values("start_utc", ignore_index=True)


# -------------------------------------------------------------- HR zones
ZONE_FIELDS = {
    "sport": "sport",
    "training_method": "trainingMethod",
    "resting_hr": "restingHeartRateUsed",
    "lthr": "lactateThresholdHeartRateUsed",
    "max_hr": "maxHeartRateUsed",
    "zone1_floor": "zone1Floor",
    "zone2_floor": "zone2Floor",
    "zone3_floor": "zone3Floor",
    "zone4_floor": "zone4Floor",
    "zone5_floor": "zone5Floor",
}


def parse_hr_zones(versions: list[tuple[datetime, list[dict]]]) -> pd.DataFrame:
    """One row per sport and stored version, so zone features stay reproducible."""
    rows = [
        {"valid_from_utc": t.astimezone(UTC), **{c: z.get(s) for c, s in ZONE_FIELDS.items()}}
        for t, zones in versions
        for z in zones
    ]
    return _frame(rows, ["valid_from_utc", *ZONE_FIELDS])
