/*
 * ECG (AD8232) + PPG (MAX30102) acquisition - Arduino Nano (ATmega328P).
 *
 * Wiring (Nano, 5 V board):
 *   MAX30102: VIN -> 3V3, GND -> GND, SDA -> A4, SCL -> A5, INT -> D2
 *   AD8232:   3.3V -> 3V3, GND -> GND, OUTPUT -> A0, LO+ -> D10, LO- -> D11,
 *             SDN -> 3V3
 *   Optional: pushbutton between D4 and GND (stops a recording in the PC app)
 *
 * Design:
 *   - ECG: timer-driven 500 Hz. Timestamp = micros() just before analogRead.
 *   - PPG: MAX30102 runs at 400 Hz and pulls INT low for every new sample.
 *     An interrupt routine stores micros() at that instant, so each PPG
 *     sample gets its true time (error of a few microseconds) on the SAME
 *     clock as the ECG. The FIFO is read later in the main loop and every
 *     sample is paired, in order, with its interrupt timestamp.
 *   - I2C reads are only started when the next ECG tick is > 0.9 ms away,
 *     so they never delay an ECG sample.
 *   - If INT is not working (loose wire), the firmware falls back to polling
 *     and marks every PPG sample as POLLED (imprecise timestamp).
 *
 * Frame (little endian):
 *   0xA5 0x5A | type u8 | payload | crc8      (crc8: poly 0x07, init 0, over
 *                                              type + payload)
 *   type 1 ECG: t_us u32, ecg u16, flags u8       (flags: 1=LO+, 2=LO-)
 *   type 2 PPG: t_us u32, ir u32, red u32, flags u8
 *                                      (flags: 1=POLLED, 2=ISR event lost)
 *
 * PC -> Nano (1 byte): 'R' = LED on (recording), 'r' = LED off.
 * Text lines starting with '#' are sent between frames:
 *   "# READY ..." once at boot, "# BTN" when the button is pressed.
 */

#include <Arduino.h>
#include <Wire.h>
#include "MAX30105.h"

// ---------------- Configuration ----------------
const uint8_t PIN_ECG = A0;
const uint8_t PIN_LO_PLUS = 10;
const uint8_t PIN_LO_MINUS = 11;
const uint8_t PIN_PPG_INT = 2;  // INT0
const uint8_t PIN_BUTTON = 4;

#define ECG_EXTERNAL_AREF 0  // 1 only if the 3.3 V rail is wired to AREF!

const uint32_t SERIAL_BAUD = 250000;
const uint32_t ECG_FS_HZ = 500;
const uint32_t TICK_US = 1000000UL / ECG_FS_HZ;

const byte PPG_LED_BRIGHTNESS = 60;
const byte PPG_SAMPLE_AVERAGE = 1;
const byte PPG_LED_MODE = 2;  // Red + IR
const int PPG_SAMPLE_RATE = 400;
const int PPG_PULSE_WIDTH = 411;
const int PPG_ADC_RANGE = 4096;

const uint32_t PPG_GUARD_US = 900;       // no I2C if ECG tick is closer
const uint32_t PPG_MIN_EVENT_US = 1500;  // ignore INT glitches (period 2500)
const uint32_t PPG_STALE_EVENT_US = 50000;
const uint32_t PPG_WATCHDOG_US = 10000;  // interrupt mode: poll anyway
const uint32_t PPG_POLL_US = 1000;       // polling fallback

// ---------------- Protocol ----------------
const uint8_t SYNC0 = 0xA5;
const uint8_t SYNC1 = 0x5A;
const uint8_t TYPE_ECG = 1;
const uint8_t TYPE_PPG = 2;

const uint8_t ECG_LO_PLUS = 1;
const uint8_t ECG_LO_MINUS = 2;
const uint8_t PPG_POLLED = 1;
const uint8_t PPG_ISR_LOST = 2;

struct __attribute__((packed)) EcgFrame {
  uint32_t t_us;
  uint16_t ecg;
  uint8_t flags;
};
struct __attribute__((packed)) PpgFrame {
  uint32_t t_us;
  uint32_t ir;
  uint32_t red;
  uint8_t flags;
};

// ---------------- State ----------------
MAX30105 sensor;
bool ppgPresent = false;
bool intMode = false;
uint32_t nextTick = 0;
uint32_t lastPpgService = 0;

// Interrupt event queue (single producer = ISR, single consumer = loop)
const uint8_t QMASK = 7;
volatile uint32_t isrTimes[QMASK + 1];
volatile uint8_t isrHead = 0;
volatile uint8_t isrTail = 0;
volatile bool isrOverflow = false;
volatile uint32_t isrLast = 0;

void ppgIsr() {
  uint32_t t = micros();
  if (t - isrLast < PPG_MIN_EVENT_US) return;
  uint8_t next = (isrHead + 1) & QMASK;
  if (next == isrTail) {
    isrOverflow = true;
    return;
  }
  isrLast = t;
  isrTimes[isrHead] = t;
  isrHead = next;
}

uint8_t crc8(const uint8_t *d, uint8_t n) {
  uint8_t c = 0;
  while (n--) {
    c ^= *d++;
    for (uint8_t i = 0; i < 8; i++) {
      c = (c & 0x80) ? (uint8_t)((c << 1) ^ 0x07) : (uint8_t)(c << 1);
    }
  }
  return c;
}

template <typename T>
void sendFrame(uint8_t type, const T &payload) {
  uint8_t f[3 + sizeof(T) + 1];
  f[0] = SYNC0;
  f[1] = SYNC1;
  f[2] = type;
  memcpy(&f[3], &payload, sizeof(T));
  f[3 + sizeof(T)] = crc8(&f[2], 1 + sizeof(T));
  Serial.write(f, sizeof(f));
}

void pollCommands() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'R') digitalWrite(LED_BUILTIN, HIGH);
    else if (c == 'r') digitalWrite(LED_BUILTIN, LOW);
  }
}

void pollButton() {
  static bool last = HIGH;
  static uint32_t lastChange = 0;
  bool s = digitalRead(PIN_BUTTON);
  if (s != last && millis() - lastChange > 30) {
    lastChange = millis();
    last = s;
    if (s == LOW) Serial.println(F("# BTN"));
  }
}

// Read everything in the sensor FIFO and send one PPG frame per sample.
void servicePpg() {
  uint32_t now = micros();
  bool pending = (isrHead != isrTail);
  uint32_t interval = intMode ? PPG_WATCHDOG_US : PPG_POLL_US;
  if (!pending && (now - lastPpgService) < interval) return;
  lastPpgService = now;

  sensor.getINT1();  // reading the status register releases INT
  sensor.check();    // I2C burst read of the FIFO

  while (sensor.available()) {
    uint32_t t;
    uint8_t flags = 0;
    // Drop interrupt events that are too old to belong to a sample.
    while (isrHead != isrTail &&
           (uint32_t)(micros() - isrTimes[isrTail]) > PPG_STALE_EVENT_US) {
      isrTail = (isrTail + 1) & QMASK;
    }
    if (intMode && isrHead != isrTail) {
      t = isrTimes[isrTail];
      isrTail = (isrTail + 1) & QMASK;
    } else {
      t = micros();
      flags |= PPG_POLLED;
    }
    if (isrOverflow) {
      isrOverflow = false;
      flags |= PPG_ISR_LOST;
    }
    PpgFrame f = {t, sensor.getFIFOIR(), sensor.getFIFORed(), flags};
    sensor.nextSample();
    sendFrame(TYPE_PPG, f);
  }
}

void ecgTick() {
  nextTick += TICK_US;
  uint32_t t = micros();
  // Fell far behind (should not happen): resync instead of bursting.
  if ((int32_t)(t - nextTick) > (int32_t)(4 * TICK_US)) nextTick = t + TICK_US;

  uint16_t ecg = analogRead(PIN_ECG);
  uint8_t flags = 0;
  if (digitalRead(PIN_LO_PLUS)) flags |= ECG_LO_PLUS;
  if (digitalRead(PIN_LO_MINUS)) flags |= ECG_LO_MINUS;
  EcgFrame f = {t, ecg, flags};
  sendFrame(TYPE_ECG, f);
}

// Does INT really fire? Wait for a few events; otherwise fall back to polling.
bool selfTestInterrupt() {
  sensor.getINT1();  // clear power-on flag so INT can go high
  uint8_t seen = 0;
  uint32_t t0 = millis();
  while (millis() - t0 < 300 && seen < 5) {
    if (isrHead != isrTail) {
      isrTail = (isrTail + 1) & QMASK;
      sensor.getINT1();
      sensor.check();
      while (sensor.available()) sensor.nextSample();
      seen++;
    }
  }
  noInterrupts();
  isrTail = isrHead;
  isrOverflow = false;
  interrupts();
  return seen >= 5;
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  pinMode(LED_BUILTIN, OUTPUT);
  pinMode(PIN_LO_PLUS, INPUT);
  pinMode(PIN_LO_MINUS, INPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_PPG_INT, INPUT);  // breakout pulls INT up (open-drain output)

#if ECG_EXTERNAL_AREF
  analogReference(EXTERNAL);
#endif

  Wire.begin();
#ifdef WIRE_HAS_TIMEOUT
  // A hung I2C bus (loose wire) must not freeze the ECG loop.
  Wire.setWireTimeout(3000, true);
#endif

  ppgPresent = sensor.begin(Wire, I2C_SPEED_FAST);
  if (ppgPresent) {
    sensor.setup(PPG_LED_BRIGHTNESS, PPG_SAMPLE_AVERAGE, PPG_LED_MODE,
                 PPG_SAMPLE_RATE, PPG_PULSE_WIDTH, PPG_ADC_RANGE);
    sensor.enableDATARDY();  // INT low on every new sample
    attachInterrupt(digitalPinToInterrupt(PIN_PPG_INT), ppgIsr, FALLING);
    intMode = selfTestInterrupt();
    if (!intMode) detachInterrupt(digitalPinToInterrupt(PIN_PPG_INT));
  }

  Serial.print(F("# READY fw=3 fmt=2 ecg_fs="));
  Serial.print(ECG_FS_HZ);
  Serial.print(F(" ppg_fs="));
  Serial.print(PPG_SAMPLE_RATE);
  Serial.print(F(" ppg="));
  Serial.print(ppgPresent ? 1 : 0);
  Serial.print(F(" int="));
  Serial.println(intMode ? 1 : 0);

  lastPpgService = micros();
  nextTick = micros() + TICK_US;
}

void loop() {
  pollCommands();
  pollButton();

  int32_t untilTick = (int32_t)(nextTick - micros());
  if (untilTick <= 0) {
    ecgTick();
  } else if (ppgPresent && untilTick > (int32_t)PPG_GUARD_US) {
    servicePpg();
  }
}
