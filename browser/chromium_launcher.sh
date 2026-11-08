#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${1:-$SCRIPT_DIR/.launch.env}"

[ -r "$ENV_FILE" ] || { echo "ERROR: missing launch env file: $ENV_FILE" >&2; exit 1; }
. "$ENV_FILE"

[ -n "${CHROME:-}" ] && [ -x "$CHROME" ] || { echo "ERROR: CHROME not executable: ${CHROME:-unset}" >&2; exit 1; }
[ -n "${PROFILE:-}" ] || { echo "ERROR: PROFILE unset" >&2; exit 1; }
[ -n "${PORT:-}" ] || { echo "ERROR: PORT unset" >&2; exit 1; }
[ -n "${ENTRANCE_URL:-}" ] || { echo "ERROR: ENTRANCE_URL unset" >&2; exit 1; }

EXTRA_ARGS=()
if [ -n "${ENV_WAYLAND_DISPLAY:-}" ]; then
    export WAYLAND_DISPLAY="$ENV_WAYLAND_DISPLAY"
    EXTRA_ARGS+=(--ozone-platform-hint=auto)
fi
if [ -n "${ENV_DISPLAY:-}" ]; then
    export DISPLAY="$ENV_DISPLAY"
fi

exec "$CHROME" \
    --user-data-dir="$PROFILE" \
    --remote-debugging-address=127.0.0.1 \
    --remote-debugging-port="$PORT" \
    --no-first-run \
    --no-default-browser-check \
    --window-size=1440,900 \
    ${EXTRA_ARGS+"${EXTRA_ARGS[@]}"} \
    "$ENTRANCE_URL"
