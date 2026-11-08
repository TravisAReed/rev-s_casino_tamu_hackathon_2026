"""CLI for the visible-UI planner. It never opens a browser."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from uiadapt.planner import (
    CT,
    RESULT_SCHEMA,
    apply_result,
    load_ledger,
    new_ledger,
    parse_now,
    plan_next,
    save_ledger,
    score_total,
)

DEFAULT_LEDGER = Path(__file__).resolve().parents[1] / "runs" / "ui-adapt" / "ledger.json"


def _now(value):
    if value:
        return parse_now(value)
    return datetime.now(CT)


def _print(payload):
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _cmd_seed(args):
    path = Path(args.ledger)
    if path.exists() and not args.force:
        print("ledger already exists; pass --force to replace it", file=sys.stderr)
        return 2
    save_ledger(path, new_ledger(_now(args.now)))
    _print({"ok": True, "ledger": str(path), "seed_total": score_total(load_ledger(path)["seed_bests"])})
    return 0


def _cmd_next(args):
    path = Path(args.ledger)
    if not path.exists():
        print("ledger missing; run: python -m uiadapt seed --ledger " + str(path), file=sys.stderr)
        return 2
    ledger = load_ledger(path)
    plan = plan_next(ledger, _now(args.now), ledger_path=path)
    save_ledger(path, ledger)
    text = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    sys.stdout.write(text)
    return 0


def _cmd_ingest(args):
    path = Path(args.ledger)
    if not path.exists():
        print("ledger missing; run seed first", file=sys.stderr)
        return 2
    try:
        result = json.loads(Path(args.result).read_text())
    except (OSError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    ledger = load_ledger(path)
    outcome = apply_result(ledger, result, _now(args.now))
    if outcome.get("ok"):
        save_ledger(path, ledger)
    _print(outcome)
    return 0 if outcome.get("ok") else 2


def _cmd_status(args):
    ledger = load_ledger(args.ledger)
    _print({
        "phase": ledger["phase"],
        "stop": ledger.get("stop"),
        "seed_total": ledger["seed_total"],
        "verified_total": score_total(ledger.get("verified_bests")),
        "verified_bests": ledger.get("verified_bests"),
        "chat_messages_used": ledger["chat_messages_used"],
        "replays": ledger["replays"],
        "pending_action_id": (ledger.get("pending_action") or {}).get("id"),
    })
    return 0


def _cmd_schema(_args):
    _print(RESULT_SCHEMA)
    return 0


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    common.add_argument("--now", help="ISO time in America/Chicago, for tests and the deadline gate")
    parser = argparse.ArgumentParser(description="Emit the next visible casino UI action and ingest its result")
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed", parents=[common], help="Write a fresh ledger with the verified baseline bests")
    seed.add_argument("--force", action="store_true")
    nxt = sub.add_parser("next", parents=[common], help="Print the next authorized action")
    nxt.add_argument("--out")
    ingest = sub.add_parser("ingest", parents=[common], help="Apply an observed UI result file")
    ingest.add_argument("--result", required=True)
    sub.add_parser("status", parents=[common])
    sub.add_parser("schema", parents=[common])
    args = parser.parse_args(argv)
    if args.command == "seed":
        return _cmd_seed(args)
    if args.command == "next":
        return _cmd_next(args)
    if args.command == "ingest":
        return _cmd_ingest(args)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "schema":
        return _cmd_schema(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
