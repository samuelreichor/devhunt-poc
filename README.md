# devhunt PoC: voting after the launch window ended

## The vulnerability

`public.toggleProductVote` only checks for future launches (`launch_date > NOW()`); it does
**not** enforce the **end** of the launch window (`launch_end`) server-side. The browser
blocks votes after the window closed purely client-side ("Voting has ended"). A signed-in
user can call the RPC directly and retroactively add or remove their own vote on
**finished** launches.

**Impact** (launch_end bypass, ±1 vote per account, both directions):

| Surface | Time filter? | Reaction |
|---|---|---|
| `votes_count` (tool pages, cards, lists) | no | jumps immediately |
| `weekly_winners` → past-winners list on the homepage | no | historical weekly winner flips retroactively |
| `get_prev_launch_weeks` (sorts `votes_count DESC`) → winner cron emails (congrats mails, top-3 newsletter) | no | flips if the bump lands **before** the cron runs |
| `winner_of_the_day/week/month` → badges, `product_week_ranks` | yes (`created_at BETWEEN launch_start AND launch_end`) | only affected by vote **REMOVAL**, not addition |

## Files

| File | Purpose |
|---|---|
| `poc_vote_after_launch_end.py` | PoC **and** regression test after the fix. Self-healing: toggles the own vote back and forth, restores the initial state. |
| `fix_vote_after_launch_end.sql` | Migration for `toggleProductVote` (raises `42501 voting closed` once the window ended). |

## Running the PoC

1. Log in on devhunt.org and export the cookies (same format as `devhunt.org.cookies.json`: JSON array containing the `sb-*` cookie)
2. Then:

```bash
cd devhunt-poc
./poc_vote_after_launch_end.py --cookie-file ~/Downloads/devhunt.org.cookies.json

# alternatives:
#   ./poc_vote_after_launch_end.py --token <access-jwt>
#   DEVHUNT_ACCESS_TOKEN=<jwt> ./poc_vote_after_launch_end.py
#   ./poc_vote_after_launch_end.py --cookie-file ... --product-id <id>   # own tool as target
```

The default target is the winner of the most recent **closed** week — the realistic attack
scenario (flipping a historical ranking).

## Expected output

**Before the fix** (vulnerable, exit code 0):

```
[3] VULNERABLE — the server ACCEPTED a vote AFTER the window closed ... votes_count now: 53
    -> weekly_winners week 38 reacted IMMEDIATELY: total_upvotes 52 -> 53 (+1)
[4] Restore: second toggle -> votes_count 52 (expected: 52) OK
```

**After the fix** (regression test, exit code 2):

```
[+] PATCHED: server rejects ('voting closed') — the vulnerability is closed.
```

## Applying the fix

1. Supabase Dashboard → SQL Editor → run the contents of `fix_vote_after_launch_end.sql`
   (or add it to the repo as `supabase/migrations/20260929200000_enforce_launch_end_in_vote_rpc.sql`)
2. Re-run the PoC → expect exit code 2

## Optional hardening (separate decision, changes semantics)

`weekly_winners` and `get_prev_launch_weeks` count/sort **all** votes with no time filter.
Restricting them to in-window votes (`created_at BETWEEN launch_start AND launch_end`, the
way `winner_of_the_day` does it) would also make those surfaces robust against stale rows
with wrong timestamps — but it may change historically displayed numbers, so decide
separately.
