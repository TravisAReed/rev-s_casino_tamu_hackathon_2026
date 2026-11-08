# revs-casino Phase 1 — Persistent Playwright Chromium Harness

One long-lived **headed** Chromium with a persistent profile, exposing CDP on
`127.0.0.1:9333` (loopback only). Attaching tools connect over CDP, open a
**new tab** in the existing persistent context, and close only their own tab.

## Layout

| Path | Purpose |
|---|---|
| `browser/start.sh` | Safe launcher: reuse alive browser, reject foreign listener on 9333, no parallel launches, display gate, loopback-only bind check |
| `browser/chromium_launcher.sh` | Internal launcher exec'd by start.sh (tmux or setsid) — the browser runs independently of any attaching harness |
| `browser/status.sh` | Read-only status: display, browser PID/bind, CDP reachability, tmux, install health. No auth values |
| `browser/attach_check.py` | Playwright `connect_over_cdp` attach: new tab, opens the casino entrance, reports sanitized URL/title/sign-in status; read-only passive crawl only when signed in |
| `browser/pwharness.py` | Pure helpers + tiny CLI (`listener` / `display` / `cdp`) used by the shell scripts |
| `tests/test_pwharness.py` | Unit tests of the pure helpers |
| `tests/shell_checks.sh` | Static safety + install checks |
| `.pw-profile/` | Persistent Chromium user-data dir (gitignored) |
| `.venv/`, `.cache/ms-playwright/` | Local Python env and Chromium build (gitignored) |
| `docs/phase1-status.md` | Current status, blocker, next commands |

## Quickstart (requires an alexn desktop session)

```bash
cd /home/alexn/Documents/code/datathon_2026/revs-casino
./browser/start.sh                     # launch (or reuse) the shared headed Chromium
./browser/status.sh                    # show PID, bind, CDP, install health
.venv/bin/python browser/attach_check.py   # attach: new tab, entrance, report
.venv/bin/python browser/attach_check.py   # run again: same PID + profile (proof)
```

`start.sh` opens `https://td-revs.vercel.app/casino/entrance` in the shared
browser. The browser lives in tmux session `revs-casino-browser`
(`tmux attach-session -t revs-casino-browser` to see it) and keeps running
after start.sh exits. `attach_check.py` never closes the shared browser; it
closes only its own tab and disconnects (documented Playwright CDP behavior).
Every attach appends one line to `browser/attach.log` with the browser PID and
profile, so repeated runs prove the same browser is reused.

## Safety rules enforced by this harness

- CDP bound to `127.0.0.1:9333` only; post-launch verification kills the
  browser if it ever binds non-loopback.
- Reuse of an already-running browser; unrelated listeners on 9333 are
  rejected; `flock` prevents parallel launches.
- Headed only. If no alexn graphical session exists, start.sh fails (exit 3)
  with instructions. GDM-owned displays are never touched; xhost is never
  broadened; no VNC/virtual display is installed; no headless substitute.
- attach_check.py: URL reported without query strings/fragments, emails
  redacted, no cookie/localStorage reads, no page HTML dumps, no clicks, no
  form fills, no Google sign-in automation. If not signed in it stops
  immediately.
- Crawl (only when signed in and not explicitly blocked) is strictly passive:
  visible-element selectors, requests observed during the page load, and
  static analysis of already-downloaded public JS bundles. No messages are
  sent, no game actions, no spend, no challenge attempts, no authenticated
  HTTP API replays.
- Outputs contain no auth values. Logs are gitignored.

## Exit codes (start.sh)

| Code | Meaning |
|---|---|
| 0 | Browser running (launched or reused) |
| 2 | Foreign listener on 9333, or parallel launch attempt |
| 3 | No usable alexn desktop display (log into the tower desktop, re-run) |
| 4 | Local venv / Chromium install missing |
| 5 | Loopback-only bind violation (browser terminated) |
| 6 | Browser started but CDP did not come up / post-launch check failed |

## Install (already done in this folder)

```bash
python3 -m venv .venv
PIP_CACHE_DIR="$PWD/.cache/pip" .venv/bin/pip install playwright
PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/ms-playwright" .venv/bin/python -m playwright install chromium
```

## Verification

```bash
bash tests/shell_checks.sh
.venv/bin/python -m unittest discover -s tests -t . -v
```
