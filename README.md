# Rev's Casino — Team Runner

Automation for the TAMU Datathon 2026 **Rev's Casino** challenge
(https://td-revs.vercel.app/), a 6-level prompt-puzzle game where the goal is to
clear each level using the **fewest tokens**. This package clears levels 1–5 with
fixed, pre-tuned messages, then spends the remaining budget guessing the level-6
vault code.

**Scores only count on the account that is signed in.** Each teammate must sign
in with their **own** Google account and run this on their own machine (or their
own browser session). Running it on someone else's account does nothing for your
score.

> Contest deadline: about **noon CST, Sunday Nov 8 2026**. Do not submit anything
> through automation; the runner only plays, it does not submit.

## Coordinator browser (no CDP)

`python -m uiadapt` is the opt-in planner for a browser this VM cannot attach to.
It reads `scripts/team_prompts.json`, writes one visible UI action as JSON, and
accepts an observed result file. It does not launch a browser, attach CDP, or
call the game. `scripts/team_runner.py` remains the legacy CDP path; do not point
it at the coordinator's signed-in browser. The planner leaves Slots alone and
does not guess level-6 codes. See `python -m uiadapt schema`.

## 1. Prerequisites

- Linux or macOS, Python 3.10+.
- [uv](https://docs.astral.sh/uv/) (recommended) or plain `venv`.
- Google Chrome / Chromium via Playwright.

## 2. Install

```bash
uv sync                      # or: python -m venv .venv && .venv/bin/pip install -e .
.venv/bin/playwright install chromium
```

## 3. Prepare the browser

The runner does **not** log in for you. It attaches to an already-running
Chromium over the Chrome DevTools Protocol (CDP) on `127.0.0.1:9333`.

```bash
./browser/start.sh           # launches headed Chromium with CDP on :9333
```

If Chromium aborts with **"No usable sandbox"** (common on hardened Linux after a
reboot), relaunch with the sandbox disabled:

```bash
CHROME_FLAGS="--no-sandbox --disable-setuid-sandbox" ./browser/start.sh
```

Then, **in that Chromium window**, go to https://td-revs.vercel.app/ and sign in
with **your own Google account**. Leave the window open.

Verify CDP is up:

```bash
curl -s http://127.0.0.1:9333/json/version
```

See [docs/BROWSER.md](docs/BROWSER.md) for details and the manual launch command.

## 4. Run

```bash
export CASINO_CDP=http://127.0.0.1:9333
.venv/bin/python -u scripts/team_runner.py
```

It clears L1–L5 (one message per attempt, reset-and-retry on a miss, each clear
kept inside its quality band), then guesses the L6 vault code until you stop it or
the daily message limit is hit.

### Flags

| Flag | Meaning |
|------|---------|
| `--from-level N` | Start clearing from level N. |
| `--skip-clear` / `--moonshot-only` | Skip L1–L5, go straight to L6 guessing. |
| `--max-l5-tries N` | Cap L5 hallway attempts. |
| `--max-simple-tries N` | Cap retries per simple level. |
| `--max-guesses N` | Cap L6 guesses this run. |
| `--gap SECONDS` | Delay between messages (respects the server's rate limit). |
| `--cdp URL` | Override `CASINO_CDP`. |

See [docs/RUNNER.md](docs/RUNNER.md) for the level-by-level behavior and expected
token bands.

## 5. AI agent / Grok Build wiring

**The runner makes no model calls.** L1–L4 prompts and the L5 cashier template are
fixed strings in `scripts/team_prompts.json`. L5 still scrapes the four-digit code
from the cashier reply when the hallway is right. L6 is a numeric guess loop.
There is no LLM in the decision path — no API key, no Grok Build or OpenCode
instance needed for it to run.

If a teammate wants their coding agent (Grok Build, OpenCode, etc.) to **operate
or monitor** the runner, point it at this repo and let it run the shell commands
above and read `runs/team-runner.log`. The optional `mcp_server.py` (if present)
exposes the harness over MCP for an agent to drive; it needs no secrets. Do not
commit any API keys.

See [docs/AGENTS.md](docs/AGENTS.md).

## 6. Troubleshooting

- **CDP connection refused**: the browser isn't running or isn't on `:9333`. Re-run `./browser/start.sh` and re-check `/json/version`.
- **"No usable sandbox"**: relaunch with `--no-sandbox` (section 3).
- **After a reboot**: re-run `./browser/start.sh`, then confirm you are still signed in to td-revs in that window.
- **Rate limit / "Wait 1s between messages"**: the runner backs off automatically; raise `--gap` if it persists.
- **Daily message limit**: resets at **midnight CT**, not after the stated retry window. When hit, the runner stops; resume after reset.
- **Never run two instances against one account** — they share the message budget and trip the rate limit.
