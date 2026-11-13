"""App shell: navigation, date range, refresh."""

from datetime import timedelta
from pathlib import Path

import streamlit as st

from physio_app import common

st.set_page_config(page_title="Physio", page_icon=":material/monitor_heart:", layout="wide")
VIEWS = Path(__file__).with_name("views")

nav = st.navigation(
    {
        "Record": [
            st.Page(
                VIEWS / "measure.py",
                title="Measure",
                icon=":material/radio_button_checked:",
                default=True,
            )
        ],
        "Measurements": [
            st.Page(VIEWS / "measurements.py", title="Overview", icon=":material/fact_check:"),
            st.Page(
                VIEWS / "recording.py", title="Recording browser", icon=":material/monitor_heart:"
            ),
        ],
        "Body": [
            st.Page(VIEWS / "training.py", title="Training", icon=":material/directions_run:"),
            st.Page(VIEWS / "lifestyle.py", title="Lifestyle", icon=":material/restaurant:"),
        ],
        "System": [st.Page(VIEWS / "data.py", title="Data & sync", icon=":material/sync:")],
    }
)

with st.sidebar:
    st.subheader("Period")
    presets = {"7 d": 6, "30 d": 29, "90 d": 89, "1 y": 364}
    choice = st.segmented_control(
        "Period",
        [*presets, "Custom"],
        default="30 d",
        label_visibility="collapsed",
        key="period",
    )
    end = common.today()
    if choice == "Custom":
        picked = st.date_input("Range", (end - timedelta(days=29), end), max_value=end)
        if isinstance(picked, tuple) and len(picked) == 2:
            st.session_state["range"] = picked
    else:
        st.session_state["range"] = (end - timedelta(days=presets.get(choice or "30 d", 29)), end)
    start, end = common.date_range()
    st.caption(f"{start:%d %b %Y} – {end:%d %b %Y}")
    st.divider()
    if st.button(
        "Rebuild tables",
        width="stretch",
        help="Re-read recordings, logs and Garmin raw files (no network)",
    ):
        with st.spinner("Rebuilding…"):
            common.rebuild()
        st.toast("Tables rebuilt")

nav.run()
