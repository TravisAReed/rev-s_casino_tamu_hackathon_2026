import argparse
import json
from . import core

def main():
    parser = argparse.ArgumentParser(description="Attach to existing casino Chromium only")
    sub = parser.add_subparsers(dest="command",required=True)
    sub.add_parser("progress")
    for name in ("open","transcript","attempt","attempts"):
        item = sub.add_parser(name)
        item.add_argument("--level",type=int,required=name!="attempts")
        if name == "attempt":
            item.add_argument("--text",required=True)
            item.add_argument("--dedupe",action="store_true",help="Reuse latest identical recorded attempt instead of sending")
    args = parser.parse_args()
    if args.command == "progress": result = core.get_progress()
    elif args.command == "open": result = core.open_level(args.level)
    elif args.command == "transcript": result = core.get_transcript(args.level)
    elif args.command == "attempts": result = core.list_attempts(args.level)
    else: result = core.send_message(args.level,args.text,dedupe=args.dedupe)
    print(json.dumps(result,ensure_ascii=False,indent=2))
