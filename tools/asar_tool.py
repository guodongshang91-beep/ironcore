#!/usr/bin/env python3
"""
asar_tool.py - Read files out of an Electron app.asar without unpacking it.

Desktop AI apps are usually Electron shells around a local agent runtime. The
system prompt, config loader and safety code all live inside app.asar (often
hundreds of MB). You rarely need the whole thing - just a handful of files.
This reads individual entries straight out of the archive via its JSON header.

Usage
-----
    python asar_tool.py --list "prompt"                  # find files by substring
    python asar_tool.py --cat /build/main/prompt.js      # print one file
    python asar_tool.py --dump /build/ main/ --out ./x    # extract a subtree
    python asar_tool.py --asar "C:\\path\\app.asar" --list system

Notes
-----
* asar layout: 8-byte pickle header | 4-byte header-size | JSON header | data
* `unpacked: true` entries live in app.asar.unpacked/ next to the archive
* A PyInstaller runtime (box-agent-runtime/bin/*.exe) is a different format and
  is usually encrypted - don't burn time on it; use trace-based verification
  (verify_armor.py) instead.
"""

import os
import sys
import json
import struct
import argparse


class Asar:
    def __init__(self, path):
        self.path = path
        self.entries = {}
        with open(path, "rb") as f:
            head = f.read(16)
            if len(head) < 16:
                raise ValueError("not an asar archive")
            self.header_size = struct.unpack("<I", head[12:16])[0]
            header = json.loads(f.read(self.header_size).decode("utf-8", "replace"))
        base = 16 + self.header_size
        self.base = base + (4 - (base % 4)) % 4
        self._walk(header)

    def _walk(self, node, prefix=""):
        for name, meta in node.get("files", {}).items():
            p = prefix + "/" + name
            if "files" in meta:
                self._walk(meta, p)
            else:
                self.entries[p] = (meta.get("offset"), meta.get("size", 0),
                                   meta.get("unpacked", False))

    def read(self, entry):
        if not entry.startswith("/"):
            entry = "/" + entry
        off, size, unpacked = self.entries[entry]
        if unpacked:
            real = os.path.join(os.path.dirname(self.path), "app.asar.unpacked",
                                entry.lstrip("/").replace("/", os.sep))
            return open(real, "rb").read()
        with open(self.path, "rb") as f:
            f.seek(self.base + int(off))
            return f.read(size)


def autodetect():
    roots = [
        r"C:\Program Files",
        r"C:\Program Files (x86)",
        os.path.expanduser("~/AppData/Local/Programs"),
        "/Applications",
        os.path.expanduser("~/.local/share"),
    ]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.count(os.sep) - root.count(os.sep) > 3:
                dirnames[:] = []
                continue
            if "app.asar" in filenames:
                return os.path.join(dirpath, "app.asar")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asar", help="path to app.asar (auto-detected if omitted)")
    ap.add_argument("--list", metavar="PATTERN", help="list entries containing PATTERN")
    ap.add_argument("--cat", metavar="ENTRY")
    ap.add_argument("--dump", nargs=2, metavar=("PATTERN", "SUBDIR"))
    ap.add_argument("--out", default="./extract")
    args = ap.parse_args()

    path = args.asar or autodetect()
    if not path or not os.path.isfile(path):
        print("app.asar not found; pass --asar", file=sys.stderr)
        return 1

    a = Asar(path)
    print(f"# {path}: {len(a.entries)} entries", file=sys.stderr)

    if args.list:
        pat = args.list.lower()
        for p in sorted(a.entries):
            if pat in p.lower():
                off, size, unpacked = a.entries[p]
                print(f"{size:>10} {'unpacked' if unpacked else '':>9} {p}")
        return 0

    if args.cat:
        sys.stdout.buffer.write(a.read(args.cat))
        return 0

    if args.dump:
        pat, sub = args.dump
        n = 0
        for p in sorted(a.entries):
            if pat.lower() in p.lower() and p.startswith("/" + sub.strip("/")):
                dst = os.path.join(args.out, p.lstrip("/").replace("/", os.sep))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                open(dst, "wb").write(a.read(p))
                n += 1
        print(f"dumped {n} files to {args.out}", file=sys.stderr)
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
