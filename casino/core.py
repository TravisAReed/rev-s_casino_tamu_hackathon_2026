"""All browser work is serialized; no browser launch, auth export, or automatic retry."""
import fcntl
import json
import os
from contextlib import contextmanager
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from .store import ROOT, Store, timestamp

BASE = "https://td-revs.vercel.app"
CDP = "http://127.0.0.1:9333"
TAB = os.environ.get("CASINO_TAB", "casino-harness-v1")
PLACEHOLDERS = {
    1:"Say something to the bouncer…", 2:"Talk to the machine…",
    3:"Ask the dealer…", 4:"Tell the dealer your play…",
    5:"Talk to the staff member…", 6:"Message vault security…",
}


def validate_level(level):
    if type(level) is not int or not 1 <= level <= 6:
        raise ValueError("level must be an integer 1..6")


@contextmanager
def locked():
    with (ROOT / (".casino.lock" if TAB == "casino-harness-v1" else ".casino-%s.lock" % TAB)).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def connect():
    """Attach only. The tagged harness tab and existing browser stay running."""
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP, timeout=15000)
        if not browser.contexts:
            raise RuntimeError("Existing browser has no context")
        context = browser.contexts[0]
        page = None
        for candidate in context.pages:
            if candidate.url.startswith(BASE + "/"):
                try:
                    if candidate.evaluate("window.name") == TAB:
                        page = candidate; break
                except Exception:
                    continue
        if page is None:
            page = context.new_page()
            page.goto(BASE + "/casino/entrance",wait_until="networkidle",timeout=30000)
            page.evaluate("(t) => { window.name = t }", TAB)
        yield page
        # Playwright driver shutdown disconnects; never browser.close/context.close.


def _progress(page):
    if urlparse(page.url).netloc != "td-revs.vercel.app":
        raise RuntimeError("Harness tab left the game origin; sign in manually")
    result = page.evaluate("""async () => {
        const r = await fetch('/api/progress', {cache:'no-store'});
        return {status:r.status,body:await r.json()};
    }""")
    body = result["body"]
    if result["status"] != 200 or not isinstance(body,dict) or "completed" not in body:
        raise RuntimeError("Progress unavailable or signed out (no auth changes attempted)")
    # Do not expose account identifiers to iteration agents.
    return {k:v for k,v in body.items() if k != "player"}


def _open(page, level):
    validate_level(level)
    target = f"{BASE}/level/{level}"
    if page.url != target:
        page.goto(target,wait_until="networkidle",timeout=30000)
    composer = page.get_by_placeholder(PLACEHOLDERS[level], exact=True)
    composer.wait_for(state="visible",timeout=15000)
    if page.url != target:
        raise RuntimeError("Level is locked or sign-in is required")
    return composer


def get_progress():
    with locked(), connect() as page:
        return _progress(page)


def open_level(level):
    validate_level(level)
    with locked(), connect() as page:
        composer = _open(page,level)
        return dict(level=level,url=page.url,composer=PLACEHOLDERS[level],
                    enabled=composer.is_enabled(),**run_state(_progress(page),level))


def run_state(progress,level):
    """Authoritative per-run state from GET /api/progress (keys are str level ids)."""
    k=str(level)
    def g(name):
        v=progress.get(name)
        return v.get(k) if isinstance(v,dict) else None
    won=g("attemptWon")
    return dict(cleared=won if isinstance(won,bool) else None,won=won,
                level_completed_ever=level in (progress.get("completed") or []),
                run_tokens=g("attemptTokens"),best_tokens=g("best"),server_run=g("run"))


def response_output_tokens(body):
    """Return only an explicit server output/completion count; never tokenize locally."""
    if not isinstance(body, dict):
        return None
    exchange = body.get("exchange")
    exchange = exchange if isinstance(exchange, dict) else {}
    for obj in (exchange, exchange.get("usage"), body.get("usage"), body):
        if not isinstance(obj, dict):
            continue
        for key in ("output_tokens", "outputTokens", "completion_tokens", "completionTokens"):
            value = obj.get(key)
            if type(value) is int and value >= 0:
                return value
    return None


def parse_message(body):
    """Field paths evidenced in public frontend, not a fabricated token formula."""
    if not isinstance(body,dict) or not isinstance(body.get("reply"),str):
        raise ValueError("Unsupported response schema: missing string reply")
    exchange = body.get("exchange")
    level = body.get("level")
    if not isinstance(exchange,dict) or not isinstance(level,dict):
        raise ValueError("Unsupported response schema: missing exchange/level")
    def count(obj,key):
        value = obj.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError("Invalid server token value at " + key)
        return value
    # level.completed is sticky (level EVER cleared); the per-run win flag is attemptWon.
    ever = level.get("completed")
    if ever is not None and type(ever) is not bool:
        raise ValueError("Invalid level.completed")
    won = level.get("attemptWon")
    if won is not None and type(won) is not bool:
        raise ValueError("Invalid level.attemptWon")
    cleared = won
    return dict(response=body["reply"],assistant_text=body["reply"],
        output_tokens=response_output_tokens(body),tokens=count(exchange,"tokens"),tokens_this_message=count(exchange,"tokens"),
        tokens_total_level=count(level,"tokens"),tokens_current_attempt=count(level,"attemptTokens"),
        best_tokens=count(level,"best"),cleared=cleared,won=won,level_completed_ever=ever,
        attempt_won=level.get("attemptWon"),server_run=level.get("run"))


def _send(page,level,text):
    composer = _open(page,level)
    if not composer.is_enabled() or composer.input_value().strip():
        raise RuntimeError("Composer busy or contains a human draft; not overwritten")
    composer.fill(text)
    send = page.get_by_role("button",name="Send",exact=True)
    def matches(response):
        request = response.request
        if response.url != f"{BASE}/api/level/{level}/message" or request.method != "POST":
            return False
        try:
            return request.post_data_json.get("message") == text.strip()
        except Exception:
            return False
    with page.expect_response(matches,timeout=65000) as event:
        send.click(timeout=10000)
    response = event.value
    raw_body = response.text()
    network = dict(url=response.url,method="POST",status=response.status,
                   request_body=response.request.post_data_json,response_body=raw_body,
                   retry_after=getattr(response,"headers",{}).get("retry-after"))
    try:
        body = json.loads(raw_body)
        parsed = parse_message(body) if response.ok else None
    except (ValueError,TypeError) as error:
        return dict(status="unknown",error=str(error),raw_network=network)
    if parsed is None:
        return dict(status="rejected",error=f"HTTP {response.status}; not retried",raw_network=network)
    # The captured complete server body is authoritative, even if a celebration hides UI.
    return dict(status="complete",**parsed,raw_network=network)


def send_message(level,text,*,dedupe=False):
    validate_level(level)
    if not isinstance(text,str) or not text.strip():
        raise ValueError("text must be nonempty")
    if type(dedupe) is not bool:
        raise ValueError("dedupe must be a boolean")
    with locked():
        store = Store()
        try:
            previous = store.lookup(level,text) if dedupe else None
            if previous is not None:
                return dict(previous,deduplicated=True)
            result = store.begin(level,text)  # durable BEFORE any possible click
            try:
                with connect() as page:
                    result.update(_send(page,level,text))
                    if result.get("status")=="complete":
                        try:
                            result.update(win_source="message response level.attemptWon",progress_check=run_state(_progress(page),level))
                        except Exception as error:
                            result["progress_error"]=str(error)
            except Exception as error:
                # Never retry within a call; a new explicit call is a new attempt by default.
                result.update(status="unknown",error=type(error).__name__ + ": " + str(error))
            result["finished_at"] = timestamp()
            return store.finish(result)
        finally:
            store.close()


def list_attempts(level=None,limit=100):
    if level is not None:
        validate_level(level)
    with locked():
        store = Store()
        try:
            return store.list(level,limit)
        finally:
            store.close()


def get_transcript(level):
    validate_level(level)
    with locked(), connect() as page:
        progress = _progress(page)
        threads = {k:v for k,v in progress.get("threads",{}).items() if k.startswith(f"{level}:")}
        store = Store()
        try:
            local = store.list(level,1000)
        finally:
            store.close()
        return dict(level=level,server_threads=threads,local_attempts=local,
                    note="Server current-run threads and local harness ledger are distinct.")
