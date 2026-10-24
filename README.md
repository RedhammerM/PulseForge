# physio-daq

Home-built acquisition system for **synchronized ECG and PPG**. An Arduino Nano
reads an AD8232 ECG front end and a MAX30102 PPG sensor on one clock and streams
them to a PC, where a live viewer records sessions with metadata.

It is the data-collection layer of a larger project on HRV, autonomic coupling
and recovery (processing and analysis live elsewhere). The point of recording
both signals together is **beat-to-beat pulse arrival time (PAT)**: ECG R-peak
to PPG pulse foot, which a single-sensor HRV setup cannot provide.

> **Safety.** Electrodes are attached to a body. Power the setup from a battery
> or through a USB isolator, never from a laptop on its charger. This is a
> research/hobby tool, **not a medical device**.

## Features

- ECG at 500 Hz, PPG (red + IR) at 400 Hz, one shared microsecond clock
- PPG samples timestamped by the **sensor interrupt**, not by when they were read
  (see [data format](docs/data_format.md#timing))
- Binary protocol with CRC-8 and automatic resynchronisation; serial dropouts
  reconnect on their own
- Live view is always on; **Start** opens a metadata form and begins a recording
  that stops after 300 s (configurable)
- Every recording: two CSV files + a JSON with metadata, timing quality
  (gaps, jitter, effective sampling rates), and SHA-256 checksums

## Hardware

| Part | Pin | Arduino Nano |
|---|---|---|
| MAX30102 | VIN / GND | 3V3 / GND |
|  | SDA / SCL | A4 / A5 |
|  | INT | D2 |
| AD8232 | 3.3V / GND | 3V3 / GND |
|  | OUTPUT | A0 |
|  | LO+ / LO- | D10 / D11 |
|  | SDN | 3V3 |
| Button (optional) | D4 to GND | stops a recording |

Details, notes and safety: [docs/hardware.md](docs/hardware.md).

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and [PlatformIO](https://platformio.org/).

```bash
# 1. Firmware (from the repository root)
pio run -t upload

# 2. Software
uv sync
uv run physio-daq --list-ports
uv run physio-daq --port /dev/ttyUSB0 --subject S01     # COM4 on Windows
```

The status line should show `PPG time: interrupt`. Press **Start**, fill in the
form, and the recording runs until the countdown ends or you press **Stop**.

Useful options: `--duration 0` (no auto-stop), `--outdir`, `--notes`.
On Linux add yourself to the `dialout` group for serial access.

## Output

```
recordings/S01_20261023_103539_ecg.csv    t_us, ecg, lo_plus, lo_minus
recordings/S01_20261023_103539_ppg.csv    t_us, ir, red, polled
recordings/S01_20261023_103539.json       metadata + quality summary
```

Both CSVs use the same clock (`t_us`). Column and JSON reference:
[docs/data_format.md](docs/data_format.md). Recordings are personal health data:
`recordings/` is git-ignored, use pseudonymous subject IDs.

## Repository layout

```
firmware/main.cpp        Nano firmware (PlatformIO, src_dir = firmware)
src/physio_daq/
  protocol.py            frame parser, CRC, wrap-safe clock
  session.py             one recording: CSV writers, timing stats, JSON
  acquisition.py         serial thread, preview vs recording state
  metadata_form.py       Qt form shown on Start
  viewer.py              live plot window
  cli.py                 command line entry point
docs/                    hardware notes and data format
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `WAITING FOR DATA FROM DEVICE` | wrong baud/old firmware on the board, or port held by another program (close the PlatformIO serial monitor) |
| `PPG time: POLLED` | INT wire missing or loose; recordings still work but PPG timestamps are imprecise |
| `ECG LEADS OFF` | electrode or cable not making contact |
| no port listed | charge-only USB cable, or missing CH340 driver |

## License

MIT, see [LICENSE](LICENSE).
