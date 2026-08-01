# KICKR RUN — observed BLE protocol

Notes from a read-only GATT dump (`explore.html`) of a KICKR RUN 6D7C.
Firmware `2.0.43`, hardware rev `7`, manufacturer `Wahoo Fitness`.

Confidence is marked per field. Nothing here was produced by writing to the
machine — it is all read and notify traffic.

## Services

| UUID | What |
|---|---|
| `0x180A` | Device Information |
| `0x1814` | Running Speed and Cadence |
| `0x1826` | Fitness Machine (FTMS) |
| `0xFE59` | Nordic DFU — **firmware flashing, do not write** |
| `a026ee01` | Wahoo proprietary |
| `a026ee06` | Wahoo proprietary |
| `a026ee0e` | Wahoo proprietary |

Wahoo's 128-bit base is `-0a7d-4ab3-97fa-f1500f9feb8b`.

## FTMS (0x1826)

| Characteristic | Properties | Notes |
|---|---|---|
| `0x2ACC` Fitness Machine Feature | read | `0C 00 00 00 02 00 00 00` |
| `0x2ACD` Treadmill Data | notify | 11 bytes, see below |
| `0x2AD5` Supported Inclination Range | read | `E2 FF 96 00 01 00` → −3.0%…+15.0%, step 0.1% |
| `0x2AD9` Control Point | write, indicate | |
| `0x2ADA` Machine Status | notify | |

**`0x2AD4` (Supported Speed Range) is absent.** Speed target setting was never
implemented, rather than being disabled.

Feature flags decode as:
- machine features `0x0000000C` — total distance (bit 2), inclination (bit 3)
- target setting `0x00000002` — inclination only (bit 1). **Speed (bit 0) clear.**

Confirmed empirically: `02 E3 01` (set speed 4.83 km/h) → `80 02 02`, op code
not supported. Same for a second value, so it is the opcode and not the range.
Incline `03 0A 00` → `80 03 04` operation failed, but that was with the belt
stopped and is probably a state condition rather than a capability limit —
untested with the belt running.

`0x2ACD` layout as observed (flags `0x000C`):

```
0C 00        flags: total distance + inclination present
F1 00        instantaneous speed, uint16 LE, 0.01 km/h   → 2.41 km/h
17 00 00     total distance, uint24 LE, metres
00 00        inclination, int16 LE, 0.1 %
FF 7F        ramp angle, int16 LE — 0x7FFF = not available
```

## RSC (0x1814)

`0x2A53` Measurement, standard layout, verified against FTMS:

```
03 AC 00 47 1C 00 EE 00 00 00
03           flags: stride length + total distance present
AC 00        speed, uint16 LE, 1/256 m/s   → 0.672 m/s = 2.42 km/h
47           cadence, uint8                → 71 spm
1C 00        stride length, uint16 LE, cm  → 28 cm
EE 00 00 00  total distance, uint32 LE, 1/10 m → 23.8 m
```

Speed and distance agree with FTMS to within rounding.

## Wahoo proprietary — telemetry (notify only)

### `a026e03d` (service `a026ee0e`)

24-byte record, delivered as a 20-byte notification plus a 4-byte remainder
(ATT MTU 23). Little-endian throughout.

| Offset | Bytes | Meaning | Confidence |
|---|---|---|---|
| 0–1 | `FF 01` | constant header | high |
| 2–5 | uint32 | **belt speed in µm/s** (m/s × 10⁶) | **high** |
| 6–9 | uint32 | counter, +2,870/s while the belt runs, frozen when stopped | medium — rate is speed-independent, so a device timer rather than distance |
| 10–11 | `00 00` | always zero in this capture | — |
| 12–13 | uint16 | noisy per-step value, zero when stopped | low — plausibly deck force |
| 14–15 | uint16 | monotonic while active, ~2/s | low |
| 16–17 | int16 | signed, grows with speed | low |
| 18–19 | int16 | signed, grows with speed | low |
| 20–23 | `00 00 00 00` | zero in this capture | — |

The speed decode was checked against FTMS across six samples: the ratio
`u32 ÷ (m/s)` was 1,000,000 within 0.17% every time.

### `a026e040` (service `a026ee0e`)

11 bytes, `7F 00 00 00 00 00 00 00 00 XX YY`. Bytes 9 and 10 change
independently and fire more often around console interaction. Unidentified —
possibly console/button state.

### `a026e004`, `a026e03b` others — silent in this capture.

## Wahoo proprietary — probable command channels

These never emitted anything unprompted. `writeWithoutResponse` + `notify` is
the classic request/response shape.

| Characteristic | Service |
|---|---|
| `a026e018` | `a026ee06` |
| `a026e023` | `a026ee06` |
| `a026e002` | `a026ee01` |
| `a026e03b` | `a026ee01` |
| `a026e03e` | `a026ee0e` |

**Message formats are entirely unknown.** Do not write speculative bytes to
these — it is a belt that moves under a person, and one of the five may not be
a motion channel at all. The only sound way to learn the formats is to capture
the Wahoo app driving the machine (Android HCI snoop log, or PacketLogger on
iOS) during a planned workout with pace targets, then match writes to observed
behaviour.

## Prior art — none

Checked 2026-08-01:

- qdomyos-zwift, the most complete open treadmill-control project, has no
  KICKR RUN driver (`src/devices` has `wahookickrheadwind` and
  `wahookickrsnapbike` only).
- GitHub code search for `a026e018`, `a026e023`, `a026e03e`, `a026ee06` and
  `a026ee0e` returns only false positives — Tasmota firmware hex blobs and
  binary game assets where the characters coincidentally appear.
- The community-documented Wahoo trainer control characteristic is `a026e005`,
  which this treadmill does not expose. That work does not transfer.

The command formats cannot be derived from telemetry. The only route is
capturing the Wahoo app's writes — which is also how the trainer protocol was
originally worked out.

## Open questions

- Does incline (`0x03`) succeed with the belt running? Never tested.
- Is firmware `2.0.43` current?
- Which of the five write characteristics carries pace targets, and does the
  treadmill still require the physical paddle confirmation regardless?
