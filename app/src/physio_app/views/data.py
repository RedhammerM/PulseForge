"""Data & sync: Garmin sync, what is stored, coverage per day, backup reminder."""

import subprocess
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from physio_app import charts, common, data
from physio_state.garmin.sync import key_expiry_warning

cfg = common.cfg()
start, end = common.date_range()
tables = common.tables()

common.header("Data & sync")

warning = key_expiry_warning(cfg, common.today())
if warning:
    st.warning(warning, icon=":material/key:")

runs = tables["sync_runs"]
left, right = st.columns([1, 2], gap="large")
with left:
    st.subheader("Garmin")
    if not runs.empty:
        last = runs.iloc[-1]
        when = pd.Timestamp(last["started_utc"]).tz_convert(cfg.tz)
        icon = common.STATUS_ICON["good" if last["status"] == "ok" else "critical"]
        st.markdown(
            f"Last run: {icon} **{last['status']}** · {when:%a %d %b %H:%M} · "
            f"{last['calls']} calls, {last['changed']} new or changed"
        )
        if last.get("error"):
            st.code(last["error"])
    st.caption(
        "The daily timer syncs at 10:00. Sync now pulls the last "
        f"{cfg.repull_days + 1} days and rebuilds the tables."
    )
    if st.button("Sync now", type="primary", icon=":material/sync:"):
        with st.status("Syncing Garmin…", expanded=True) as status:
            proc = subprocess.run(
                [sys.executable, "-m", "physio_state.cli", "garmin", "sync"],
                capture_output=True,
                text=True,
                timeout=900,
            )
            st.code((proc.stdout + proc.stderr)[-4000:] or "(no output)")
            ok = proc.returncode == 0
            status.update(
                label="Sync done" if ok else "Sync failed", state="complete" if ok else "error"
            )
        st.cache_data.clear()
    st.markdown("**Rows per table**")
    counts = pd.DataFrame({"rows": {k: len(v) for k, v in tables.items()}})
    st.dataframe(counts, height=300)

with right:
    st.subheader("Coverage")
    cov = data.coverage(tables, start, end)
    fig = go.Figure(
        go.Heatmap(
            x=pd.to_datetime(cov.index),
            y=cov.columns,
            z=cov.T.values,
            colorscale=[[0, "#262624"], [1, charts.SERIES[0]]],
            zmin=0,
            zmax=1,
            xgap=2,
            ygap=2,
            showscale=False,
            hovertemplate="%{x|%a %d %b} · %{y}: %{z}<extra></extra>",
        )
    )
    fig.update_yaxes(autorange="reversed")
    st.plotly_chart(charts.style(fig, height=320, legend=False, unified=False))
    st.caption("Blue = data present for that day.")
    if not runs.empty:
        st.markdown("**Recent sync runs**")
        recent = runs.iloc[::-1].head(10).copy()
        recent["started"] = pd.to_datetime(recent["started_utc"]).dt.tz_convert(cfg.tz)
        st.dataframe(
            recent[["kind", "started", "status", "calls", "changed", "error"]],
            hide_index=True,
            column_config={
                "started": st.column_config.DatetimeColumn("Started", format="ddd DD MMM HH:mm")
            },
        )

st.divider()
st.markdown(
    f"""**Backup.** Nothing backs these up automatically. Copy them to an encrypted disk:

- `{cfg.recordings_dir}`: recordings and session files, cannot be re-created
- `{cfg.manual_dir}`: food log, journal, recording notes, cannot be re-created
- `{cfg.raw_dir}`: raw Garmin responses (re-fetchable only while Garmin keeps them)

`{cfg.tables_dir}` and `{cfg.db_path}` are rebuilt from the above."""
)
