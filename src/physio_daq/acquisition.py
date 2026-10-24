"""Background thread: reads the serial port and routes samples.

The Nano streams continuously. Every sample goes to the live view; while a
Session exists it is also written to disk (preview vs recording).
"""

import threading
import time
from collections import deque
from pathlib import Path

import serial

from .config import BAUD, DEFAULT_DURATION_S, DEFAULT_OUTDIR, PPG_STALE_S
from .protocol import EcgSample, FrameParser, PpgSample
from .session import Session

_MAX_PENDING = 20_000  # cap on undrained display samples (e.g. no viewer attached)


class Acquisition(threading.Thread):
    def __init__(
        self,
        port: str,
        baud: int = BAUD,
        outdir: str | Path = DEFAULT_OUTDIR,
        subject: str = "owner",
        notes: str = "",
        duration_s: float = DEFAULT_DURATION_S,
    ) -> None:
        super().__init__(daemon=True)
        self.port, self.baud = port, baud
        self.outdir = Path(outdir)
        self.duration_s = duration_s
        self.metadata: dict = {"subject": subject, "notes": notes}

        self.lock = threading.RLock()
        self.alive = True
        self.ser: serial.Serial | None = None
        self.connected = False
        self.epoch = 0  # increments on every (re)connect
        self.parser = FrameParser()
        self.last_rx: float | None = None
        self.last_ppg_rx: float | None = None
        self.int_mode: bool | None = None  # PPG interrupt working? (from '# READY')
        self.firmware_info = ""
        self.messages: deque[str] = deque(maxlen=6)

        self.session: Session | None = None
        self._crc_at_start = 0
        self.new_ecg: list[int] = []
        self.new_ppg: list[int] = []
        self.lo = (False, False)
        self.last_ir = 0

    @property
    def state(self) -> str:
        return "recording" if self.session is not None else "preview"

    # ---------------------------------------------------------- serial thread
    def run(self) -> None:
        while self.alive:
            if self.ser is None:
                if not self._open():
                    time.sleep(1.0)
                continue
            try:
                data = self.ser.read(max(1, self.ser.in_waiting))
            except (serial.SerialException, OSError):
                self._on_disconnect()
                continue
            samples, msgs = self.parser.feed(data)
            for m in msgs:
                self._on_message(m)
            for s in samples:
                if isinstance(s, EcgSample):
                    self._on_ecg(s)
                else:
                    self._on_ppg(s)

    def _open(self) -> bool:
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.05)
        except serial.SerialException as e:
            self.messages.append(f"cannot open {self.port}: {e}")
            self.ser = None
            return False
        # Opening the port resets the Nano; its boot noise is absorbed by the
        # resynchronising parser, so no sleep or flush is needed.
        self.parser = FrameParser()
        self.last_rx = self.last_ppg_rx = None
        self.epoch += 1
        self.connected = True
        self.messages.append(f"connected to {self.port}")
        return True

    def _on_disconnect(self) -> None:
        with self.lock:
            self.connected = False
            self._end_session("serial disconnected")
        try:
            self.ser.close()
        except Exception:
            pass
        self.ser = None
        self.messages.append("DISCONNECTED - retrying every second")

    def _send(self, byte: bytes) -> None:
        try:
            self.ser.write(byte)
        except (serial.SerialException, OSError, AttributeError):
            pass

    # --------------------------------------------------------------- routing
    def _on_message(self, m: str) -> None:
        self.messages.append(m)
        with self.lock:
            if m.startswith("# READY"):
                self.firmware_info = m
                self.int_mode = "int=1" in m
                self._end_session("device reset")  # board rebooted mid-recording
            elif m.startswith("# BTN"):
                self._end_session("stopped (button)")

    def _on_ecg(self, s: EcgSample) -> None:
        with self.lock:
            self.last_rx = time.time()
            self.new_ecg.append(s.ecg)
            if len(self.new_ecg) > _MAX_PENDING:
                del self.new_ecg[: _MAX_PENDING // 2]
            self.lo = (s.lo_plus, s.lo_minus)
            if self.session is not None and self.session.add_ecg(s):
                self._end_session(f"auto-stop ({self.duration_s:g} s)")

    def _on_ppg(self, s: PpgSample) -> None:
        with self.lock:
            self.last_rx = self.last_ppg_rx = time.time()
            self.new_ppg.append(s.ir)
            if len(self.new_ppg) > _MAX_PENDING:
                del self.new_ppg[: _MAX_PENDING // 2]
            self.last_ir = s.ir
            if self.session is not None:
                self.session.add_ppg(s)

    def _end_session(self, reason: str) -> None:
        """Close the current recording, if any (lock must be held)."""
        if self.session is None:
            return
        session, self.session = self.session, None
        crc = self.parser.crc_errors - self._crc_at_start
        try:
            path = session.close(reason, self.firmware_info, crc)
            self.messages.append(
                f"saved {path.stem} (ECG {session.ecg.n}, PPG {session.ppg.n} rows, "
                f"{session.elapsed_s:.1f} s)"
            )
        except OSError as e:
            self.messages.append(f"ERROR saving recording: {e}")
        self._send(b"r")  # LED off

    def _data_flowing(self) -> bool:
        return self.connected and self.last_rx is not None and time.time() - self.last_rx < 1.0

    # ------------------------------------------------------------ public API
    def get_metadata(self) -> dict:
        with self.lock:
            return dict(self.metadata)

    def can_start(self) -> bool:
        with self.lock:
            return self.session is None and self._data_flowing()

    def start_recording(self, metadata: dict) -> bool:
        with self.lock:
            if not self.can_start():
                return False
            self.metadata = dict(metadata)
            self.session = Session(self.outdir, self.metadata, self.duration_s)
            self._crc_at_start = self.parser.crc_errors
            self._send(b"R")  # LED on
            return True

    def stop_recording(self) -> None:
        with self.lock:
            self._end_session("stopped")

    def drain(self) -> tuple[list[int], list[int]]:
        """Return and clear the samples received since the last call."""
        with self.lock:
            e, p = self.new_ecg, self.new_ppg
            self.new_ecg, self.new_ppg = [], []
        return e, p

    def snapshot(self) -> dict:
        """Status values for the live view."""
        with self.lock:
            now = time.time()
            s = self.session
            ppg_age = None if self.last_ppg_rx is None else now - self.last_ppg_rx
            return {
                "connected": self.connected,
                "recording": s is not None,
                "epoch": self.epoch,
                "age": None if self.last_rx is None else now - self.last_rx,
                "ppg_stale": ppg_age is None or ppg_age > PPG_STALE_S,
                "int_mode": self.int_mode,
                "elapsed": s.elapsed_s if s else 0.0,
                "e_n": s.ecg.n if s else 0,
                "p_n": s.ppg.n if s else 0,
                "gaps": (s.ecg.gaps + s.ppg.gaps) if s else 0,
                "polled": s.ppg_polled if s else 0,
                "crc": self.parser.crc_errors - self._crc_at_start,
                "lo": self.lo,
                "last_ir": self.last_ir,
                "msg": self.messages[-1] if self.messages else "",
            }

    def shutdown(self) -> None:
        self.stop_recording()
        self.alive = False
        time.sleep(0.15)
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
