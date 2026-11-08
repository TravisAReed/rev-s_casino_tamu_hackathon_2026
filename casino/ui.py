"""UI-control connectors: level reset, table actions, staff selection, engine-panel read.

Every operation drives the already-running signed-in page through the page's
own buttons via Playwright connect_over_cdp (never a browser launch, never a
direct game-API call). Reset/action results come from the network response the
page itself produces, matched by URL, method, and body. Nothing here writes to
the attempts ledger; no call retries itself.
"""
import json
from . import core

LEVEL_NAMES = {1:"The Bouncer",2:"Slots",3:"Blackjack",4:"Poker",5:"Casino Floor",6:"The Vault"}
STAFF = {5: {"pitboss":"Dominic","cashier":"Lena","host":"Theo"}}
STAFF_ROLES = {5: {"pitboss":"Pit Boss","cashier":"Cage Cashier","host":"VIP Host"}}
RANKS = {"A":"Ace","2":"Two","3":"Three","4":"Four","5":"Five","6":"Six",
         "7":"Seven","8":"Eight","9":"Nine","10":"Ten","J":"Jack","Q":"Queen","K":"King"}
RANK_NAMES = {name.lower():code for code,name in RANKS.items()}
RESET_DOC = {
    "header_button": "button.btn-quiet named 'New attempt' (level cleared) or 'Restart' (uncleared)",
    "confirm_dialog": "[role=alertdialog] titled 'New attempt at <Name>?' / 'Restart <Name>?' "
                      "with primary button 'Start new attempt' / 'Restart level'",
    "request": {"method":"POST","path":"/api/level/<n>/reset","body":None},
    "response_fields_unverified": True,
    "effect": "Fresh conversation/run for that level; best score is kept. One guess/hand and "
              "door-route state reset with it.",
}

def _rank_code(value):
    text = value.strip()
    if text in RANKS:
        return text
    if text.upper() in RANKS:
        return text.upper()
    return RANK_NAMES.get(text.lower())

def _actions_for(level):
    if level == 3:
        return [{
            "name":"lock_in_rank",
            "alias":"guess_rank",
            "summary":"Lock in a guess at the dealer's face-down card by rank (one guess per hand)",
            "args":{"rank":"rank code A,2..10,J,Q,K or full name e.g. 'Ace'"},
            "request":{"method":"POST","path":"/api/level/3/action","body":{"rank":"<rank code>"}},
            "ui":{"pick_button":"button.bj-mini[aria-label='<Rank name>'] (aria-pressed marks the pick)",
                  "confirm_button":"section.bj-guess button.btn-gold named 'Lock in <Rank name>' "
                                   "('Pick a card' and disabled until the dealer has replied and a rank is picked)"},
            "gating":"Picker enabled only after the dealer replies in the current hand; one guess "
                     "per hand; a wrong guess offers 'Deal a new hand', the same reset flow as reset_level",
            "cost":"The action itself carries no token charge; only chat messages cost tokens",
            "response_fields_unverified":True,
        }]
    if level == 5:
        return [{
            "name":"try_the_door",
            "summary":"Submit the assembled vault route (hallway, code, phrase) at the service door",
            "args":{"hallway":"hallway name (max 40 chars)","code":"door code (numeric field, max 12)",
                    "phrase":"spoken phrase (max 60 chars)"},
            "request":{"method":"POST","path":"/api/level/5/action",
                       "body":{"hallway":"…","code":"…","phrase":"…"}},
            "ui":{"inputs":"form.route-form inputs in order Hallway (maxlength 40), Code "
                           "(inputmode=numeric, maxlength 12), Phrase (maxlength 60)",
                  "submit_button":"form.route-form button.btn-gold named 'Try the door' ('Trying…' while busy)"},
            "gating":"All three fields nonempty (trimmed); disabled once the level is solved or "
                     "attemptsLeft is 0; the route header shows tries left",
            "response_fields_unverified":True,
        }]
    return []

def _engine_panel_doc():
    return {
        "tool":"read_engine_panel",
        "summary":"Read the Table engine panel: status line and the executed move JSON behind the Raw toggle",
        "ui":{"section":'section.pk-engine[aria-label="Table engine"]',
              "status_line":"p.pk-engine-line[role=status] ('No moves yet…', 'Waiting for The House…', or 'The House checks/bets/calls/raises/pays the pot…')",
              "paid_flag":"span.pk-flag 'Paid before the showdown' appears when the engine paid early (the level-4 win state)",
              "raw_toggle":"button.pk-raw-toggle ('Raw'/'Hide', aria-pressed) — free local toggle, restored as found",
              "raw_json":"pre.action-json with JSON.stringify(lastAction) or '// nothing executed yet'"},
        "cost":"Free; no request is sent and no tokens are charged",
    }

def list_actions(level):
    """Documented table-button actions and reset/staff/panel controls; no browser, no send."""
    core.validate_level(level)
    return {
        "level": level,
        "level_name": LEVEL_NAMES[level],
        "actions": _actions_for(level),
        "staff": dict(STAFF[level]) if level in STAFF else None,
        "engine_panel": _engine_panel_doc() if level == 4 else None,
        "reset": {**RESET_DOC, "request": dict(RESET_DOC["request"])},
        "note": "Chat messages use the separate send_message tool; this catalog lists only "
                "table-button actions, reset, staff selection, and the engine-panel read. "
                "Selectors and requests are evidenced in docs/ui-actions.md; action/reset "
                "response bodies are unverified live.",
    }

def _expect(page, url, body, click, timeout=30000):
    """Click the page's own button and capture the POST it sends; never retry."""
    def matches(response):
        request = response.request
        if response.url != url or request.method != "POST":
            return False
        data = request.post_data_json
        if body is None:
            return data is None
        try:
            return data == body
        except Exception:
            return False
    try:
        with page.expect_response(matches, timeout=timeout) as event:
            click()
        response = event.value
        raw = response.text()
    except Exception as error:
        # The click may or may not have gone through; a new explicit call is a new decision.
        return dict(status="unknown", error=type(error).__name__ + ": " + str(error))
    network = dict(url=response.url, method="POST", status=response.status,
                   request_body=response.request.post_data_json, response_body=raw,
                   retry_after=getattr(response, "headers", {}).get("retry-after"))
    if not response.ok:
        return dict(status="rejected", error="HTTP " + str(response.status) + "; not retried",
                    raw_network=network)
    if response.status == 204 or not raw:
        return dict(status="complete", body=None, raw_network=network)
    try:
        parsed = json.loads(raw)
    except ValueError:
        return dict(status="unknown", error="non-JSON response body", raw_network=network)
    return dict(status="complete", body=parsed, raw_network=network)

def _finish(result, level, **extra):
    body = result.pop("body", None)
    snapshot = body.get("level") if isinstance(body, dict) else None
    result["message"] = body.get("message") if isinstance(body, dict) else None
    result["level_snapshot"] = snapshot if isinstance(snapshot, dict) else None
    result["completed"] = body.get("completed") if isinstance(body, dict) else None
    result["level"] = level
    result.update(**extra)
    if result["status"] == "unknown":
        result["note"] = "The request may or may not have been applied; inspect get_progress or open_level before repeating. Never retried automatically."
    return result

def _reset_flow(page, level):
    header = None
    for name in ("New attempt", "Restart"):
        candidate = page.get_by_role("button", name=name, exact=True)
        if candidate.count() == 1:
            header = candidate
            break
    if header is None:
        raise RuntimeError("Reset button not found; level page not loaded or level locked")
    if not header.is_enabled():
        raise RuntimeError("Reset button is disabled; nothing clicked")
    # A post-win celebration can briefly intercept pointer events; the header click
    # only opens a confirm dialog (no request), so waiting and retrying it is safe.
    for i in range(4):
        try:
            header.click(timeout=8000)
            break
        except Exception:
            if i == 3:
                raise
            page.keyboard.press("Escape")
            page.wait_for_timeout(4000)
    dialog = page.get_by_role("alertdialog")
    try:
        dialog.wait_for(state="visible", timeout=10000)
    except Exception as error:
        raise RuntimeError("Reset confirmation dialog did not appear; nothing sent") from error
    confirm = None
    for name in ("Start new attempt", "Restart level"):
        candidate = dialog.get_by_role("button", name=name, exact=True)
        if candidate.count() == 1:
            confirm = candidate
            break
    if confirm is None:
        raise RuntimeError("Reset dialog showed no confirm button; nothing sent")
    if not confirm.is_enabled():
        raise RuntimeError("Reset confirm button is disabled; nothing sent")
    result = _expect(page, core.BASE + "/api/level/" + str(level) + "/reset", None,
                     lambda: confirm.click(timeout=10000))
    snapshot = result.pop("body", None)
    result["level_snapshot"] = snapshot if isinstance(snapshot, dict) else None
    return result

def reset_level(level):
    """Click the page's own New attempt/Restart + confirm; captures the reset POST."""
    core.validate_level(level)
    with core.locked(), core.connect() as page:
        core._open(page, level)
        result = _reset_flow(page, level)
    result["level"] = level
    if result["status"] == "unknown":
        result["note"] = "The reset may or may not have been applied; inspect get_progress or open_level before repeating. Never retried automatically."
    return result

def _prepare_args(level, action, args):
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError("args must be a dict")
    if action in ("lock_in_rank", "guess_rank"):  # guess_rank is a deprecated alias
        if set(args) != {"rank"}:
            raise ValueError("lock_in_rank takes exactly one arg: {'rank': …}")
        value = args["rank"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("rank must be a nonempty string")
        code = _rank_code(value)
        if code is None:
            raise ValueError("rank must be a code " + "/".join(RANKS) + " or a full name like 'Ace'")
        return {"rank": code, "rank_name": RANKS[code]}
    if action == "try_the_door":
        if set(args) != {"hallway", "code", "phrase"}:
            raise ValueError("try_the_door takes exactly {'hallway','code','phrase'}")
        prepared = {}
        for key in ("hallway", "code", "phrase"):
            value = args[key]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(key + " must be a nonempty string")
            prepared[key] = value
        return prepared
    raise ValueError("unknown action " + repr(action))

def _lock_in_rank(page, level, prepared):
    code, name = prepared["rank"], prepared["rank_name"]
    pick = page.get_by_role("button", name=name, exact=True)
    if pick.count() != 1:
        raise RuntimeError("Rank button " + repr(name) + " not found on the blackjack table")
    if not pick.is_enabled():
        raise RuntimeError("Rank picker is disabled (dealer has not replied or the hand was already guessed); nothing clicked")
    pick.click(timeout=10000)  # local pick only; sends no request
    confirm = page.get_by_role("button", name="Lock in " + name, exact=True)
    try:
        confirm.wait_for(state="visible", timeout=5000)
    except Exception as error:
        raise RuntimeError("Confirm button did not appear after picking (guess gate closed); nothing sent") from error
    if not confirm.is_enabled():
        raise RuntimeError("Confirm button is disabled (guess gate closed); nothing sent")
    return _expect(page, core.BASE + "/api/level/" + str(level) + "/action", {"rank": code},
                   lambda: confirm.click(timeout=10000))

def _try_the_door(page, level, prepared):
    inputs = page.locator("form.route-form input")
    if inputs.count() != 3:
        raise RuntimeError("Vault-route form not found on the Casino Floor")
    fields = [("hallway", inputs.nth(0), prepared["hallway"], "40", None),
              ("code", inputs.nth(1), prepared["code"], "12", "numeric"),
              ("phrase", inputs.nth(2), prepared["phrase"], "60", None)]
    for key, field, value, maxlength, inputmode in fields:
        if inputmode is not None and field.get_attribute("inputmode") != inputmode:
            raise RuntimeError("Vault-route form shape changed (code input not numeric); refusing to fill")
        if field.get_attribute("maxlength") != maxlength:
            raise RuntimeError("Vault-route " + key + " input shape changed (maxlength != " + maxlength + "); refusing to fill")
        if not field.is_enabled():
            raise RuntimeError("Vault-route " + key + " input is disabled (door solved or no attempts left); nothing sent")
    for key, field, value, maxlength, inputmode in fields:
        field.fill(value)
    submit = page.get_by_role("button", name="Try the door", exact=True)
    if submit.count() != 1:
        raise RuntimeError("'Try the door' button not found")
    if not submit.is_enabled():
        raise RuntimeError("'Try the door' is disabled after filling (level solved, no attempts left, or a try in progress); nothing sent")
    return _expect(page, core.BASE + "/api/level/" + str(level) + "/action",
                   {"hallway": prepared["hallway"], "code": prepared["code"], "phrase": prepared["phrase"]},
                   lambda: submit.click(timeout=10000))

ACTION_RUNNERS = {3: {"lock_in_rank": _lock_in_rank, "guess_rank": _lock_in_rank},
                  5: {"try_the_door": _try_the_door}}

def perform_action(level, action, args=None):
    """Run a table action by clicking the page's own buttons; captures its POST."""
    core.validate_level(level)
    if not isinstance(action, str) or not action.strip():
        raise ValueError("action must be a nonempty string")
    runners = ACTION_RUNNERS.get(level, {})
    runner = runners.get(action)
    if runner is None:
        available = sorted(runners) or ["none"]
        raise ValueError("Level " + str(level) + " has no action " + repr(action) + "; available: " + ", ".join(available))
    prepared = _prepare_args(level, action, args)
    from casino.store import Store
    store = Store(); rec = store.begin(level, "[action " + action + "] " + json.dumps(args, ensure_ascii=False))
    try:
        result = _perform_logged(level, action, runner, prepared)
    except Exception as error:
        rec.update(status="unknown", error=type(error).__name__ + ": " + str(error)); store.finish(rec); store.close(); raise
    rec.update({k: v for k, v in result.items() if k not in ("id", "level", "prompt", "timestamp")})
    rec["response"] = result.get("message") if isinstance(result.get("message"), str) else json.dumps(result.get("message"))
    rec["tokens"] = 0
    store.finish(rec); store.close()
    return result

def _perform_logged(level, action, runner, prepared):
    with core.locked(), core.connect() as page:
        core._open(page, level)
        result = runner(page, level, prepared)
        result = _finish(result, level, action=action)
        if result.get("status") == "complete":
            try:
                snap = result.get("level_snapshot") or {}
                won = snap.get("attemptWon")
                result["cleared"] = won if isinstance(won, bool) else None
                result["win_source"] = "action response level.attemptWon"
                result["progress_check"] = core.run_state(core._progress(page), level)
                if result["cleared"] is None:
                    result["cleared"] = result["progress_check"]["cleared"]
                    result["win_source"] = "api/progress (action body lacked attemptWon)"
            except Exception as error:
                result["progress_error"] = str(error)
    return result

def _resolve_staff(level, staff):
    if level != 5:
        raise ValueError("Staff selection exists only on level 5 (Casino Floor)")
    if not isinstance(staff, str) or not staff.strip():
        raise ValueError("staff must be a nonempty string")
    key = staff.strip().lower()
    for agent_id, name in STAFF[5].items():
        if key in (agent_id, name.lower(), STAFF_ROLES[5][agent_id].lower()):
            return agent_id, name
    raise ValueError("Unknown staff " + repr(staff) + "; choose one of " +
                     ", ".join(k + " (" + v + ", " + STAFF_ROLES[5][k] + ")" for k, v in STAFF[5].items()))

def select_staff(level, staff):
    """Click the page's own roster card; local selection only, sends no request."""
    agent_id, name = _resolve_staff(level, staff)
    with core.locked(), core.connect() as page:
        core._open(page, 5)
        card = page.locator("button.roster-card", has_text=name)
        if card.count() != 1:
            raise RuntimeError("Staff card " + repr(name) + " not found on the Casino Floor")
        if not card.is_enabled():
            raise RuntimeError("Staff card is disabled; nothing clicked")
        card.click(timeout=10000)  # local UI selection; sends no request
        tab = page.locator("button.chat-tab", has_text=name)
        if tab.count() != 1 or tab.get_attribute("aria-selected") != "true":
            raise RuntimeError("Staff selection could not be verified in the chat tabs")
        if card.get_attribute("aria-pressed") != "true":
            raise RuntimeError("Staff selection could not be verified on the roster card")
    return dict(level=5, staff=agent_id, name=name, agent_id=agent_id, verified=True,
                note="Local UI selection only; no request sent. The next level 5 send_message "
                     "uses agentId " + agent_id + ".")

def read_engine_panel(level):
    """Read the Poker Table engine panel; the Raw toggle is free and left as found."""
    if level != 4:
        raise ValueError("The Table engine panel exists only on level 4 (Poker)")
    with core.locked(), core.connect() as page:
        core._open(page, 4)
        section = page.locator("section.pk-engine")
        if section.count() != 1:
            raise RuntimeError("Table engine panel not found on the Poker table")
        line = section.locator("p.pk-engine-line")
        status_line = line.inner_text() if line.count() == 1 else None
        flag = section.locator("span.pk-flag")
        flag_text = flag.inner_text() if flag.count() == 1 else None
        toggle = section.locator("button.pk-raw-toggle")
        if toggle.count() != 1:
            raise RuntimeError("Raw toggle not found in the Table engine panel")
        was_on = toggle.get_attribute("aria-pressed") == "true"
        turned_on = False
        raw_text = None
        try:
            if not was_on:
                toggle.click(timeout=10000)  # free local toggle; sends no request
                turned_on = True
            pre = section.locator("pre.action-json")
            try:
                pre.wait_for(state="visible", timeout=5000)
                raw_text = pre.inner_text()
            except Exception as error:
                raise RuntimeError("Raw action JSON did not appear after toggling Raw") from error
        finally:
            if turned_on:
                toggle.click(timeout=10000)  # leave the panel exactly as found
    last_action = None
    if raw_text is not None and not raw_text.lstrip().startswith("//"):
        try:
            parsed = json.loads(raw_text)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            last_action = parsed
    return dict(level=4, status_line=status_line, paid_early=flag_text is not None,
                paid_flag=flag_text, last_action=last_action, raw_text=raw_text,
                raw_was_on=was_on, raw_restored=turned_on,
                note="Free read: the Raw toggle is local UI (restored as found); no request sent "
                     "and no tokens charged. Only chat messages cost tokens.")
