#!/usr/bin/env bash
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if curl -fsS http://127.0.0.1:9333/json/version 2>/dev/null; then echo; echo "UP pid=$(cat "$ROOT/browser/chromium.pid" 2>/dev/null)";
  curl -fsS http://127.0.0.1:9333/json/list | grep -E '"(title|url)"'; else echo "DOWN"; exit 1; fi
