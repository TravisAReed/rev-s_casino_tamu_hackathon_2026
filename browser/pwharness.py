import json
import os
import pwd
import re
import stat
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

CDP_HOST = "127.0.0.1"
CDP_PORT = 9333
CDP_HTTP_URL = "http://" + CDP_HOST + ":" + str(CDP_PORT)
ENTRANCE_URL = "https://td-revs.vercel.app/casino/entrance"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_DIR = os.path.join(PROJECT_ROOT, ".pw-profile")

SIGN_OUT_MARKERS = ("sign out", "sign-out", "log out", "logout")
SIGN_IN_MARKERS = (
    "sign in",
    "sign-in",
    "log in",
    "login",
    "continue with google",
    "google sign-in",
    "sign in with google",
)
BLOCKED_MARKERS = (
    "403",
    "forbidden",
    "access denied",
    "unauthorized",
    "not authorized",
    "permission denied",
    "blocked",
    "login required",
    "sign in required",
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
API_ENDPOINT_RE = re.compile(r"/api/[A-Za-z0-9_\-./]{1,120}")
WS_ENDPOINT_RE = re.compile(r"wss?://[A-Za-z0-9._\-]+(?::\d+)?(?:/[A-Za-z0-9_\-./]*)?")


def extract_endpoints(body):
    found = set(API_ENDPOINT_RE.findall(str(body)))
    found.update(WS_ENDPOINT_RE.findall(str(body)))
    return sorted(found)


def sanitize_url(url):
    return str(url).split("#", 1)[0].split("?", 1)[0]


def redact_emails(text):
    return EMAIL_RE.sub("[email-redacted]", str(text))


def detect_sign_in(visible_texts):
    lows = [str(t).strip().lower() for t in visible_texts if t is not None and str(t).strip()]
    for marker in SIGN_OUT_MARKERS:
        for text in lows:
            if marker in text:
                return "signed_in", "visible text contains '" + marker + "'"
    for marker in SIGN_IN_MARKERS:
        for text in lows:
            if marker in text:
                return "signed_out", "visible text contains '" + marker + "'"
    return "unknown", "no sign-in state markers in visible text"


def detect_docs_blocked(visible_texts):
    lows = [str(t).strip().lower() for t in visible_texts if t is not None and str(t).strip()]
    matched = [m for m in BLOCKED_MARKERS if any(m in t for t in lows)]
    return (bool(matched), matched)


def validate_cdp_version(payload):
    browser = ""
    ws_ok = False
    ok = False
    if isinstance(payload, dict):
        browser = str(payload.get("Browser", ""))
        ws = str(payload.get("webSocketDebuggerUrl", ""))
        ws_ok = ws.startswith("ws://" + CDP_HOST + ":" + str(CDP_PORT) + "/devtools/browser/")
        ok = bool(browser) and ws_ok
    return {"ok": ok, "browser": browser, "websocket_loopback": ws_ok}


def parse_ss_listening(ss_text, port):
    results = []
    for line in ss_text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] != "LISTEN":
            continue
        addr, _, sport = parts[3].rpartition(":")
        if sport != str(port):
            continue
        users = " ".join(parts[4:])
        pids = [int(m) for m in re.findall(r"pid=(\d+)", users)]
        procs = re.findall(r'\("([^"]+)"', users)
        results.append(
            {"addr": addr.strip("[]"), "port": int(sport), "pids": pids, "procs": procs}
        )
    return results


def listener_loopback_only(entry):
    return entry.get("addr") in ("127.0.0.1", "::1")


def read_cmdline(pid):
    try:
        with open("/proc/" + str(int(pid)) + "/cmdline", "rb") as fh:
            raw = fh.read()
    except (OSError, ValueError):
        return []
    return [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]


def cmdline_matches_browser(argv, profile_dir, port=CDP_PORT):
    if not argv:
        return False
    has_port = False
    has_profile = False
    for i, arg in enumerate(argv):
        if arg == "--remote-debugging-port=" + str(port):
            has_port = True
        elif arg == "--remote-debugging-port" and i + 1 < len(argv) and argv[i + 1] == str(port):
            has_port = True
        elif arg.startswith("--user-data-dir="):
            if os.path.realpath(arg.split("=", 1)[1]) == os.path.realpath(profile_dir):
                has_profile = True
        elif arg == "--user-data-dir" and i + 1 < len(argv):
            if os.path.realpath(argv[i + 1]) == os.path.realpath(profile_dir):
                has_profile = True
    return has_port and has_profile


def profile_from_cmdline(argv):
    for i, arg in enumerate(argv):
        if arg.startswith("--user-data-dir="):
            return arg.split("=", 1)[1]
        if arg == "--user-data-dir" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def gather_listener(port=CDP_PORT, profile_dir=PROFILE_DIR):
    try:
        out = subprocess.run(
            ["ss", "-tlnpH", "sport = :" + str(port)],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    entries = parse_ss_listening(out, port)
    if not entries:
        return {"state": "none"}
    entry = entries[0]
    pid = entry["pids"][0] if entry["pids"] else None
    ours = bool(pid) and cmdline_matches_browser(read_cmdline(pid), profile_dir, port)
    return {
        "state": "ours" if ours else "foreign",
        "pid": pid,
        "addr": entry["addr"],
        "loopback_only": listener_loopback_only(entry),
        "procs": entry["procs"],
    }


def cdp_version(timeout=5):
    try:
        with urllib.request.urlopen(CDP_HTTP_URL + "/json/version", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return {"reachable": False, "ok": False, "browser": "", "websocket_loopback": False}
    info = validate_cdp_version(payload)
    info["reachable"] = True
    return info


def list_x_sockets():
    entries = {}
    base = "/tmp/.X11-unix"
    if not os.path.isdir(base):
        return entries
    for name in os.listdir(base):
        if not name.startswith("X"):
            continue
        try:
            entries[name[1:]] = os.stat(os.path.join(base, name)).st_uid
        except OSError:
            continue
    return entries


def list_wayland_sockets():
    entries = {}
    runtime = os.environ.get("XDG_RUNTIME_DIR", "/run/user/" + str(os.getuid()))
    if not os.path.isdir(runtime):
        return entries
    for name in os.listdir(runtime):
        if not name.startswith("wayland-"):
            continue
        try:
            st = os.stat(os.path.join(runtime, name))
            if stat.S_ISSOCK(st.st_mode):
                entries[name] = st.st_uid
        except OSError:
            continue
    return entries


def gather_loginctl_sessions():
    try:
        out = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    sessions = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        sid, uid_text, user = parts[0], parts[1], parts[2]
        try:
            uid = int(uid_text)
        except ValueError:
            continue
        props = {}
        try:
            show = subprocess.run(
                [
                    "loginctl",
                    "show-session",
                    sid,
                    "--property=Type",
                    "--property=Class",
                    "--property=State",
                    "--property=Display",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
            for pline in show.splitlines():
                if "=" in pline:
                    key, value = pline.split("=", 1)
                    props[key] = value
        except (OSError, subprocess.SubprocessError):
            continue
        sessions.append(
            {
                "id": sid,
                "uid": uid,
                "user": user,
                "type": props.get("Type", ""),
                "class": props.get("Class", ""),
                "state": props.get("State", ""),
                "display": props.get("Display", ""),
            }
        )
    return sessions


def evaluate_display(x_sockets, wayland_sockets, sessions, uid):
    for sess in sessions:
        if sess.get("uid") != uid:
            continue
        if sess.get("class") != "user" or sess.get("state") != "active":
            continue
        if sess.get("type") == "x11":
            name = str(sess.get("display", "")).lstrip(":").split(".", 1)[0]
            if name and x_sockets.get(name) == uid:
                return {
                    "type": "x11",
                    "display": ":" + name,
                    "env": {"DISPLAY": ":" + name},
                    "reason": "",
                }
        if sess.get("type") == "wayland":
            owned = sorted(n for n, ouid in wayland_sockets.items() if ouid == uid)
            if owned:
                name = "wayland-0" if "wayland-0" in owned else owned[0]
                return {
                    "type": "wayland",
                    "display": name,
                    "env": {"WAYLAND_DISPLAY": name},
                    "reason": "",
                }
    reasons = []
    foreign = sorted(n for n, ouid in x_sockets.items() if ouid != uid)
    if foreign:
        names = ", ".join(":" + n for n in foreign)
        owner_names = set()
        for n in foreign:
            try:
                owner_names.add(pwd.getpwuid(x_sockets[n]).pw_name)
            except KeyError:
                owner_names.add("uid:" + str(x_sockets[n]))
        reasons.append(
            "X sockets " + names + " are owned by " + ", ".join(sorted(owner_names)) + " and must not be used"
        )
    has_graphical = any(
        s.get("uid") == uid
        and s.get("class") == "user"
        and s.get("state") == "active"
        and s.get("type") in ("x11", "wayland")
        for s in sessions
    )
    if not has_graphical:
        reasons.append("no active graphical (x11 or wayland) login session for uid " + str(uid))
    return {"type": "none", "display": "", "env": {}, "reason": "; ".join(reasons) or "no usable display"}


def find_display():
    return evaluate_display(
        list_x_sockets(), list_wayland_sockets(), gather_loginctl_sessions(), os.getuid()
    )


def attach_log_line(pid, profile, url, title, signin):
    fields = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pid": pid,
        "profile": profile,
        "url": sanitize_url(url),
        "title": redact_emails(title),
        "signin": signin,
    }
    return json.dumps(fields, ensure_ascii=False)


def _emit_listener():
    info = gather_listener()
    print("listener_state=" + info["state"])
    if info["state"] == "none":
        return 0
    print("listener_pid=" + str(info.get("pid") or ""))
    print("listener_addr=" + str(info.get("addr") or ""))
    print("listener_loopback_only=" + ("yes" if info.get("loopback_only") else "no"))
    print("listener_procs=" + ",".join(info.get("procs") or []))
    return 0 if info["state"] == "ours" else 2


def _emit_display():
    info = find_display()
    print("display_type=" + info["type"])
    if info["type"] == "x11":
        print("display=" + info["display"])
        print("env_DISPLAY=" + info["env"]["DISPLAY"])
    elif info["type"] == "wayland":
        print("wayland_display=" + info["display"])
        print("env_WAYLAND_DISPLAY=" + info["env"]["WAYLAND_DISPLAY"])
    else:
        print("reason=" + info["reason"])
    return 0 if info["type"] != "none" else 3


def _emit_cdp():
    info = cdp_version()
    print("cdp_reachable=" + ("yes" if info["reachable"] else "no"))
    print("cdp_valid=" + ("yes" if info["ok"] else "no"))
    print("browser=" + info["browser"])
    return 0 if info["ok"] else 1


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "listener":
        return _emit_listener()
    if cmd == "display":
        return _emit_display()
    if cmd == "cdp":
        return _emit_cdp()
    sys.stderr.write("usage: pwharness.py listener|display|cdp\n")
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))
