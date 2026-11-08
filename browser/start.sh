#!/usr/bin/env bash
# Launch ONE long-lived headed Chromium with CDP on 127.0.0.1:9333. Idempotent.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT=9333
URL="https://td-revs.vercel.app/casino/entrance"
if curl -fsS "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  echo "already running on :$PORT"; exit 0; fi
CHROME="$(ls -d "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux*/chrome | sort | tail -1)"
UID_N=$(id -u)
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$UID_N}"
export DISPLAY="${DISPLAY:-:0}"
if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -S "$XDG_RUNTIME_DIR/wayland-0" ]; then export WAYLAND_DISPLAY=wayland-0; fi
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
mkdir -p "$ROOT/.pw-profile" "$ROOT/browser/logs"
EXTRA=(${CHROME_FLAGS:-})
setsid nohup "$CHROME" "${EXTRA[@]}" --user-data-dir="$ROOT/.pw-profile" \
  --remote-debugging-port=$PORT --remote-debugging-address=127.0.0.1 \
  --no-first-run --no-default-browser-check "$URL" \
  >>"$ROOT/browser/logs/chromium.log" 2>&1 < /dev/null &
echo $! > "$ROOT/browser/chromium.pid"
for i in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1 && { echo "started pid $(cat "$ROOT/browser/chromium.pid") on :$PORT"; exit 0; }
  sleep 0.5; done
echo "failed to come up; see browser/logs/chromium.log" >&2; exit 1
