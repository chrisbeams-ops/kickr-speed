#!/usr/bin/env python3
"""
Read-only DIRCON (Wahoo Fitness TNP) explorer.

DIRCON is "BLE over TCP/IP" — the same GATT services and characteristics,
carried over a TCP connection on port 36866 instead of Bluetooth. If the
KICKR RUN speaks it, this enumerates everything over the network with no
phone in the loop.

Usage:
    python3 scripts/dircon_explore.py --discover
    python3 scripts/dircon_explore.py 192.168.1.50
    python3 scripts/dircon_explore.py 192.168.1.50 --port 36866

Discovers services, discovers characteristics with their properties, reads
the readable ones, subscribes to notifications, and logs everything.

Never writes. Message type 4 (Write Characteristic) is deliberately not
implemented — same rule as the Bluetooth explorer.

Protocol per the unofficial spec at github.com/elfrances/wahoo-fitness-tnp
"""

import argparse
import re
import socket
import struct
import subprocess
import sys
import time

PORT = 36866
SERVICE = "_wahoo-fitness-tnp._tcp"

VERSION = 1
MSG_DISCOVER_SERVICES = 1
MSG_DISCOVER_CHARS = 2
MSG_READ_CHAR = 3
MSG_ENABLE_NOTIFY = 5
MSG_NOTIFICATION = 6

RESP = {
    0: "success", 1: "invalid message type", 2: "generic error",
    3: "service not found", 4: "characteristic not found",
    5: "operation not supported", 6: "write failed",
}

PROP_READ, PROP_WRITE, PROP_NOTIFY = 0x01, 0x02, 0x04
SIG_BASE = "0000100080000080 5f9b34fb".replace(" ", "")
WAHOO_BASE = "0a7d4ab397faf1500f9feb8b"


# ── protocol ──────────────────────────────────────────────────────────

def pack(msg_type, seq, data=b""):
    return struct.pack("!BBBBH", VERSION, msg_type, seq, 0, len(data)) + data


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed by device")
        buf += chunk
    return buf


def recv_msg(sock):
    hdr = recv_exact(sock, 6)
    ver, mtype, seq, resp, dlen = struct.unpack("!BBBBH", hdr)
    return mtype, seq, resp, recv_exact(sock, dlen) if dlen else b""


def txn(sock, msg_type, seq, data=b"", deadline=8.0):
    """Send a request and return its matching response, logging notifications
    that arrive in between.

    The deadline matters: a026e03d notifies continuously, so a plain blocking
    read never times out even when the response is never coming — it just keeps
    consuming notifications forever.
    """
    sock.sendall(pack(msg_type, seq, data))
    end = time.time() + deadline
    while True:
        if time.time() > end:
            raise TimeoutError(f"no response to msg type {msg_type} seq {seq}")
        mtype, rseq, resp, payload = recv_msg(sock)
        if mtype == MSG_NOTIFICATION:
            log_notification(payload)
            continue
        if mtype == msg_type and rseq == seq:
            return resp, payload
        print(f"    (unexpected msg type {mtype} seq {rseq})")


# ── UUID helpers ──────────────────────────────────────────────────────

def uuid_hex(raw):
    return raw.hex()


def uuid_label(raw):
    h = raw.hex()
    if h.startswith("0000") and h[8:] == SIG_BASE:
        return f"0x{h[4:8].upper()}"
    if h.endswith(WAHOO_BASE):
        return f"a026{h[4:8]} (WAHOO)"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def props_label(p):
    out = []
    if p & PROP_READ:
        out.append("read")
    if p & PROP_WRITE:
        out.append("write")
    if p & PROP_NOTIFY:
        out.append("notify")
    return ",".join(out) or "none"


# ── decoders, same as the Bluetooth side ──────────────────────────────

def decode(uuid_h, val):
    if uuid_h.startswith("00002acd"):
        if len(val) < 4:
            return None
        flags = int.from_bytes(val[0:2], "little")
        if flags & 0x01:
            return None
        return f"speed {int.from_bytes(val[2:4], 'little') / 100:.2f} km/h"
    if uuid_h.startswith("00002a53"):
        if len(val) < 4:
            return None
        return (f"speed {int.from_bytes(val[1:3], 'little') / 256 * 3.6:.2f} km/h  "
                f"cadence {val[3]} spm")
    if uuid_h.startswith("a026e03d") and len(val) >= 6:
        return f"belt {int.from_bytes(val[2:6], 'little') / 1e6 * 3.6:.2f} km/h"
    return None


def log_notification(payload):
    if len(payload) < 16:
        return
    uuid_raw, val = payload[:16], payload[16:]
    line = f"  {time.strftime('%H:%M:%S')}  NOTIFY {uuid_label(uuid_raw)}  {val.hex(' ').upper()}"
    extra = decode(uuid_hex(uuid_raw), val)
    print(line + (f"\n           {extra}" if extra else ""))


# ── mDNS discovery ────────────────────────────────────────────────────

def discover(timeout=10):
    print(f"Browsing for {SERVICE} …")
    try:
        out = subprocess.run(
            ["perl", "-e", "alarm shift; exec @ARGV", str(timeout),
             "dns-sd", "-B", SERVICE, "local"],
            capture_output=True, text=True).stdout
    except FileNotFoundError:
        sys.exit("dns-sd not available (macOS only)")

    names = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 7 and parts[1] == "Add":
            names.append(" ".join(parts[6:]))
    if not names:
        print("No DIRCON devices found.")
        print("If the treadmill is on and on Wi-Fi, it does not support DIRCON.")
        return None

    name = names[0]
    print(f"Found: {name}")

    out = subprocess.run(
        ["perl", "-e", "alarm shift; exec @ARGV", "6",
         "dns-sd", "-L", name, SERVICE, "local"],
        capture_output=True, text=True).stdout
    print(out.strip())

    m = re.search(r"can be reached at (\S+):(\d+)", out)
    if not m:
        print("Could not parse host/port; pass the IP address manually.")
        return None
    host, port = m.group(1).rstrip("."), int(m.group(2))
    for line in out.splitlines():
        if "ble-service-uuids" in line or "serial-number" in line:
            print(f"  TXT: {line.strip()}")
    return host, port


# ── main ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host", nargs="?", help="device IP (omit with --discover)")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--listen", type=int, default=60,
                    help="seconds to log notifications (default 60)")
    ap.add_argument("--only", help="comma-separated substrings of characteristics "
                                   "to subscribe to, e.g. e03d,2acd")
    ap.add_argument("--gap", type=float, default=0.3,
                    help="seconds between subscribe requests (default 0.3)")
    ap.add_argument("--no-read", action="store_true", help="skip reading characteristics")
    ap.add_argument("--skip", help="comma-separated substrings to NOT subscribe to")
    args = ap.parse_args()

    host, port = args.host, args.port
    if args.discover or not host:
        found = discover()
        if not found:
            return
        host, port = found

    print(f"\nConnecting to {host}:{port} …")
    sock = socket.create_connection((host, port), timeout=10)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    print("connected\n")

    seq = 0
    seq += 1
    resp, data = txn(sock, MSG_DISCOVER_SERVICES, seq)
    if resp != 0:
        sys.exit(f"discover services failed: {RESP.get(resp, resp)}")

    services = [data[i:i + 16] for i in range(0, len(data), 16)]
    print(f"=== {len(services)} services ===")
    for s in services:
        print(f"  {uuid_label(s)}")

    notify_chars = []
    for svc in services:
        seq += 1
        resp, data = txn(sock, MSG_DISCOVER_CHARS, seq, svc)
        print(f"\nSERVICE {uuid_label(svc)}")
        if resp != 0:
            print(f"  characteristics unavailable: {RESP.get(resp, resp)}")
            continue
        body = data[16:]
        for i in range(0, len(body), 17):
            rec = body[i:i + 17]
            if len(rec) < 17:
                break
            cu, props = rec[:16], rec[16]
            print(f"  {uuid_label(cu):<28} [{props_label(props)}]")

            if props & PROP_READ and not args.no_read:
                seq += 1
                r, d = txn(sock, MSG_READ_CHAR, seq, cu)
                if r == 0 and len(d) > 16:
                    val = d[16:]
                    printable = "".join(chr(b) if 32 <= b < 127 else "." for b in val)
                    print(f"      read: {val.hex(' ').upper()}   \"{printable}\"")
                else:
                    print(f"      read failed: {RESP.get(r, r)}")

            if props & PROP_NOTIFY:
                notify_chars.append(cu)

    if args.only:
        want = [w.lower() for w in args.only.split(",")]
        notify_chars = [c for c in notify_chars
                        if any(w in uuid_label(c).lower() or w in c.hex() for w in want)]
    if args.skip:
        drop = [w.lower() for w in args.skip.split(",")]
        notify_chars = [c for c in notify_chars
                        if not any(w in uuid_label(c).lower() or w in c.hex() for w in drop)]

    # Telemetry first: a bad subscribe can drop the link, and losing the
    # known-good channels would waste the whole run.
    priority = ("00002acd", "00002a53", "a026e03d", "a026e040")
    notify_chars.sort(key=lambda c: 0 if c.hex().startswith(priority) else 1)

    print(f"\n=== subscribing to {len(notify_chars)} characteristics ===")
    subscribed = 0
    for cu in notify_chars:
        seq += 1
        try:
            r, _ = txn(sock, MSG_ENABLE_NOTIFY, seq, cu + b"\x01")
            if r == 0:
                subscribed += 1
            else:
                print(f"  {uuid_label(cu)}: {RESP.get(r, r)}")
        except TimeoutError as e:
            # No response, but the link is still alive — keep going
            print(f"  {uuid_label(cu)}: no response ({e})")
        except (ConnectionError, OSError) as e:
            # The device drops the link if subscriptions are fired too fast
            print(f"  {uuid_label(cu)}: connection lost during subscribe ({e})")
            break
        time.sleep(args.gap)
    print(f"  {subscribed} subscribed")

    print(f"\n=== listening {args.listen}s — use the console now ===")
    sock.settimeout(1.0)
    end = time.time() + args.listen
    while time.time() < end:
        try:
            mtype, _, _, payload = recv_msg(sock)
            if mtype == MSG_NOTIFICATION:
                log_notification(payload)
        except socket.timeout:
            continue
        except (ConnectionError, OSError) as e:
            print(f"connection lost: {e}")
            break

    sock.close()
    print("\ndone")


if __name__ == "__main__":
    main()
