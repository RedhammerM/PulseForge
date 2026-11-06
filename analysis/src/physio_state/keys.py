"""Shared keys. `day` is the wake-up date: a physio day runs from wake to next wake."""

import pandas as pd

ROLLOVER_HOUR = 4  # without sleep data, times before this hour belong to the previous day


def utc_from_ms(ms: pd.Series) -> pd.Series:
    return pd.to_datetime(ms, unit="ms", utc=True)


def utc_offset_min(utc: pd.Series, tz) -> pd.Series:
    """UTC offset in minutes at each instant in `tz` (handles clock changes)."""
    local = utc.dt.tz_convert(tz).dt.tz_localize(None)
    return ((local - utc.dt.tz_localize(None)).dt.total_seconds() // 60).astype("Int64")


def physio_day(utc: pd.Series, tz, wakes: pd.DataFrame | None = None) -> pd.Series:
    """Map instants to physio days.

    `wakes` has columns `day` (date) and `sleep_end_utc`. An instant belongs to
    the latest wake before it, if that wake is under 30 h old. Otherwise the
    local date is used, with times before ROLLOVER_HOUR on the previous date.
    """
    local = utc.dt.tz_convert(tz).dt.tz_localize(None)
    fallback = (local - pd.Timedelta(hours=ROLLOVER_HOUR)).dt.date
    if wakes is None or wakes.empty or utc.empty:
        return fallback
    left = pd.DataFrame({"t": utc, "pos": range(len(utc))}).sort_values("t")
    right = (
        wakes[["day", "sleep_end_utc"]]
        .dropna()
        .rename(columns={"sleep_end_utc": "t", "day": "wake_day"})
        .sort_values("t")
    )
    if right.empty:
        return fallback
    left["t"] = left["t"].astype(right["t"].dtype)
    merged = pd.merge_asof(
        left, right, on="t", direction="backward", tolerance=pd.Timedelta(hours=30)
    ).sort_values("pos")
    days = merged["wake_day"].to_numpy()
    return pd.Series(
        [f if pd.isna(d) else d for d, f in zip(days, fallback, strict=True)], index=utc.index
    )
