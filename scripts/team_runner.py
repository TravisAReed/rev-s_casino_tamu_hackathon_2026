#!/usr/bin/env python3
"""Teammate practice runner: clear levels 1-5 with locked prompts, then vault guessing.

Uses the already-signed-in Chromium on CASINO_CDP (default http://127.0.0.1:9333).
One chat message per attempt on levels 1-5; reset and resend on a miss.
Level 5 uses the cashier-only route (skip host and pit boss).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import uuid
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from casino import core, ui  # noqa: E402
from casino.store import timestamp  # noqa: E402

PROMPTS_PATH = Path(__file__).with_name("team_prompts.json")
LOG = ROOT / "runs/team-runner.log"
L5_LOG = ROOT / "runs/leads/L5-team-skip.jsonl"
GUESS_TRIED = ROOT / "runs/leads/L6-guess-tried.json"
GUESS_LOG = ROOT / "runs/leads/L6-guess.jsonl"

HALLS = ["Marigold", "Vermilion", "Obsidian", "Juniper", "Larkspur", "Cinnabar"]
PHRASES = [
    "paper falcon",
    "velvet owl",
    "amber lantern",
    "copper tide",
    "quiet harbor",
    "silver orchard",
]
RANKS = "A 2 3 4 5 6 7 8 9 10 J Q K".split()


def load_prompts():
    return json.loads(PROMPTS_PATH.read_text())


def log(obj):
    obj = dict(obj)
    obj.setdefault("at", timestamp())
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    print(json.dumps(obj, ensure_ascii=False)[:700], flush=True)


def sleep_retry(resp, default=3.0):
    """Honor Retry-After when the page/API returns a rate limit."""
    ra = None
    if isinstance(resp, dict):
        ra = resp.get("retry_after") or resp.get("retryAfter")
        err = str(resp.get("error") or resp.get("message") or "")
        m = re.search(r"try again in (\d+)", err, re.I)
        if m and ra is None:
            ra = m.group(1)
    sec = default
    if ra is not None:
        try:
            sec = float(ra)
        except (TypeError, ValueError):
            try:
                dt = parsedate_to_datetime(str(ra))
                sec = max(1.0, (dt.timestamp() - time.time()))
            except Exception:
                sec = default
    # Cap multi-hour waits in clear mode so the teammate sees the block.
    if sec > 120:
        log(dict(kind="daily_or_long_limit", sleep_s=sec, note="stop clear; resume later"))
        return False
    time.sleep(sec)
    return True


def with_backoff(fn, *, level, operation, retries=5):
    last = None
    for i in range(retries):
        last = fn()
        status = (last or {}).get("status")
        if status in ("complete", "ok", True) or (last or {}).get("ok") is True:
            return last
        err = str((last or {}).get("error") or (last or {}).get("message") or "")
        if "429" in err or "rate" in err.lower() or "too fast" in err.lower() or "message limit" in err.lower():
            if not sleep_retry(last, default=2.0 + i):
                return last
            continue
        if status == "complete" or (last or {}).get("cleared") is not None:
            return last
        time.sleep(0.5 + 0.25 * i)
    return last


def send_once(level, text):
    return with_backoff(lambda: core.send_message(level, text), level=level, operation="send")


def reset(level):
    return with_backoff(lambda: ui.reset_level(level), level=level, operation="reset")


def band(level):
    b = load_prompts().get("bands", {}).get(str(level))
    return (b[0], b[1]) if b else (0, 10**9)


def good_enough(level, tokens):
    lo, hi = band(level)
    return tokens is not None and int(tokens) <= hi


def clear_simple(level, text, *, max_tries=8):
    """One locked message per attempt; reset and resend on miss. No chat follow-ups."""
    best_over = None
    for attempt in range(1, max_tries + 1):
        r = reset(level)
        if (r or {}).get("status") != "complete":
            log(dict(kind="reset_fail", level=level, attempt=attempt, result=r))
            continue
        last = send_once(level, text)
        tokens = (last or {}).get("tokens_current_attempt") or (last or {}).get("tokens")
        cleared = (last or {}).get("cleared") is True
        log(dict(kind="clear_try", level=level, attempt=attempt, text=text,
                 tokens=tokens, cleared=cleared, reply=(last or {}).get("response")))
        if cleared and good_enough(level, tokens):
            return dict(ok=True, won=True, tokens=tokens, level=level, attempts=attempt)
        if cleared:
            log(dict(kind="over_band", level=level, tokens=tokens, band=band(level)))
            best_over = tokens if best_over is None else min(best_over, tokens)
        # L1-L4: if not cleared, do not follow up — just retry.
    if best_over is not None:
        return dict(ok=True, won=True, in_band=False, tokens=best_over, level=level)
    return dict(ok=False, won=False, level=level, why="max_tries")


def infer_rank(reply: str):
    """Pick a blackjack rank from a short dealer hint. Prefer royalty / ten-value."""
    t = (reply or "").lower()
    if "king" in t or re.search(r"\bk\b", t):
        return "K"
    if "queen" in t or re.search(r"\bq\b", t):
        return "Q"
    if "jack" in t or re.search(r"\bj\b", t):
        return "J"
    if "ace" in t or re.search(r"\ba\b", t):
        return "A"
    if "royalt" in t or "face" in t or "court" in t:
        return random.choice(["J", "Q", "K"])
    if "10" in t or "ten" in t or "worth 10" in t:
        return "10"
    m = re.search(r"\b([2-9]|10|j|q|k|a)\b", t)
    if m:
        return m.group(1).upper()
    return random.choice(["J", "Q", "K", "10"])


def clear_blackjack(text="one word", *, max_tries=12):
    """Send one message, then UI-pick a rank. Wrong card → reset + resend. No chat follow-ups."""
    for attempt in range(1, max_tries + 1):
        r = reset(3)
        if (r or {}).get("status") != "complete":
            continue
        last = send_once(3, text)
        reply = (last or {}).get("response") or ""
        if (last or {}).get("cleared") is True:
            return dict(ok=True, won=True, tokens=(last or {}).get("tokens_current_attempt"),
                        level=3, attempts=attempt, reply=reply)
        rank = infer_rank(reply)
        action = with_backoff(
            lambda: ui.perform_action(3, "lock_in_rank", {"rank": rank}),
            level=3, operation="lock_in_rank",
        )
        cleared = (action or {}).get("cleared") is True or (action or {}).get("status") == "complete" and (action or {}).get("cleared")
        # Re-check progress in case cleared flag lives on action differently
        if (action or {}).get("cleared") is True:
            cleared = True
        tokens = (last or {}).get("tokens_current_attempt") or (last or {}).get("tokens")
        log(dict(kind="bj_try", attempt=attempt, text=text, reply=reply, rank=rank,
                 tokens=tokens, action=action, cleared=cleared))
        if cleared and good_enough(3, tokens):
            return dict(ok=True, won=True, tokens=tokens, level=3, attempts=attempt, rank=rank)
        if cleared:
            log(dict(kind="over_band", level=3, tokens=tokens, band=band(3)))
        # Miss: next loop resets.
    return dict(ok=False, won=False, level=3, why="max_tries")


def clear_l5_double_skip(*, max_hall_tries=18):
    """Guess hallway → one cashier message → try all six phrases at the door."""
    prompts = load_prompts()["5"]
    halls = list(prompts.get("hallways") or HALLS)
    phrases = list(prompts.get("phrases") or PHRASES)
    template = prompts.get("cashier") or "{hallway} code no other words"
    random.shuffle(halls)

    for attempt, hallway in enumerate(halls * ((max_hall_tries // len(halls)) + 1), start=1):
        if attempt > max_hall_tries:
            break
        text = template.format(hallway=hallway, hall=hallway, halll=hallway.lower())
        r = reset(5)
        if (r or {}).get("status") != "complete":
            log(dict(kind="l5_reset_fail", attempt=attempt, result=r))
            continue
        selected = ui.select_staff(5, "cashier")
        if selected.get("verified") is not True:
            log(dict(kind="l5_staff_fail", attempt=attempt, result=selected))
            continue
        last = send_once(5, text)
        reply = (last or {}).get("response") or ""
        tokens = (last or {}).get("tokens_current_attempt") or (last or {}).get("tokens")
        if (last or {}).get("cleared") is True:
            log(dict(kind="l5_cleared_on_cashier", hallway=hallway, tokens=tokens, reply=reply))
            return dict(ok=True, won=True, tokens=tokens, hallway=hallway)
        codes = set(re.findall(r"\b\d{4}\b", reply))
        if len(codes) != 1:
            log(dict(kind="l5_no_code", attempt=attempt, hallway=hallway, reply=reply, tokens=tokens))
            L5_LOG.parent.mkdir(parents=True, exist_ok=True)
            with L5_LOG.open("a") as f:
                f.write(json.dumps(dict(hallway=hallway, won=False, why="no_code", reply=reply, at=timestamp())) + "\n")
            continue
        code = next(iter(codes))
        for phrase in phrases:
            action = with_backoff(
                lambda p=phrase: ui.perform_action(
                    5, "try_the_door",
                    {"hallway": hallway, "code": code, "phrase": p},
                ),
                level=5, operation="try_the_door",
            )
            won = (action or {}).get("cleared") is True
            log(dict(kind="l5_door", hallway=hallway, code=code, phrase=phrase,
                     tokens=tokens, won=won, action_status=(action or {}).get("status")))
            if won and not good_enough(5, tokens):
                log(dict(kind="over_band", level=5, tokens=tokens, band=band(5)))
                break
            if won:
                L5_LOG.parent.mkdir(parents=True, exist_ok=True)
                with L5_LOG.open("a") as f:
                    f.write(json.dumps(dict(
                        hallway=hallway, code=code, phrase=phrase,
                        tokens=tokens, won=True, at=timestamp(),
                    )) + "\n")
                return dict(ok=True, won=True, tokens=tokens, hallway=hallway, code=code, phrase=phrase)
        L5_LOG.parent.mkdir(parents=True, exist_ok=True)
        with L5_LOG.open("a") as f:
            f.write(json.dumps(dict(
                hallway=hallway, code=code, won=False, why="phrases_exhausted",
                tokens=tokens, at=timestamp(),
            )) + "\n")
    return dict(ok=False, won=False, why="max_hall_tries")


def load_tried():
    if GUESS_TRIED.exists():
        try:
            return set(json.loads(GUESS_TRIED.read_text()))
        except Exception:
            return set()
    return set()


def claim_guess():
    import fcntl

    GUESS_TRIED.parent.mkdir(parents=True, exist_ok=True)
    with (ROOT / ".guess.lock").open("a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        tried = load_tried()
        rem = [f"{i:04d}" for i in range(10000) if f"{i:04d}" not in tried]
        if not rem:
            return None
        g = random.choice(rem)
        tried.add(g)
        GUESS_TRIED.write_text(json.dumps(sorted(tried)))
        return g


def run_moonshot(*, gap=None, max_guesses=None):
    """Vault guessing via the existing signed-in page (raw fetch in-page)."""
    from playwright.sync_api import sync_playwright

    gap = float(gap if gap is not None else os.environ.get("L6_RAW_GAP_SEC", "7.5"))
    gap_min = float(os.environ.get("L6_RAW_GAP_MIN", "0.8"))
    gap_step = float(os.environ.get("L6_RAW_GAP_STEP", "0.25"))
    gap_backoff = float(os.environ.get("L6_RAW_GAP_BACKOFF", "1.5"))
    cdp = os.environ.get("CASINO_CDP", "http://127.0.0.1:9333")
    js = """
    async ({ guess }) => {
      const resetResp = await fetch('/api/level/6/reset', { method: 'POST', credentials: 'include' });
      const resetText = await resetResp.text();
      let resetBody; try { resetBody = JSON.parse(resetText); } catch (e) { resetBody = { raw: resetText }; }
      const msgResp = await fetch('/api/level/6/message', {
        method: 'POST', credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ message: String(guess) }),
      });
      const msgText = await msgResp.text();
      let msgBody; try { msgBody = JSON.parse(msgText); } catch (e) { msgBody = { raw: msgText }; }
      return {
        ok: msgResp.ok, status: msgResp.status,
        resetStatus: resetResp.status,
        retryAfter: msgResp.headers.get('retry-after'),
        resetBody, msgBody,
      };
    }
    """
    n = 0
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp, timeout=15000)
        context = browser.contexts[0]
        page = None
        for cand in context.pages:
            if "td-revs.vercel.app" in (cand.url or ""):
                page = cand
                break
        if page is None:
            page = context.new_page()
            page.goto("https://td-revs.vercel.app/level/6", wait_until="networkidle", timeout=30000)
        else:
            if "/level/6" not in page.url:
                page.goto("https://td-revs.vercel.app/level/6", wait_until="networkidle", timeout=30000)

        while True:
            if max_guesses is not None and n >= max_guesses:
                log(dict(kind="moonshot_stop", why="max_guesses", n=n))
                return dict(ok=True, won=False, guesses=n)
            guess = claim_guess()
            if guess is None:
                log(dict(kind="moonshot_stop", why="exhausted"))
                return dict(ok=True, won=False, guesses=n)
            try:
                result = page.evaluate(js, {"guess": guess})
            except Exception as e:
                log(dict(kind="moonshot_eval_error", guess=guess, error=str(e)[:200]))
                time.sleep(gap)
                continue
            n += 1
            status = result.get("status")
            body = result.get("msgBody") or {}
            won = bool(body.get("won") or body.get("cleared") or body.get("success"))
            reply = body.get("response") or body.get("message") or body.get("raw")
            GUESS_LOG.parent.mkdir(parents=True, exist_ok=True)
            with GUESS_LOG.open("a") as f:
                f.write(json.dumps(dict(
                    guess=guess, status=status, won=won, reply=reply,
                    at=timestamp(), source="team_runner",
                )) + "\n")
            log(dict(kind="moonshot_guess", guess=guess, status=status, won=won,
                     reply=str(reply)[:160] if reply else None, gap=gap, n=n))
            if won:
                return dict(ok=True, won=True, guess=guess, guesses=n)
            if status == 429:
                ra = result.get("retryAfter")
                try:
                    wait = float(ra) if ra is not None else gap * gap_backoff
                except ValueError:
                    wait = gap * gap_backoff
                if wait > 3600:
                    log(dict(kind="moonshot_daily_limit", retry_after=wait, guess=guess))
                    time.sleep(min(wait, 77000))
                else:
                    gap = min(30.0, max(gap * gap_backoff, gap + gap_step))
                    time.sleep(wait)
                continue
            if status == 200:
                gap = max(gap_min, gap - gap_step * 0.25)
            time.sleep(gap)


def clear_all(*, from_level=1, max_l5_tries=18, max_simple=10):
    prompts = load_prompts()
    results = {}
    if from_level <= 1:
        results[1] = clear_simple(1, prompts["1"], max_tries=max_simple)
        if not results[1].get("won"):
            return results
    if from_level <= 2:
        results[2] = clear_simple(2, prompts["2"], max_tries=max_simple)
        if not results[2].get("won"):
            return results
    if from_level <= 3:
        results[3] = clear_blackjack(prompts["3"], max_tries=max(12, max_simple))
        if not results[3].get("won"):
            return results
    if from_level <= 4:
        results[4] = clear_simple(4, prompts["4"], max_tries=max_simple)
        if not results[4].get("won"):
            return results
    if from_level <= 5:
        results[5] = clear_l5_double_skip(max_hall_tries=max_l5_tries)
    return results


def main():
    ap = argparse.ArgumentParser(description="Clear L1-L5 then vault-guess for teammate practice accounts")
    ap.add_argument("--from-level", type=int, default=1)
    ap.add_argument("--skip-clear", action="store_true", help="Skip L1-L5 and only run vault guessing")
    ap.add_argument("--moonshot-only", action="store_true", help="Alias for --skip-clear")
    ap.add_argument("--max-l5-tries", type=int, default=18)
    ap.add_argument("--max-simple-tries", type=int, default=10)
    ap.add_argument("--max-guesses", type=int, default=None)
    ap.add_argument("--gap", type=float, default=None)
    ap.add_argument("--cdp", default=None, help="Override CASINO_CDP")
    args = ap.parse_args()
    if args.cdp:
        os.environ["CASINO_CDP"] = args.cdp
        # core module captured CDP at import; set env before moonshot path uses it.
        core.CDP = args.cdp

    log(dict(kind="team_runner_start", from_level=args.from_level,
             skip_clear=args.skip_clear or args.moonshot_only,
             cdp=os.environ.get("CASINO_CDP", core.CDP)))

    if not (args.skip_clear or args.moonshot_only):
        results = clear_all(
            from_level=args.from_level,
            max_l5_tries=args.max_l5_tries,
            max_simple=args.max_simple_tries,
        )
        log(dict(kind="clear_done", results=results))
        if not results.get(5, {}).get("won") and args.from_level <= 5:
            log(dict(kind="stop_before_moonshot", why="L5 not cleared", results=results))
            raise SystemExit(2)
        # If starting mid-run past L5, still moonshot.
        if args.from_level <= 5 and not results.get(5, {}).get("won"):
            raise SystemExit(2)

    moon = run_moonshot(gap=args.gap, max_guesses=args.max_guesses)
    log(dict(kind="moonshot_done", result=moon))
    raise SystemExit(0 if moon.get("won") or moon.get("ok") else 1)


if __name__ == "__main__":
    main()
