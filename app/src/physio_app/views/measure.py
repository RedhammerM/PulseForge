"""Measure: check-in, recording metadata, open the live window, review the result."""

import json
from datetime import date, datetime

import pandas as pd
import streamlit as st
from physio_app import common, data, live
from physio_state import manual

from physio_daq import checkin as ck
from physio_daq.config import STAND_PROTOCOL
from physio_daq.metadata_form import CAFFEINE, POSTURE, TESTS, WHEN

cfg = common.cfg()
tables = common.tables()
now = datetime.now(cfg.tz)
today = common.today()

common.header("Measure", "Check in, open the live window, then review the recording here.")

# ---------------------------------------------------------------- today strip
done = data.sessions_today(tables, today)
hrv_now, hrv_base = data.night_vs_baseline(tables["hrv_nightly"], today)
sleep = data.in_range(tables["sleep_nightly"], today, today)
rhr = data.in_range(tables["hr_daily"], today, today)
c = st.columns(5)
c[0].metric("AM session", "done" if done["AM"] else "open", border=True)
c[1].metric("PM session", "done" if done["PM"] else "open", border=True)
c[2].metric(
    "Night HRV",
    common.fmt(hrv_now, 0, " ms"),
    None if hrv_now is None or hrv_base is None else f"{hrv_now - hrv_base:+.0f} ms vs 7 d",
    delta_color="normal",
    border=True,
)
sleep_s = sleep["sleep_s"].iloc[-1] if not sleep.empty else None
c[3].metric("Sleep", common.fmt(None if sleep_s is None else sleep_s / 3600, 1, " h"), border=True)
c[4].metric(
    "Resting HR",
    common.fmt(rhr["resting_hr"].iloc[-1] if not rhr.empty else None, 0, " bpm"),
    border=True,
)

# ------------------------------------------------------------------ check-in
default_day, default_slot = ck.session_day_slot(now.replace(tzinfo=None))
left, right = st.columns(2, gap="large")

with left:
    st.subheader("1 · Check-in")
    st.caption("Rate before you see any cuff number. Ratings are validation anchors.")
    cc = st.columns(3)
    subject = cc[0].text_input("Subject", cfg.subject_id)
    day = cc[1].date_input("Wake-up date", date.fromisoformat(default_day), max_value=today)
    slot = cc[2].segmented_control("Slot", data.SLOTS, default=default_slot)
    sid = ck.session_id(subject, day.isoformat(), slot or default_slot)
    path = ck.session_path(cfg.recordings_dir, sid)
    existing = json.loads(path.read_text()) if path.exists() else None

    if existing:
        t = pd.Timestamp(existing["checkin_time"])
        st.success(f"Checked in at {t:%H:%M} · session `{sid}`", icon=":material/check_circle:")
        with st.expander("Ratings"):
            st.write({k: existing["ratings"].get(k) for k in ck.RATINGS})
        redo = st.toggle("Redo check-in", False)
    else:
        redo = True

    if redo:
        with st.form("checkin", border=True):
            ratings = {}
            for key, label in ck.RATINGS.items():
                ratings[key] = st.pills(label, list(range(11)), key=f"r_{key}")
            waking = st.number_input("Minutes since waking", 0, 720, value=None, step=5)
            if st.form_submit_button("Save check-in", type="primary"):
                entry = ck.make_checkin(
                    subject, day.isoformat(), slot or default_slot, ratings, waking, datetime.now()
                )
                ck.save_session(cfg.recordings_dir, entry)
                common.rebuild()
                st.rerun()

# ------------------------------------------------------------------ recording
with right:
    st.subheader("2 · Recording")
    pre = data.prefill(tables, today)
    ports = live.list_ports()
    st.caption(
        "Values prefill the form the live window shows on Start. Garmin fills sleep "
        "and last workout where it can."
    )
    with st.container(border=True):
        pc = st.columns([3, 1], vertical_alignment="bottom")
        port_labels = {p: f"{p} · {d}" for p, d in ports}
        port = pc[0].selectbox(
            "Serial port",
            list(port_labels) or ["/dev/ttyUSB0"],
            format_func=lambda p: port_labels.get(p, f"{p} (not found)"),
        )
        pc[1].button("↻ Ports", width="stretch")
        rc = st.columns([2, 1, 1])
        test = rc[0].selectbox("Test", TESTS, index=TESTS.index(STAND_PROTOCOL))
        when_default = "morning" if (slot or default_slot) == "AM" else "evening"
        when = rc[1].selectbox("When", WHEN, index=WHEN.index(when_default))
        posture = rc[2].selectbox("Posture", POSTURE, index=POSTURE.index("sitting"))
        rc = st.columns(3)
        sleep_h = rc[0].number_input("Sleep [h]", 0.0, 16.0, pre.get("sleep_h"), 0.5, format="%.1f")
        workout = rc[1].number_input("Last workout [min]", 0, 600, pre.get("last_workout_min"))
        rpe = rc[2].number_input("Last session RPE", 0, 10, pre.get("last_session_rpe"))
        rc = st.columns(3)
        caffeine = rc[0].selectbox("Caffeine today", CAFFEINE)
        meal = rc[1].number_input("Hours since meal", 0.0, 24.0, None, 0.5, format="%.1f")
        notes = rc[2].text_input("Notes")

        proc = live.state()
        defaults = {
            "subject": subject,
            "test": test,
            "when": when,
            "posture": posture,
            "sleep_h": sleep_h,
            "last_workout_min": workout,
            "last_session_rpe": rpe,
            "caffeine_today": None if caffeine == "n/a" else caffeine,
            "hours_since_meal": meal,
            "notes": notes,
        }
        if test == STAND_PROTOCOL and not path.exists():
            st.warning("No check-in for this session yet. The stand protocol expects one.")
        if st.button(
            "Open live window",
            type="primary",
            width="stretch",
            disabled=proc.running,
            icon=":material/monitor_heart:",
        ):
            live.launch(port, cfg.recordings_dir, sid if path.exists() else None, defaults)
            st.rerun()


@st.fragment(run_every=2)
def live_status() -> None:
    proc = live.state()
    if proc.running:
        st.info(
            "Live window is open. Press **Start** there (Space). Close it when done.",
            icon=":material/sensors:",
        )
    elif not proc.handled:
        proc.handled = True
        if proc.returncode not in (0, None):
            st.error(f"Live window exited with code {proc.returncode}")
            st.code("\n".join(proc.log[-30:]) or "(no output)")
        common.rebuild()
        st.rerun(scope="app")
    if proc.log:
        with st.expander("Live window log"):
            st.code("\n".join(proc.log[-30:]))


with right:
    live_status()

# ------------------------------------------------------------ latest result
st.divider()
st.subheader("3 · Latest recording")
rec = tables["recordings"]
if rec.empty:
    st.info("No recordings yet.")
    st.stop()

last = rec.sort_values("start_utc").iloc[-1]
start_local = data.local_time(
    pd.Series([last["start_utc"]]), pd.Series([last["utc_offset_min"]])
).iloc[0]
lo = float(last["leads_off_fraction"] or 0)
st.markdown(
    f"**{last['recording_id']}** · {start_local:%a %d %b %H:%M} · {last['test']} · "
    f"{last['posture']} · {last['end_reason']}"
)
c = st.columns(6)
c[0].metric("Duration", f"{last['duration_s'] / 60:.1f} min")
c[1].metric(common.flagged("Leads off", common.leads_off_status(lo)), f"{lo:.1%}")
c[2].metric("ECG gaps", int(last["ecg_gaps"]))
c[3].metric("PPG gaps", int(last["ppg_gaps"]))
c[4].metric("CRC errors", int(last["crc_errors"]))
ok = bool(last["checksums_ok"])
c[5].metric(common.flagged("Checksums", "good" if ok else "critical"), "ok" if ok else "mismatch")

col_a, col_b = st.columns(2, gap="large")
with col_a:
    sess_id = last["session_id"]
    bp = tables["bp_readings"]
    has_bp = not bp.empty and (bp["session_id"] == sess_id).any()
    if sess_id and not has_bp:
        with st.form("bp"):
            st.markdown("**Cuff BP for this session**")
            vals = {}
            for key, title in (
                ("before_seated", "Before, seated"),
                ("during_stand", "During stand"),
            ):
                bc = st.columns(3)
                vals[key] = (
                    bc[0].number_input(f"{title} SBP", 60, 260, None),
                    bc[1].number_input("DBP", 30, 160, None, key=f"dbp_{key}"),
                    bc[2].number_input("HR", 30, 200, None, key=f"hr_{key}"),
                )
            arm = st.segmented_control("Arm", ["left", "right"], default="left")
            if st.form_submit_button("Save BP"):
                readings = {k: v for k, v in vals.items() if v[0] is not None and v[1] is not None}
                try:
                    cuff = ck.make_bp(readings, arm or "left", datetime.now())
                except ValueError as e:
                    st.error(str(e))
                else:
                    session_file = ck.session_path(cfg.recordings_dir, sess_id)
                    if readings and session_file.exists():
                        ck.save_session(cfg.recordings_dir, {"session_id": sess_id, **cuff})
                        common.rebuild()
                        st.rerun()
    elif not sess_id:
        st.caption("This recording is not part of a check-in session, so it has no cuff BP.")
    elif has_bp:
        st.dataframe(
            bp.loc[bp["session_id"] == sess_id, ["label", "sbp", "dbp", "hr", "arm"]],
            hide_index=True,
        )
with col_b:
    notes_t = tables["recording_notes"]
    cur = notes_t[notes_t["recording_id"] == last["recording_id"]] if not notes_t.empty else notes_t
    with st.form("rec_note"):
        st.markdown("**Your assessment**")
        q = st.segmented_control(
            "Quality", manual.QUALITY_FLAGS, default=None if cur.empty else cur.iloc[0]["quality"]
        )
        n = st.text_area("Note", "" if cur.empty else cur.iloc[0]["note"], height=80)
        if st.form_submit_button("Save note"):
            manual.save_recording_note(cfg, last["recording_id"], q, n)
            common.rebuild_manual()
            st.rerun()
    if st.button("Open in recording browser →"):
        st.session_state["rec"] = last["recording_id"]
        st.switch_page("views/recording.py")
