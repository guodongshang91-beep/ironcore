#!/usr/bin/env python3
"""
locate_home.py - Find the REAL agent home directory that a desktop app is using.

Why this exists
---------------
Most "prompt override" guides tell you to drop a file somewhere like
`~/.my-agent/config/system_prompt.md`. Half the time that directory exists,
the file gets created... and nothing reads it, because the desktop app uses a
different home (a differently-named dotdir, a userData path, a CLI home, etc.).

The reliable signal is not the app name. It is *which directory is being written
to right now*: session traces, locks, and pid files carry fresh mtimes. So we
scan every plausible agent home, score by freshness of runtime artifacts, and
report the winner with its config path.

Usage
-----
    python locate_home.py                 # human readable report
    python locate_home.py --json          # machine readable
    python locate_home.py --deploy prompt.md   # copy prompt into the winner's config/

Scoring
-------
  +50  log/sessions/*.jsonl modified within --hours (default 24)
  +30  sessions/*/session.jsonl modified within --hours
  +20  config/config.yaml exists
  +15  config/auth.json exists
  +10  run/*.json touched recently
   +5  a known prompt file already exists in config/
A directory with score 0 is a stale leftover; ignore it.
"""

import os
import sys
import json
import glob
import time
import shutil
import argparse

HOME = os.path.expanduser("~")

# Directories that commonly hold an agent runtime's private home.
CANDIDATE_NAMES = [
    ".office-raccoon/agent",
    ".box-agent",
    ".raccoon",
    ".claude",
    ".codex",
    ".zcode",
    ".dsh",
    ".opencode",
    ".aider",
    ".cursor",
    ".continue",
    ".config/raccoon",
    "AppData/Roaming/office-raccoon/agent",
    "AppData/Roaming/box-agent",
    "AppData/Local/office-raccoon/agent",
]

PROMPT_NAMES = ["system_prompt.md", "system.md", "prompt.md", "AGENTS.md"]


def candidate_paths():
    seen = set()
    out = []
    for name in CANDIDATE_NAMES:
        p = os.path.join(HOME, name.replace("/", os.sep))
        if os.path.isdir(p) and p not in seen:
            seen.add(p)
            out.append(p)
    # Also catch any top-level dotdir that looks like an agent home.
    try:
        for entry in os.scandir(HOME):
            if not entry.is_dir():
                continue
            n = entry.name.lower()
            if not entry.name.startswith("."):
                continue
            if any(k in n for k in ("agent", "raccoon", "claude", "codex", "llm")):
                if entry.path not in seen:
                    seen.add(entry.path)
                    out.append(entry.path)
    except OSError:
        pass
    return out


def newest_mtime(patterns, hours):
    cutoff = time.time() - hours * 3600
    best = 0.0
    files = []
    for pat in patterns:
        for f in glob.glob(pat, recursive=True):
            try:
                m = os.path.getmtime(f)
            except OSError:
                continue
            if m >= cutoff:
                files.append(f)
                best = max(best, m)
    return best, files


def score_dir(path, hours):
    detail = {}
    score = 0

    trace_m, trace_files = newest_mtime(
        [os.path.join(path, "log", "sessions", "*.jsonl")], hours)
    if trace_m:
        score += 50
        detail["live_traces"] = len(trace_files)

    sess_m, sess_files = newest_mtime(
        [os.path.join(path, "sessions", "*", "session.jsonl")], hours)
    if sess_m:
        score += 30
        detail["recent_sessions"] = len(sess_files)

    cfg_yaml = os.path.join(path, "config", "config.yaml")
    cfg_toml = os.path.join(path, "config", "config.toml")
    cfg_json = os.path.join(path, "config", "config.json")
    for c in (cfg_yaml, cfg_toml, cfg_json):
        if os.path.isfile(c):
            score += 20
            detail["config_file"] = c
            break

    auth = os.path.join(path, "config", "auth.json")
    if os.path.isfile(auth):
        score += 15
        detail["auth_file"] = True

    run_m, _ = newest_mtime([os.path.join(path, "run", "*.json")], hours)
    if run_m:
        score += 10

    cfg_dir = os.path.join(path, "config")
    existing = [n for n in PROMPT_NAMES if os.path.isfile(os.path.join(cfg_dir, n))]
    if existing:
        score += 5
        detail["existing_prompts"] = existing

    detail["last_write"] = time.strftime(
        "%Y-%m-%d %H:%M", time.localtime(max(trace_m, sess_m, 0))) if (trace_m or sess_m) else None
    return score, detail


def read_endpoint(cfg_path):
    """Best-effort peek at api_base / model so the user can sanity-check."""
    info = {}
    if not cfg_path or not os.path.isfile(cfg_path):
        return info
    try:
        for line in open(cfg_path, encoding="utf-8", errors="replace"):
            s = line.strip()
            for key in ("api_base", "base_url", "model", "provider"):
                if s.startswith(key + ":"):
                    info[key] = s.split(":", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--deploy", metavar="PROMPT_FILE",
                    help="copy this file to <winner>/config/system_prompt.md")
    ap.add_argument("--mode-file", metavar="NAME=FILE", action="append", default=[],
                    help="extra file to deploy, e.g. --mode-file code_prompt.md=my_code.md")
    args = ap.parse_args()

    rows = []
    for p in candidate_paths():
        sc, detail = score_dir(p, args.hours)
        rows.append({"path": p, "score": sc, "detail": detail})

    rows.sort(key=lambda r: r["score"], reverse=True)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print("agent home candidates (higher score = actually in use)\n")
        for r in rows:
            mark = "**" if r["score"] >= 50 else "  "
            print(f"{mark} [{r['score']:3d}] {r['path']}")
            d = r["detail"]
            if d.get("live_traces"):
                print(f"          live traces: {d['live_traces']}  last write: {d.get('last_write')}")
            if d.get("recent_sessions"):
                print(f"          recent sessions: {d['recent_sessions']}")
            if d.get("config_file"):
                ep = read_endpoint(d["config_file"])
                print(f"          config: {d['config_file']}")
                for k, v in ep.items():
                    print(f"            {k} = {v}")
            if d.get("existing_prompts"):
                print(f"          prompts present: {', '.join(d['existing_prompts'])}")
        print()
        if rows and rows[0]["score"] > 0:
            print(f"==> target: {rows[0]['path']}")
            print(f"==> drop your prompt at: {os.path.join(rows[0]['path'], 'config', 'system_prompt.md')}")
        else:
            print("No live agent home detected. Start the app, send one message, re-run.")

    if args.deploy:
        if not rows or rows[0]["score"] == 0:
            print("refusing to deploy: no live target", file=sys.stderr)
            return 1
        target = rows[0]["path"]
        cfg_dir = os.path.join(target, "config")
        os.makedirs(cfg_dir, exist_ok=True)
        shutil.copyfile(args.deploy, os.path.join(cfg_dir, "system_prompt.md"))
        print(f"deployed {args.deploy} -> {os.path.join(cfg_dir, 'system_prompt.md')}")
        for spec in args.mode_file:
            name, _, src = spec.partition("=")
            if not src:
                continue
            shutil.copyfile(src, os.path.join(cfg_dir, name))
            print(f"deployed {src} -> {os.path.join(cfg_dir, name)}")
        print("restart the agent runtime (close the app fully, or kill the agent process) to reload.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
