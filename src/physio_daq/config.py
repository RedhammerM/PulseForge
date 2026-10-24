"""Shared settings. ECG_FS and PPG_FS must match firmware/main.cpp."""

BAUD = 250000
ECG_FS = 500.0  # Hz, firmware ECG tick rate
PPG_FS = 400.0  # Hz, nominal MAX30102 sample rate

DEFAULT_OUTDIR = "recordings"
DEFAULT_DURATION_S = 300.0  # auto-stop; 0 disables

# Live view
WINDOW_SEC = 10.0  # seconds shown on screen
MAINS_HZ = 50.0  # notch frequency (use 60 in the Americas)
PPG_STALE_S = 0.1  # no PPG packet for this long -> "PPG NO DATA"
NO_FINGER_IR = 5000  # raw IR below this -> "no finger"
