"""One recording: two CSV files (ECG, PPG) plus a JSON summary."""

import csv
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import ECG_FS, PPG_FS
from .protocol import Clock, EcgSample, PpgSample

SCHEMA_VERSION = 1
ECG_GAP_US = 1.5 * 1e6 / ECG_FS
PPG_GAP_US = 1.5 * 1e6 / PPG_FS


@dataclass
class StreamStats:
    """Running timing statistics of one stream (times in microseconds)."""

    gap_us: float  # an interval above this counts as a gap
    n: int = 0
    gaps: int = 0
    max_gap_us: float = 0.0
    first_t: float | None = None
    last_t: float | None = None
    _dt_n: int = 0
    _dt_mean: float = 0.0
    _dt_m2: float = 0.0  # Welford accumulator

    def update(self, t: float) -> None:
        if self.last_t is None:
            self.first_t = t
        else:
            dt = t - self.last_t
            if dt > self.gap_us:
                self.gaps += 1
                self.max_gap_us = max(self.max_gap_us, dt)
            self._dt_n += 1
            delta = dt - self._dt_mean
            self._dt_mean += delta / self._dt_n
            self._dt_m2 += delta * (dt - self._dt_mean)
        self.last_t = t
        self.n += 1

    @property
    def dt_mean_us(self) -> float | None:
        return self._dt_mean if self._dt_n else None

    @property
    def dt_std_us(self) -> float | None:
        return math.sqrt(self._dt_m2 / (self._dt_n - 1)) if self._dt_n > 1 else None

    @property
    def effective_fs(self) -> float | None:
        if self.n < 2 or self.last_t == self.first_t:
            return None
        return (self.n - 1) / ((self.last_t - self.first_t) / 1e6)


def _round(x: float | None, digits: int) -> float | None:
    return None if x is None else round(x, digits)


def _safe(name: str) -> str:
    return re.sub(r"[^\w.-]", "_", name.strip()) or "subject"


class Session:
    """Writes samples to disk while recording. Not thread-safe by itself."""

    def __init__(self, outdir: Path, metadata: dict, duration_s: float) -> None:
        outdir.mkdir(parents=True, exist_ok=True)
        self.outdir = outdir
        self.metadata = dict(metadata)
        self.duration_s = duration_s
        self.start = datetime.now()
        self.stem = f"{_safe(metadata.get('subject') or '')}_{self.start:%Y%m%d_%H%M%S}"

        self._clock = Clock()
        self.ecg = StreamStats(ECG_GAP_US)
        self.ppg = StreamStats(PPG_GAP_US)
        self.lo_rows = 0
        self.ppg_polled = 0
        self.ppg_isr_lost = 0

        self._f_ecg = open(outdir / f"{self.stem}_ecg.csv", "w", newline="")
        self._f_ppg = open(outdir / f"{self.stem}_ppg.csv", "w", newline="")
        self._w_ecg, self._w_ppg = csv.writer(self._f_ecg), csv.writer(self._f_ppg)
        self._w_ecg.writerow(["t_us", "ecg", "lo_plus", "lo_minus"])
        self._w_ppg.writerow(["t_us", "ir", "red", "polled"])
        self._last_flush = time.monotonic()

    @property
    def elapsed_s(self) -> float:
        return (self.ecg.last_t or 0) / 1e6

    def add_ecg(self, s: EcgSample) -> bool:
        """Store one ECG sample. Returns True once the target duration is reached."""
        t = self._clock.rel("ecg", s.t_raw)
        self.ecg.update(t)
        self.lo_rows += s.lo_plus or s.lo_minus
        self._w_ecg.writerow((t, s.ecg, int(s.lo_plus), int(s.lo_minus)))
        self._flush_if_due()
        return bool(self.duration_s) and t >= self.duration_s * 1e6

    def add_ppg(self, s: PpgSample) -> None:
        t = self._clock.rel("ppg", s.t_raw)
        self.ppg.update(t)
        self.ppg_polled += s.polled
        self.ppg_isr_lost += s.isr_lost
        self._w_ppg.writerow((t, s.ir, s.red, int(s.polled)))

    def _flush_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_flush > 1.0:  # survive a crash with <= 1 s lost
            self._f_ecg.flush()
            self._f_ppg.flush()
            self._last_flush = now

    def close(self, reason: str, firmware: str, crc_errors: int) -> Path:
        """Close the files and write the JSON summary. Returns its path."""
        self._f_ecg.close()
        self._f_ppg.close()
        sha = {
            k: hashlib.sha256((self.outdir / f"{self.stem}_{k}.csv").read_bytes()).hexdigest()
            for k in ("ecg", "ppg")
        }
        e, p = self.ecg, self.ppg
        summary = {
            "schema_version": SCHEMA_VERSION,
            "software": f"physio-daq {__version__}",
            "start": self.start.isoformat(timespec="seconds"),
            "end": datetime.now().isoformat(timespec="seconds"),
            "end_reason": reason,
            "target_duration_s": self.duration_s or None,
            "duration_s": round(self.elapsed_s, 3),
            "firmware": firmware,
            "crc_errors": crc_errors,
            "ecg": {
                "file": f"{self.stem}_ecg.csv",
                "nominal_fs": ECG_FS,
                "n_rows": e.n,
                "effective_fs": _round(e.effective_fs, 3),
                "dt_mean_us": _round(e.dt_mean_us, 2),
                "dt_std_us": _round(e.dt_std_us, 2),
                "gaps_gt_1p5_ticks": e.gaps,
                "max_gap_ms": round(e.max_gap_us / 1000, 3),
                "leads_off_fraction": round(self.lo_rows / e.n, 4) if e.n else None,
                "sha256": sha["ecg"],
            },
            "ppg": {
                "file": f"{self.stem}_ppg.csv",
                "nominal_fs": PPG_FS,
                "n_rows": p.n,
                "effective_fs": _round(p.effective_fs, 3),
                "dt_mean_us": _round(p.dt_mean_us, 2),
                "dt_std_us": _round(p.dt_std_us, 2),
                "gaps_gt_1p5_periods": p.gaps,
                "max_gap_ms": round(p.max_gap_us / 1000, 3),
                "polled_rows": self.ppg_polled,
                "isr_events_lost": self.ppg_isr_lost,
                "sha256": sha["ppg"],
            },
            "metadata": self.metadata,
        }
        path = self.outdir / f"{self.stem}.json"
        path.write_text(json.dumps(summary, indent=2))
        return path
