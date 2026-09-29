# devhunt PoC: voting after the launch window ended

`public.toggleProductVote` only checks for **future** launches (`launch_date > NOW()`); it does
**not** enforce the **end** of the launch window (`launch_end`). The browser blocks votes after
the window closed purely client-side ("Voting has ended") — the RPC still accepts them. A
signed-in user can call the RPC directly and retroactively add or remove their own vote on
**finished** launches.

**Impact** (±1 vote per account, both directions):

| Surface | Time filter? | Reaction |
|---|---|---|
| `votes_count` (tool pages, cards, lists) | no | jumps immediately |
| `weekly_winners` → past-winners list on the homepage | no | historical weekly winner flips retroactively |
| `get_prev_launch_weeks` (sorts `votes_count DESC`) → winner cron emails | no | flips if the bump lands before the cron runs |
| `winner_of_the_day/week/month` → badges, `product_week_ranks` | yes (`created_at BETWEEN launch_start AND launch_end`) | only affected by vote **removal** |

## Running

Log in on devhunt.org, copy your access token (DevTools → Network → any `supabase.co` request →
`Authorization: Bearer …`), then:

```bash
./poc_vote_after_launch_end.py --token <access-jwt>          # target: most recently closed launch
./poc_vote_after_launch_end.py --token <access-jwt> --product-id <id>
```

The PoC toggles one vote and toggles it back (self-healing). Exit codes: `0` vulnerable,
`2` patched, `1` error.

Expected output (vulnerable):

```
[1] Target: <name> (id=123, launch_end=2026-09-22T22:00:00+00:00, votes_count=52)
[2] VULNERABLE: server accepted a vote AFTER the window closed (votes_count 52 -> 53)
[3] Restore: second toggle -> votes_count 52 (expected 52) OK
```

## The fix

`fix_vote_after_launch_end.sql` — replaces `toggleProductVote` so it raises `42501 voting
closed` once the window ended. Run it in the Supabase SQL editor (or add it to the repo as a
migration), then re-run the PoC (expect exit code 2).

Optional hardening (separate decision, changes semantics): `weekly_winners` and
`get_prev_launch_weeks` count/sort **all** votes with no time filter. Restricting them to
in-window votes (the way `winner_of_the_day` does it) would make them robust too — but it may
change historically displayed numbers.
