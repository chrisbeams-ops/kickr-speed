#!/usr/bin/env python3
"""
Push a structured pace workout to your Wahoo account via the Cloud API.

The point is to get a workout with PACE targets into the Wahoo app, since only
pace-target intervals drive the KICKR RUN's belt. That gives us something to run
through the DIRCON proxy so the speed command can be captured — and it is a
reasonable way to build interval sessions in its own right.

Setup (once):
  1. Create an app at https://developers.wahooligan.com/applications
     Redirect URI:  http://localhost:8080/callback
     Scopes:        user_read workouts_read workouts_write plans_read plans_write
  2. export WAHOO_CLIENT_ID=...
     export WAHOO_CLIENT_SECRET=...

Usage:
  python3 scripts/wahoo_push_plan.py --paces 4,5,6,7 --minutes 1.5
  python3 scripts/wahoo_push_plan.py --paces 5,6.5,8 --minutes 2 --name "Capture run"

Paces are in mph. Each becomes one interval. Four distinct values is ideal for
decoding: it makes the speed field obvious and lets the encoding be derived
rather than guessed.

The OAuth token is cached in .wahoo_token.json (gitignored). Credentials are
never written to disk by this script.
"""

import argparse
import base64
import http.server
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

AUTH = "https://api.wahooligan.com/oauth/authorize"
TOKEN = "https://api.wahooligan.com/oauth/token"
API = "https://api.wahooligan.com/v1"
SCOPES = "user_read workouts_read workouts_write plans_read plans_write"
REDIRECT = "http://localhost:8080/callback"
TOKEN_FILE = Path(".wahoo_token.json")

CLIENT_ID = os.environ.get("WAHOO_CLIENT_ID")
CLIENT_SECRET = os.environ.get("WAHOO_CLIENT_SECRET")

_code = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(q)
        if "code" in params:
            _code["code"] = params["code"][0]
            body = b"<h2>Authorised.</h2><p>You can close this tab.</p>"
        else:
            body = b"<h2>No code received.</h2><pre>" + q.encode() + b"</pre>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"{url} failed: {e.code}\n{e.read().decode()}")


def api_post(path, fields, token):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(f"{API}{path}", data=data, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"POST {path} -> {e.code}\n{e.read().decode()}")
        return None


def authorise():
    if TOKEN_FILE.exists():
        cached = json.loads(TOKEN_FILE.read_text())
        if cached.get("expires_at", 0) > time.time() + 60:
            return cached["access_token"]
        if cached.get("refresh_token"):
            print("refreshing token…")
            t = post_form(TOKEN, {
                "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                "grant_type": "refresh_token", "refresh_token": cached["refresh_token"],
                "redirect_uri": REDIRECT,
            })
            return save_token(t)

    srv = http.server.HTTPServer(("localhost", 8080), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    url = f"{AUTH}?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
        "scope": SCOPES, "response_type": "code",
    })
    print("Opening browser to authorise…")
    print(f"If it does not open, visit:\n{url}\n")
    webbrowser.open(url)

    for _ in range(300):
        if "code" in _code:
            break
        time.sleep(1)
    srv.shutdown()
    if "code" not in _code:
        sys.exit("timed out waiting for authorisation")

    t = post_form(TOKEN, {
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "code": _code["code"], "grant_type": "authorization_code",
        "redirect_uri": REDIRECT,
    })
    return save_token(t)


def save_token(t):
    t["expires_at"] = time.time() + t.get("expires_in", 7200)
    TOKEN_FILE.write_text(json.dumps(t, indent=1))
    print(f"token saved to {TOKEN_FILE}")
    return t["access_token"]


def build_plan(paces_mph, minutes, name, threshold_mps):
    """Targets are expressed as a fraction of the header's threshold_speed."""
    intervals = []
    for i, mph in enumerate(paces_mph):
        mps = mph * 0.44704
        frac = mps / threshold_mps
        intervals.append({
            "name": f"{mph:g} mph",
            "exit_trigger_type": "time",
            "exit_trigger_value": int(minutes * 60),
            "intensity_type": 1,
            "targets": [{"type": "threshold_speed",
                         "low": round(frac, 4), "high": round(frac, 4)}],
        })
    return {
        "header": {
            "name": name,
            "version": "1.0.0",
            "description": "Pace intervals",
            "workout_type_family": 1,      # running
            "workout_type_location": 0,    # indoor
            "threshold_speed": threshold_mps,
        },
        "intervals": intervals,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paces", default="4,5,6,7", help="comma-separated mph")
    ap.add_argument("--minutes", type=float, default=1.5, help="minutes per interval")
    ap.add_argument("--name", default="Pace capture")
    ap.add_argument("--threshold", type=float, default=3.0,
                    help="threshold speed in m/s used as the target reference")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and stop")
    args = ap.parse_args()

    paces = [float(p) for p in args.paces.split(",")]
    plan = build_plan(paces, args.minutes, args.name, args.threshold)

    print(json.dumps(plan, indent=1))
    for p in paces:
        print(f"  {p:g} mph = {p * 1.609344:.2f} km/h = {60 / p:.2f} min/mile")
    if args.dry_run:
        return

    if not CLIENT_ID or not CLIENT_SECRET:
        sys.exit("Set WAHOO_CLIENT_ID and WAHOO_CLIENT_SECRET first (see docstring).")

    token = authorise()

    blob = base64.b64encode(json.dumps(plan).encode()).decode()
    result = api_post("/plans", {
        "plan[file]": f"data:application/json;base64,{blob}",
        "plan[filename]": "pace_intervals.json",
        "plan[external_id]": f"RUN_{int(time.time())}",
        "plan[provider_updated_at]": datetime.now(timezone.utc).isoformat(),
    }, token)

    if not result:
        sys.exit("plan upload failed — see the response above")
    print(f"\nplan uploaded, id={result.get('id')}")

    workout = api_post("/workouts", {
        "workout[name]": args.name,
        "workout[workout_type_id]": 5,      # running treadmill
        "workout[starts]": datetime.now(timezone.utc).isoformat(),
        "workout[minutes]": int(len(paces) * args.minutes) or 1,
        "workout[plan_id]": result.get("id"),
    }, token)
    if workout:
        print(f"workout scheduled, id={workout.get('id')}")
        print("\nIt should now appear in the Wahoo app under today's planned workouts.")
    else:
        print("\nPlan uploaded but scheduling failed — it may still be in the app's "
              "plan library, check there.")


if __name__ == "__main__":
    main()
