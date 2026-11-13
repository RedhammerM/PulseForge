"""Recording browser: traces, events, quality and the session around one recording."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from physio_app import charts, common, data
from physio_state import manual
from plotly.subplots import make_subplots
from scipy.signal import butter, iirnotch, sosfiltfilt, tf2sos

from physio_daq.config import ECG_FS, MAINS_HZ, PPG_FS

cfg = common.cfg()
tables = common.tables()
rec = tables["recordings"]

common.header("Recording browser")
if rec.empty:
    st.info("No recordings yet.")
    st.stop()

rec = rec.sort_values("start_utc", ascending=False).reset_index(drop=True)
rec["local"] = data.local_time(rec["start_utc"], rec["utc_offset_min"])
labels = {
    r.recording_id: f"{r.local:%a %d %b %Y %H:%M} · {r.test} · {r.duration_s / 60:.1f} min"
    for r in rec.itertuples()
}
ids = list(labels)
wanted = st.session_state.get("rec")
rid = st.selectbox(
    "Recording", ids, index=ids.index(wanted) if wanted in ids else 0, format_func=labels.get
)
st.session_state["rec"] = rid
row = rec[rec["recording_id"] == rid].iloc[0]


@st.cache_data(show_spinner="Loading signals…", max_entries=4)
def load(folder: str, recording_id: str):
    r = data.load_recording(cfg.recordings_dir, recording_id)
    ecg = r.ecg["ecg"].to_numpy(float)
    ir = r.ppg["ir"].to_numpy(float)
    band = butter(3, [0.5, 40.0], btype="band", fs=ECG_FS, output="sos")
    notch = tf2sos(*iirnotch(MAINS_HZ, 30.0, fs=ECG_FS))
    ppg_sos = butter(3, [0.5, 8.0], btype="band", fs=PPG_FS, output="sos")
    ecg_f = sosfiltfilt(np.vstack([band, notch]), ecg) if len(ecg) > 30 else ecg
    ir_f = sosfiltfilt(ppg_sos, ir) if len(ir) > 30 else ir
    lo = (r.ecg["lo_plus"] | r.ecg["lo_minus"]).to_numpy(bool)
    return r.summary, r.ecg["t_s"].to_numpy(), ecg_f, lo, r.ppg["t_s"].to_numpy(), ir_f, r.events


try:
    summary, t_e, ecg_f, lo, t_p, ir_f, events = load(str(cfg.recordings_dir), rid)
except FileNotFoundError as e:
    st.error(f"Files of this recording are missing: {e.filename}")
    st.stop()

# ---------------------------------------------------------------- metrics
e, p = summary["ecg"], summary["ppg"]
c = st.columns(7)
c[0].metric("Duration", f"{summary['duration_s'] / 60:.1f} min")
c[1].metric("ECG fs", f"{e['effective_fs']:.1f} Hz")
c[2].metric("PPG fs", f"{p['effective_fs']:.1f} Hz")
lo_frac = e["leads_off_fraction"]
c[3].metric(common.flagged("Leads off", common.leads_off_status(lo_frac)), f"{lo_frac:.1%}")
c[4].metric("Gaps", e["gaps_gt_1p5_ticks"] + p["gaps_gt_1p5_periods"])
c[5].metric("CRC errors", summary["crc_errors"])
ok = bool(row["checksums_ok"])
c[6].metric(common.flagged("Checksums", "good" if ok else "critical"), "ok" if ok else "mismatch")


def add_events(fig: go.Figure) -> None:
    for ev in events.itertuples():
        fig.add_vline(x=ev.t_s, line=dict(color=charts.STATUS["warning"], width=1.5, dash="dash"))
        fig.add_annotation(
            x=ev.t_s,
            y=1.02,
            yref="paper",
            text=ev.label,
            showarrow=False,
            font=dict(color=charts.STATUS["warning"], size=11),
        )


def lead_off_spans(fig: go.Figure, t: np.ndarray, mask: np.ndarray) -> None:
    """Shade stretches with ECG leads off (merged; at most 50 spans)."""
    if not mask.any():
        return
    edges = np.flatnonzero(np.diff(np.r_[0, mask.astype(int), 0]))
    for a, b in list(zip(edges[::2], edges[1::2], strict=True))[:50]:
        fig.add_vrect(
            x0=t[a],
            x1=t[min(b, len(t) - 1)],
            fillcolor=charts.STATUS["critical"],
            opacity=0.12,
            line_width=0,
            row=1,
            col=1,
        )


def traces(t0: float, t1: float, bins: int | None, height: int) -> go.Figure:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        subplot_titles=("ECG (0.5–40 Hz, zero-phase)", "PPG IR (0.5–8 Hz)"),
    )
    fig.update_annotations(font=dict(size=12, color=charts.MUTED))  # the subplot titles
    for r, (t, y, color, name) in enumerate(
        ((t_e, ecg_f, charts.SIGNAL["ecg"], "ECG"), (t_p, ir_f, charts.SIGNAL["ppg"], "PPG IR")),
        start=1,
    ):
        m = (t >= t0) & (t <= t1)
        x, v = data.minmax_downsample(t[m], y[m], bins) if bins else (t[m], y[m])
        fig.add_trace(
            go.Scattergl(x=x, y=v, mode="lines", name=name, line=dict(color=color, width=1.2)),
            row=r,
            col=1,
        )
    lead_off_spans(fig, t_e, lo)
    add_events(fig)
    fig.update_xaxes(range=[t0, t1])
    fig.update_xaxes(title="time [s]", row=2, col=1)
    charts.style(fig, height=height, legend=False, unified=False)
    fig.update_layout(margin=dict(t=34))  # room for the subplot titles
    fig.update_yaxes(title=None)
    return fig


total = float(max(t_e[-1] if len(t_e) else 0, t_p[-1] if len(t_p) else 0))
st.subheader("Overview")
st.plotly_chart(traces(0, total, 1500, 360), key="overview")
if lo.any():
    st.caption("Red shading: ECG leads off.")

st.subheader("Detail")
dc = st.columns([1.4, 4])
width = dc[0].segmented_control(
    "Window", [5, 10, 30, 60], default=10, format_func=lambda s: f"{s} s"
)
width = float(width or 10)
t0 = dc[1].slider("Start [s]", 0.0, max(total - width, 0.0), 0.0, step=1.0)
st.plotly_chart(traces(t0, t0 + width, None, 420), key="detail")

# ------------------------------------------------------------ context
left, mid, right = st.columns(3, gap="large")
with left:
    st.markdown("**Metadata**")
    meta = summary.get("metadata") or {}
    st.dataframe(pd.DataFrame({"value": {k: str(v) for k, v in meta.items()}}), width="stretch")
    st.caption(f"Firmware: `{summary.get('firmware', '').lstrip('# ')}`")
with mid:
    st.markdown("**Session**")
    sid = row["session_id"]
    if not sid:
        st.caption("Not part of a check-in session.")
    else:
        st.caption(f"`{sid}`")
        a = tables["anchors"]
        a = a[a["session_id"] == sid] if not a.empty else a
        if not a.empty:
            st.dataframe(
                a.iloc[0][["readiness", "sleep_quality", "soreness", "stress"]].rename("rating")
            )
        bp = tables["bp_readings"]
        bp = bp[bp["session_id"] == sid] if not bp.empty else bp
        if not bp.empty:
            st.dataframe(bp[["label", "sbp", "dbp", "hr"]], hide_index=True)
    if not events.empty:
        st.markdown("**Events**")
        st.dataframe(events[["label", "t_s"]].rename(columns={"t_s": "time [s]"}), hide_index=True)
with right:
    notes_t = tables["recording_notes"]
    cur = notes_t[notes_t["recording_id"] == rid] if not notes_t.empty else notes_t
    with st.form(f"note_{rid}"):
        st.markdown("**Your assessment**")
        q = st.segmented_control(
            "Quality", manual.QUALITY_FLAGS, default=None if cur.empty else cur.iloc[0]["quality"]
        )
        n = st.text_area("Note", "" if cur.empty else cur.iloc[0]["note"], height=100)
        if st.form_submit_button("Save"):
            manual.save_recording_note(cfg, rid, q, n)
            common.rebuild_manual()
            st.toast("Saved")
            st.rerun()
