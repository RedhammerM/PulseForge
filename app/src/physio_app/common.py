"""Streamlit glue shared by the pages: config, cached tables, the date range."""

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
from physio_state.config import Config, load_config

from . import data

TABLES = [
    "activities",
    "anchors",
    "bp_readings",
    "daily_summary",
    "events",
    "hr_daily",
    "hrv_nightly",
    "journal",
    "load_daily",
    "load_events",
    "nutrition_daily",
    "recording_notes",
    "recordings",
    "sessions",
    "sleep_nightly",
    "sync_runs",
    "weight",
]


def cfg() -> Config:
    """Read on every run (a tiny file), so config edits apply without a restart."""
    return load_config()


def today() -> date:
    return datetime.now(cfg().tz).date()


@st.cache_data(show_spinner=False)
def _load(stamp: float, data_dir: str) -> dict[str, pd.DataFrame]:
    c = cfg()
    return {name: data.read_table(c, name) for name in TABLES}


def tables() -> dict[str, pd.DataFrame]:
    """All tables, re-read only when a Parquet file changed."""
    c = cfg()
    return _load(data.table_stamp(c), str(c.data_dir))


def date_range() -> tuple[date, date]:
    return st.session_state.get("range", (today() - timedelta(days=29), today()))


def ranged(name: str) -> pd.DataFrame:
    start, end = date_range()
    return data.in_range(tables()[name], start, end)


def rebuild() -> None:
    """Full rebuild from raw files (no network, about a second)."""
    from physio_state.build import rebuild as _rebuild
    from physio_state.training import obsidian

    obsidian.snapshot(cfg())
    _rebuild(cfg())
    st.cache_data.clear()


def rebuild_manual() -> None:
    from physio_state.manual import rebuild_manual as _rebuild

    _rebuild(cfg())
    st.cache_data.clear()


def header(title: str, caption: str | None = None) -> None:
    st.title(title)
    if caption:
        st.caption(caption)


def fmt(v, digits: int = 0, unit: str = "") -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "–"
    return f"{v:,.{digits}f}{unit}"


def empty(message: str, height: int = 160) -> None:
    """Placeholder where a chart would be."""
    with st.container(
        border=True, height=height, vertical_alignment="center", horizontal_alignment="center"
    ):
        st.caption(message)


STATUS_ICON = {
    "good": ":green[:material/check_circle:]",
    "warning": ":orange[:material/warning:]",
    "critical": ":red[:material/error:]",
}


def leads_off_status(fraction: float) -> str:
    return "good" if fraction < 0.05 else "warning" if fraction < 0.2 else "critical"


def flagged(label: str, status: str) -> str:
    """Markdown label with a status icon in front (icon + text, never color alone)."""
    return f"{STATUS_ICON[status]} {label}"


def duration(seconds: float) -> str:
    """'42 min' below two hours, else '3.5 h'."""
    return f"{seconds / 60:.0f} min" if seconds < 7200 else f"{seconds / 3600:.1f} h"
