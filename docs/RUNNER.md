# Runner behavior

`scripts/team_runner.py` attaches to the CDP browser and plays the game in order.

## Levels 1–5

One **fixed** chat message per attempt, from `scripts/team_prompts.json`. On a
miss it resets the level and resends. No follow-up messages are sent on L1–L5.
Level 5 uses the cashier-only route (it skips the host and the pit boss): guess a
hallway, send one cashier message, then try the door phrases (door tries are
free).

Each clear is kept only if its token count lands inside that level's quality band.
An over-band clear is retried; if none lands in band, the lowest clear is used.

### Expected token bands

See the `bands` object in `scripts/team_prompts.json`. As tuned:

| Level | Band (tokens) |
|-------|---------------|
| 1 | 354–390 |
| 2 | 447–480 |
| 3 | 255–300 |
| 4 | 421–450 |
| 5 | 249–300 |

## Level 6

The vault code is one of 10,000 values. After the badge-opener step, the runner
submits numeric guesses one per message, tracking tried values in
`runs/leads/L6-guess-tried.json` and logging to `runs/leads/L6-guess.jsonl`. It
honors the server's per-message rate limit and the daily message cap, and stops
when the cap is hit. A single account only gets a few hundred guesses a day, so
run it on every eligible account to raise the odds.

## Logs

- `runs/team-runner.log` — overall progress.
- `runs/leads/L5-team-skip.jsonl` — L5 attempts.
- `runs/leads/L6-guess.jsonl` — every L6 guess and result.
