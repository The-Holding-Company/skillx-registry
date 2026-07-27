#!/usr/bin/env python3
"""Verify registry entries against their live, signed artifacts.

For each entry: run `skillx verify` against the live URL, check the registry
sha256 matches the signature payload, and require a transparency-log line with
the same digest. Exits nonzero on any failure.

Two modes:

  (default, full sweep)  Verify every entry. Use this on a schedule as a drift
                         monitor — it catches a published artifact whose bytes
                         changed on its host after it was logged.

  --changed-only         Verify only entries added/changed versus a base ref
                         (--base). Use this as the PR/merge gate: a new publish
                         must not be blocked by some *other* publisher's host
                         being briefly down, and re-verifying the whole registry
                         on every PR is what makes this gate flake.

Transient failures (artifact not fetchable yet — e.g. a first-party skill whose
landing-pages deploy is still in flight) are retried with backoff. An INVALID
result (signature/kid/digest failure) is a hard fail and is NEVER retried.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_registry_at(ref: str) -> dict:
    """registry/skills.json as of a git ref; {} if absent (e.g. new registry)."""
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{ref}:registry/skills.json"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def entry_key(skill: dict) -> tuple:
    """Identity that must be re-verified if any part changes."""
    return (skill.get("name"), skill.get("url"), skill.get("sha256"))


def verify_one(skill: dict, log_digests: set, retries: int, wait: int) -> bool:
    name, url = skill.get("name"), skill.get("url")
    attempt = 0
    while True:
        attempt += 1
        proc = subprocess.run(
            [sys.executable, str(ROOT / "skillx.py"), "verify", url],
            capture_output=True, text=True, timeout=120,
        )
        out = proc.stdout + proc.stderr
        verified = proc.returncode == 0 and "VERIFIED" in proc.stdout
        digest_ok = skill.get("sha256") in proc.stdout
        logged = skill.get("sha256") in log_digests

        if verified and digest_ok and logged:
            print(f"OK    {name}  verified=True digest=True logged={logged}")
            return True

        # A real signature/digest failure is authoritative — never retry it.
        invalid = "INVALID" in out
        transient = not invalid and not verified

        if transient and attempt <= retries:
            print(
                f"...   {name}  not verifiable yet "
                f"(attempt {attempt}/{retries + 1}); retrying in {wait}s"
            )
            time.sleep(wait)
            continue

        print(
            f"FAIL  {name}  verified={verified} digest={digest_ok} "
            f"logged={logged}"
            + ("  [INVALID — not retried]" if invalid else "")
        )
        print(out.strip())
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--changed-only", action="store_true",
        help="verify only entries added/changed vs --base (PR/merge gate mode)",
    )
    ap.add_argument(
        "--base", default="origin/main",
        help="git ref to diff against when --changed-only (default: origin/main)",
    )
    ap.add_argument(
        "--retries", type=int, default=4,
        help="retries for transient (not-yet-live) failures (default: 4)",
    )
    ap.add_argument(
        "--retry-wait", type=int, default=15,
        help="seconds between transient retries (default: 15)",
    )
    args = ap.parse_args()

    registry = json.loads((ROOT / "registry" / "skills.json").read_text())
    log_digests = {
        json.loads(line)["sha256"]
        for line in (ROOT / "log" / "transparency.jsonl").read_text().splitlines()
        if line.strip()
    }
    skills = registry.get("skills", [])

    if args.changed_only:
        base_skills = load_registry_at(args.base).get("skills", [])
        base_keys = {entry_key(s) for s in base_skills}
        skills = [s for s in skills if entry_key(s) not in base_keys]
        if not skills:
            print(f"No registry entries changed vs {args.base}; nothing to verify.")
            sys.exit(0)
        print(
            f"Changed-only: verifying {len(skills)} new/changed "
            f"entr{'y' if len(skills) == 1 else 'ies'} vs {args.base}."
        )

    failures = sum(
        0 if verify_one(s, log_digests, args.retries, args.retry_wait) else 1
        for s in skills
    )
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
