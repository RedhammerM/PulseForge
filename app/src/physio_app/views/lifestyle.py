"""Lifestyle: food and journal entry, weight, energy balance, sleep, daily activity."""

from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from physio_app import charts, common, data
from physio_state import manual

cfg = common.cfg()
start, end = common.date_range()
tables = common.tables()
journal = common.ranged("journal")

common.header(
    "Lifestyle",
    "Food and journal are entered here. Weight, calories burned, sleep and steps come from Garmin.",
)

# ------------------------------------------------------------------ entry
with st.container(border=True):
    st.markdown("**Log a day**")
    day = st.date_input("Day", common.today(), max_value=common.today(), key="log_day")
    nut = tables["nutrition_daily"]
    jr = tables["journal"]
    n_cur = nut[nut["day"] == day].iloc[0] if not nut.empty and (nut["day"] == day).any() else None
    j_cur = jr[jr["day"] == day].iloc[0] if not jr.empty and (jr["day"] == day).any() else None

    def val(row, key):
        return None if row is None or pd.isna(row[key]) else row[key]

    left, right = st.columns(2, gap="large")
    with left, st.form(f"food_{day}", border=False):
        st.markdown(":material/restaurant: **Food**")
        fc = st.columns(4)
        kcal = fc[0].number_input("kcal", 0, 15000, val(n_cur, "kcal"), 50)
        prot = fc[1].number_input("Protein g", 0.0, 1000.0, val(n_cur, "protein_g"), 5.0)
        carb = fc[2].number_input("Carbs g", 0.0, 2000.0, val(n_cur, "carbs_g"), 5.0)
        fat = fc[3].number_input("Fat g", 0.0, 1000.0, val(n_cur, "fat_g"), 5.0)
        fnote = st.text_input("Note", val(n_cur, "note") or "", key="fnote")
        if st.form_submit_button("Save food", type="primary"):
            manual.save_nutrition(cfg, day, kcal, prot, carb, fat, fnote)
            common.rebuild_manual()
            st.toast(f"Food saved for {day:%d %b}")
            st.rerun()
    with right, st.form(f"journal_{day}", border=False):
        st.markdown(":material/edit_note: **Journal**")
        tags = st.pills(
            "Tags",
            manual.JOURNAL_TAGS,
            selection_mode="multi",
            default=list(j_cur["tags"]) if j_cur is not None else None,
        )
        jnote = st.text_input("Note", j_cur["note"] if j_cur is not None else "", key="jnote")
        if st.form_submit_button("Save journal", type="primary"):
            manual.save_journal(cfg, day, tags or [], jnote)
            common.rebuild_manual()
            st.toast(f"Journal saved for {day:%d %b}")
            st.rerun()

# ---------------------------------------------------------------- numbers
weight = data.daily_weight(tables["weight"])
w_range = data.in_range(weight, start, end)
energy = data.in_range(
    data.energy_balance(tables["daily_summary"], tables["nutrition_daily"]), start, end
)
complete = energy[pd.to_datetime(energy["day"]).dt.date < common.today()]
sleep = common.ranged("sleep_nightly")

c = st.columns(5)
if w_range.empty:
    c[0].metric("Weight", "–", border=True)
else:
    first, last = w_range.iloc[0], w_range.iloc[-1]
    c[0].metric(
        "Weight",
        f"{last['weight_kg']:.1f} kg",
        f"{last['weight_7d'] - first['weight_7d']:+.1f} kg",
        help="Change of the 7-day mean over the selected period",
        delta_color="off",
        delta_arrow="off",
        border=True,
    )
c[1].metric("Eaten / day", common.fmt(complete["kcal"].mean(), 0, " kcal"), border=True)
c[2].metric(
    "Burned / day",
    common.fmt(complete["calories_total"].mean(), 0, " kcal"),
    help="Garmin total (BMR + active); today is left out because it is incomplete",
    border=True,
)
c[3].metric(
    "Balance / day",
    common.fmt(complete["balance"].mean(), 0, " kcal"),
    help="Only days with both a food log and a Garmin total",
    border=True,
)
c[4].metric(
    "Sleep / night",
    common.fmt(sleep["sleep_s"].mean() / 3600 if not sleep.empty else None, 1, " h"),
    border=True,
)

x_range = [pd.Timestamp(start) - pd.Timedelta(hours=12), pd.Timestamp(end) + pd.Timedelta(hours=12)]

# ---------------------------------------------------------------- weight / energy
left, right = st.columns(2, gap="large")
with left:
    st.subheader("Weight")
    if w_range.empty:
        common.empty("No weigh-ins (sync Garmin after weighing in)")
    else:
        fig = go.Figure()
        fig.add_scatter(
            x=pd.to_datetime(w_range["day"]),
            y=w_range["weight_kg"],
            name="weigh-in",
            mode="markers",
            marker=dict(size=8, color=charts.SERIES[0], line=dict(width=2, color="#1a1a19")),
        )
        charts.line(
            fig,
            pd.to_datetime(w_range["day"]),
            w_range["weight_7d"],
            "7-day mean",
            charts.SERIES[1],
            markers=False,
        )
        fig.update_xaxes(range=x_range)
        fig.update_yaxes(title="kg")
        st.plotly_chart(charts.style(fig, height=300))
with right:
    st.subheader("Calories")
    if energy.empty:
        common.empty("No calorie data")
    else:
        d = pd.to_datetime(energy["day"])
        fig = go.Figure()
        fig.add_bar(
            x=d,
            y=energy["calories_total"],
            name="burned (Garmin)",
            marker=dict(color=charts.SERIES[0], cornerradius=4),
        )
        fig.add_bar(
            x=d, y=energy["kcal"], name="eaten", marker=dict(color=charts.SERIES[1], cornerradius=4)
        )
        fig.update_layout(barmode="group", bargroupgap=0.08)
        fig.update_xaxes(range=x_range)
        fig.update_yaxes(title="kcal")
        st.plotly_chart(charts.style(fig, height=300))
        if common.today() in set(energy["day"]):
            st.caption("Today's burned value is only what Garmin has counted so far.")

if complete["balance"].notna().any():
    st.subheader("Energy balance")
    b = complete.dropna(subset=["balance"])
    fig = go.Figure(
        go.Bar(
            x=pd.to_datetime(b["day"]),
            y=b["balance"],
            marker=dict(
                color=[charts.SERIES[0] if v >= 0 else charts.SERIES[7] for v in b["balance"]],
                cornerradius=4,
            ),
            hovertemplate="%{x|%a %d %b}: %{y:+,.0f} kcal<extra></extra>",
        )
    )
    fig.add_hline(y=0, line=dict(color=charts.MUTED, width=1))
    fig.update_xaxes(range=x_range)
    fig.update_yaxes(title="eaten − burned [kcal]")
    st.plotly_chart(charts.style(fig, height=220, legend=False, unified=False))
    st.caption("Blue: surplus · red: deficit. Garmin's burned estimate can be off by 10 % or more.")

# ------------------------------------------------------------------ sleep
st.subheader("Sleep")
left, right = st.columns([2, 1], gap="large")
with left:
    if sleep.empty:
        common.empty("No sleep data")
    else:
        d = pd.to_datetime(sleep["day"])
        fig = go.Figure()
        for i, (col, name) in enumerate(
            (("deep_s", "deep"), ("light_s", "light"), ("rem_s", "REM"), ("awake_s", "awake"))
        ):
            fig.add_bar(
                x=d,
                y=sleep[col] / 3600,
                name=name,
                marker=dict(color=charts.SERIES[i], line=dict(width=1, color="#141413")),
                hovertemplate=f"{name}: %{{y:.1f}} h<extra></extra>",
            )
        fig.update_layout(barmode="stack")
        charts.tag_markers(fig, journal)
        fig.update_xaxes(range=x_range)
        fig.update_yaxes(title="hours")
        st.plotly_chart(charts.style(fig, height=300))
with right:
    if not sleep.empty and sleep["garmin_sleep_score"].notna().any():
        fig = go.Figure()
        charts.line(
            fig,
            pd.to_datetime(sleep["day"]),
            sleep["garmin_sleep_score"],
            "score",
            charts.SERIES[0],
        )
        fig.update_yaxes(range=[0, 100], title="Garmin sleep score")
        fig.update_xaxes(range=x_range)
        st.plotly_chart(charts.style(fig, height=300, legend=False))

# ------------------------------------------------------------- daily activity
st.subheader("Daily activity and stress")
summary = common.ranged("daily_summary")
left, right = st.columns(2, gap="large")
with left:
    if summary.empty or "steps" not in summary:
        common.empty("No step data")
    else:
        fig = go.Figure(
            go.Bar(
                x=pd.to_datetime(summary["day"]),
                y=summary["steps"],
                name="steps",
                marker=dict(color=charts.SERIES[0], cornerradius=4),
            )
        )
        if "steps_goal" in summary:
            fig.add_scatter(
                x=pd.to_datetime(summary["day"]),
                y=summary["steps_goal"],
                name="goal",
                mode="lines",
                line=dict(color=charts.MUTED, dash="dot", width=2, shape="hv"),
            )
        fig.update_xaxes(range=x_range)
        fig.update_yaxes(title="steps")
        st.plotly_chart(charts.style(fig, height=260))
with right:
    if summary.empty:
        common.empty("No stress data")
    else:
        fig = go.Figure()
        charts.line(
            fig,
            pd.to_datetime(summary["day"]),
            summary["garmin_stress_avg"],
            "Garmin stress (avg)",
            charts.SERIES[0],
        )
        charts.line(
            fig,
            pd.to_datetime(summary["day"]),
            summary["garmin_bb_max"],
            "Body Battery max",
            charts.SERIES[1],
        )
        charts.tag_markers(fig, journal)
        fig.update_xaxes(range=x_range)
        fig.update_yaxes(range=[0, 100])
        st.plotly_chart(charts.style(fig, height=260))

# ---------------------------------------------------------------- journal
if not journal.empty:
    st.subheader("Journal")
    j = journal.sort_values("day", ascending=False).copy()
    j["tags"] = j["tags"].map(lambda t: ", ".join(t))
    st.dataframe(j[["day", "tags", "note"]], hide_index=True)
    st.caption(
        f"◆ markers on the charts here and on Training show these days "
        f"({len(journal)} in the last {(end - start + timedelta(days=1)).days} days)."
    )
