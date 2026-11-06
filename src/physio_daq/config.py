"""Shared settings. ECG_FS and PPG_FS must match firmware/main.cpp."""

BAUD = 250000
ECG_FS = 500.0  # Hz, firmware ECG tick rate
PPG_FS = 400.0  # Hz, nominal MAX30102 sample rate

DEFAULT_OUTDIR = "recordings"
DEFAULT_DURATION_S = 300.0  # auto-stop; 0 disables

# Core session recording: seated rest, then active stand, in one file so the
# transition is kept. BP reading 1 is taken just before the recording and
# reading 2 during the stand. D4 marks the stand, then the cuff reading.
STAND_PROTOCOL = "seated rest + active stand"
STAND_AT_S = 420.0
CUFF_AFTER_STAND_S = 60.0
STAND_PROTOCOL_DURATION_S = 540.0

# Live view
WINDOW_SEC = 10.0  # seconds shown on screen
MAINS_HZ = 50.0  # notch frequency (use 60 in the Americas)
PPG_STALE_S = 0.1  # no PPG packet for this long -> "PPG NO DATA"
NO_FINGER_IR = 5000  # raw IR below this -> "no finger"
