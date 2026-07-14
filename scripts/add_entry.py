#!/usr/bin/env python3
"""Add (or update) a registry entry from a skill URL.

Runs `skillx.py verify --scan <url>`; on VERIFIED it appends a line to the
append-only transparency log and inserts/updates the registry entry for that
URL. Existing log lines are never touched. Exits nonzero if verification
fails or the exact content (same sha256) is already listed.

Usage:
  add_entry.py --url https://example.com/SKILL.md \
               --description "..." [--version 1.0.0] [--name override]
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "registry" / "skills.json"
LOG = ROOT / "log" / "transparency.jsonl"


def fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--description", required=True)
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--name", default="")
    args = ap.parse_args()

    if not re.match(r"^https://[^\s]+$", args.url):
        fail(f"not an https URL: {args.url!r}")

    proc = subprocess.run(
        [sys.executable, str(ROOT / "skillx.py"), "verify", "--scan", args.url],
        capture_output=True, text=True, timeout=120,
    )
    print(proc.stdout)
    if proc.returncode != 0 or "VERIFIED" not in proc.stdout:
        fail(f"verification did not pass (exit {proc.returncode})\n{proc.stderr}")

    fields = dict(
        re.findall(r"^\s+(skill|publisher|sha256):\s+(\S+)", proc.stdout, re.M)
    )
    digest, kid = fields.get("sha256"), fields.get("publisher")
    if not digest or not kid:
        fail("could not parse sha256/publisher from verify output")
    name = args.name or fields.get("skill", "").strip("()") or "unnamed"
    if name == "unnamed":
        fail("skill has no name in its signature payload; pass --name")

    with urllib.request.urlopen(args.url + ".sig", timeout=30) as r:
        sig_sha256 = hashlib.sha256(r.read()).hexdigest()

    registry = json.loads(REGISTRY.read_text())
    lines = [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()]
    if any(l["sha256"] == digest for l in lines):
        fail(f"this exact content is already in the log (sha256 {digest[:12]}…)")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    seq = max((l["seq"] for l in lines), default=0) + 1
    entry = {
        "name": name,
        "description": args.description,
        "version": args.version,
        "url": args.url,
        "sig": args.url + ".sig",
        "sha256": digest,
        "publisher": {"kid": kid, "verified": False},
        "log_seq": seq,
    }

    skills = registry.get("skills", [])
    existing = next((i for i, s in enumerate(skills) if s["url"] == args.url), None)
    if existing is not None:
        entry["publisher"]["verified"] = skills[existing]["publisher"].get("verified", False)
        skills[existing] = entry
        action = "updated"
    else:
        skills.append(entry)
        action = "added"
    registry["skills"] = skills
    registry["updated_at"] = now

    with LOG.open("a") as f:
        f.write(json.dumps({
            "v": 1, "seq": seq, "logged_at": now, "skill": name,
            "url": args.url, "sha256": digest, "kid": kid,
            "sig_sha256": sig_sha256,
        }, separators=(",", ":")) + "\n")
    REGISTRY.write_text(json.dumps(registry, indent=2) + "\n")

    print(f"{action}: {name} v{args.version} (log seq {seq}, publisher {kid})")
    out = Path(os.environ.get("ADD_ENTRY_OUT", "/tmp/add_entry_out.json"))
    out.write_text(json.dumps({"name": name, "seq": seq, "kid": kid, "sha256": digest, "action": action}))


if __name__ == "__main__":
    main()
