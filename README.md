# KICKR Run

A single-file Web Bluetooth app for the Wahoo KICKR RUN treadmill. It shows the
metrics the console doesn't, and — unlike anything else outside Wahoo's own app —
it can **set the belt speed**.

## What it does

- **Set an exact speed.** Tap 8 and the belt goes to 8, rather than hunting up and
  down with the paddle. The treadmill asks you to tap the speed paddle to confirm;
  that's its own safety interlock and cannot be skipped.
- **Set the grade** (−3% to +15%), which needs the belt running.
- **Display** speed, pace, distance, grade, cadence, calories, last mile/km split
  and elapsed time, in mph or km/h.
- **Stop**, from any screen — this also needs the paddle tap, as any speed change does.

## How to use

**iPhone / iPad** — install [Bluefy](https://apps.apple.com/us/app/bluefy-web-ble-browser/id1492822055),
open the Pages URL, tap **Connect**.

**Mac / PC** — open the Pages URL in Chrome or Edge and click **Connect**. There's
also a slim-panel launcher for putting it alongside a video.

Safari has no Web Bluetooth, and the page must be served over HTTPS.

## Files

| File | What it is |
|---|---|
| `index.html` | The app. This is the one that gets used on the treadmill. |
| `intervals.html` | Standalone interval runner; where speed control was first proven. |
| `test.html` | Sandbox — workout builder, audio cues, protocol diagnostics. |
| `explore.html` | Read-only GATT explorer. |
| `PROTOCOL.md` | Everything known about the machine's Bluetooth protocol. |
| `scripts/` | DIRCON (Bluetooth-over-TCP) tools and a capture analyser. |

## How speed control works

FTMS — the open standard — genuinely refuses speed on this machine: the feature
characteristic reports no speed-target support and the control point answers "op
code not supported". Grade goes over FTMS fine.

Speed goes over Wahoo's proprietary channel (`a026e03e`), which needs an init
sequence, a device-id handshake and an unlock before it accepts anything. Speeds
are sent as micrometres per second. Every change is gated on the physical paddle:
the machine issues a challenge only after the paddle sensor fires, and the app
echoes it back. Protocol details and the reasoning are in `PROTOCOL.md`.

## Working on this repo

`main` is what the treadmill loads. GitHub Pages publishes it within a minute or
two of a push, so **anything merged to `main` is live on the machine someone may
be standing on**.

- Do work on a branch and merge to `main` only once it's been tried.
- Legacy Pages can't preview a branch, so to test on the iPad, put experiments in
  `test.html` (which is published but isn't the app anyone runs) rather than
  pointing `index.html` at something unproven.
- Tags mark states known to work on the hardware. To get back to one:

```bash
git revert <bad-commit>     # preferred — keeps history
git checkout v1.0.0 -- index.html && git commit -m "roll back index.html"
```

Anything that can move the belt deserves testing at walking pace, on the side
rails, before it's trusted. The physical stop button and safety key always
override the app.
