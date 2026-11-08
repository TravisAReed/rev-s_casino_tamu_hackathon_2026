"""Opt-in planner: next visible UI action in, observed result file out.

This module does not drive a browser and does not call the casino. It reuses
the committed prompt list in ``scripts/team_prompts.json`` and the visible
control names already documented for the legacy harness. Level 6 numeric
guessing is intentionally absent.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PROMPTS_PATH = ROOT / "scripts" / "team_prompts.json"
CT = ZoneInfo("America/Chicago")
DEADLINE_CT = "2026-11-08T11:45:00-06:00"
SCHEMA = "revs-ui-plan/v1"

SEED_BESTS = {"1": 360, "2": 445, "3": 522, "4": 427, "5": 1046, "6": 3085}
LIMITS = {
    "max_chat_messages": 8,
    "max_replays_per_level": 2,
    "max_cooldown_s": 120,
    "max_door_tries_per_run": 8,
}
HALLWAY = "Cinnabar"
PRIMARY_PHRASE = "amber lantern"
FACE_ORDER = ("K", "Q", "J")
RANK_NAMES = {
    "A": "Ace", "2": "Two", "3": "Three", "4": "Four", "5": "Five", "6": "Six",
    "7": "Seven", "8": "Eight", "9": "Nine", "10": "Ten", "J": "Jack",
    "Q": "Queen", "K": "King",
}
WORD_RANKS = (
    ("king", "K"), ("queen", "Q"), ("jack", "J"), ("ace", "A"), ("ten", "10"),
    ("nine", "9"), ("eight", "8"), ("seven", "7"), ("six", "6"), ("five", "5"),
    ("four", "4"), ("three", "3"), ("two", "2"),
)
FORBIDDEN_KEYS = {
    "cookie", "cookies", "authorization", "password", "set-cookie", "set_cookie",
    "cdp", "session", "sessionid", "proxy-authorization",
}
PLACEHOLDERS = {3: "Ask the dealer…", 5: "Talk to the staff member…"}
LEVEL_NAMES = {3: "Blackjack", 5: "Casino Floor"}

OUTLINE = [
    {
        "id": "verify",
        "authorized": False,
        "text": "Read visible Best for levels 1-6. Click nothing else. A Best higher than the seed stops the plan before any reset.",
    },
    {
        "id": "l3",
        "level": 3,
        "max_chats": 2,
        "max_replays": 2,
        "authorized": False,
        "text": (
            "Per hand: Deal a new hand (or New attempt / Restart plus confirm), "
            "send the repo level-3 prompt once, then lock one inferred rank. "
            "A Royalty/Red hint uses King, then Queen, then Jack across hands. "
            "A wrong lock needs a new hand. A clear with more tokens than the "
            "saved Best still counts as cleared and does not replace that Best."
        ),
    },
    {
        "id": "l5",
        "level": 5,
        "max_chats": 2,
        "max_replays": 2,
        "authorized": False,
        "text": (
            "Keep a single route. Hallway Cinnabar, one Lena message from the "
            "repo cashier template, then at most 8 door tries. The first phrase "
            "is amber lantern; the rest follow scripts/team_prompts.json. "
            "Do not restart between door tries. Restart only when this run cannot "
            "accept a door try. A restart draws a new route; drop the previous code."
        ),
    },
    {
        "id": "leave",
        "authorized": False,
        "text": "Do not open Slots. Do not send level-6 numeric guesses. The known vault clear is the two-message AURELIA badge flow and is outside this bounded plan.",
    },
]


def deadline_at():
    return datetime.fromisoformat(DEADLINE_CT)


def parse_now(value):
    if isinstance(value, datetime):
        now = value
    else:
        now = datetime.fromisoformat(str(value))
    if now.tzinfo is None:
        now = now.replace(tzinfo=CT)
    return now.astimezone(CT)


def load_prompts(path=PROMPTS_PATH):
    data = json.loads(Path(path).read_text())
    halls = data["5"]["hallways"]
    phrases = data["5"]["phrases"]
    if HALLWAY not in halls:
        raise RuntimeError("repo hallway list is missing Cinnabar")
    if PRIMARY_PHRASE not in phrases:
        raise RuntimeError("repo phrase list is missing amber lantern")
    if not isinstance(data.get("3"), str) or not data["3"].strip():
        raise RuntimeError("repo level-3 prompt is missing")
    return data


def score_total(bests):
    if not isinstance(bests, dict):
        return None
    total = 0
    for key in ("1", "2", "3", "4", "5", "6"):
        value = bests.get(key)
        if value is None:
            return None
        total += int(value)
    return total


def chats_left(ledger):
    return ledger["limits"]["max_chat_messages"] - ledger["chat_messages_used"]


def new_ledger(now=None):
    now = parse_now(now or datetime.now(CT))
    return {
        "version": 1,
        "adapter": "uiadapt",
        "schema": SCHEMA,
        "created_at": now.isoformat(),
        "deadline_ct": DEADLINE_CT,
        "limits": dict(LIMITS),
        "seed_bests": dict(SEED_BESTS),
        "seed_total": score_total(SEED_BESTS),
        "verified_bests": None,
        "bests_verified": False,
        "cleared": {str(i): True for i in range(1, 7)},
        "chat_messages_used": 0,
        "replays": {"3": 0, "5": 0},
        "protected_levels": [2],
        "slots_policy": "do_not_replay",
        "team_slots_target": 447,
        "l6_policy": "do_not_numeric_guess",
        "phase": "verify_bests",
        "stop": None,
        "pending_action": None,
        "applied_action_ids": [],
        "l3": {"tried_ranks": [], "last_reply": None, "pending_rank": None},
        "l5": {
            "hallway": HALLWAY,
            "primary_phrase": PRIMARY_PHRASE,
            "code": None,
            "code_available": False,
            "code_ref": None,
            "hallways_tried": [],
            "phrases_tried": [],
            "door_tries": 0,
            "run_anchored": False,
            "improved": False,
        },
        "history": [],
    }


def load_ledger(path):
    ledger = json.loads(Path(path).read_text())
    if ledger.get("version") != 1 or ledger.get("adapter") != "uiadapt":
        raise ValueError("ledger is not a uiadapt v1 ledger")
    return ledger


def save_ledger(path, ledger):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n")


def _as_int(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(label + " must be an integer")
    if value < 0:
        raise ValueError(label + " must be >= 0")
    return value


def _forbidden_key(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).strip().lower() in FORBIDDEN_KEYS:
                return str(key)
            found = _forbidden_key(value)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _forbidden_key(item)
            if found:
                return found
    return None


def infer_rank(reply, tried):
    """Deterministic rank from a dealer reply. No random fallback."""
    text = (reply or "").lower()
    used = set(tried or [])
    for word, code in WORD_RANKS:
        if re.search(r"\b" + word + r"\b", text) and code not in used:
            return code
    if re.search(r"\broyal|\bface\b|\bcourt\b", text):
        for code in FACE_ORDER:
            if code not in used:
                return code
        return None
    match = re.search(r"\b(10|[2-9]|j|q|k|a)\b", text)
    if match:
        code = match.group(1).upper()
        if code not in used:
            return code
    return None


def contains_door_code(text):
    return re.search(r"\b\d{4}\b", str(text or "")) is not None


def extract_code(reply):
    found = re.findall(r"\b\d{4}\b", reply or "")
    if len(found) != 1:
        return None
    return found[0]


def hallway_order(prompts=None):
    """Prefer the previously verified name once, then the remaining repo names."""
    prompts = prompts or load_prompts()
    names = list(prompts["5"]["hallways"])
    if HALLWAY in names:
        return [HALLWAY] + [name for name in names if name != HALLWAY]
    return names


def next_hallway(ledger, prompts=None):
    tried = set(ledger["l5"].get("hallways_tried") or [])
    for name in hallway_order(prompts):
        if name not in tried:
            return name
    return None


def door_phrases(prompts=None):
    prompts = prompts or load_prompts()
    rest = [phrase for phrase in prompts["5"]["phrases"] if phrase != PRIMARY_PHRASE]
    return [PRIMARY_PHRASE] + rest


def next_phrase(ledger, prompts=None):
    tried = set(ledger["l5"]["phrases_tried"])
    for phrase in door_phrases(prompts):
        if phrase not in tried:
            return phrase
    return None


def _past_deadline(now):
    return parse_now(now) >= deadline_at()


def _stop(ledger, reason, **detail):
    ledger["phase"] = "done"
    ledger["stop"] = {"reason": reason, **detail}
    return _stop_action(ledger)


def _stop_action(ledger):
    info = ledger.get("stop") or {"reason": "stopped"}
    return {
        "id": "stop-" + str(info.get("reason") or "stopped"),
        "type": "stop",
        "level": None,
        "counts_as_chat": False,
        "mutates_run": False,
        "reason": info.get("reason"),
        "detail": info,
        "instruction": "Do not click the casino. This plan is stopped.",
    }


def _budget(ledger):
    return {
        "max_chat_messages": ledger["limits"]["max_chat_messages"],
        "chat_messages_used": ledger["chat_messages_used"],
        "chat_messages_remaining": chats_left(ledger),
        "max_replays_per_level": ledger["limits"]["max_replays_per_level"],
        "replays": dict(ledger["replays"]),
        "max_cooldown_s": ledger["limits"]["max_cooldown_s"],
        "max_door_tries_per_run": ledger["limits"]["max_door_tries_per_run"],
        "door_tries_this_run": ledger["l5"]["door_tries"],
        "deadline_ct": ledger["deadline_ct"],
    }


def _base_action(action_id, kind, level, instruction, **extra):
    action = {
        "id": action_id,
        "type": kind,
        "level": level,
        "level_name": LEVEL_NAMES.get(level),
        "counts_as_chat": False,
        "mutates_run": kind in {"new_hand", "restart_level", "send_chat", "lock_rank", "try_door"},
        "instruction": instruction,
        "forbid": ["Slots", "level 2", "level 6 numeric guess", "debugger", "cookie copy"],
    }
    action.update(extra)
    return action


def _observe_bests_action():
    return _base_action(
        "verify-bests",
        "observe_bests",
        None,
        (
            "In the already signed-in casino tab, read the visible Best token "
            "count for levels 1 through 6. Opening a level only to read Best is "
            "allowed. Do not click Send, Restart, New attempt, Deal a new hand, "
            "Lock in, Try the door, or Slots."
        ),
        mutates_run=False,
        expected_saved_bests=dict(SEED_BESTS),
        expected_total=score_total(SEED_BESTS),
    )


def _new_hand_action(ledger):
    attempt = ledger["replays"]["3"] + 1
    return _base_action(
        f"l3-r{attempt}-new-hand",
        "new_hand",
        3,
        (
            "On Blackjack only, start a fresh hand. Click 'Deal a new hand' if it "
            "is visible; otherwise click 'New attempt' or 'Restart', then confirm "
            "with 'Start new attempt' or 'Restart level'. Do not type a chat message."
        ),
        replay_attempt=attempt,
    )


def _send_action(ledger, level, text, action_id):
    return _base_action(
        action_id,
        "send_chat",
        level,
        (
            f"Click the composer whose placeholder is {PLACEHOLDERS[level]!r}. "
            "If it is not empty, do not send; report ok false and notes "
            "composer_not_empty. Otherwise enter exactly the text in this action "
            "and click Send once."
        ),
        text=text,
        composer_placeholder=PLACEHOLDERS[level],
        send_button="Send",
        counts_as_chat=True,
        replay_attempt=ledger["replays"][str(level)] + 1,
    )


def _lock_action(ledger, rank):
    name = RANK_NAMES[rank]
    attempt = ledger["replays"]["3"]
    return _base_action(
        f"l3-r{attempt}-lock-{rank}",
        "lock_rank",
        3,
        (
            f"Click the rank button {name!r}, then click 'Lock in {name}'. "
            "One guess is allowed on this hand. Do not send another chat."
        ),
        rank=rank,
        rank_name=name,
        confirm_button="Lock in " + name,
        replay_attempt=attempt,
    )


def _observe_l5_action(ledger):
    attempt = ledger["replays"]["5"] + 1
    return _base_action(
        f"l5-r{attempt}-observe",
        "observe_level",
        5,
        (
            "Open Casino Floor only to read it. Report whether Try the door is "
            "available, how many tries remain, and whether a restart would be "
            "required before another try. Do not restart, send, or submit the door."
        ),
        replay_attempt=attempt,
        mutates_run=False,
    )


def _restart_l5_action(ledger, prompts):
    attempt = ledger["replays"]["5"] + 1
    upcoming = next_hallway(ledger, prompts)
    refused = ledger["l5"].get("hallways_tried") or []
    instruction = (
        "Restart Casino Floor. Click 'New attempt' or 'Restart', then confirm with "
        "'Start new attempt' or 'Restart level'. This draws a new route. Do not send a chat. "
        "Do not reuse a hallway Lena already refused"
        + (": " + ", ".join(refused) if refused else "")
        + "."
    )
    if upcoming:
        instruction += " The next cashier message will try " + upcoming + ", the next unused repo hallway."
    return _base_action(
        f"l5-r{attempt}-restart",
        "restart_level",
        5,
        instruction,
        replay_attempt=attempt,
        next_hallway=upcoming,
        refused_hallways=list(refused),
        route_rule="A restart starts a new route. Do not assume the previous hallway is still correct.",
    )


def _select_lena_action(ledger):
    attempt = ledger["replays"]["5"] + 1
    return _base_action(
        f"l5-r{attempt}-select-lena",
        "select_staff",
        5,
        "Click Lena the Cage Cashier in the staff roster or chat tabs. Do not send a message yet.",
        staff="cashier",
        name="Lena",
        replay_attempt=attempt,
        mutates_run=False,
    )


def _send_l5_action(ledger, prompts):
    hallway = next_hallway(ledger, prompts)
    if hallway is None:
        return _stop(ledger, "hallways_exhausted")
    text = prompts["5"]["cashier"].format(hallway=hallway)
    action = _send_action(ledger, 5, text, f"l5-r{ledger['replays']['5'] + 1}-send")
    action["instruction"] = (
        "Click the composer whose placeholder is "
        + repr(PLACEHOLDERS[5])
        + ". If it is not empty, do not send; report ok false and notes composer_not_empty. "
        + "Otherwise enter exactly the text in this action and click Send once. "
        + "Keep any door code on this browser only. In the result, set code_available true and "
        + "code_ref to a label with no digits. Do not put the code in reply, notes, or code_ref."
    )
    action["evidence"] = {
        "hallway": hallway,
        "hallway_in_repo": True,
        "hallway_note": "Unused name from scripts/team_prompts.json. A hallway Lena refused is not reused on the next route.",
        "code_note": "Ask Lena once. Leave the digits in the browser. Report code_available and code_ref only.",
        "template": prompts["5"]["cashier"],
    }
    action["route_rule"] = "Do not restart after this message until its door tries are finished or no code was captured."
    action["code_policy"] = "browser_only"
    return action


def _door_action(ledger, phrase):
    attempt = ledger["replays"]["5"]
    index = ledger["l5"]["door_tries"] + 1
    slug = re.sub(r"[^a-z0-9]+", "-", phrase.lower()).strip("-")
    return _base_action(
        f"l5-r{attempt}-door-{index}-{slug}",
        "try_door",
        5,
        (
            "Fill Hallway and Phrase with the values in this action. "
            "For Code, use code captured in current browser run. "
            "Do not restart. Do not send a chat. Do not include the code digits in the result."
        ),
        fields={
            "hallway": ledger["l5"]["hallway"],
            "phrase": phrase,
            "code_ref": ledger["l5"].get("code_ref"),
            "code_source": "use code captured in current browser run",
        },
        submit_button="Try the door",
        door_try=index,
        replay_attempt=attempt,
        route_rule="Same hallway and the browser-held code for every door try in this run.",
    )


def _skip_l3(ledger):
    return ledger["replays"]["3"] >= ledger["limits"]["max_replays_per_level"] or chats_left(ledger) < 1


def _skip_l5_message(ledger):
    return ledger["replays"]["5"] >= ledger["limits"]["max_replays_per_level"] or chats_left(ledger) < 1


def _clear_l5_route(ledger):
    ledger["l5"]["code"] = None
    ledger["l5"]["code_available"] = False
    ledger["l5"]["code_ref"] = None
    ledger["l5"]["phrases_tried"] = []
    ledger["l5"]["door_tries"] = 0
    ledger["l5"]["run_anchored"] = False
    ledger["l5"]["improved"] = False


def compute_action(ledger, now=None):
    now = parse_now(now or datetime.now(CT))
    prompts = load_prompts()
    for _ in range(8):
        if ledger.get("stop"):
            return _stop_action(ledger)
        if _past_deadline(now) and ledger.get("pending_action") is None:
            return _stop(ledger, "deadline", deadline_ct=DEADLINE_CT)
        phase = ledger["phase"]
        if phase == "verify_bests":
            return _observe_bests_action()
        if phase == "l3_new_hand":
            if _skip_l3(ledger):
                ledger["phase"] = "l5_observe"
                continue
            return _new_hand_action(ledger)
        if phase == "l3_send":
            if _skip_l3(ledger):
                ledger["phase"] = "l5_observe"
                continue
            return _send_action(
                ledger, 3, prompts["3"], f"l3-r{ledger['replays']['3'] + 1}-send"
            )
        if phase == "l3_lock":
            rank = ledger["l3"].get("pending_rank")
            if not rank:
                ledger["phase"] = "l5_observe" if _skip_l3(ledger) else "l3_new_hand"
                continue
            return _lock_action(ledger, rank)
        if phase == "l5_observe":
            return _observe_l5_action(ledger)
        if phase == "l5_restart":
            if _skip_l5_message(ledger):
                return _stop(ledger, "replay_budget" if ledger["replays"]["5"] >= 2 else "chat_budget")
            return _restart_l5_action(ledger, prompts)
        if phase == "l5_select":
            return _select_lena_action(ledger)
        if phase == "l5_send":
            if _skip_l5_message(ledger):
                return _stop(ledger, "replay_budget" if ledger["replays"]["5"] >= 2 else "chat_budget")
            return _send_l5_action(ledger, prompts)
        if phase == "l5_door":
            if ledger["l5"]["door_tries"] >= ledger["limits"]["max_door_tries_per_run"]:
                return _after_l5_run(ledger)
            if ledger["l5"].get("code_available") is not True:
                return _stop(ledger, "missing_code")
            phrase = next_phrase(ledger, prompts)
            if phrase is None:
                return _after_l5_run(ledger)
            return _door_action(ledger, phrase)
        if phase == "done":
            return _stop(ledger, (ledger.get("stop") or {}).get("reason") or "plan_complete")
        raise RuntimeError("unknown phase " + str(phase))
    raise RuntimeError("planner did not resolve a phase")


def _after_l5_run(ledger):
    if (
        not ledger["l5"].get("improved")
        and ledger["replays"]["5"] < ledger["limits"]["max_replays_per_level"]
        and chats_left(ledger) >= 1
    ):
        _clear_l5_route(ledger)
        ledger["phase"] = "l5_restart"
        return compute_action(ledger)
    return _stop(ledger, "plan_complete", score_total=score_total(ledger.get("verified_bests")))


def _example_result(action):
    example = {
        "action_id": action["id"],
        "ok": True,
        "cooldown_s": 0,
        "rate_limited": False,
        "example_only": True,
        "notes": "Delete example_only and replace values with what is visible before ingest.",
    }
    kind = action["type"]
    if kind == "observe_bests":
        example["bests"] = dict(SEED_BESTS)
    elif kind == "send_chat":
        example["reply"] = ""
        example["cleared"] = False
        example["tokens_current_attempt"] = None
    elif kind in {"lock_rank", "try_door"}:
        example["cleared"] = False
        example["tokens_current_attempt"] = None
        example["tries_remaining"] = None
    elif kind == "observe_level":
        example["door_available"] = None
        example["needs_restart"] = None
        example["tries_remaining"] = None
        example["best"] = None
    return example


def assemble_plan(ledger, action, now, ledger_path=None):
    now = parse_now(now)
    path = str(ledger_path) if ledger_path else "LEDGER.json"
    verified = ledger.get("verified_bests")
    return {
        "schema": SCHEMA,
        "generated_at": now.isoformat(),
        "execute": action["type"] != "stop" and not (
            _past_deadline(now) and action.get("mutates_run") is True
        ),
        "execution": "coordinator computerUse only",
        "vm_must_not": [
            "cdp",
            "playwright",
            "puppeteer",
            "in_page_fetch",
            "raw_game_http",
            "cookie_or_session_transfer",
        ],
        "budget": _budget(ledger),
        "score": {
            "seed_total": ledger["seed_total"],
            "verified_total": score_total(verified) if verified else None,
            "verified_bests": verified,
            "rule": (
                "Score is the sum of per-level Best. A failed attempt does not "
                "change Best. A clear with a higher token count stays cleared and "
                "does not replace a lower saved Best."
            ),
        },
        "protected": {"levels": [2], "slots_best": ledger["seed_bests"]["2"]},
        "actions": [action],
        "queued_outline": OUTLINE,
        "outline_note": "Outline steps are not authorized until a later next command emits them as actions.",
        "example_result": _example_result(action),
        "ingest": {
            "command": "python -m uiadapt ingest --ledger " + path + " --result RESULT.json",
            "next_command": "python -m uiadapt next --ledger " + path,
            "required": ["action_id", "ok"],
            "optional": [
                "reply",
                "cleared",
                "tokens_current_attempt",
                "bests",
                "bests_after",
                "cooldown_s",
                "rate_limited",
                "door_available",
                "needs_restart",
                "tries_remaining",
                "best",
                "notes",
            ],
            "forbidden_keys": sorted(FORBIDDEN_KEYS),
            "stop_if": [
                "rate_limited is true",
                "cooldown_s > 120",
                "local time is at or after " + DEADLINE_CT,
                "any reported Best is higher than the protected Best",
            ],
        },
        "stop": ledger.get("stop"),
    }


def plan_next(ledger, now=None, ledger_path=None):
    now = parse_now(now or datetime.now(CT))
    pending = ledger.get("pending_action")
    if pending is not None:
        plan = assemble_plan(ledger, pending, now, ledger_path)
        if _past_deadline(now) and pending.get("mutates_run") is True:
            plan["execute"] = False
            plan["execute_note"] = (
                "Deadline passed. Do not perform this pending action unless it "
                "was already performed; ingest that result if so."
            )
        return plan
    action = compute_action(ledger, now)
    ledger["pending_action"] = action
    return assemble_plan(ledger, action, now, ledger_path)


def _cooldown_block(result, ledger):
    if result.get("rate_limited") is True:
        return True
    if "cooldown_s" not in result or result.get("cooldown_s") is None:
        return False
    value = result["cooldown_s"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("cooldown_s must be a number")
    return value > ledger["limits"]["max_cooldown_s"]


def _bests_map(raw, label):
    if not isinstance(raw, dict):
        raise ValueError(label + " must be an object of level bests")
    parsed = {}
    for level in range(1, 7):
        key = str(level)
        if key not in raw:
            raise ValueError(label + " missing level " + key)
        parsed[key] = _as_int(raw[key], label + " " + key)
    return parsed


def _regressed(baseline, observed):
    worse = {}
    for key, value in observed.items():
        current = baseline.get(key)
        if current is not None and value > current:
            worse[key] = {"observed": value, "protected": current}
    return worse


def _apply_clear(ledger, level, tokens, cleared):
    """A clear always records cleared. Best changes only when tokens are lower."""
    if cleared is not True:
        return {"cleared": False, "best_changed": False}
    key = str(level)
    ledger["cleared"][key] = True
    bests = ledger.get("verified_bests")
    if not isinstance(bests, dict):
        return {"cleared": True, "best_changed": False}
    if tokens is None:
        return {"cleared": True, "best_changed": False}
    tokens = _as_int(tokens, "tokens_current_attempt")
    current = bests.get(key)
    if current is None or tokens < current:
        bests[key] = tokens
        return {"cleared": True, "best_changed": True, "tokens": tokens}
    return {"cleared": True, "best_changed": False, "tokens": tokens}


def _validate_l5_code(result):
    """Accept a browser-held code reference and refuse any copied digits."""
    if "code_available" in result and not isinstance(result.get("code_available"), bool):
        raise ValueError("code_available must be a boolean")
    for field in ("reply", "notes", "code_ref"):
        value = result.get(field)
        if isinstance(value, str) and contains_door_code(value):
            raise ValueError("omit the door code from " + field + "; keep the digits in the browser")
    if result.get("code_available") is True:
        ref = result.get("code_ref")
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError("code_ref is required when code_available is true")


def _require(result, *names):
    missing = [name for name in names if name not in result]
    if missing:
        raise ValueError("result missing " + ", ".join(missing))


def _validate(action, result):
    if not isinstance(result, dict):
        raise ValueError("result must be a JSON object")
    if result.get("example_only") is True:
        raise ValueError("example result is not an observation; remove example_only")
    forbidden = _forbidden_key(result)
    if forbidden:
        raise ValueError("result contains forbidden key " + forbidden)
    if result.get("action_id") != action["id"]:
        raise ValueError("action_id does not match the pending action")
    if not isinstance(result.get("ok"), bool):
        raise ValueError("ok must be a boolean")
    if result["ok"] is not True:
        return
    kind = action["type"]
    if kind == "observe_bests":
        _require(result, "bests")
        _bests_map(result["bests"], "bests")
    elif kind == "send_chat":
        _require(result, "reply", "cleared")
        if not isinstance(result["reply"], str):
            raise ValueError("reply must be a string")
        if not isinstance(result["cleared"], bool):
            raise ValueError("cleared must be a boolean")
        if action.get("level") == 5:
            _validate_l5_code(result)
    elif kind in {"lock_rank", "try_door"}:
        _require(result, "cleared")
        if not isinstance(result["cleared"], bool):
            raise ValueError("cleared must be a boolean")
    elif kind == "observe_level":
        _require(result, "door_available", "needs_restart")
        if not isinstance(result["door_available"], bool) or not isinstance(result["needs_restart"], bool):
            raise ValueError("door_available and needs_restart must be booleans")
        tries = result.get("tries_remaining", None)
        counter_hidden = result["door_available"] is False or result["needs_restart"] is True
        if tries is None:
            if not counter_hidden:
                raise ValueError("tries_remaining is required while the door can be tried")
        else:
            _as_int(tries, "tries_remaining")
        if "best" in result and result["best"] is not None:
            _as_int(result["best"], "best")
    if "tokens_current_attempt" in result and result["tokens_current_attempt"] is not None:
        _as_int(result["tokens_current_attempt"], "tokens_current_attempt")
    if "bests_after" in result and result["bests_after"] is not None:
        _bests_map(result["bests_after"], "bests_after")


def _mark_applied(ledger, action_id):
    ledger["pending_action"] = None
    applied = ledger.setdefault("applied_action_ids", [])
    if action_id not in applied:
        applied.append(action_id)


def _remember(ledger, action, result):
    ledger.setdefault("history", []).append({
        "action_id": action["id"],
        "type": action["type"],
        "ok": result.get("ok"),
        "phase_after": ledger["phase"],
    })


def _maybe_bests_after(ledger, result):
    if "bests_after" not in result or result["bests_after"] is None:
        return None
    observed = _bests_map(result["bests_after"], "bests_after")
    worse = _regressed(ledger["verified_bests"], observed)
    return worse


def apply_result(ledger, result, now=None):
    now = parse_now(now or datetime.now(CT))
    pending = ledger.get("pending_action")
    if not isinstance(result, dict):
        return {"ok": False, "error": "result must be a JSON object"}
    action_id = result.get("action_id")
    if action_id in ledger.get("applied_action_ids", []):
        return {"ok": True, "duplicate": True, "action_id": action_id}
    if not isinstance(pending, dict):
        return {"ok": False, "error": "no pending action; run next first"}
    try:
        _validate(pending, result)
        if "cooldown_s" in result and result["cooldown_s"] is not None:
            _cooldown_block(result, ledger)
    except ValueError as error:
        return {"ok": False, "error": str(error)}

    if _cooldown_block(result, ledger):
        if pending.get("counts_as_chat") is True and result.get("ok") is True:
            ledger["chat_messages_used"] += 1
            if pending["level"] in (3, 5):
                ledger["replays"][str(pending["level"])] += 1
        _mark_applied(ledger, pending["id"])
        _stop(ledger, "cooldown", cooldown_s=result.get("cooldown_s"), rate_limited=result.get("rate_limited") is True)
        _remember(ledger, pending, result)
        return {"ok": True, "stopped": True, "reason": "cooldown"}

    if result.get("ok") is not True:
        _mark_applied(ledger, pending["id"])
        _stop(ledger, "action_failed", action_id=pending["id"], action_type=pending["type"])
        _remember(ledger, pending, result)
        return {"ok": True, "stopped": True, "reason": "action_failed"}

    kind = pending["type"]
    if kind == "observe_bests":
        observed = _bests_map(result["bests"], "bests")
        worse = _regressed(SEED_BESTS, observed)
        if worse:
            _mark_applied(ledger, pending["id"])
            _stop(ledger, "best_regressed", worse=worse)
            _remember(ledger, pending, result)
            return {"ok": True, "stopped": True, "reason": "best_regressed"}
        ledger["verified_bests"] = observed
        ledger["bests_verified"] = True
        ledger["phase"] = "l3_new_hand"
    elif kind == "new_hand":
        ledger["phase"] = "l3_send"
    elif kind == "send_chat" and pending["level"] == 3:
        ledger["chat_messages_used"] += 1
        ledger["replays"]["3"] += 1
        reply = result["reply"][:500]
        ledger["l3"]["last_reply"] = reply
        info = _apply_clear(
            ledger, 3,
            result.get("tokens_current_attempt"),
            result["cleared"],
        )
        if result["cleared"] is True:
            ledger["l3"]["pending_rank"] = None
            ledger["phase"] = "l5_observe" if info["best_changed"] or ledger["replays"]["3"] >= 2 else "l3_new_hand"
        else:
            rank = infer_rank(reply, ledger["l3"]["tried_ranks"])
            ledger["l3"]["pending_rank"] = rank
            ledger["phase"] = "l3_lock" if rank else ("l5_observe" if ledger["replays"]["3"] >= 2 else "l3_new_hand")
    elif kind == "lock_rank":
        rank = pending["rank"]
        if rank not in ledger["l3"]["tried_ranks"]:
            ledger["l3"]["tried_ranks"].append(rank)
        ledger["l3"]["pending_rank"] = None
        info = _apply_clear(ledger, 3, result.get("tokens_current_attempt"), result["cleared"])
        if info["best_changed"] or ledger["replays"]["3"] >= 2:
            ledger["phase"] = "l5_observe"
        else:
            ledger["phase"] = "l3_new_hand"
    elif kind == "observe_level":
        if "best" in result and isinstance(result["best"], int) and not isinstance(result["best"], bool):
            worse = _regressed(ledger["verified_bests"], {"5": result["best"]})
            if worse:
                _mark_applied(ledger, pending["id"])
                _stop(ledger, "best_regressed", worse=worse)
                _remember(ledger, pending, result)
                return {"ok": True, "stopped": True, "reason": "best_regressed"}
        tries = result.get("tries_remaining", None)
        tries_known_zero = isinstance(tries, int) and not isinstance(tries, bool) and tries == 0
        needs_restart = result["needs_restart"] is True or result["door_available"] is False or tries_known_zero
        if needs_restart:
            _clear_l5_route(ledger)
            ledger["phase"] = "l5_restart"
        else:
            ledger["l5"]["run_anchored"] = True
            ledger["phase"] = "l5_select"
    elif kind == "restart_level":
        _clear_l5_route(ledger)
        ledger["l5"]["run_anchored"] = True
        ledger["phase"] = "l5_select"
    elif kind == "select_staff":
        ledger["phase"] = "l5_send"
    elif kind == "send_chat" and pending["level"] == 5:
        ledger["chat_messages_used"] += 1
        ledger["replays"]["5"] += 1
        ledger["l5"]["code"] = None
        ledger["l5"]["phrases_tried"] = []
        ledger["l5"]["door_tries"] = 0
        if result["cleared"] is True:
            info = _apply_clear(ledger, 5, result.get("tokens_current_attempt"), True)
            ledger["l5"]["improved"] = info["best_changed"]
            ledger["phase"] = "done"
            _stop(ledger, "plan_complete", score_total=score_total(ledger.get("verified_bests")))
        elif result.get("code_available") is True:
            ledger["l5"]["code_available"] = True
            ledger["l5"]["code_ref"] = result["code_ref"].strip()
            ledger["phase"] = "l5_door"
        else:
            tried = ledger["l5"].setdefault("hallways_tried", [])
            refused = (pending.get("evidence") or {}).get("hallway")
            if refused and refused not in tried:
                tried.append(refused)
            _clear_l5_route(ledger)
            ledger["l5"]["hallways_tried"] = tried
            if ledger["replays"]["5"] < ledger["limits"]["max_replays_per_level"] and chats_left(ledger) >= 1:
                ledger["phase"] = "l5_restart"
            else:
                ledger["phase"] = "done"
                _stop(ledger, "plan_complete", score_total=score_total(ledger.get("verified_bests")))
    elif kind == "try_door":
        phrase = pending["fields"]["phrase"]
        if phrase not in ledger["l5"]["phrases_tried"]:
            ledger["l5"]["phrases_tried"].append(phrase)
        ledger["l5"]["door_tries"] += 1
        info = _apply_clear(ledger, 5, result.get("tokens_current_attempt"), result["cleared"])
        ledger["l5"]["improved"] = bool(info["best_changed"])
        if info["best_changed"]:
            _stop(ledger, "plan_complete", score_total=score_total(ledger.get("verified_bests")))
        elif result.get("tries_remaining") == 0 or ledger["l5"]["door_tries"] >= ledger["limits"]["max_door_tries_per_run"] or next_phrase(ledger) is None or result["cleared"] is True:
            _after_l5_run(ledger)
        else:
            ledger["phase"] = "l5_door"
    elif kind == "stop":
        ledger["phase"] = "done"
    else:
        return {"ok": False, "error": "unsupported pending action " + kind}

    if "bests_after" in result and result["bests_after"] is not None and ledger.get("verified_bests"):
        try:
            worse = _maybe_bests_after(ledger, result)
        except ValueError as error:
            return {"ok": False, "error": str(error)}
        if worse:
            _mark_applied(ledger, pending["id"])
            _stop(ledger, "best_regressed", worse=worse)
            _remember(ledger, pending, result)
            return {"ok": True, "stopped": True, "reason": "best_regressed"}

    _mark_applied(ledger, pending["id"])
    _remember(ledger, pending, result)
    return {
        "ok": True,
        "phase": ledger["phase"],
        "stop": ledger.get("stop"),
        "verified_total": score_total(ledger.get("verified_bests")),
        "chat_messages_used": ledger["chat_messages_used"],
        "replays": dict(ledger["replays"]),
    }


RESULT_SCHEMA = {
    "schema": SCHEMA,
    "description": "Observed UI result file accepted by python -m uiadapt ingest.",
    "required": ["action_id", "ok"],
    "properties": {
        "action_id": "string, must equal the pending action id",
        "ok": "boolean",
        "example_only": "must be omitted or false",
        "reply": "string dealer or cashier text",
        "cleared": "boolean, required for send_chat, lock_rank, and try_door when ok",
        "tokens_current_attempt": "integer token count for this attempt, or null",
        "bests": "object with string keys 1..6 and integer Best values, required for observe_bests",
        "bests_after": "optional full best map after the action",
        "cooldown_s": "number; values above 120 stop the plan",
        "rate_limited": "boolean; true stops the plan",
        "door_available": "boolean, required for observe_level",
        "needs_restart": "boolean, required for observe_level",
        "tries_remaining": "integer when a tries-left counter is visible. Omit or null when the door is unavailable or a restart is already required and no counter is shown. A missing value is not zero.",
        "notes": "string. On the level-5 cashier result, do not include the door code.",
        "code_available": "boolean. True when the browser captured one door code and will keep the digits locally.",
        "code_ref": "opaque label for that browser-held code. Required with code_available, and must not contain the digits.",
    },
    "forbidden_keys": sorted(FORBIDDEN_KEYS),
    "rules": [
        "Do not include cookies, session tokens, CDP URLs, or credentials.",
        "A clear with more tokens than the saved Best does not lower the score.",
        "Failed attempts do not change Best.",
    ],
}
