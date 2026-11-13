"""Table access and the calculations behind the pages. No Streamlit imports here."""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from physio_state.config import Config

SLOTS = ["AM", "PM"]


def read_table(cfg: Config, name: str) -> pd.DataFrame:
    """A table from `data/tables`, or an empty frame if it was never built."""
    path = cfg.tables_dir / f"{name}.parquet"
    return pd.read_parquet(path) if path.exists() else pd.DataFrame()


def table_stamp(cfg: Config) -> float:
    """Changes whenever any table is rewritten; used as a cache key."""
    files = list(cfg.tables_dir.glob("*.parquet"))
    return max((f.stat().st_mtime for f in files), default=0.0)


def in_range(df: pd.DataFrame, start: date, end: date, col: str = "day") -> pd.DataFrame:
    """Rows with `col` in [start, end], sorted by it."""
    if df.empty or col not in df:
        return df
    d = pd.to_datetime(df[col]).dt.date
    return df[(d >= start) & (d <= end)].sort_values(col, kind="stable")


def local_time(utc: pd.Series, offset_min: pd.Series) -> pd.Series:
    """Wall-clock time from UTC plus the stored offset (naive, for display)."""
    off = pd.to_timedelta(pd.to_numeric(offset_min, errors="coerce").fillna(0), unit="min")
    return pd.to_datetime(utc, utc=True).dt.tz_localize(None) + off


# ----------------------------------------------------------------- measure
def prefill(tables: dict[str, pd.DataFrame], today: date) -> dict:
    """Metadata form defaults from Garmin: last night's sleep and the last activity."""
    out: dict = {}
    sleep = tables.get("sleep_nightly", pd.DataFrame())
    if not sleep.empty:
        row = sleep[pd.to_datetime(sleep["day"]).dt.date == today]
        if not row.empty and pd.notna(row.iloc[-1]["sleep_s"]):
            out["sleep_h"] = round(float(row.iloc[-1]["sleep_s"]) / 3600, 1)
    acts = tables.get("activities", pd.DataFrame())
    if not acts.empty:
        last = acts.sort_values("start_utc").iloc[-1]
        if pd.notna(last["duration_s"]):
            out["last_workout_min"] = int(round(last["duration_s"] / 60))
        if pd.notna(last.get("garmin_rpe")):
            out["last_session_rpe"] = int(round(last["garmin_rpe"]))
    return out


def sessions_today(tables: dict[str, pd.DataFrame], day: date) -> dict[str, bool]:
    """Which slots of `day` already have a recording that belongs to a session."""
    rec = tables.get("recordings", pd.DataFrame())
    done = {s: False for s in SLOTS}
    if rec.empty:
        return done
    for sid in rec.loc[pd.to_datetime(rec["day"]).dt.date == day, "session_id"].dropna():
        slot = str(sid).rsplit("_", 1)[-1]
        if slot in done:
            done[slot] = True
    return done


def night_vs_baseline(
    hrv: pd.DataFrame, day: date, days: int = 7
) -> tuple[float | None, float | None]:
    """Last night's mean HRV and the mean of the `days` nights before it."""
    if hrv.empty:
        return None, None
    h = hrv.assign(d=pd.to_datetime(hrv["day"]).dt.date).set_index("d")["hrv_mean_ms"]
    tonight = h.get(day)
    prior = h[(h.index < day) & (h.index >= day - timedelta(days=days))].dropna()
    return (
        None if tonight is None or pd.isna(tonight) else float(tonight),
        float(prior.mean()) if len(prior) else None,
    )


# ------------------------------------------------------------ measurements
def session_grid(recordings: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """One row per day and slot with the number of recordings (0 = none).

    Recordings without a session are counted by their `when` field
    (morning -> AM, everything else -> PM).
    """
    days = pd.date_range(start, end, freq="D").date
    grid = pd.DataFrame([(d, s) for d in days for s in SLOTS], columns=["day", "slot"])
    if recordings.empty:
        return grid.assign(n=0)
    r = recordings.copy()
    r["day"] = pd.to_datetime(r["day"]).dt.date
    from_sid = r["session_id"].astype("string").str.rsplit("_", n=1).str[-1]
    from_when = np.where(r["when"].eq("morning"), "AM", "PM")
    r["slot"] = from_sid.where(from_sid.isin(SLOTS), from_when)
    counts = r.groupby(["day", "slot"]).size().rename("n").reset_index()
    return grid.merge(counts, on=["day", "slot"], how="left").fillna({"n": 0}).astype({"n": int})


def streak(grid: pd.DataFrame, today: date) -> int:
    """Consecutive days up to today (or yesterday, if today is still open) with any recording."""
    per_day = grid.groupby("day")["n"].sum()
    d = today if per_day.get(today, 0) > 0 else today - timedelta(days=1)
    n = 0
    while per_day.get(d, 0) > 0:
        n += 1
        d -= timedelta(days=1)
    return n


def bp_wide(bp: pd.DataFrame, sessions: pd.DataFrame) -> pd.DataFrame:
    """One row per session with seated and standing SBP/DBP and the change on standing."""
    if bp.empty:
        return pd.DataFrame(columns=["day", "session_id"])
    w = bp.pivot_table(index="session_id", columns="label", values=["sbp", "dbp", "hr"])
    w.columns = [f"{v}_{label}" for v, label in w.columns]
    w = w.reset_index()
    if not sessions.empty:
        w = w.merge(sessions[["session_id", "day", "slot"]], on="session_id", how="left")
    if {"sbp_during_stand", "sbp_before_seated"} <= set(w.columns):
        w["sbp_change"] = w["sbp_during_stand"] - w["sbp_before_seated"]
    return w


# ---------------------------------------------------------- recordings
@dataclass
class Recording:
    summary: dict
    ecg: pd.DataFrame
    ppg: pd.DataFrame
    events: pd.DataFrame


def load_recording(folder: Path, recording_id: str) -> Recording:
    summary = json.loads((folder / f"{recording_id}.json").read_text())
    ecg = pd.read_csv(folder / summary["ecg"]["file"])
    ppg = pd.read_csv(folder / summary["ppg"]["file"])
    ev_block = summary.get("events")
    ev_path = folder / ev_block["file"] if ev_block else None
    events = (
        pd.read_csv(ev_path)
        if ev_path is not None and ev_path.exists()
        else pd.DataFrame(columns=["t_us", "label"])
    )
    t0 = min(ecg["t_us"].min(), ppg["t_us"].min()) if len(ecg) and len(ppg) else 0
    for df in (ecg, ppg, events):
        df["t_s"] = (df["t_us"] - t0) / 1e6
    return Recording(summary, ecg, ppg, events)


def minmax_downsample(t: np.ndarray, y: np.ndarray, bins: int) -> tuple[np.ndarray, np.ndarray]:
    """Keep the min and max of each bin, so peaks survive (for long overview plots)."""
    n = len(y)
    if n <= 2 * bins:
        return t, y
    edges = np.linspace(0, n, bins + 1, dtype=int)
    idx = []
    for a, b in zip(edges[:-1], edges[1:], strict=True):
        seg = y[a:b]
        i, j = a + int(np.nanargmin(seg)), a + int(np.nanargmax(seg))
        idx += sorted((i, j))
    idx = np.asarray(idx)
    return t[idx], y[idx]


# -------------------------------------------------------------- training
def weekly_by_type(activities: pd.DataFrame, value: str) -> pd.DataFrame:
    """Week (Monday) x activity type sum of `value`."""
    if activities.empty:
        return pd.DataFrame(columns=["week", "activity_type", value])
    a = activities.copy()
    local = local_time(a["start_utc"], a["utc_offset_min"])
    a["week"] = (local.dt.normalize() - pd.to_timedelta(local.dt.weekday, unit="D")).dt.date
    return a.groupby(["week", "activity_type"], as_index=False)[value].sum()


def weekly_zones(activities: pd.DataFrame) -> pd.DataFrame:
    """Hours in each HR zone per week (long format)."""
    cols = [f"hr_zone{i}_s" for i in range(1, 6)]
    if activities.empty:
        return pd.DataFrame(columns=["week", "zone", "hours"])
    a = activities.copy()
    local = local_time(a["start_utc"], a["utc_offset_min"])
    a["week"] = (local.dt.normalize() - pd.to_timedelta(local.dt.weekday, unit="D")).dt.date
    w = a.groupby("week")[cols].sum().reset_index().melt("week", var_name="zone", value_name="s")
    w["zone"] = "Z" + w["zone"].str[7]
    w["hours"] = w.pop("s") / 3600
    return w


# ------------------------------------------------------------- lifestyle
def energy_balance(summary: pd.DataFrame, nutrition: pd.DataFrame) -> pd.DataFrame:
    """Per day: burned (Garmin total), eaten (manual), balance where both exist.

    Today's Garmin total is only the calories burned so far, so its balance is not shown.
    """
    burned = (
        summary[["day", "calories_total", "calories_active"]].copy()
        if not summary.empty
        else pd.DataFrame(columns=["day", "calories_total", "calories_active"])
    )
    eaten = (
        nutrition[["day", "kcal"]].copy()
        if not nutrition.empty
        else pd.DataFrame(columns=["day", "kcal"])
    )
    for df in (burned, eaten):
        df["day"] = pd.to_datetime(df["day"]).dt.date
    out = burned.merge(eaten, on="day", how="outer").sort_values("day", ignore_index=True)
    out["balance"] = pd.to_numeric(out["kcal"]) - pd.to_numeric(out["calories_total"])
    return out


def daily_weight(weight: pd.DataFrame) -> pd.DataFrame:
    """Last weigh-in per day plus a 7-day rolling mean over calendar days."""
    if weight.empty:
        return pd.DataFrame(columns=["day", "weight_kg", "body_fat_pct", "weight_7d"])
    w = weight.sort_values("time_utc").groupby("day", as_index=False).last()
    w["day"] = pd.to_datetime(w["day"])
    s = w.set_index("day")["weight_kg"].asfreq("D")
    w["weight_7d"] = s.rolling(7, min_periods=1).mean().reindex(w["day"]).to_numpy()
    w["day"] = w["day"].dt.date
    return w[["day", "weight_kg", "body_fat_pct", "weight_7d"]]


# ------------------------------------------------------------------ data
COVERAGE = {  # label: (table, day column)
    "HRV": ("hrv_nightly", "day"),
    "Sleep": ("sleep_nightly", "day"),
    "Daily summary": ("daily_summary", "day"),
    "Weight": ("weight", "day"),
    "Activity": ("load_events", "day"),
    "Recording": ("recordings", "day"),
    "Check-in": ("sessions", "day"),
    "Food log": ("nutrition_daily", "day"),
    "Journal": ("journal", "day"),
}


def coverage(tables: dict[str, pd.DataFrame], start: date, end: date) -> pd.DataFrame:
    """Day x source matrix of 1 (has data) / 0."""
    days = pd.date_range(start, end, freq="D").date
    out = pd.DataFrame(index=days)
    for label, (name, col) in COVERAGE.items():
        df = tables.get(name, pd.DataFrame())
        have = set(pd.to_datetime(df[col]).dt.date) if not df.empty and col in df else set()
        out[label] = [int(d in have) for d in days]
    return out
