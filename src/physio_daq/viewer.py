"""Live plot window with Start/Stop buttons (matplotlib, Qt backend)."""

from collections import deque

import matplotlib

matplotlib.use("QtAgg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.widgets import Button  # noqa: E402
from scipy.signal import butter, iirnotch, sosfilt, sosfilt_zi, tf2sos  # noqa: E402

from .acquisition import Acquisition  # noqa: E402
from .config import ECG_FS, MAINS_HZ, NO_FINGER_IR, PPG_FS, WINDOW_SEC  # noqa: E402
from .metadata_form import ask_metadata  # noqa: E402


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


class LiveViewer:
    def __init__(self, acq: Acquisition) -> None:
        self.acq = acq
        self._epoch = 0
        self._n_ecg, self._n_ppg = int(WINDOW_SEC * ECG_FS), int(WINDOW_SEC * PPG_FS)
        self.ecg_buf = deque([0.0] * self._n_ecg, maxlen=self._n_ecg)
        self.ppg_buf = deque([0.0] * self._n_ppg, maxlen=self._n_ppg)
        self.raw_buf = deque([0.0] * self._n_ppg, maxlen=self._n_ppg)
        self.ecg_filt, self.ppg_filt = ecg_filter(), ppg_filter()

        self.fig, (ax_e, ax_p, ax_r) = plt.subplots(3, 1, figsize=(12, 8))
        self.axes = (ax_e, ax_p, ax_r)
        self.fig.subplots_adjust(left=0.07, right=0.98, top=0.94, bottom=0.2, hspace=0.5)
        t_ecg = np.arange(self._n_ecg) / ECG_FS
        t_ppg = np.arange(self._n_ppg) / PPG_FS
        (self.l_ecg,) = ax_e.plot(t_ecg, np.zeros(self._n_ecg), lw=0.8, color="tab:green")
        (self.l_ppg,) = ax_p.plot(t_ppg, np.zeros(self._n_ppg), lw=0.9, color="tab:red")
        (self.l_raw,) = ax_r.plot(t_ppg, np.zeros(self._n_ppg), lw=0.9)
        ax_e.set_title(f"ECG (0.5-40 Hz + {MAINS_HZ:g} Hz notch, display only)")
        ax_p.set_title("PPG IR (0.5-8 Hz)")
        ax_r.set_title("PPG IR raw")
        ax_r.set_xlabel("time [s]")
        for ax in self.axes:
            ax.set_xlim(0, WINDOW_SEC)

        self.b_start = Button(
            self.fig.add_axes([0.07, 0.03, 0.11, 0.07]), "Start",
            color="#b7e4c7", hovercolor="#74c69d",
        )  # fmt: skip
        self.b_stop = Button(
            self.fig.add_axes([0.20, 0.03, 0.11, 0.07]), "Stop",
            color="#ffb3b3", hovercolor="#ff8080",
        )  # fmt: skip
        self.b_start.on_clicked(self._on_start)
        self.b_stop.on_clicked(lambda _e: self.acq.stop_recording())
        kw = {"family": "monospace", "fontsize": 9, "va": "center"}
        self.t1 = self.fig.text(0.34, 0.095, "", **kw)
        self.t2 = self.fig.text(0.34, 0.060, "", **kw)
        self.t3 = self.fig.text(0.34, 0.025, "", **kw)

        self.timer = self.fig.canvas.new_timer(interval=50)
        self.timer.add_callback(self.update)
        self.fig.canvas.mpl_connect("close_event", lambda _e: self.acq.shutdown())

    def show(self) -> None:
        self.timer.start()
        plt.show()

    # ----------------------------------------------------------------- events
    def _on_start(self, _event) -> None:
        if not self.acq.can_start():
            return
        meta = ask_metadata(self.acq.get_metadata())
        if meta is not None:
            self.acq.start_recording(meta)

    # --------------------------------------------------------------- drawing
    @staticmethod
    def _fit(ax, buf) -> None:
        a = np.fromiter(buf, float, len(buf))
        lo, hi = np.percentile(a, [0.5, 99.5])
        pad = 0.2 * (hi - lo) + 1e-6
        ax.set_ylim(lo - pad, hi + pad)

    def _reset_buffers(self) -> None:
        self.ecg_filt.reset()
        self.ppg_filt.reset()
        for buf, n in ((self.ecg_buf, self._n_ecg), (self.ppg_buf, self._n_ppg),
                       (self.raw_buf, self._n_ppg)):  # fmt: skip
            buf.extend([0.0] * n)

    def _status_text(self, snap: dict) -> tuple[str, str, str, str]:
        no_data = snap["connected"] and (snap["age"] is None or snap["age"] > 1.0)
        if not snap["connected"]:
            head, color = "NOT CONNECTED", "tab:red"
        elif no_data:
            head, color = "WAITING FOR DATA FROM DEVICE...", "tab:red"
        elif snap["recording"]:
            target = f" / {_clock(self.acq.duration_s)}" if self.acq.duration_s else ""
            head, color = f"REC {_clock(snap['elapsed'])}{target}", "tab:green"
        else:
            head, color = "PREVIEW (not recording) - press Start", "gray"

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
            color = "tab:red"
        line3 = (
            f"ecg={snap['e_n']} ppg={snap['p_n']} gaps={snap['gaps']} "
            f"crc={snap['crc']} | {snap['msg']}"
        )
        return head, color, f"{ecg_txt}   {ppg_txt}   {timing}", line3

    def update(self) -> None:
        snap = self.acq.snapshot()
        if snap["epoch"] != self._epoch:  # reconnected: fresh filters and buffers
            self._epoch = snap["epoch"]
            self._reset_buffers()

        ecg, ppg = self.acq.drain()
        if ecg:
            self.ecg_buf.extend(self.ecg_filt(ecg))
        if ppg:
            self.ppg_buf.extend(self.ppg_filt(ppg))
            self.raw_buf.extend(ppg)
        if ecg or ppg:
            self.l_ecg.set_ydata(self.ecg_buf)
            self.l_ppg.set_ydata(self.ppg_buf)
            self.l_raw.set_ydata(self.raw_buf)
            for ax, buf in zip(self.axes, (self.ecg_buf, self.ppg_buf, self.raw_buf), strict=True):
                self._fit(ax, buf)

        head, color, line2, line3 = self._status_text(snap)
        self.t1.set_text(head)
        self.t1.set_color(color)
        self.t2.set_text(line2)
        self.t3.set_text(line3)
        self.fig.canvas.draw_idle()
