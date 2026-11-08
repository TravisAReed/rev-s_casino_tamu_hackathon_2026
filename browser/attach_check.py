#!/usr/bin/env python3
"""Attach to the running Chromium over CDP, read the casino entrance in a NEW tab, report state, close only that tab. Never launches a browser; never sends prompts."""
import json, re, sys
from playwright.sync_api import sync_playwright
URL = "https://td-revs.vercel.app/casino/entrance"
with sync_playwright() as p:
    try:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9333")
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"no browser on 9333: {e}"})); sys.exit(1)
    ctx = b.contexts[0]
    page = ctx.new_page()
    try:
        page.goto(URL, wait_until="networkidle", timeout=30000)
        text = page.inner_text("body")
        low = text.lower()
        signed_in = not any(k in low for k in ["sign in with google", "continue with google", "log in"]) 
        m = re.search(r"([\d,]+)\s*tokens?", text, re.I)
        print(json.dumps({"ok": True, "url": page.url, "title": page.title(),
            "signed_in_guess": signed_in, "tokens": m.group(1) if m else None,
            "open_tabs": len(ctx.pages), "body_head": text[:400]}, indent=1))
    finally:
        page.close()  # only our tab
    # do not b.close(): that is a disconnect for CDP, but be explicit and leave browser alive
