#!/usr/bin/env python3
"""
Write a characteristic over DIRCON, and watch what the machine does about it.

Separate from dircon_explore.py on purpose: that one is read-only and stays
that way. This one moves the treadmill.

Usage:
    # replay the incline target the machine itself emitted (0.5%)
    python3 scripts/dircon_write.py 192.168.1.153 \
        --char a026e03e --data "FD 02 32 00 00" \
        --watch 12 --restore "FD 02 00 00 00"

Subscribes to the observation channels first (FTMS treadmill data, FTMS
machine status, and the target characteristic itself), writes, then logs
everything that follows so the effect can be seen.

SAFETY: only run with the belt stopped and nobody standing on the deck.
Always pass --restore to put the machine back where it started.
"""

import argparse
import socket
import struct
import sys
import time

PORT = 36866
SIG_BASE = "00001000800000805f9b34fb"
WAHOO_BASE = "0a7d4ab397faf1500f9feb8b"

MSG_WRITE, MSG_ENABLE_NOTIFY, MSG_NOTIFICATION = 4, 5, 6
RESP = {
    0: "success", 1: "invalid message type", 2: "generic error",
    3: "service not found", 4: "characteristic not found",
    5: "operation not supported", 6: "write failed",
}

# Short SIG names must be 4 hex chars — an 8-char name gets Wahoo's base
# appended and silently becomes a UUID that does not exist.
WATCH = ["2acd", "2ada", "a026e03e", "a026e040"]


def full_uuid(short):
    s = short.lower().replace("0x", "")
    if len(s) == 4:
        return bytes.fromhex("0000" + s + SIG_BASE)
    if len(s) == 8:
        return bytes.fromhex(s + WAHOO_BASE)
    if len(s) == 32:
        return bytes.fromhex(s)
    sys.exit(f"cannot interpret UUID: {short}")


def label(raw):
    h = raw.hex()
    if h.startswith("0000") and h[8:] == SIG_BASE:
        return f"0x{h[4:8].upper()}"
    if h.endswith(WAHOO_BASE):
        return f"a026{h[4:8]}"
    return h


def pack(t, seq, data=b""):
    return struct.pack("!BBBBH", 1, t, seq, 0, len(data)) + data


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        c = sock.recv(n - len(buf))
        if not c:
            raise ConnectionError("closed")
        buf += c
    return buf


def recv_msg(sock):
    ver, t, seq, resp, dlen = struct.unpack("!BBBBH", recv_exact(sock, 6))
    return t, seq, resp, (recv_exact(sock, dlen) if dlen else b"")


def show(payload):
    if len(payload) < 16:
        return
    u, val = payload[:16], payload[16:]
    line = f"  {time.strftime('%H:%M:%S')}  {label(u):<10} {val.hex(' ').upper()}"
    h = u.hex()
    if h.startswith("00002acd") and len(val) >= 9:
        spd = int.from_bytes(val[2:4], "little") / 100
        inc = int.from_bytes(val[7:9], "little", signed=True) / 10
        line += f"   speed {spd:.2f} km/h  incline {inc:+.1f}%"
    elif h.startswith("00002ada") and len(val) >= 1:
        names = {0x05: "TARGET SPEED CHANGED", 0x06: "TARGET INCLINE CHANGED"}
        line += f"   {names.get(val[0], f'status 0x{val[0]:02x}')}"
    print(line, flush=True)


def txn(sock, t, seq, data=b"", deadline=8.0):
    sock.sendall(pack(t, seq, data))
    end = time.time() + deadline
    while time.time() < end:
        mt, ms, resp, payload = recv_msg(sock)
        if mt == MSG_NOTIFICATION:
            show(payload)
            continue
        if mt == t and ms == seq:
            return resp, payload
    raise TimeoutError(f"no response to type {t}")


def drain(sock, seconds):
    sock.settimeout(1.0)
    end = time.time() + seconds
    while time.time() < end:
        try:
            mt, _, _, payload = recv_msg(sock)
            if mt == MSG_NOTIFICATION:
                show(payload)
        except socket.timeout:
            continue
        except (ConnectionError, OSError) as e:
            print(f"link closed: {e}")
            return
    sock.settimeout(None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--char", required=True, help="characteristic, e.g. a026e03e")
    ap.add_argument("--data", required=True, help="hex bytes, e.g. \"FD 02 32 00 00\"")
    ap.add_argument("--restore", help="hex bytes written at the end to undo")
    ap.add_argument("--watch", type=int, default=12, help="seconds to observe after the write")
    args = ap.parse_args()

    target = full_uuid(args.char)
    payload = bytes.fromhex(args.data.replace(" ", ""))

    print(f"connecting to {args.host}:{args.port}")
    sock = socket.create_connection((args.host, args.port), timeout=10)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    seq = 0
    for name in WATCH:
        seq += 1
        try:
            resp, _ = txn(sock, MSG_ENABLE_NOTIFY, seq, full_uuid(name) + b"\x01")
            status = "watching" if resp == 0 else f"NOT watching ({RESP.get(resp, resp)})"
            print(f"  {status} {name}")
        except (TimeoutError, ConnectionError, OSError) as e:
            print(f"  cannot watch {name}: {e}")
        time.sleep(0.4)

    print(f"\n>>> WRITE {label(target)}  {payload.hex(' ').upper()}")
    seq += 1
    try:
        resp, _ = txn(sock, MSG_WRITE, seq, target + payload)
        print(f">>> response: {RESP.get(resp, resp)}\n")
    except (TimeoutError, ConnectionError, OSError) as e:
        print(f">>> write failed: {e}")
        sock.close()
        return

    drain(sock, args.watch)

    if args.restore:
        rp = bytes.fromhex(args.restore.replace(" ", ""))
        print(f"\n>>> RESTORE {label(target)}  {rp.hex(' ').upper()}")
        seq += 1
        try:
            resp, _ = txn(sock, MSG_WRITE, seq, target + rp)
            print(f">>> response: {RESP.get(resp, resp)}\n")
        except (TimeoutError, ConnectionError, OSError) as e:
            print(f">>> restore failed: {e}")
        drain(sock, 8)

    sock.close()
    print("done")


if __name__ == "__main__":
    main()
