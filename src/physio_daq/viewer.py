"""Live ECG/PPG window (PyQt6 + pyqtgraph) with recording controls."""

import json
from collections import deque

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtGui
from PyQt6 import QtWidgets as W
from scipy.signal import butter, iirnotch, sosfilt, sosfilt_zi, tf2sos

from .acquisition import Acquisition
from .checkin import protocol_cue, save_session, session_path
from .checkin_form import ask_bp, ask_checkin
from .config import ECG_FS, MAINS_HZ, NO_FINGER_IR, PPG_FS, STAND_PROTOCOL, WINDOW_SEC
from .metadata_form import ask_metadata

FRAME_MS = 33  # ~30 fps
COLORS = {"red": "#ff6b6b", "green": "#51cf66", "gray": "#adb5bd", "amber": "#ffd43b"}
TRACE = {"ecg": "#69db7c", "ppg": "#ff8787", "raw": "#74c0fc"}


class StreamFilter:
    """Causal second-order-sections filter that keeps its state between calls."""

    def __init__(self, sos: np.ndarray) -> None:
        self.sos = sos
        self.zi = None

    def reset(self) -> None:
        self.zi = None

    def __call__(self, x) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        if self.zi is None:
            self.zi = sosfilt_zi(self.sos) * x[0]
        y, self.zi = sosfilt(self.sos, x, zi=self.zi)
        return y


def ecg_filter() -> StreamFilter:
    """0.5-40 Hz band-pass plus mains notch (display only)."""
    band = butter(3, [0.5 / (ECG_FS / 2), 40.0 / (ECG_FS / 2)], btype="band", output="sos")
    notch = tf2sos(*iirnotch(MAINS_HZ, 30.0, fs=ECG_FS))
    return StreamFilter(np.vstack([band, notch]))


def ppg_filter() -> StreamFilter:
    """0.5-8 Hz band-pass (display only)."""
    return StreamFilter(
        butter(3, [0.5 / (PPG_FS / 2), 8.0 / (PPG_FS / 2)], btype="band", output="sos")
    )


def _clock(sec: float) -> str:
    return f"{int(sec) // 60:02d}:{int(sec) % 60:02d}"


def status_text(snap: dict) -> tuple[str, str, str, str]:
    """Headline, its color key, sensor line and counters line for a snapshot."""
    no_data = snap["connected"] and (snap["age"] is None or snap["age"] > 1.0)
    if not snap["connected"]:
        head, color = "NOT CONNECTED", "red"
    elif no_data:
        head, color = "WAITING FOR DATA FROM DEVICE...", "red"
    elif snap["recording"]:
        target = f" / {_clock(snap['target_s'])}" if snap["target_s"] else ""
        head, color = f"REC {_clock(snap['elapsed'])}{target}", "green"
        if snap["stand_protocol"]:
            cue = protocol_cue(snap["elapsed"], snap["events"])
            if cue:
                head += "   " + cue
                if "NOW" in cue:
                    color = "amber"
    else:
        head, color = "PREVIEW (not recording)", "gray"

    lo = snap["lo"]
    ecg_txt = "ECG LEADS OFF" if any(lo) else "ECG leads ok"
    if snap["ppg_stale"]:
        ppg_txt = "PPG NO DATA"
    elif snap["last_ir"] < NO_FINGER_IR:
        ppg_txt = "PPG no finger"
    else:
        ppg_txt = "PPG ok"
    timing = {
        True: "PPG time: interrupt",
        False: "PPG time: POLLED (check INT wire)",
        None: "",
    }[snap["int_mode"]]
    problem = any(lo) or (snap["ppg_stale"] and not no_data) or snap["int_mode"] is False
    if snap["recording"] and problem:
        color = "red"
    line3 = (
        f"ecg={snap['e_n']} ppg={snap['p_n']} gaps={snap['gaps']} crc={snap['crc']} | {snap['msg']}"
    )
    return head, color, f"{ecg_txt}   {ppg_txt}   {timing}", line3


class Trace:
    """Rolling window of one signal with a smoothed y range (no jumpy autoscale)."""

    def __init__(self, plot: pg.PlotItem, fs: float, color: str, title: str) -> None:
        self.n = int(WINDOW_SEC * fs)
        self.buf: deque[float] = deque([np.nan] * self.n, maxlen=self.n)
        self.plot = plot
        plot.setTitle(title, color="#ced4da", size="10pt")
        plot.showGrid(x=True, y=True, alpha=0.15)
        plot.setXRange(0, WINDOW_SEC, padding=0)
        plot.setMouseEnabled(x=False, y=False)
        plot.hideButtons()
        plot.getAxis("left").setWidth(56)
        self.t = np.arange(self.n) / fs
        self.curve = plot.plot(pen=pg.mkPen(color, width=1.4))
        self.range: tuple[float, float] | None = None

    def reset(self) -> None:
        self.buf.extend([np.nan] * self.n)
        self.range = None

    def extend(self, values) -> None:
        self.buf.extend(values)

    def redraw(self) -> None:
        y = np.fromiter(self.buf, float, self.n)
        self.curve.setData(self.t, y, connect="finite")  # NaN padding is not drawn
        if np.isnan(y).all():
            return
        lo, hi = np.nanpercentile(y, [0.5, 99.5])
        pad = 0.2 * (hi - lo) + 1e-6
        target = (lo - pad, hi + pad)
        if self.range is None:
            self.range = target
        else:  # ease toward the new range
            self.range = tuple(0.85 * a + 0.15 * b for a, b in zip(self.range, target, strict=True))
        self.plot.setYRange(*self.range, padding=0)


class LiveViewer(W.QMainWindow):
    def __init__(self, acq: Acquisition, session_id: str | None = None) -> None:
        super().__init__()
        self.acq = acq
        self._epoch = 0
        self.ecg_filt, self.ppg_filt = ecg_filter(), ppg_filter()
        self.session_id: str | None = None
        self.session_checkin: dict | None = None
        self._protocol_running = False

        self.setWindowTitle("physio-daq live")
        self.resize(1280, 820)
        pg.setConfigOptions(antialias=True, background="#16181d", foreground="#adb5bd")

        central = W.QWidget()
        self.setCentralWidget(central)
        root = W.QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 10)

        # header: status headline + session
        head = W.QHBoxLayout()
        self.l_head = W.QLabel()
        self.l_head.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.l_session = W.QLabel("no session (Check-in first, or start from the app)")
        self.l_session.setStyleSheet("color: #868e96;")
        head.addWidget(self.l_head, 1)
        head.addWidget(self.l_session)
        root.addLayout(head)
        self.l_sensors = W.QLabel()
        self.l_sensors.setStyleSheet("font-family: monospace; font-size: 13px;")
        root.addWidget(self.l_sensors)

        # plots
        glw = pg.GraphicsLayoutWidget()
        self.ecg = Trace(glw.addPlot(row=0, col=0), ECG_FS, TRACE["ecg"],
                         f"ECG  (0.5-40 Hz + {MAINS_HZ:g} Hz notch, display only)")  # fmt: skip
        self.ppg = Trace(glw.addPlot(row=1, col=0), PPG_FS, TRACE["ppg"], "PPG IR  (0.5-8 Hz)")
        self.raw = Trace(glw.addPlot(row=2, col=0), PPG_FS, TRACE["raw"], "PPG IR raw")
        self.raw.plot.setLabel("bottom", "time [s]")
        root.addWidget(glw, 1)

        # controls
        row = W.QHBoxLayout()
        self.b_checkin = self._button("Check-in", None, self._on_checkin)
        self.b_start = self._button("Start  ␣", "#2b8a3e", self._on_start)
        self.b_stop = self._button("Stop  ␣", "#c92a2a", self.acq.stop_recording)
        self.b_stand = self._button("Stand  S", "#e67700", lambda: self.acq.mark("stand"))
        self.b_cuff = self._button("Cuff  C", "#e67700", lambda: self.acq.mark("cuff"))
        self.b_mark = self._button("Mark  M", None, lambda: self.acq.mark("mark"))
        for b in (self.b_checkin, self.b_start, self.b_stop):
            row.addWidget(b)
        row.addSpacing(24)
        for b in (self.b_stand, self.b_cuff, self.b_mark):
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)
        self.l_counts = W.QLabel()
        self.l_counts.setStyleSheet("font-family: monospace; color: #868e96;")
        root.addWidget(self.l_counts)

        self._shortcut("Space", self._toggle_recording)
        self._shortcut("S", lambda: self.acq.mark("stand"))
        self._shortcut("C", lambda: self.acq.mark("cuff"))
        self._shortcut("M", lambda: self.acq.mark("mark"))

        if session_id:
            self._load_session(session_id)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.update_view)
        self.timer.start(FRAME_MS)

    # ---------------------------------------------------------------- widgets
    def _button(self, text: str, color: str | None, slot) -> W.QPushButton:
        b = W.QPushButton(text)
        b.setMinimumSize(110, 38)
        b.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)  # keep Space for the shortcut
        if color:
            b.setStyleSheet(
                f"QPushButton {{ background: {color}; color: white; font-weight: 600;"
                " border-radius: 6px; padding: 6px 14px; }"
                " QPushButton:disabled { background: #495057; color: #868e96; }"
            )
        b.clicked.connect(lambda _checked=False: slot())
        return b

    def _shortcut(self, key: str, slot) -> None:
        QtGui.QShortcut(QtGui.QKeySequence(key), self, activated=slot)

    # ----------------------------------------------------------------- events
    def _load_session(self, sid: str) -> None:
        """A check-in already saved by the app: BP is asked for after the protocol."""
        path = session_path(self.acq.outdir, sid)
        if path.exists():
            self.session_checkin = json.loads(path.read_text())
        self.session_id = sid
        self.l_session.setText(f"session {sid}")

    def _toggle_recording(self) -> None:
        if self.acq.state == "recording":
            self.acq.stop_recording()
        else:
            self._on_start()

    def _on_start(self) -> None:
        if not self.acq.can_start():
            return
        meta = ask_metadata(self.acq.get_metadata(), parent=self)
        if meta is not None:
            self.acq.start_recording({**meta, "session_id": self.session_id})

    def _on_checkin(self) -> None:
        """Ratings are saved here, before any cuff number is seen. BP follows the recording."""
        if self.acq.state == "recording":
            return
        checkin = ask_checkin(self.acq.get_metadata().get("subject", ""), parent=self)
        if checkin is None:
            return
        save_session(self.acq.outdir, checkin)
        self.session_id, self.session_checkin = checkin["session_id"], checkin
        self.l_session.setText(f"session {self.session_id}")
        self.acq.set_defaults(
            subject=checkin["subject"],
            when={"AM": "morning", "PM": "evening"}[checkin["slot"]],
            test=STAND_PROTOCOL,
            posture="sitting",
        )

    def _ask_bp_after_protocol(self) -> None:
        if self.session_checkin is None:
            return
        bp = ask_bp(parent=self)
        if bp is not None:
            self.session_checkin = {**self.session_checkin, **bp}
            save_session(self.acq.outdir, self.session_checkin)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 (Qt name)
        if self.acq.state == "recording":
            answer = W.QMessageBox.question(
                self, "Recording in progress", "Stop the recording and close?"
            )
            if answer != W.QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.timer.stop()
        self.acq.shutdown()
        event.accept()

    # ---------------------------------------------------------------- drawing
    def update_view(self) -> None:
        snap = self.acq.snapshot()
        if snap["epoch"] != self._epoch:  # reconnected: fresh filters and buffers
            self._epoch = snap["epoch"]
            self.ecg_filt.reset()
            self.ppg_filt.reset()
            for tr in (self.ecg, self.ppg, self.raw):
                tr.reset()

        ecg, ppg = self.acq.drain()
        if ecg:
            self.ecg.extend(self.ecg_filt(ecg))
        if ppg:
            self.ppg.extend(self.ppg_filt(ppg))
            self.raw.extend(ppg)
        if ecg or ppg:
            for tr in (self.ecg, self.ppg, self.raw):
                tr.redraw()

        head, color, sensors, counts = status_text(snap)
        self.l_head.setText(head)
        self.l_head.setStyleSheet(f"font-size: 22px; font-weight: 600; color: {COLORS[color]};")
        self.l_sensors.setText(sensors)
        self.l_counts.setText(counts)

        recording = snap["recording"]
        self.b_start.setEnabled(not recording and self.acq.can_start())
        self.b_stop.setEnabled(recording)
        self.b_checkin.setEnabled(not recording)
        for b in (self.b_stand, self.b_cuff, self.b_mark):
            b.setEnabled(recording)

        ended = self._protocol_running and not snap["stand_protocol"]
        self._protocol_running = snap["stand_protocol"]
        if ended:
            self._ask_bp_after_protocol()


def run(acq: Acquisition, session_id: str | None = None) -> int:
    app = W.QApplication.instance() or W.QApplication([])
    app.setStyle("Fusion")
    palette = QtGui.QPalette()
    dark, text = QtGui.QColor("#1e2026"), QtGui.QColor("#e9ecef")
    for role, c in (
        (QtGui.QPalette.ColorRole.Window, dark),
        (QtGui.QPalette.ColorRole.Base, QtGui.QColor("#16181d")),
        (QtGui.QPalette.ColorRole.AlternateBase, dark),
        (QtGui.QPalette.ColorRole.Button, QtGui.QColor("#343a40")),
        (QtGui.QPalette.ColorRole.WindowText, text),
        (QtGui.QPalette.ColorRole.Text, text),
        (QtGui.QPalette.ColorRole.ButtonText, text),
        (QtGui.QPalette.ColorRole.Highlight, QtGui.QColor("#1971c2")),
    ):
        palette.setColor(role, c)
    app.setPalette(palette)
    win = LiveViewer(acq, session_id)
    win.show()
    return app.exec()
