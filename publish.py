#!/usr/bin/env python3
"""
publish.py - Publish this repo to GitHub through the REST API only.

Why not `git push`?
------------------
In networks where `github.com:443` is blocked but `api.github.com` is reachable
(a common situation on mainland China connections), `git push` cannot connect at
all, yet the REST API works fine. This script builds the commit server-side with
the Git Data API: blobs -> tree -> commit -> ref. No git transport involved.

Why not username/password?
--------------------------
GitHub removed password authentication for both git and API in 2021/2020.
An account password cannot be exchanged for a token without reaching
`github.com` (device flow also lives there). You need a Personal Access Token.

Get one at: https://github.com/settings/tokens  (classic, scope: `repo`)
If github.com is unreachable from your machine, create the token on a phone or
any other network - it is just a string, paste it here.

Usage
-----
    set GITHUB_TOKEN=ghp_xxx
    python publish.py --repo ironcore --description "..." 
    python publish.py --dry-run          # show what would be uploaded

Options
-------
    --repo NAME        repository name (default: ironcore)
    --description STR  repo description
    --private          create a private repo
    --branch NAME      default branch (default: main)
    --dir PATH         source directory (default: script's parent)
    --dry-run          list files and sizes, no network writes
"""

import os
import sys
import json
import base64
import argparse
import urllib.request
import urllib.error

API = "https://api.github.com"


def api(method, path, token=None, payload=None, timeout=60):
    url = API + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "ironcore-publish")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body[:400]}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


SKIP_DIRS = {".git", "__pycache__", "extract", ".venv", "node_modules", ".idea"}
SKIP_EXT = {".pyc", ".log"}


def collect(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1] in SKIP_EXT:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            out.append((rel, full))
    out.sort()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="ironcore")
    ap.add_argument("--description",
                    default="Verifiable system-prompt override kit for local LLM agents")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--message", default="IronCore: initial release")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = collect(args.dir)
    if not files:
        print("no files found in", args.dir)
        return 1

    print(f"source : {args.dir}")
    print(f"files  : {len(files)}")
    total = 0
    for rel, full in files:
        sz = os.path.getsize(full)
        total += sz
        print(f"  {sz:>8}  {rel}")
    print(f"total  : {total} bytes")
    if args.dry_run:
        print("\n[dry-run] nothing sent.")
        return 0

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("\nGITHUB_TOKEN is not set. Create a classic token with the `repo` scope at\n"
              "  https://github.com/settings/tokens\n"
              "then:  set GITHUB_TOKEN=ghp_xxx   (Windows cmd)\n"
              "   or:  $env:GITHUB_TOKEN='ghp_xxx'  (PowerShell)\n"
              "   or:  export GITHUB_TOKEN=ghp_xxx  (bash)", file=sys.stderr)
        return 2

    code, me = api("GET", "/user", token)
    if code != 200:
        print(f"token rejected ({code}): {me}", file=sys.stderr)
        return 3
    owner = me["login"]
    print(f"\nauthenticated as: {owner}")

    # 1. create repo (idempotent)
    code, repo = api("POST", "/user/repos", token, {
        "name": args.repo,
        "description": args.description,
        "private": args.private,
        "auto_init": False,
        "has_issues": True,
        "has_wiki": False,
    })
    if code == 201:
        print(f"created repo: {repo['full_name']}")
    elif code == 422 and "already exists" in json.dumps(repo):
        code, repo = api("GET", f"/repos/{owner}/{args.repo}", token)
        print(f"repo exists, reusing: {repo.get('full_name')}")
    else:
        print(f"repo creation failed ({code}): {repo}", file=sys.stderr)
        return 4

    full = repo["full_name"]

    # 2. blobs
    tree = []
    for rel, path_ in files:
        raw = open(path_, "rb").read()
        try:
            text = raw.decode("utf-8")
            payload = {"content": text, "encoding": "utf-8"}
        except UnicodeDecodeError:
            payload = {"content": base64.b64encode(raw).decode(), "encoding": "base64"}
        code, blob = api("POST", f"/repos/{full}/git/blobs", token, payload)
        if code != 201:
            print(f"  blob failed for {rel}: {code} {blob}", file=sys.stderr)
            return 5
        tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"  uploaded blob {rel}")

    # 3. tree
    code, tr = api("POST", f"/repos/{full}/git/trees", token, {"tree": tree})
    if code != 201:
        print(f"tree failed ({code}): {tr}", file=sys.stderr)
        return 6
    print(f"tree: {tr['sha']}")

    # 4. commit (no parents for a fresh repo)
    code, cm = api("POST", f"/repos/{full}/git/commits", token,
                   {"message": args.message, "tree": tr["sha"], "parents": []})
    if code != 201:
        print(f"commit failed ({code}): {cm}", file=sys.stderr)
        return 7
    print(f"commit: {cm['sha']}")

    # 5. point branch at commit
    code, ref = api("POST", f"/repos/{full}/git/refs", token,
                    {"ref": f"refs/heads/{args.branch}", "sha": cm["sha"]})
    if code == 422:
        code, ref = api("PATCH", f"/repos/{full}/git/refs/heads/{args.branch}", token,
                        {"sha": cm["sha"], "force": True})
    if code not in (200, 201):
        print(f"ref update failed ({code}): {ref}", file=sys.stderr)
        return 8

    print()
    print("DONE")
    print(f"  https://github.com/{full}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
