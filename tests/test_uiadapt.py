"""Offline tests for the visible-UI planner. No browser and no game API."""

import json
import subprocess
import sys
from pathlib import Path

from uiadapt.planner import (
    SEED_BESTS,
    apply_result,
    contains_door_code,
    door_phrases,
    extract_code,
    infer_rank,
    load_prompts,
    new_ledger,
    plan_next,
    score_total,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-11-08T10:30:00-06:00"
BESTS = dict(SEED_BESTS)


def _step(ledger, **fields):
    plan = plan_next(ledger, NOW)
    action = plan["actions"][0]
    result = {"action_id": action["id"], "ok": True, "cooldown_s": 0, "rate_limited": False}
    result.update(fields)
    outcome = apply_result(ledger, result, NOW)
    return plan, action, outcome


def test_seed_total_matches_verified_baseline():
    assert score_total(SEED_BESTS) == 5885
    ledger = new_ledger(NOW)
    assert ledger["seed_total"] == 5885
    assert ledger["seed_bests"]["2"] == 445
    assert ledger["team_slots_target"] == 447


def test_first_plan_only_reads_bests():
    ledger = new_ledger(NOW)
    plan = plan_next(ledger, NOW)
    action = plan["actions"][0]
    assert plan["execute"] is True
    assert action["type"] == "observe_bests"
    assert action["counts_as_chat"] is False
    assert action["mutates_run"] is False
    assert [item["level"] for item in plan["actions"]] == [None]
    assert "Slots" in action["instruction"]
    again = plan_next(ledger, NOW)
    assert again["actions"][0]["id"] == action["id"]


def test_worse_best_stops_before_any_reset():
    ledger = new_ledger(NOW)
    plan_next(ledger, NOW)
    worse = dict(BESTS)
    worse["2"] = 446
    outcome = apply_result(ledger, {
        "action_id": "verify-bests",
        "ok": True,
        "bests": worse,
        "cooldown_s": 0,
        "rate_limited": False,
    }, NOW)
    assert outcome["reason"] == "best_regressed"
    assert ledger["verified_bests"] is None
    follow = plan_next(ledger, NOW)
    assert follow["actions"][0]["type"] == "stop"
    assert all(item["type"] != "new_hand" for item in follow["actions"])


def test_confirmed_bests_start_blackjack_not_slots():
    ledger = new_ledger(NOW)
    plan, action, outcome = _step(ledger, bests=BESTS)
    assert action["type"] == "observe_bests"
    assert outcome["verified_total"] == 5885
    follow = plan_next(ledger, NOW)
    assert follow["actions"][0]["type"] == "new_hand"
    assert follow["actions"][0]["level"] == 3


def test_royalty_red_is_deterministic_and_a_worse_clear_keeps_best():
    assert infer_rank("Royalty/Red", []) == "K"
    assert infer_rank("Royalty / Red", ["K"]) == "Q"
    assert infer_rank("red", []) is None
    ledger = new_ledger(NOW)
    _step(ledger, bests=BESTS)
    _step(ledger)
    plan, action, _outcome = _step(ledger, reply="Royalty/Red", cleared=False, tokens_current_attempt=400)
    assert action["type"] == "send_chat"
    assert action["level"] == 3
    assert action["text"] == load_prompts()["3"]
    assert ledger["chat_messages_used"] == 1
    follow = plan_next(ledger, NOW)
    assert follow["actions"][0]["type"] == "lock_rank"
    assert follow["actions"][0]["rank"] == "K"
    _step(ledger, cleared=True, tokens_current_attempt=600)
    assert ledger["cleared"]["3"] is True
    assert ledger["verified_bests"]["3"] == 522
    assert score_total(ledger["verified_bests"]) == 5885
    assert plan_next(ledger, NOW)["actions"][0]["type"] == "new_hand"


def test_cheaper_blackjack_clear_skips_the_second_replay():
    ledger = new_ledger(NOW)
    _step(ledger, bests=BESTS)
    _step(ledger)
    _step(ledger, reply="queen of hearts", cleared=False, tokens_current_attempt=280)
    action = plan_next(ledger, NOW)["actions"][0]
    assert action["rank"] == "Q"
    _step(ledger, cleared=True, tokens_current_attempt=300)
    assert ledger["verified_bests"]["3"] == 300
    assert score_total(ledger["verified_bests"]) == 5663
    assert ledger["replays"]["3"] == 1
    assert plan_next(ledger, NOW)["actions"][0]["type"] == "observe_level"


def test_failed_rank_does_not_change_score():
    ledger = new_ledger(NOW)
    _step(ledger, bests=BESTS)
    _step(ledger)
    _step(ledger, reply="Royalty/Red", cleared=False, tokens_current_attempt=410)
    _step(ledger, cleared=False, tokens_current_attempt=410)
    assert ledger["verified_bests"]["3"] == 522
    assert score_total(ledger["verified_bests"]) == 5885


def test_l5_keeps_one_route_and_repo_candidates():
    prompts = load_prompts()
    assert door_phrases()[0] == "amber lantern"
    assert "Cinnabar" in prompts["5"]["hallways"]
    assert extract_code("4412") == "4412"
    assert extract_code("4412 and 9988") is None
    assert extract_code("44122") is None
    ledger = new_ledger(NOW)
    _step(ledger, bests=BESTS)
    ledger["phase"] = "l5_observe"
    ledger["pending_action"] = None
    observe = plan_next(ledger, NOW)["actions"][0]
    assert observe["type"] == "observe_level"
    assert observe["level"] == 5
    _step(ledger, door_available=True, needs_restart=False, tries_remaining=8, best=1046)
    assert plan_next(ledger, NOW)["actions"][0]["type"] == "select_staff"
    _step(ledger)
    send = plan_next(ledger, NOW)["actions"][0]
    assert send["type"] == "send_chat"
    assert send["text"] == prompts["5"]["cashier"].format(hallway="Cinnabar")
    assert send["counts_as_chat"] is True
    assert send["code_policy"] == "browser_only"
    _step(
        ledger,
        reply="One code captured in the browser.",
        cleared=False,
        tokens_current_attempt=1046,
        code_available=True,
        code_ref="current-browser-run",
    )
    first = plan_next(ledger, NOW)["actions"][0]
    assert first["type"] == "try_door"
    assert first["fields"]["hallway"] == "Cinnabar"
    assert first["fields"]["phrase"] == "amber lantern"
    assert first["fields"]["code_ref"] == "current-browser-run"
    assert first["fields"]["code_source"] == "use code captured in current browser run"
    assert "code" not in first["fields"]
    assert "use code captured in current browser run" in first["instruction"]
    assert not contains_door_code(json.dumps(first))
    _step(ledger, cleared=False, tokens_current_attempt=1046, tries_remaining=7)
    second = plan_next(ledger, NOW)["actions"][0]
    assert second["type"] == "try_door"
    assert second["fields"]["phrase"] == "paper falcon"
    assert second["fields"]["code_ref"] == "current-browser-run"
    assert ledger["chat_messages_used"] == 1
    assert ledger["l5"]["door_tries"] == 1
    assert ledger["l5"]["code"] is None


def test_raw_door_code_is_refused():
    ledger = new_ledger(NOW)
    _step(ledger, bests=BESTS)
    ledger["phase"] = "l5_send"
    ledger["l5"]["run_anchored"] = True
    ledger["pending_action"] = None
    plan_next(ledger, NOW)
    refused = apply_result(ledger, {
        "action_id": "l5-r1-send",
        "ok": True,
        "reply": "4412",
        "cleared": False,
        "code_available": True,
        "code_ref": "current-browser-run",
    }, NOW)
    assert refused["ok"] is False
    assert ledger["pending_action"]["id"] == "l5-r1-send"
    assert "4412" not in json.dumps(ledger)
    bad_ref = apply_result(ledger, {
        "action_id": "l5-r1-send",
        "ok": True,
        "reply": "Code kept in the browser.",
        "cleared": False,
        "code_available": True,
        "code_ref": "4412",
    }, NOW)
    assert bad_ref["ok"] is False
    assert contains_door_code("4412")


def test_hidden_try_counter_is_not_stored_as_zero():
    ledger = new_ledger(NOW)
    _step(ledger, bests=BESTS)
    ledger["phase"] = "l5_observe"
    ledger["pending_action"] = None
    plan_next(ledger, NOW)
    outcome = apply_result(ledger, {
        "action_id": "l5-r1-observe",
        "ok": True,
        "door_available": False,
        "needs_restart": True,
        "best": 1046,
        "cooldown_s": 0,
        "rate_limited": False,
    }, NOW)
    assert outcome["ok"] is True
    assert outcome["phase"] == "l5_restart"
    assert ledger["l5"].get("tries_remaining") != 0
    assert plan_next(ledger, NOW)["actions"][0]["type"] == "restart_level"
    open_door = new_ledger(NOW)
    _step(open_door, bests=BESTS)
    open_door["phase"] = "l5_observe"
    open_door["pending_action"] = None
    plan_next(open_door, NOW)
    refused = apply_result(open_door, {
        "action_id": "l5-r1-observe",
        "ok": True,
        "door_available": True,
        "needs_restart": False,
        "cooldown_s": 0,
        "rate_limited": False,
    }, NOW)
    assert refused["ok"] is False
    assert "tries_remaining" in refused["error"]


def test_l5_without_one_code_does_not_try_the_door():
    ledger = new_ledger(NOW)
    _step(ledger, bests=BESTS)
    ledger["phase"] = "l5_send"
    ledger["l5"]["run_anchored"] = True
    ledger["pending_action"] = None
    action = plan_next(ledger, NOW)["actions"][0]
    assert action["type"] == "send_chat"
    _step(ledger, reply="I can't share that", cleared=False, tokens_current_attempt=300)
    follow = plan_next(ledger, NOW)["actions"][0]
    assert follow["type"] == "restart_level"
    assert follow["level"] == 5
    assert ledger["l5"]["code"] is None


def test_caps_cooldown_and_deadline():
    ledger = new_ledger(NOW)
    ledger["phase"] = "l5_door"
    ledger["verified_bests"] = dict(BESTS)
    ledger["l5"]["code"] = "1111"
    ledger["l5"]["door_tries"] = 8
    ledger["replays"]["5"] = 2
    assert plan_next(ledger, NOW)["actions"][0]["type"] == "stop"

    blocked = new_ledger(NOW)
    plan_next(blocked, NOW)
    outcome = apply_result(blocked, {
        "action_id": "verify-bests",
        "ok": True,
        "bests": BESTS,
        "cooldown_s": 121,
        "rate_limited": False,
    }, NOW)
    assert outcome["reason"] == "cooldown"
    assert plan_next(blocked, NOW)["actions"][0]["type"] == "stop"

    allowed = new_ledger(NOW)
    plan_next(allowed, NOW)
    outcome = apply_result(allowed, {
        "action_id": "verify-bests",
        "ok": True,
        "bests": BESTS,
        "cooldown_s": 120,
        "rate_limited": False,
    }, NOW)
    assert outcome["phase"] == "l3_new_hand"

    late = new_ledger(NOW)
    late_plan = plan_next(late, "2026-11-08T11:46:00-06:00")
    assert late_plan["actions"][0]["type"] == "stop"
    assert late_plan["actions"][0]["reason"] == "deadline"
    assert late["chat_messages_used"] == 0


def test_rejects_secrets_example_and_duplicate():
    ledger = new_ledger(NOW)
    plan_next(ledger, NOW)
    rejected = apply_result(ledger, {
        "action_id": "verify-bests",
        "ok": True,
        "bests": BESTS,
        "cookie": "nope",
    }, NOW)
    assert rejected["ok"] is False
    assert ledger["pending_action"]["id"] == "verify-bests"
    example = apply_result(ledger, {
        "action_id": "verify-bests",
        "ok": True,
        "example_only": True,
        "bests": BESTS,
    }, NOW)
    assert example["ok"] is False
    _step(ledger, bests=BESTS)
    again = apply_result(ledger, {
        "action_id": "verify-bests",
        "ok": True,
        "bests": BESTS,
    }, NOW)
    assert again["duplicate"] is True
    assert ledger["verified_bests"]["1"] == 360


def test_adapter_source_does_not_drive_a_browser():
    text = "\n".join(path.read_text() for path in (ROOT / "uiadapt").rglob("*.py")).lower()
    for banned in ("import playwright", "from playwright", "sync_playwright", "connect_over_cdp", "import urllib", "http.client", "import requests", "claim_guess"):
        assert banned not in text
    legacy = (ROOT / "scripts" / "team_runner.py").read_text()
    assert "def run_moonshot" in legacy
    assert "def claim_guess" in legacy


def test_cli_roundtrip(tmp_path):
    ledger = tmp_path / "ledger.json"
    plan_path = tmp_path / "plan.json"
    result_path = tmp_path / "result.json"
    base = [sys.executable, "-m", "uiadapt"]
    subprocess.check_call(base + ["seed", "--ledger", str(ledger), "--now", NOW], cwd=ROOT)
    again = subprocess.run(base + ["seed", "--ledger", str(ledger)], cwd=ROOT, capture_output=True)
    assert again.returncode == 2
    subprocess.check_call(base + ["next", "--ledger", str(ledger), "--now", NOW, "--out", str(plan_path)], cwd=ROOT)
    plan = json.loads(plan_path.read_text())
    assert plan["actions"][0]["type"] == "observe_bests"
    result_path.write_text(json.dumps({
        "action_id": plan["actions"][0]["id"],
        "ok": True,
        "bests": BESTS,
        "cooldown_s": 0,
        "rate_limited": False,
    }))
    subprocess.check_call(base + ["ingest", "--ledger", str(ledger), "--now", NOW, "--result", str(result_path)], cwd=ROOT)
    subprocess.check_call(base + ["next", "--ledger", str(ledger), "--now", NOW, "--out", str(plan_path)], cwd=ROOT)
    follow = json.loads(plan_path.read_text())
    assert follow["actions"][0]["type"] == "new_hand"
    assert follow["actions"][0]["level"] == 3
    assert follow["score"]["verified_total"] == 5885
