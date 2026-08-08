#!/usr/bin/env python3
"""
Pull the KICKR RUN command protocol out of a Bluetooth capture.

Usage:
    python3 scripts/analyze_capture.py captures/wahoo-workout.pklg

Reads a PacketLogger .pklg (or pcap/pcapng/btsnoop) via tshark, then:

  * maps ATT handles to UUIDs from whatever discovery traffic is present
  * lists every ATT write, flagging those to Wahoo's proprietary services
  * decodes belt speed from FTMS 0x2ACD and Wahoo a026e03d notifications
  * prints a timeline so a write can be lined up against the speed change
    it produced

Requires tshark:  brew install wireshark
"""

import json
import subprocess
import sys
from pathlib import Path

WAHOO_BASE = "0a7d4ab397faf1500f9feb8b"          # Wahoo's 128-bit UUID base, hyphens stripped

# ATT opcodes that carry a client->server write
WRITE_OPS = {
    0x12: "write request",
    0x52: "write command",          # writeWithoutResponse
    0x16: "prepare write",
    0x18: "execute write",
    0xD2: "signed write",
}
NOTIFY_OPS = {0x1B: "notification", 0x1D: "indication"}


def run_tshark(path):
    cmd = ["tshark", "-r", str(path), "-Y", "btatt", "-T", "json"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except FileNotFoundError:
        sys.exit("tshark not found. Install it with:  brew install wireshark")
    except subprocess.CalledProcessError as e:
        sys.exit(f"tshark failed:\n{e.stderr.strip()}")
    if not out.strip():
        sys.exit("No ATT traffic in that capture. Was the phone actually talking to the treadmill?")
    return json.loads(out)


def flat(layer):
    """tshark repeats fields as str or list; normalise to a list of str."""
    def get(key):
        v = layer.get(key)
        if v is None:
            return []
        return v if isinstance(v, list) else [v]
    return get


def parse(packets):
    handles = {}        # handle -> uuid
    events = []

    for pkt in packets:
        layers = pkt.get("_source", {}).get("layers", {})
        att = layers.get("btatt")
        if not att:
            continue
        if isinstance(att, list):
            att = att[0]
        get = flat(att)

        try:
            t = float(layers["frame"]["frame.time_relative"])
        except (KeyError, ValueError, TypeError):
            t = 0.0

        # Handle -> UUID mapping, from discovery responses
        for h, u in zip(get("btatt.handle"), get("btatt.uuid128")):
            handles[norm_handle(h)] = u.replace(":", "").replace("-", "").lower()
        for h, u in zip(get("btatt.handle"), get("btatt.uuid16")):
            handles.setdefault(norm_handle(h), norm_handle(u))

        for op_s in get("btatt.opcode"):
            op = norm_int(op_s)
            if op is None:
                continue
            hs = get("btatt.handle")
            vs = get("btatt.value")
            handle = norm_handle(hs[0]) if hs else None
            value = (vs[0] if vs else "").replace(":", "").lower()

            if op in WRITE_OPS:
                events.append(("write", t, handle, value, WRITE_OPS[op]))
            elif op in NOTIFY_OPS:
                events.append(("notify", t, handle, value, NOTIFY_OPS[op]))

    return handles, events


def norm_handle(h):
    v = norm_int(h)
    return f"0x{v:04x}" if v is not None else str(h)


def norm_int(s):
    try:
        s = str(s).strip()
        return int(s, 16) if s.lower().startswith("0x") else int(s)
    except (ValueError, TypeError):
        return None


def uuid_label(uuid):
    if not uuid:
        return "?"
    if uuid.endswith(WAHOO_BASE) and len(uuid) == 32:
        return f"a026{uuid[4:8]} (WAHOO)"
    if len(uuid) == 32 and uuid.startswith("0000") and uuid[8:].startswith("00001000"):
        return f"0x{uuid[4:8].upper()}"
    if len(uuid) <= 4:
        return f"0x{uuid.upper()}"
    return uuid


def is_wahoo(uuid):
    return bool(uuid) and uuid.endswith(WAHOO_BASE)


def hexb(v):
    return " ".join(v[i:i + 2].upper() for i in range(0, len(v), 2))


def speed_from_ftms(v):
    """0x2ACD: flags u16, then instantaneous speed u16 LE at 0.01 km/h."""
    b = bytes.fromhex(v)
    if len(b) < 4:
        return None
    flags = int.from_bytes(b[0:2], "little")
    if flags & 0x01:               # more-data set means speed absent
        return None
    return int.from_bytes(b[2:4], "little") / 100


def speed_from_wahoo(v):
    """a026e03d: bytes 2-5 are belt speed in um/s."""
    b = bytes.fromhex(v)
    if len(b) < 6:
        return None
    return int.from_bytes(b[2:6], "little") / 1e6 * 3.6


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = Path(sys.argv[1])
    if not path.exists():
        sys.exit(f"No such file: {path}")

    handles, events = parse(run_tshark(path))

    print(f"\n=== handle map ({len(handles)} resolved) ===")
    for h, u in sorted(handles.items()):
        mark = "  <-- WAHOO" if is_wahoo(u) else ""
        print(f"  {h}  {uuid_label(u)}{mark}")

    writes = [e for e in events if e[0] == "write"]
    print(f"\n=== {len(writes)} writes ===")
    if not writes:
        print("  none — the capture has no client writes at all")
    for _, t, h, v, kind in writes:
        u = handles.get(h)
        mark = "  <== CANDIDATE" if is_wahoo(u) else ""
        print(f"  [{t:8.3f}]  {h}  {uuid_label(u):<22}  {kind:<14}  {hexb(v)}{mark}")

    # Timeline: writes interleaved with the speed the machine then reported
    print("\n=== timeline (writes + reported speed) ===")
    last_speed = None
    for kind, t, h, v, label in sorted(events, key=lambda e: e[1]):
        u = handles.get(h) or ""
        if kind == "notify":
            spd = None
            if u.startswith("00002acd"):
                spd = speed_from_ftms(v)
            elif u.startswith("a026e03d"):
                spd = speed_from_wahoo(v)
            if spd is not None and (last_speed is None or abs(spd - last_speed) >= 0.05):
                print(f"  [{t:8.3f}]  speed -> {spd:5.2f} km/h  ({spd * 0.621371:.2f} mph)")
                last_speed = spd
        else:
            flag = "  <== CANDIDATE" if is_wahoo(u) else ""
            print(f"  [{t:8.3f}]  WRITE {uuid_label(u)}  {hexb(v)}{flag}")

    print("\nLook for a write that lands a second or two before each speed change,")
    print("on one of the a026 characteristics. That is the pace-target command.\n")


if __name__ == "__main__":
    main()
