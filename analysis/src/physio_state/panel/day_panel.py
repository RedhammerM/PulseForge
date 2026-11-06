"""Model input table: one row per day and slot, in the order things happen.

AM observation -> load after AM -> PM observation -> load after PM -> sleep and
overnight observation -> next AM. Load columns hold the load that follows the
row's observation, so an observation never sees load from after it.

Subjective anchors and Garmin composite scores are left out on purpose: the
model must not be built from what it is validated against.
"""

import pandas as pd

from .baselines import past_mean, past_z

LOADS = {
    "load_srpe": "srpe_load",
    "load_garmin": "garmin_training_load",
    "load_trimp": "edwards_trimp",
}
NIGHT_COLS = {
    "night_hrv_ln": ("hrv_nightly", "hrv_ln_mean"),
    "night_hrv_n": ("hrv_nightly", "n_readings"),
    "sleep_s": ("sleep_nightly", "sleep_s"),
    "sleep_hr": ("sleep_nightly", "avg_hr"),
    "sleep_respiration": ("sleep_nightly", "avg_respiration"),
    "resting_hr": ("hr_daily", "resting_hr"),
}
COLS = [
    "day", "slot", "session_id", *NIGHT_COLS, "night_hrv_ln_rel7", "night_hrv_ln_z",
    "resting_hr_z", *LOADS,
]  # fmt: skip


def _load_slots(events: pd.DataFrame, sessions: pd.DataFrame) -> pd.DataFrame:
    """Sum load by day and slot. Training counts as after PM only if it began after check-in."""
    if events.empty:
        return pd.DataFrame(columns=["day", "slot", *LOADS])
    pm = sessions[sessions["slot"] == "PM"].set_index("day")["checkin_utc"]
    e = events.copy()
    cutoff = pd.to_datetime(e["day"].map(pm), utc=True)
    e["slot"] = (e["start_utc"] > cutoff).map({True: "PM", False: "AM"})
    e = e.rename(columns={v: k for k, v in LOADS.items()})
    out = e.groupby(["day", "slot"])[list(LOADS)].sum(min_count=1).reset_index()
    missing = e[e["load_srpe"].isna()][["day", "slot"]].drop_duplicates()
    keys = pd.MultiIndex.from_frame(out[["day", "slot"]])
    out.loc[keys.isin(pd.MultiIndex.from_frame(missing)), "load_srpe"] = None
    return out


def build_day_panel(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    sources = [tables[n] for n in ("hrv_nightly", "sleep_nightly", "hr_daily", "load_events")]
    days = pd.concat([t["day"] for t in sources if not t.empty] + [tables["sessions"]["day"]])
    if days.empty:
        return pd.DataFrame(columns=COLS)
    index = pd.date_range(pd.to_datetime(days).min(), pd.to_datetime(days).max(), freq="D")
    panel = pd.DataFrame({"day": index.date}).merge(
        pd.DataFrame({"slot": ["AM", "PM"]}), how="cross"
    )

    night = pd.DataFrame({"day": index.date})
    for col, (table, source) in NIGHT_COLS.items():
        night = night.merge(
            tables[table][["day", source]].rename(columns={source: col}), on="day", how="left"
        )
    night[list(NIGHT_COLS)] = night[list(NIGHT_COLS)].apply(pd.to_numeric)
    night["night_hrv_ln_rel7"] = night["night_hrv_ln"] - past_mean(night["night_hrv_ln"], 7, 3)
    night["night_hrv_ln_z"] = past_z(night["night_hrv_ln"])
    night["resting_hr_z"] = past_z(night["resting_hr"])
    night["slot"] = "AM"  # overnight values are known by the morning

    panel = panel.merge(night, on=["day", "slot"], how="left")
    panel = panel.merge(
        tables["sessions"][["day", "slot", "session_id"]], on=["day", "slot"], how="left"
    )
    loads = _load_slots(tables["load_events"], tables["sessions"])
    panel = panel.merge(loads, on=["day", "slot"], how="left", indicator="has_event")
    known = panel["day"].isin(set(tables["load_daily"]["day"]))
    rest = known & (panel["has_event"] == "left_only")
    panel.loc[rest, list(LOADS)] = 0.0  # inside the logged range, no event means no training
    return panel[COLS]
