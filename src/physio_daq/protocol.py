"""Serial protocol: frame parser and wrap-safe device clock.

Frame (little endian): 0xA5 0x5A | type u8 | payload | crc8
  type 1 ECG: t_us u32, ecg u16, flags u8   (1 = LO+, 2 = LO-)
  type 2 PPG: t_us u32, ir u32, red u32, flags u8   (1 = polled, 2 = ISR event lost)
crc8 is CRC-8 (poly 0x07, init 0) over type + payload. Text lines starting
with '#' are interleaved between frames. See docs/data_format.md.
"""

import struct
from typing import NamedTuple

SYNC = b"\xa5\x5a"
TYPE_ECG, TYPE_PPG = 1, 2
_FRAMES = {
    TYPE_ECG: struct.Struct("<IHB"),
    TYPE_PPG: struct.Struct("<IIIB"),
}


class EcgSample(NamedTuple):
    t_raw: int  # device micros(), uint32
    ecg: int  # raw ADC counts
    lo_plus: bool  # electrode off
    lo_minus: bool


class PpgSample(NamedTuple):
    t_raw: int
    ir: int
    red: int
    polled: bool  # timestamp from polling, not from the sensor interrupt
    isr_lost: bool  # the Nano dropped an interrupt event


def _crc8_bitwise(data) -> int:
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


_CRC_TABLE = [_crc8_bitwise([i]) for i in range(256)]


def crc8(data) -> int:
    c = 0
    for b in data:
        c = _CRC_TABLE[c ^ b]
    return c


class FrameParser:
    """Resynchronising parser for binary frames mixed with '#' text lines."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.text = bytearray()
        self.crc_errors = 0

    def _to_text(self, chunk: bytes, messages: list[str]) -> None:
        self.text += chunk
        while b"\n" in self.text:
            line, _, rest = bytes(self.text).partition(b"\n")
            self.text = bytearray(rest)
            s = line.decode("ascii", errors="ignore").strip()
            k = s.rfind("#")  # garbage from a corrupted frame may precede it
            if k >= 0:
                messages.append(s[k:])
        if len(self.text) > 512:
            self.text.clear()

    def feed(self, data: bytes) -> tuple[list, list[str]]:
        """Consume raw bytes; return (EcgSample/PpgSample list, '#' messages)."""
        self.buf += data
        samples: list = []
        messages: list[str] = []
        while True:
            i = self.buf.find(SYNC)
            if i < 0:
                keep = 1 if self.buf.endswith(SYNC[:1]) else 0
                cut = len(self.buf) - keep
                self._to_text(bytes(self.buf[:cut]), messages)
                del self.buf[:cut]
                break
            if i > 0:
                self._to_text(bytes(self.buf[:i]), messages)
                del self.buf[:i]
            if len(self.buf) < 3:
                break
            st = _FRAMES.get(self.buf[2])
            if st is None:  # not a real frame start
                del self.buf[:1]
                continue
            flen = 3 + st.size + 1
            if len(self.buf) < flen:
                break
            body = bytes(self.buf[2 : 3 + st.size])  # type + payload
            if crc8(body) == self.buf[flen - 1]:
                samples.append(_decode(body[0], st.unpack(body[1:])))
                del self.buf[:flen]
            else:
                self.crc_errors += 1
                del self.buf[:1]
        return samples, messages


def _decode(frame_type: int, f: tuple):
    if frame_type == TYPE_ECG:
        t, ecg, flags = f
        return EcgSample(t, ecg, bool(flags & 1), bool(flags & 2))
    t, ir, red, flags = f
    return PpgSample(t, ir, red, bool(flags & 1), bool(flags & 2))


class Clock:
    """Wrap-safe device time shared by both streams.

    The device clock is a uint32 of microseconds (wraps every 71.6 min). Each
    stream is unwrapped by accumulating differences, and both are referenced
    to the first sample seen, so ECG and PPG stay on one time axis.
    """

    def __init__(self) -> None:
        self._origin: int | None = None
        self._last: dict[str, int] = {}
        self._val: dict[str, int] = {}

    def rel(self, stream: str, t_raw: int) -> int:
        if self._origin is None:
            self._origin = t_raw
        if stream not in self._last:
            v = ((t_raw - self._origin + 2**31) & 0xFFFFFFFF) - 2**31
        else:
            v = self._val[stream] + ((t_raw - self._last[stream]) & 0xFFFFFFFF)
        self._last[stream], self._val[stream] = t_raw, v
        return v
