"""Training: the basics in one place. intervals.icu stays the full training tool."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from physio_app import charts, common, data

start, end = common.date_range()
tables = common.tables()
acts = common.ranged("load_events")
activities = tables["activities"]
if not activities.empty:
    activities = activities[activities["activity_id"].isin(acts.get("activity_id", []))]
journal = common.ranged("journal")

top = st.columns([4, 1], vertical_alignment="bottom")
with top[0]:
    common.header("Training", "Garmin activities, load and recovery markers.")
top[1].link_button("Open intervals.icu ↗", "https://intervals.icu", width="stretch")

hours = acts["duration_min"].sum() / 60 if not acts.empty else 0
rpe = acts["rpe"].dropna() if not acts.empty else pd.Series(dtype=float)
c = st.columns(4)
c[0].metric("Sessions", len(acts), border=True)
c[1].metric("Hours", common.fmt(hours, 1), border=True)
c[2].metric(
    "Garmin load",
    common.fmt(acts["garmin_training_load"].sum() if not acts.empty else 0),
    border=True,
)
c[3].metric(
    "Mean RPE",
    common.fmt(rpe.mean() if len(rpe) else None, 1),
    help="From the watch's self-evaluation, or rpe= notes in the workout log",
    border=True,
)

# ------------------------------------------------------------------ load
left, right = st.columns(2, gap="large")
with left:
    st.subheader("Daily load")
    metric = st.segmented_control(
        "Load metric",
        ["garmin_training_load", "edwards_trimp", "srpe_load"],
        default="garmin_training_load",
        format_func={
            "garmin_training_load": "Garmin",
            "edwards_trimp": "TRIMP",
            "srpe_load": "sRPE",
        }.get,
        label_visibility="collapsed",
    )
    metric = metric or "garmin_training_load"
    if acts.empty:
        common.empty("No training in this period")
    else:
        d = acts.groupby(["day", "activity_type"], as_index=False)[metric].sum()
        types = sorted(d["activity_type"].dropna().unique())
        fig = go.Figure()
        for i, t in enumerate(types):
            s = d[d["activity_type"] == t]
            fig.add_bar(
                x=pd.to_datetime(s["day"]),
                y=s[metric],
                name=t,
                marker=dict(color=charts.SERIES[i % 8], cornerradius=4),
            )
        fig.update_layout(barmode="stack")
        fig.update_xaxes(
            range=[
                pd.Timestamp(start) - pd.Timedelta(hours=12),
                pd.Timestamp(end) + pd.Timedelta(hours=12),
            ]
        )
        st.plotly_chart(charts.style(fig, height=300))
        if metric == "srpe_load" and acts["srpe_load"].isna().any():
            st.caption("sRPE is missing for sessions without an RPE rating.")
with right:
    st.subheader("Weekly time in HR zones")
    z = data.weekly_zones(activities)
    if z.empty or z["hours"].sum() == 0:
        common.empty("No HR zone data")
    else:
        ramp = ["#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4"]  # Z1 -> Z5
        fig = go.Figure()
        for i, zone in enumerate(["Z1", "Z2", "Z3", "Z4", "Z5"]):
            s = z[z["zone"] == zone]
            fig.add_bar(
                x=pd.to_datetime(s["week"]),
                y=s["hours"],
                name=zone,
                marker=dict(color=ramp[i], cornerradius=4),
                hovertemplate=f"{zone}: %{{y:.1f}} h<extra></extra>",
            )
        fig.update_layout(barmode="stack")
        fig.update_xaxes(title="week starting")
        fig.update_yaxes(title="hours")
        st.plotly_chart(charts.style(fig, height=300))

# ------------------------------------------------------------- recovery
left, right = st.columns(2, gap="large")
hrv = tables["hrv_nightly"].copy()
with left:
    st.subheader("Night HRV")
    if hrv.empty:
        common.empty("No HRV nights yet")
    else:
        hrv["day"] = pd.to_datetime(hrv["day"])
        hrv = hrv.sort_values("day")
        s = hrv.set_index("day")["hrv_mean_ms"].asfreq("D")
        hrv["roll7"] = s.rolling(7, min_periods=3).mean().reindex(hrv["day"]).to_numpy()
        h = data.in_range(hrv, start, end)
        fig = go.Figure()
        charts.band(
            fig,
            h["day"],
            h["garmin_baseline_balanced_low"],
            h["garmin_baseline_balanced_upper"],
            "Garmin balanced range",
        )
        charts.line(fig, h["day"], h["hrv_mean_ms"], "night mean", charts.SERIES[0])
        charts.line(fig, h["day"], h["roll7"], "7-night mean", charts.SERIES[1], markers=False)
        charts.tag_markers(fig, journal)
        fig.update_yaxes(title="ms")
        st.plotly_chart(charts.style(fig, height=300))
with right:
    st.subheader("Resting heart rate")
    rhr = common.ranged("hr_daily")
    if rhr.empty:
        common.empty("No resting HR yet")
    else:
        fig = go.Figure()
        charts.line(
            fig, pd.to_datetime(rhr["day"]), rhr["resting_hr"], "resting HR", charts.SERIES[0]
        )
        charts.line(
            fig,
            pd.to_datetime(rhr["day"]),
            rhr["resting_hr_7d"],
            "Garmin 7-day mean",
            charts.SERIES[1],
            markers=False,
        )
        charts.tag_markers(fig, journal)
        fig.update_yaxes(title="bpm")
        st.plotly_chart(charts.style(fig, height=300))

# ------------------------------------------------------------ activities
st.subheader("Activities")
if acts.empty:
    st.caption("No activities in this period.")
else:
    t = acts.sort_values("start_utc", ascending=False).merge(
        activities[
            [
                "activity_id",
                "avg_hr",
                "max_hr",
                "distance_m",
                "garmin_aerobic_te",
                "garmin_anaerobic_te",
                "utc_offset_min",
            ]
        ],
        on="activity_id",
        how="left",
    )
    t["start"] = data.local_time(t["start_utc"], t["utc_offset_min"])
    t["km"] = t["distance_m"] / 1000
    st.dataframe(
        t[
            [
                "start",
                "activity_type",
                "duration_min",
                "km",
                "avg_hr",
                "max_hr",
                "rpe",
                "garmin_training_load",
                "edwards_trimp",
                "garmin_aerobic_te",
                "garmin_anaerobic_te",
            ]
        ],
        hide_index=True,
        column_config={
            "start": st.column_config.DatetimeColumn("Start", format="ddd DD MMM HH:mm"),
            "activity_type": "Type",
            "duration_min": st.column_config.NumberColumn("Min", format="%.0f"),
            "km": st.column_config.NumberColumn("km", format="%.1f"),
            "avg_hr": "Avg HR",
            "max_hr": "Max HR",
            "rpe": "RPE",
            "garmin_training_load": st.column_config.NumberColumn("Garmin load", format="%.0f"),
            "edwards_trimp": st.column_config.NumberColumn("TRIMP", format="%.0f"),
            "garmin_aerobic_te": st.column_config.NumberColumn("Aer. TE", format="%.1f"),
            "garmin_anaerobic_te": st.column_config.NumberColumn("Anaer. TE", format="%.1f"),
        },
    )
