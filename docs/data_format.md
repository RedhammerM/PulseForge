# Data format

## Timing

Both signals use the Nano's `micros()` clock (uint32, wraps every 71.6 min;
the PC unwraps it). In the CSVs `t_us` is microseconds since the first sample
of the recording.

- **ECG**: sampled by a 500 Hz timer loop. The timestamp is taken immediately
  before the ADC read. I2C transfers are never started when the next ECG tick
  is closer than 0.9 ms, so they do not delay it.
- **PPG**: the MAX30102 pulls its INT pin low for every new sample. An
  interrupt routine stores `micros()` at that moment, so a sample is stamped
  with the time it was produced (error of microseconds) even though it is
  read from the sensor's FIFO later. Reading the FIFO and stamping it at read
  time would add a random 0 to 2 ms error to every PPG sample, which matters
  for pulse arrival time. If the interrupt is not working the firmware falls
  back to polling and marks those rows `polled = 1`.

The sensor's internal clock is not exact, so the PPG rate may differ slightly
from 400 Hz. Use the timestamps, not the nominal rate.

## CSV files

`<subject>_<YYYYmmdd_HHMMSS>_ecg.csv`

| column | meaning |
|---|---|
| `t_us` | device time in microseconds since first sample |
| `ecg` | raw ADC counts (10 bit) |
| `lo_plus`, `lo_minus` | 1 = electrode off (AD8232 leads-off pins) |

`<subject>_<YYYYmmdd_HHMMSS>_ppg.csv`

| column | meaning |
|---|---|
| `t_us` | device time in microseconds (same clock as the ECG file) |
| `ir`, `red` | raw MAX30102 counts (18 bit) |
| `polled` | 1 = timestamp from polling, not from the interrupt (should be 0) |

The files hold the raw signals; filtering in the live view is display only.

## JSON summary

`<subject>_<YYYYmmdd_HHMMSS>.json` contains:

- `schema_version`, `software`, `start`, `end`, `end_reason`
  (`stopped`, `auto-stop (300 s)`, `stopped (button)`, `serial disconnected`,
  `device reset`), `duration_s`, `firmware`, `crc_errors`
- `ecg` and `ppg` blocks: file name, row count, `effective_fs`, inter-sample
  interval mean and standard deviation, gaps (interval above 1.5 periods),
  largest gap, SHA-256. ECG adds `leads_off_fraction`; PPG adds `polled_rows`
  and `isr_events_lost`.
- `metadata`: subject, test, when, posture, last session RPE and duration,
  sleep, caffeine, hours since last meal, notes. Unset fields are `null`.

## Serial protocol

Frames, little endian: `0xA5 0x5A | type | payload | crc8`

| type | payload |
|---|---|
| 1 ECG | `t_us u32, ecg u16, flags u8` (flags: 1 = LO+, 2 = LO-) |
| 2 PPG | `t_us u32, ir u32, red u32, flags u8` (flags: 1 = polled, 2 = interrupt event lost) |

`crc8`: polynomial 0x07, initial value 0, over type and payload. Text lines
starting with `#` appear between frames (`# READY ...` at boot, `# BTN` when
the button is pressed). The PC sends `R` / `r` to switch the Nano's LED on or
off while recording. Baud rate 250000.
