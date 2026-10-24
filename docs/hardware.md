# Hardware notes

## Parts

- Arduino Nano (ATmega328P, 5 V logic) with soldered headers
- AD8232 ECG breakout with 3.5 mm electrode jack (the RA/LA/RL pins are not used)
- MAX30102 PPG breakout (I2C)

## Wiring

| Signal | From | To |
|---|---|---|
| 3.3 V | Nano 3V3 | MAX30102 VIN, AD8232 3.3V, AD8232 SDN |
| Ground | Nano GND | MAX30102 GND, AD8232 GND |
| ECG signal | AD8232 OUTPUT | A0 |
| Leads-off | AD8232 LO+ / LO- | D10 / D11 |
| I2C | MAX30102 SDA / SCL | A4 / A5 |
| Sample interrupt | MAX30102 INT | D2 |
| Stop button (optional) | D4 | GND (internal pull-up) |

Notes:

- Power both sensors from **3.3 V**. The AD8232 must never see 5 V.
- Keep **SDN tied to 3.3 V**. A Nano pin outputs 5 V when high, which is too
  much for the AD8232.
- INT is an open-drain output and needs a pull-up. Most breakouts have one. If
  the status line reports `POLLED`, check the INT wire first, then add a
  10 kΩ resistor from INT to 3.3 V.
- The Nano's ICSP header is not a mounting aid: it carries live signals.
  Mount the board through its 30 normal pins or on standoffs.

## Optional: use the 3.3 V reference for the ADC

The ADC defaults to a 5 V reference, so the AD8232 output (0 to 3.3 V) uses
about two thirds of the range. Wiring 3.3 V to AREF and setting
`ECG_EXTERNAL_AREF 1` in `firmware/main.cpp` gives about 1.5x finer steps.
Only wire AREF while firmware that calls `analogReference(EXTERNAL)` is
running, otherwise the internal 5 V reference is shorted to the 3.3 V rail.
At the board's roughly 1100x gain an R-wave already spans a couple of
hundred ADC counts, so this is optional.

## Reliability and safety

- **Battery or USB isolator** whenever electrodes are attached. This matters
  most when measuring other people.
- Breadboard contacts are the most common source of flaky data. Solder
  headers, keep the analog wires short, and move to perfboard eventually.
- Add 100 nF across the supply near each sensor, and route the ECG wire away
  from the PPG LED lines.
- Strain on the USB connector causes random resets: secure the cable.
