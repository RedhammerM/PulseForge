"""Start the Qt live window as a separate process and watch it."""

import json
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st


@dataclass
class LiveProcess:
    proc: subprocess.Popen | None = None
    log: list[str] = field(default_factory=list)
    returncode: int | None = None
    handled: bool = True  # app already reacted to the last exit

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


@st.cache_resource
def state() -> LiveProcess:
    """One live window per app server, shared across reruns and browser tabs."""
    return LiveProcess()


def list_ports() -> list[tuple[str, str]]:
    from serial.tools import list_ports as lp

    # Built-in /dev/ttyS* ports report "n/a" and are never the board
    return [(p.device, p.description) for p in lp.comports() if p.description != "n/a"]


def launch(port: str, outdir: Path, session_id: str | None, defaults: dict) -> None:
    s = state()
    if s.running:
        return
    cmd = [
        sys.executable,
        "-m",
        "physio_daq",
        "--port",
        port,
        "--outdir",
        str(outdir),
        "--defaults",
        json.dumps(defaults),
    ]
    if defaults.get("subject"):
        cmd += ["--subject", defaults["subject"]]
    if session_id:
        cmd += ["--session-id", session_id]
    s.log = []
    s.returncode = None
    s.handled = False
    s.proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )

    def pump(p: subprocess.Popen) -> None:
        for line in p.stdout:  # type: ignore[union-attr]
            s.log.append(line.rstrip())
            del s.log[:-200]
        s.returncode = p.wait()

    threading.Thread(target=pump, args=(s.proc,), daemon=True).start()
