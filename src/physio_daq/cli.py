"""Command line entry point: `physio-daq --port /dev/ttyUSB0`."""

import argparse

from .config import BAUD, DEFAULT_DURATION_S, DEFAULT_OUTDIR


def _list_ports() -> None:
    from serial.tools import list_ports

    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found. Check the cable (data, not charge-only) and driver.")
    for p in ports:
        print(f"{p.device}\t{p.description}\t{p.hwid}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="physio-daq",
        description="Live ECG + PPG acquisition. The plot is always live; "
        "Start opens a metadata form and begins a recording.",
    )
    ap.add_argument("--port", help="serial port, e.g. /dev/ttyUSB0 or COM4")
    ap.add_argument("--list-ports", action="store_true", help="list serial ports and exit")
    ap.add_argument("--baud", type=int, default=BAUD)
    ap.add_argument("--outdir", default=DEFAULT_OUTDIR, help="where recordings are written")
    ap.add_argument("--subject", default="owner", help="default pseudonymous subject ID")
    ap.add_argument("--notes", default="", help="default text for the notes field")
    ap.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION_S,
        help="auto-stop after this many seconds (0 = manual stop only)",
    )
    args = ap.parse_args(argv)

    if args.list_ports:
        _list_ports()
        return 0
    if not args.port:
        ap.error("--port is required (use --list-ports to find it)")

    # Imported late so that --help and --list-ports work without Qt.
    from .acquisition import Acquisition
    from .viewer import LiveViewer

    acq = Acquisition(args.port, args.baud, args.outdir, args.subject, args.notes, args.duration)
    acq.start()
    try:
        LiveViewer(acq).show()
    except KeyboardInterrupt:
        pass
    finally:
        acq.shutdown()
    return 0
