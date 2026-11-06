"""Workouts, load events and the daily load series.

Load measures stay side by side: session RPE (primary), Garmin training load,
and Edwards TRIMP from time in HR zones (zone number x minutes), which needs no
sex-specific constants.
"""

import pandas as pd

from ..keys import physio_day

WORKOUT_GAP = pd.Timedelta(hours=2)
MATCH_MARGIN = pd.Timedelta(minutes=15)
WORKOUT_COLS = ["workout_id", "start_utc", "end_utc", "n_sets", "volume_kg", "note_rpe"]
EVENT_COLS = [
    "event_id", "day", "start_utc", "activity_type", "activity_id", "workout_id", "duration_min",
    "duration_source", "rpe", "rpe_source", "srpe_load", "garmin_training_load", "edwards_trimp",
]  # fmt: skip


def build_workouts(sets: pd.DataFrame) -> pd.DataFrame:
    """Group sets into workouts; a pause over WORKOUT_GAP starts a new one."""
    if sets.empty:
        return pd.DataFrame(columns=WORKOUT_COLS)
    s = sets.sort_values("set_time_utc").copy()
    s["group"] = (s["set_time_utc"].diff() > WORKOUT_GAP).cumsum()
    s["volume"] = s["reps"] * s["weight_kg"]
    rows = []
    for _, g in s.groupby("group"):
        rpe = g["note_rpe"].dropna()
        start = g["set_time_utc"].iloc[0]
        rows.append(
            {
                "workout_id": f"W{start:%Y%m%dT%H%M%S}",
                "start_utc": start,
                "end_utc": g["set_time_utc"].iloc[-1],
                "n_sets": len(g),
                "volume_kg": g["volume"].sum(),
                "note_rpe": rpe.iloc[-1] if len(rpe) else None,
            }
        )
    return pd.DataFrame(rows, columns=WORKOUT_COLS)


def edwards_trimp(a: pd.DataFrame) -> pd.Series:
    zones = [a[f"hr_zone{i}_s"] * i for i in range(1, 6)]
    total = sum(z.fillna(0) for z in zones) / 60
    has_hr = a[[f"hr_zone{i}_s" for i in range(1, 6)]].notna().any(axis=1)
    return total.where(has_hr)


def match_workouts(workouts: pd.DataFrame, activities: pd.DataFrame) -> dict[str, int]:
    """Map workout_id to the strength activity overlapping it in time."""
    strength = activities[activities["activity_type"].fillna("").str.contains("strength")]
    matches = {}
    for w in workouts.itertuples():
        best, best_overlap = None, None
        for a in strength.itertuples():
            a_end = a.start_utc + pd.Timedelta(seconds=a.elapsed_s or 0)
            overlap = min(w.end_utc, a_end + MATCH_MARGIN) - max(
                w.start_utc, a.start_utc - MATCH_MARGIN
            )
            if overlap >= pd.Timedelta(0) and (best_overlap is None or overlap > best_overlap):
                best, best_overlap = a.activity_id, overlap
        if best is not None:
            matches[w.workout_id] = best
    return matches


def build_load_events(
    workouts: pd.DataFrame, activities: pd.DataFrame, tz, wakes: pd.DataFrame | None = None
) -> pd.DataFrame:
    """One row per training event: Garmin activities, plus workouts with no activity."""
    matches = match_workouts(workouts, activities)
    by_activity = {v: k for k, v in matches.items()}
    w_rpe = dict(zip(workouts["workout_id"], workouts["note_rpe"], strict=True))
    a = activities.assign(trimp=edwards_trimp(activities))
    rows = []
    for r in a.itertuples():
        wid = by_activity.get(r.activity_id)
        note = w_rpe.get(wid)
        rpe, source = (note, "obsidian_note") if pd.notna(note) else (r.garmin_rpe, "garmin")
        rows.append(
            {
                "event_id": f"A{r.activity_id}",
                "start_utc": r.start_utc,
                "activity_type": r.activity_type,
                "activity_id": r.activity_id,
                "workout_id": wid,
                "duration_min": r.elapsed_s / 60 if pd.notna(r.elapsed_s) else None,
                "duration_source": "garmin",
                "rpe": rpe,
                "rpe_source": source if pd.notna(rpe) else None,
                "garmin_training_load": r.garmin_training_load,
                "edwards_trimp": r.trimp,
            }
        )
    for w in workouts.itertuples():
        if w.workout_id in matches:
            continue
        span = (w.end_utc - w.start_utc).total_seconds() / 60
        rows.append(
            {
                "event_id": w.workout_id,
                "start_utc": w.start_utc,
                "activity_type": "strength_training",
                "workout_id": w.workout_id,
                # one logged set has no span, so its duration is unknown
                "duration_min": span if w.n_sets > 1 else None,
                "duration_source": "set_times" if w.n_sets > 1 else None,
                "rpe": w.note_rpe,
                "rpe_source": "obsidian_note" if pd.notna(w.note_rpe) else None,
            }
        )
    e = pd.DataFrame(rows, columns=EVENT_COLS).sort_values("start_utc", ignore_index=True)
    for col in ("duration_min", "rpe", "garmin_training_load", "edwards_trimp"):
        e[col] = pd.to_numeric(e[col])
    e["activity_id"] = e["activity_id"].astype("Int64")
    e["srpe_load"] = e["rpe"] * e["duration_min"]
    if not e.empty:
        e["start_utc"] = pd.to_datetime(e["start_utc"], utc=True)
        e["day"] = physio_day(e["start_utc"], tz, wakes)
    return e


def build_load_daily(events: pd.DataFrame) -> pd.DataFrame:
    """Daily sums from the first to the last event; days without events are zero.

    `srpe_load` is null on a day where any event lacks RPE or duration, so a
    partial sum is never mistaken for that day's load.
    """
    cols = [
        "day",
        "n_events",
        "n_missing_srpe",
        "srpe_load",
        "garmin_training_load",
        "edwards_trimp",
    ]
    if events.empty:
        return pd.DataFrame(columns=cols)
    e = events.assign(day=pd.to_datetime(events["day"]))
    g = e.groupby("day")
    daily = pd.DataFrame(
        {
            "n_events": g.size(),
            "n_missing_srpe": g["srpe_load"].apply(lambda s: s.isna().sum()),
            "srpe_load": g["srpe_load"].sum(min_count=1),
            "garmin_training_load": g["garmin_training_load"].sum(min_count=1),
            "edwards_trimp": g["edwards_trimp"].sum(min_count=1),
        }
    )
    daily.loc[daily["n_missing_srpe"] > 0, "srpe_load"] = None
    index = pd.date_range(daily.index.min(), daily.index.max(), freq="D", name="day")
    daily = daily.reindex(index)
    empty = daily["n_events"].isna()
    daily.loc[empty, ["n_events", "n_missing_srpe"]] = 0
    daily.loc[empty, ["srpe_load", "garmin_training_load", "edwards_trimp"]] = 0.0
    daily[["n_events", "n_missing_srpe"]] = daily[["n_events", "n_missing_srpe"]].astype(int)
    daily = daily.reset_index()
    daily["day"] = daily["day"].dt.date
    return daily[cols]
