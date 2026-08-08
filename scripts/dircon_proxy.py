#!/usr/bin/env python3
"""
DIRCON proxy — sit between the Wahoo app and the KICKR RUN and log everything.

DIRCON is BLE over TCP, so this is a plain TCP man-in-the-middle on your own
network and your own treadmill. It advertises itself over mDNS as a Wahoo
device, accepts the app's connection, forwards every message to the real
machine unchanged, and logs both directions decoded.

The point is to learn what the Wahoo app writes when a planned workout changes
pace. Those same bytes can then be written directly — over TCP here, or over
Bluetooth from the web app, since DIRCON and BLE carry identical payloads.

Usage:
    python3 scripts/dircon_proxy.py --target 192.168.1.153
    python3 scripts/dircon_proxy.py --target 192.168.1.153 --name "KICKR RUN PROXY"
    python3 scripts/dircon_proxy.py --target 192.168.1.153 --no-advertise

Then in the Wahoo app, connect to the advertised proxy rather than the
treadmill, and run a workout with pace targets.

Everything is written to captures/dircon-proxy-<timestamp>.log as well as the
terminal. Writes to Wahoo's proprietary characteristics are marked >>> so they
are easy to find afterwards.

This proxy forwards traffic unchanged. It does not synthesise or modify any
command.
"""

import argparse
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from datetime import datetime

PORT = 36866
SERVICE = "_wahoo-fitness-tnp._tcp"

MSG_NAMES = {
    1: "discover-services", 2: "discover-chars", 3: "read",
    4: "WRITE", 5: "enable-notify", 6: "notify",
}
RESP = {
    0: "ok", 1: "invalid msg type", 2: "generic error", 3: "service not found",
    4: "char not found", 5: "op not supported", 6: "write failed",
}
SIG_BASE = "00001000800000805f9b34fb"
WAHOO_BASE = "0a7d4ab397faf1500f9feb8b"

log_lock = threading.Lock()
log_file = None


def log(line):
    with log_lock:
        print(line, flush=True)
        if log_file:
            log_file.write(line + "\n")
            log_file.flush()


def uuid_label(raw):
    h = raw.hex()
    if h.startswith("0000") and h[8:] == SIG_BASE:
        return f"0x{h[4:8].upper()}"
    if h.endswith(WAHOO_BASE):
        return f"a026{h[4:8]}"
    return h


def is_wahoo(raw):
    return raw.hex().endswith(WAHOO_BASE)


def decode(raw_uuid, val):
    h = raw_uuid.hex()
    if h.startswith("00002acd") and len(val) >= 4:
        flags = int.from_bytes(val[0:2], "little")
        if not (flags & 0x01):
            return f"speed {int.from_bytes(val[2:4], 'little') / 100:.2f} km/h"
    if h.startswith("a026e03d") and len(val) >= 6:
        return f"belt {int.from_bytes(val[2:6], 'little') / 1e6 * 3.6:.2f} km/h"
    if h.startswith("00002a53") and len(val) >= 4:
        return f"rsc {int.from_bytes(val[1:3], 'little') / 256 * 3.6:.2f} km/h"
    return None


def describe(direction, data):
    """Render one DIRCON message for the log."""
    if len(data) < 6:
        return f"{direction} runt message {data.hex(' ').upper()}"
    ver, mtype, seq, resp, dlen = struct.unpack("!BBBBH", data[:6])
    body = data[6:6 + dlen]
    name = MSG_NAMES.get(mtype, f"type{mtype}")
    head = f"{direction} {name:<17} seq={seq:<3}"
    if resp:
        head += f" resp={RESP.get(resp, resp)}"

    if mtype in (3, 4, 5, 6) and len(body) >= 16:
        u, val = body[:16], body[16:]
        mark = " >>>" if (mtype == 4 and is_wahoo(u)) else ""
        line = f"{head} {uuid_label(u)}{mark}"
        if val:
            line += f"  {val.hex(' ').upper()}"
        extra = decode(u, val)
        if extra:
            line += f"   ({extra})"
        return line
    if mtype == 1 and dlen:
        n = dlen // 16
        return f"{head} {n} services: " + ", ".join(
            uuid_label(body[i:i + 16]) for i in range(0, dlen, 16))
    if mtype == 2 and dlen >= 16:
        svc = uuid_label(body[:16])
        chars = []
        rest = body[16:]
        for i in range(0, len(rest) - 16, 17):
            chars.append(uuid_label(rest[i:i + 16]))
        return f"{head} {svc}" + (f" -> {', '.join(chars)}" if chars else "")
    return head + (f"  {body.hex(' ').upper()}" if body else "")


def read_message(sock):
    """Read exactly one DIRCON message, returning its raw bytes."""
    hdr = b""
    while len(hdr) < 6:
        chunk = sock.recv(6 - len(hdr))
        if not chunk:
            return None
        hdr += chunk
    dlen = struct.unpack("!H", hdr[4:6])[0]
    body = b""
    while len(body) < dlen:
        chunk = sock.recv(dlen - len(body))
        if not chunk:
            return None
        body += chunk
    return hdr + body


def pump(src, dst, direction, stop):
    """Forward messages one at a time, logging each. Bytes pass through
    untouched — this proxy observes, it does not rewrite."""
    try:
        while not stop.is_set():
            msg = read_message(src)
            if msg is None:
                break
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            log(f"{ts}  {describe(direction, msg)}")
            dst.sendall(msg)
    except (ConnectionError, OSError) as e:
        log(f"  {direction} link closed: {e}")
    finally:
        stop.set()
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def advertise(name, port, uuids, mac, serial):
    args = ["dns-sd", "-R", name, SERVICE, "local", str(port),
            f"ble-service-uuids={uuids}", f"mac-address={mac}",
            f"serial-number={serial}"]
    log(f"advertising as \"{name}\" on port {port}")
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="real treadmill IP")
    ap.add_argument("--target-port", type=int, default=PORT)
    ap.add_argument("--port", type=int, default=PORT, help="port to listen on")
    ap.add_argument("--name", default="KICKR RUN PROXY")
    ap.add_argument("--no-advertise", action="store_true")
    ap.add_argument("--uuids", default="0x1826,0x1814,A026EE0E-0A7D-4AB3-97FA-F1500F9FEB8B")
    ap.add_argument("--mac", default="D8-3B-DA-05-EB-E5")
    ap.add_argument("--serial", default="252800029")
    args = ap.parse_args()

    global log_file
    os.makedirs("captures", exist_ok=True)
    path = f"captures/dircon-proxy-{datetime.now():%Y%m%d-%H%M%S}.log"
    log_file = open(path, "w")
    log(f"logging to {path}")

    adv = None
    if not args.no_advertise:
        adv = advertise(args.name, args.port, args.uuids, args.mac, args.serial)

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.port))
    srv.listen(1)
    log(f"listening on 0.0.0.0:{args.port}, forwarding to {args.target}:{args.target_port}")
    log("connect the Wahoo app to the proxy, then run a workout with PACE targets")
    log("writes to Wahoo proprietary characteristics are marked >>>\n")

    try:
        while True:
            client, addr = srv.accept()
            log(f"=== client connected from {addr[0]} ===")
            try:
                device = socket.create_connection((args.target, args.target_port), timeout=10)
            except OSError as e:
                log(f"cannot reach treadmill: {e}")
                client.close()
                continue
            for s in (client, device):
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.settimeout(None)

            stop = threading.Event()
            t1 = threading.Thread(target=pump, args=(client, device, "app -> run ", stop), daemon=True)
            t2 = threading.Thread(target=pump, args=(device, client, "run -> app ", stop), daemon=True)
            t1.start(); t2.start()
            while t1.is_alive() and t2.is_alive():
                time.sleep(0.2)
            stop.set()
            client.close(); device.close()
            log("=== client disconnected ===\n")
    except KeyboardInterrupt:
        log("\nstopping")
    finally:
        if adv:
            adv.terminate()
        srv.close()
        if log_file:
            log_file.close()
        print(f"\nCapture saved to {path}")


if __name__ == "__main__":
    main()
