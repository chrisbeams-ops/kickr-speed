# KICKR RUN — observed BLE protocol

Notes from a KICKR RUN 6D7C. Firmware `2.0.43`, hardware rev `7`, manufacturer
`Wahoo Fitness`.

> **Speed control is solved and working.** Much of this document was written
> while concluding the opposite. Those sections are kept because the measurements
> are sound and the negative results are still useful, but where they conclude
> speed control is impossible, they are **superseded by the section below**.

## Setting belt speed — WORKING

Protocol from qdomyos-zwift PR #4834 (an HCI trace of the Wahoo app), implemented
in `index.html` and `intervals.html`, verified on the machine.

Command characteristic: **`a026e03e`** (write-without-response + notify), in
service `a026ee0e`. Do not confuse it with `a026e002`, which has identical
properties but is a debug stream that silently swallows writes.

```
init       01, 10, 12, 18, 80, 86, F0     single bytes, ~120 ms apart
handshake  <- FE 86 01 [id x4]            machine announces itself
           -> 87 [id x4]                  app acks
unlock     19 00                          once per session
set speed  02 [LE24 um/s] 00 [flag]       flag FF on first send, 0A after
                                          um/s = round(kmh / 3.6 * 1e6)
paddle     <- FD E0 02                    machine waiting for the paddle
           <- FD E0 01 [hi][lo]           paddle tapped, challenge issued
           -> E0 [hi][lo]                 app echoes it back
target ack <- FF 01 [LE24 um/s]
```

The µm/s unit independently matches the telemetry decode below (checked to 0.17%),
which is a useful cross-confirmation from two unrelated sources.

**The paddle tap cannot be bypassed.** The challenge only exists after the
physical paddle sensor fires, so there is nothing to echo until a human taps it.
That is a deliberate firmware interlock and the reason app-driven speed is safe
to offer at all.

**Why this was missed for a long time:** a naive replay of the incline *status*
frame to `a026e03e` with no init, handshake or unlock is acknowledged with
`FE FD 02` and does nothing — which reads exactly like a read-only channel. It
isn't; it just hadn't been opened.

Confidence is marked per field below. Except where stated, the rest of this
document is read and notify traffic only.

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
Incline `03 0A 00` → `80 03 04` operation failed with the belt **stopped**.
With the belt **running** it succeeds (tested 2026-08-01). So incline control
requires an active deck; the failure was a state condition, not a capability
limit.

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

### `a026e03e` (service `a026ee0e`) — target setting

> **Superseded.** This *is* the command channel — see "Setting belt speed" at the
> top. It also reports target changes, which is what led to the wrong conclusion
> below. Both things are true: it reports, and it accepts commands once opened.

It reports every incline target change with its origin, whatever set it. Incline itself is commanded over FTMS
(`0x2AD9` opcode `0x03`) — which is how Zwift and this app already control it —
so what appears here is the announcement, not the instruction.

Observed 2026-08-08, incline changed from the console:

```
17:15:50  0x2ADA    06 05 00           FTMS machine status: target incline changed, 5 (0.1%)  = 0.5%
17:15:50  a026e03e  FD 02 32 00 00     0x32 = 50 (0.01%)                                      = 0.5%
17:15:50  0x2ACD    …00 05 00…         actual inclination now 0.5%

17:15:53  0x2ADA    06 00 00           target incline changed → 0
17:15:53  a026e03e  FD 02 00 00 00     value 0
17:15:53  0x2ACD    …00 00 00…         actual inclination back to 0
```

Inferred layout:

| Offset | Bytes | Meaning | Confidence |
|---|---|---|---|
| 0 | `FD` | message header | medium |
| 1 | `02` | field id — `02` = inclination target | medium |
| 2–3 | int16 LE | target value, 0.01% units for inclination | **high** — cross-checks against FTMS twice |
| 4 | byte | **origin of the change** — `00` console/local, `01` app/remote | **high** — two clean observations of each |

The obvious hypothesis is that a **speed** target is the same channel with a
different field id. Untested, and the field id for speed is unknown.

**Writing this payload back does not work.** Tested 2026-08-08 with the belt
running at 2.41 km/h and telemetry confirming the machine was awake:

```
>>> WRITE a026e03e  FD 02 32 00 00     (the exact bytes the machine emitted)
    a026e03e  FE FD 02                 (machine responds)
>>> DIRCON response: success
    incline stays +0.0% for 10s        (nothing happens)
```

So the write reaches the machine and is answered, but has no effect — as
expected for a reporting channel. `FE FD 02` is most likely a rejection echoing
the opcode and field.

Changing incline **from the app over FTMS** produces the same mirror message
with the origin byte set:

```
17:27:24  0x2ADA    06 14 00        target incline -> 20 (0.1%) = 2.0%
17:27:24  a026e03e  FD 02 C8 00 01  200 (0.01%) = 2.0%, origin 01 = app
17:27:31  0x2ADA    06 00 00
17:27:31  a026e03e  FD 02 00 00 01
```

**The mirror does not cover speed.** Tested 2026-08-08 during a real Wahoo app
pace workout, where the belt changed speed and the paddle prompt appeared:

- `0x2ADA` was subscribed and emitted **nothing**. This machine does not report
  Target Speed Changed (`0x05`) even though it reports Target Incline Changed
  (`0x06`) — consistent with speed targets being absent from its FTMS
  implementation entirely.
- `a026e03e` was subscribed, confirmed with `1 subscribed`, and emitted
  **nothing** across the whole workout.

So a pace target set by the Wahoo app is invisible from outside: neither the
command nor any announcement of it can be observed. The command is presumably
on one of `a026e002`, `a026e018`, `a026e023`, all silent throughout.

`a026e002`, `a026e004`, `a026e018` and `a026e023` were also subscribed and
confirmed during several app-driven pace changes, and stayed silent too.

**Why observing from outside cannot work.** Notifications are per-subscriber,
and a request/response exchange is directed to the client that made it. From a
second connection we see broadcast state — `0x2ACD`, `0x2A53`, `a026e03d`
telemetry, and `a026e03e` incline targets, including changes originated by
another client — but never another client's command traffic or its
acknowledgement. That is how BLE works, not a subscription problem, so no amount
of retrying from DIRCON will surface it.

The only vantage point that sees the phone's own traffic is the phone's own
Bluetooth stack: an HCI trace (profile + sysdiagnose, or PacketLogger). The
DIRCON proxy cannot help either, since the Wahoo app does not use DIRCON.

Useful correlation trick for a future capture: FTMS Machine Status (`0x2ADA`)
opcode `0x05` is *Target Speed Changed*. When an app sets a pace, `0x2ADA`
should emit `05 …` at the same instant `a026e03e` emits its proprietary
equivalent — which pairs the two and gives the speed encoding directly.

Note `a026e03e` is silent except when a target actually changes. The other
write characteristics (`a026e002`, `a026e023`, `a026e018`) stayed silent
throughout a full session of speed changes, incline changes and start/stop.
`a026e03b` cannot be subscribed to over DIRCON — the request times out and
kills the connection.

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

## DIRCON — the same GATT tree over TCP

**The KICKR RUN supports Wahoo Direct Connect (WFTNP).** Confirmed 2026-08-08.
This matters: DIRCON is "BLE over TCP/IP", so every service and characteristic
below is reachable from a laptop on the same network, with no phone and no
Bluetooth stack involved.

mDNS advertisement (`_wahoo-fitness-tnp._tcp.local`):

```
KICKR RUN 6D7C._wahoo-fitness-tnp._tcp.local  ->  KICKR-RUN-6D7C.local:36866
  ble-service-uuids = 0x1826,0x1814,A026EE0E-0A7D-4AB3-97FA-F1500F9FEB8B
  mac-address       = D8-3B-DA-05-EB-E4
  serial-number     = 252800028
```

Note the TXT record advertises a **proprietary service** alongside the standard
ones. Discover Services over TCP then returns all six, exactly matching the
Bluetooth dump — including all five write-capable proprietary characteristics:

| Service | Characteristics |
|---|---|
| `0x180A` | `0x2A29` `0x2A25` `0x2A27` `0x2A26` (read) |
| `0x1826` | `0x2ACC` `0x2AD5` (read), `0x2AD9` (write,notify), `0x2ACD` `0x2ADA` (notify) |
| `0x1814` | `0x2A54` (read), `0x2A53` (notify) |
| `a026ee01` | `a026e002` `a026e03b` (write,notify), `a026e004` (notify) |
| `a026ee06` | `a026e023` `a026e018` (write,notify) |
| `a026ee0e` | `a026e03e` (write,notify), `a026e03d` `a026e040` (notify) |

Device Information over DIRCON confirms firmware `2.0.43`, hardware rev `7`,
serial `252800028`, manufacturer `Wahoo Fitness`.

Practical notes:
- Connect by IP, not the `.local` name — name resolution gave "no route to host"
  while the A record resolved fine and the host pinged.
- Round-trip latency is 400–900 ms (Wi-Fi power saving), and the device drops
  the connection if subscriptions are fired back to back. Space them out.

`scripts/dircon_explore.py` implements the read-only side of this.

**The Wahoo phone app does not use DIRCON.** Tested 2026-08-08: a proxy
advertising as `KICKR RUN PROXY` was visible on the network alongside the real
machine, but never appeared in the Wahoo app's device list and was never
connected to. The app pairs sensors over Bluetooth only. DIRCON is aimed at
training apps (Zwift, SYSTM, FulGaz), which control incline rather than speed —
so proxying DIRCON cannot capture the pace-target command. Capturing it needs a
Bluetooth HCI trace of the phone (`scripts/analyze_capture.py`).

## Stop

The FTMS stop opcode (`0x2AD9` `08 01`) is **not honoured** — tested with the belt
running, which kept going. Stopping from software means sending zero speed on
Wahoo's channel, which goes through the paddle interlock like any other speed
change. There is no software path to an immediate stop; the physical stop button
is the only instant one.

## Prior art — found, eventually

Checked 2026-08-01:

- qdomyos-zwift, the most complete open treadmill-control project, has no
  KICKR RUN driver (`src/devices` has `wahookickrheadwind` and
  `wahookickrsnapbike` only).
- GitHub code search for `a026e018`, `a026e023`, `a026e03e`, `a026ee06` and
  `a026ee0e` returns only false positives — Tasmota firmware hex blobs and
  binary game assets where the characters coincidentally appear.
- The community-documented Wahoo trainer control characteristic is `a026e005`,
  which this treadmill does not expose. That work does not transfer.

The command formats cannot be derived from telemetry, and that conclusion was
right. What was wrong was concluding nobody had captured them: **qdomyos-zwift PR
#4834** contains the full protocol from an HCI trace, in
`src/devices/wahookickruntreadmill/`. It was unmerged, so it appeared in neither
code search nor the repository's device list.

Lesson: search open pull requests and branches, not just merged code.

## Open questions

- Is firmware `2.0.43` current?
- Which of the five write characteristics carries pace targets, and does the
  treadmill still require the physical paddle confirmation regardless?
