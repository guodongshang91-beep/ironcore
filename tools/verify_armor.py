#!/usr/bin/env python3
"""
verify_armor.py - Prove that your prompt actually reached the model.

Injection is not the same as effect. A file can sit in the right directory and
still never be read (wrong path, stale process, mode-specific prompt overriding
it). This tool reads the agent's own request trace and shows you the exact
system prompt that was sent on the wire.

What it looks for
-----------------
Agent runtimes usually log every LLM call. In box-agent / raccoon style homes
that is `log/sessions/*.jsonl` with an `llm.request` event carrying
`data.messages`. We pull `messages[0].content` (role=system) from the newest
request and check for your markers.

Usage
-----
    python verify_armor.py                      # auto-locate home, newest request
    python verify_armor.py --marker <armor_core
    python verify_armor.py --forbid "禁止生成政治" --forbid "礼貌拒答"
    python verify_armor.py --dump system.txt    # save the captured system prompt
    python verify_armor.py --home ~/.my-agent   # explicit home

Exit code 0 = all markers present and no forbidden strings. 1 = verification failed.
"""

import os
import sys
import json
import glob
import argparse

HOME = os.path.expanduser("~")

HOMES = [
    ".office-raccoon/agent",
    ".box-agent",
    ".raccoon",
    ".claude",
    ".codex",
    ".zcode",
    "AppData/Roaming/office-raccoon/agent",
]


def find_trace_files(home=None, limit=40):
    files = []
    if home:
        roots = [os.path.expanduser(home)]
    else:
        roots = [os.path.join(HOME, h.replace("/", os.sep)) for h in HOMES]
    for r in roots:
        files += glob.glob(os.path.join(r, "log", "sessions", "*.jsonl"))
    files = [f for f in files if os.path.isfile(f)]
    files.sort(key=os.path.getmtime, reverse=True)
    return files[:limit]


def newest_system_prompt(files):
    """Walk traces newest-first, return (system_text, file, user_text)."""
    for f in files:
        system = None
        user = None
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"llm.request"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    if o.get("event") != "llm.request":
                        continue
                    msgs = (o.get("data") or {}).get("messages") or []
                    if not msgs:
                        continue
                    if msgs[0].get("role") == "system":
                        system = msgs[0].get("content") or ""
                        for m in msgs:
                            if m.get("role") == "user":
                                user = m.get("content") or ""
                                break
        except OSError:
            continue
        if system:
            return system, f, user
    return None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home")
    ap.add_argument("--marker", action="append", default=[])
    ap.add_argument("--forbid", action="append", default=[])
    ap.add_argument("--dump")
    ap.add_argument("--head", type=int, default=3)
    args = ap.parse_args()

    files = find_trace_files(args.home)
    if not files:
        print("no trace files found. Is the agent running and has it handled a message?")
        return 1

    system, src, user = newest_system_prompt(files)
    if not system:
        print(f"trace files exist ({len(files)}) but none contain an llm.request with a system message.")
        return 1

    print(f"source   : {src}")
    print(f"mtime    : {os.path.getmtime(src) and __import__('time').strftime('%Y-%m-%d %H:%M:%S', __import__('time').localtime(os.path.getmtime(src)))}")
    print(f"system   : {len(system)} chars")
    print(f"first    : {system.splitlines()[0][:80]}")
    if user:
        print(f"last ask : {user[:100].replace(chr(10), ' ')}")
    print()

    ok = True
    if args.marker:
        print("required markers:")
        for m in args.marker:
            hit = m in system
            ok = ok and hit
            print(f"  [{'OK  ' if hit else 'FAIL'}] {m}")
    if args.forbid:
        print("forbidden strings (must be absent):")
        for m in args.forbid:
            hit = m in system
            ok = ok and not hit
            print(f"  [{'OK  ' if not hit else 'FAIL'}] {m}")
    if not args.marker and not args.forbid:
        print("(no --marker/--forbid given; showing head only)")

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as fh:
            fh.write(system)
        print(f"\ndumped system prompt -> {args.dump}")

    print()
    print("RESULT:", "ARMED - prompt reached the model" if ok else "NOT ARMED - check path / restart / mode prompt")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
