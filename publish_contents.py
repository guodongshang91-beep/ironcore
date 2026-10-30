#!/usr/bin/env python3
"""
publish_contents.py - Upload files to an (empty) GitHub repo via the Contents API.

Use this instead of publish.py when the repo was just created and is still empty:
Git Data API (blobs/trees) returns 409 {"message":"Git Repository is empty."} on a
repo with no commits, while the Contents API bootstraps it on the first PUT.

Idempotent: existing files are fetched for their sha and overwritten.

    set GITHUB_TOKEN=ghp_xxx
    python publish_contents.py --repo ironcore
"""

import os
import sys
import json
import base64
import argparse
import urllib.request
import urllib.error

API = "https://api.github.com"
SKIP_DIRS = {".git", "__pycache__", "extract", ".venv", "node_modules", ".idea"}
SKIP_EXT = {".pyc", ".log"}


def api(method, path, token, payload=None, timeout=90):
    req = urllib.request.Request(API + path,
                                 data=json.dumps(payload).encode() if payload else None,
                                 method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "ironcore-publish")
    req.add_header("Authorization", "Bearer " + token)
    if payload:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            return e.code, {"raw": "<no body>"}
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body[:300]}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def collect(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1] in SKIP_EXT:
                continue
            full = os.path.join(dirpath, fn)
            out.append((os.path.relpath(full, root).replace(os.sep, "/"), full))
    out.sort()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="ironcore")
    ap.add_argument("--owner")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--message", default="Initial commit: IronCore")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("GITHUB_TOKEN not set", file=sys.stderr)
        return 2

    code, me = api("GET", "/user", token)
    if code != 200:
        print(f"token rejected ({code}): {me}", file=sys.stderr)
        return 3
    owner = args.owner or me["login"]
    print(f"auth: {owner}")

    files = collect(args.dir)
    print(f"files: {len(files)}")

    ok = 0
    for i, (rel, full) in enumerate(files, 1):
        raw = open(full, "rb").read()
        payload = {
            "message": args.message if i == 1 else f"Add {rel}",
            "content": base64.b64encode(raw).decode(),
            "branch": args.branch,
        }
        # existing file needs its sha to be overwritten
        code, cur = api("GET", f"/repos/{owner}/{args.repo}/contents/{rel}?ref={args.branch}", token)
        if code == 200 and isinstance(cur, dict) and cur.get("sha"):
            payload["sha"] = cur["sha"]

        code, res = api("PUT", f"/repos/{owner}/{args.repo}/contents/{rel}", token, payload)
        if code in (200, 201):
            ok += 1
            print(f"  [{i:2d}/{len(files)}] OK   {rel}  ({len(raw)} B)")
        else:
            print(f"  [{i:2d}/{len(files)}] FAIL {rel}  {code} {str(res)[:200]}")

    print()
    print(f"uploaded {ok}/{len(files)}")
    print(f"  https://github.com/{owner}/{args.repo}")
    return 0 if ok == len(files) else 1


if __name__ == "__main__":
    sys.exit(main())
