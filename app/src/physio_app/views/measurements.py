"""Measurements: what was recorded, how good it was, ratings and BP over time."""

from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from physio_app import charts, common, data
from physio_state.recordings.ingest import RATINGS

start, end = common.date_range()
tables = common.tables()
rec = common.ranged("recordings")
sessions = common.ranged("sessions")
anchors = common.ranged("anchors")
notes = tables["recording_notes"]

common.header("Measurements", "Your recordings and check-ins. Nothing here is in intervals.icu.")

grid = data.session_grid(rec, start, end)
n_days = (end - start).days + 1
days_with = int((grid.groupby("day")["n"].sum() > 0).sum())
bad = (
    0
    if notes.empty or rec.empty
    else int(notes[notes["recording_id"].isin(rec["recording_id"])]["quality"].eq("bad").sum())
)
c = st.columns(5)
c[0].metric("Recordings", len(rec), border=True)
c[1].metric("Check-ins", len(sessions), border=True)
c[2].metric(
    "Days measured",
    f"{days_with} / {n_days}",
    f"{days_with / n_days:.0%} of days",
    delta_color="off",
    delta_arrow="off",
    border=True,
)
c[3].metric(
    "Streak",
    f"{data.streak(data.session_grid(tables['recordings'], end - timedelta(365), end), end)} d",
    help="Consecutive days with at least one recording",
    border=True,
)
c[4].metric(
    "Time recorded", common.duration(rec["duration_s"].sum() if not rec.empty else 0), border=True
)
if bad:
    st.caption(
        f"{common.STATUS_ICON['critical']} {bad} recording(s) in this period are flagged bad."
    )

# ------------------------------------------------------------- calendar
st.subheader("Calendar")
fig = go.Figure(
    go.Heatmap(
        x=pd.to_datetime(grid["day"]),
        y=grid["slot"],
        z=grid["n"].clip(upper=3),
        customdata=grid["n"],
        colorscale=[[0, "#262624"], [0.01, "#1c5cab"], [0.5, "#3987e5"], [1, "#86b6ef"]],
        zmin=0,
        zmax=3,
        xgap=2,
        ygap=2,
        showscale=False,
        hovertemplate="%{x|%a %d %b} %{y}<br>%{customdata} recording(s)<extra></extra>",
    )
)
fig.update_yaxes(categoryorder="array", categoryarray=["PM", "AM"])
st.plotly_chart(charts.style(fig, height=150, legend=False, unified=False))

left, right = st.columns([1, 2], gap="large")
with left:
    st.subheader("By test")
    if rec.empty:
        common.empty("No recordings in this period")
    else:
        by = rec.groupby("test", dropna=False).size().sort_values()
        fig = go.Figure(
            go.Bar(
                x=by.values,
                y=by.index.fillna("–"),
                orientation="h",
                width=0.6,
                marker=dict(color=charts.SERIES[0], cornerradius=4),
                hovertemplate="%{y}: %{x}<extra></extra>",
            )
        )
        fig.update_xaxes(dtick=1 if by.max() < 8 else None, title="recordings")
        st.plotly_chart(charts.style(fig, height=110 + 40 * len(by), legend=False, unified=False))
with right:
    st.subheader("Check-in ratings")
    if anchors.empty:
        common.empty("No check-ins in this period")
    else:
        a = anchors.sort_values("day")
        x = pd.to_datetime(a["day"]) + pd.to_timedelta(a["slot"].eq("PM") * 12, unit="h")
        fig = go.Figure()
        for i, k in enumerate(RATINGS):
            charts.line(fig, x, a[k], k.replace("_", " "), charts.SERIES[i])
        fig.update_yaxes(range=[-0.5, 10.5], dtick=2)
        st.plotly_chart(charts.style(fig, height=260))

# ------------------------------------------------------------------ BP
st.subheader("Blood pressure")
bp = data.bp_wide(tables["bp_readings"], tables["sessions"])
bp = data.in_range(bp, start, end) if "day" in bp else bp
if bp.empty or "sbp_before_seated" not in bp:
    common.empty("No cuff readings in this period")
else:
    bp = bp.sort_values("day")
    x = pd.to_datetime(bp["day"]) + pd.to_timedelta(bp["slot"].eq("PM") * 12, unit="h")
    fig = go.Figure()
    series = [
        ("sbp_before_seated", "SBP seated", 0, None),
        ("sbp_during_stand", "SBP standing", 0, "dot"),
        ("dbp_before_seated", "DBP seated", 1, None),
        ("dbp_during_stand", "DBP standing", 1, "dot"),
    ]
    for col, name, slot, dash in series:
        if col in bp:
            charts.line(fig, x, bp[col], name, charts.SERIES[slot], dash=dash)
    fig.update_yaxes(title="mmHg")
    st.plotly_chart(charts.style(fig, height=280))

# ------------------------------------------------------------- quality
st.subheader("Signal quality")
if rec.empty:
    common.empty("No recordings in this period")
else:
    q = rec.sort_values("start_utc").copy()
    q["local"] = data.local_time(q["start_utc"], q["utc_offset_min"])
    lo = q["leads_off_fraction"].fillna(0)
    status = lo.map(common.leads_off_status)
    fig = go.Figure(
        go.Bar(
            x=q["local"].dt.strftime("%d %b %H:%M"),  # one bar per recording, evenly spaced
            y=lo * 100,
            width=min(0.6, 0.1 * len(q)) if len(q) < 6 else None,
            marker=dict(color=[charts.STATUS[s] for s in status], cornerradius=4),
            customdata=q[["recording_id", "test"]],
            hovertemplate=(
                "%{customdata[0]}<br>%{customdata[1]}<br>leads off %{y:.1f}%<extra></extra>"
            ),
        )
    )
    fig.add_hline(
        y=5,
        line=dict(color=charts.MUTED, dash="dot", width=1),
        annotation_text="5 %",
        annotation_font_color=charts.MUTED,
    )
    fig.update_yaxes(title="ECG leads off [%]")
    st.plotly_chart(charts.style(fig, height=240, legend=False, unified=False))
    st.caption(
        f"{common.STATUS_ICON['good']} under 5 % · {common.STATUS_ICON['warning']} 5–20 % · "
        f"{common.STATUS_ICON['critical']} over 20 % of samples with ECG leads off"
    )

# -------------------------------------------------------------- table
st.subheader("Recordings")
if not rec.empty:
    t = rec.sort_values("start_utc", ascending=False).copy()
    t["start"] = data.local_time(t["start_utc"], t["utc_offset_min"])
    t["min"] = t["duration_s"] / 60
    t["leads off"] = t["leads_off_fraction"]
    t["gaps"] = t["ecg_gaps"] + t["ppg_gaps"]
    if not notes.empty:
        t = t.merge(notes[["recording_id", "quality", "note"]], on="recording_id", how="left")
    else:
        t["quality"], t["note"] = None, None
    t["checksums"] = t["checksums_ok"].map({True: "ok", False: "MISMATCH"})
    shown = t[
        [
            "start",
            "test",
            "posture",
            "session_id",
            "min",
            "leads off",
            "gaps",
            "crc_errors",
            "checksums",
            "quality",
            "note",
            "recording_id",
        ]
    ]
    event = st.dataframe(
        shown,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "start": st.column_config.DatetimeColumn("Start", format="ddd DD MMM HH:mm"),
            "min": st.column_config.NumberColumn("Min", format="%.1f"),
            "leads off": st.column_config.ProgressColumn(
                "Leads off", min_value=0, max_value=1, format="percent"
            ),
            "crc_errors": "CRC",
            "recording_id": None,
        },
    )
    rows = event.selection.rows
    if rows:
        rid = shown.iloc[rows[0]]["recording_id"]
        if st.button(f"Open {rid} in the recording browser →", type="primary"):
            st.session_state["rec"] = rid
            st.switch_page("views/recording.py")
    else:
        st.caption("Select a row to open it in the recording browser.")
