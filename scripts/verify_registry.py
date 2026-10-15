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

Every non-OK entry is classified so automation can route it:

  resigned      Live artifact VERIFIED under the *registered* kid, but its
                digest moved — the publisher re-published and re-signed
                without re-registering. Benign; auto-healable via
                scripts/heal_resigned.py (human PR merge still the gate).
  kid-mismatch  Live artifact verifies, but under a different key than the
                registry recorded. Never auto-healed — human review.
  invalid       Signature/digest verification failed outright (INVALID).
                Possible tamper. Never retried, never auto-healed.
  unlogged      Registry digest matches the live artifact but has no
                transparency-log line — registry/log inconsistency.
  unreachable   Artifact could not be fetched/verified (transient).
                Retried with backoff; INVALID results are NEVER retried.

--report / --report-md write JSON / Markdown summaries for CI to attach to
heal PRs and alert issues.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

AUTO_HEALABLE = {"resigned"}

STATUS_HELP = {
    "resigned": "validly re-signed by its registered publisher; auto-heal PR re-registers it",
    "kid-mismatch": "verifies under a DIFFERENT key than registered — human review required",
    "invalid": "signature/digest INVALID — possible tamper, human review required",
    "unlogged": "registry digest has no transparency-log line — registry/log inconsistency",
    "unreachable": "artifact not fetchable/verifiable after retries — host down or blocking CI",
}


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


def verify_one(skill: dict, log_digests: set, retries: int, wait: int) -> dict:
    name, url = skill.get("name"), skill.get("url")
    registered_sha = skill.get("sha256")
    registered_kid = (skill.get("publisher") or {}).get("kid")
    attempt = 0
    while True:
        attempt += 1
        proc = subprocess.run(
            [sys.executable, str(ROOT / "skillx.py"), "verify", url],
            capture_output=True, text=True, timeout=120,
        )
        out = proc.stdout + proc.stderr
        verified = proc.returncode == 0 and "VERIFIED" in proc.stdout
        live = dict(
            re.findall(r"^\s+(skill|publisher|sha256):\s+(\S+)", proc.stdout, re.M)
        )
        live_sha, live_kid = live.get("sha256"), live.get("publisher")
        digest_ok = bool(registered_sha) and registered_sha == live_sha
        logged = registered_sha in log_digests
        invalid = "INVALID" in out

        if verified and digest_ok and logged:
            status = "ok"
        elif invalid:
            status = "invalid"
        elif verified and digest_ok:
            status = "unlogged"
        elif verified:
            status = "resigned" if live_kid == registered_kid else "kid-mismatch"
        else:
            status = "unreachable"

        # Only transient fetch failures are retried; a real signature/digest
        # failure (or a successful verify of drifted content) is authoritative.
        if status == "unreachable" and attempt <= retries:
            print(
                f"...   {name}  not verifiable yet "
                f"(attempt {attempt}/{retries + 1}); retrying in {wait}s"
            )
            time.sleep(wait)
            continue
        break

    if status == "ok":
        print(f"OK    {name}  verified=True digest=True logged=True")
    else:
        print(
            f"FAIL  {name}  status={status} verified={verified} "
            f"digest={digest_ok} logged={logged}"
        )
        print(out.strip())
    return {
        "name": name,
        "url": url,
        "status": status,
        "registered_sha256": registered_sha,
        "live_sha256": live_sha,
        "registered_kid": registered_kid,
        "live_kid": live_kid,
        "auto_healable": status in AUTO_HEALABLE,
    }


def report_markdown(results: list) -> str:
    failures = [r for r in results if r["status"] != "ok"]
    lines = [
        f"Drift monitor: **{len(failures)} of {len(results)}** registry "
        "entries failed re-verification against their live artifacts.",
        "",
        "| skill | status | registered sha256 | live sha256 |",
        "|---|---|---|---|",
    ]
    for f in failures:
        reg = (f["registered_sha256"] or "?")[:12]
        liv = (f["live_sha256"] or "—")[:12]
        lines.append(f"| `{f['name']}` | **{f['status']}** | `{reg}…` | `{liv}…` |")
    lines.append("")
    for status in sorted({f["status"] for f in failures}):
        lines.append(f"- **{status}** — {STATUS_HELP[status]}")
    return "\n".join(lines) + "\n"


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
    ap.add_argument(
        "--report", metavar="FILE",
        help="write a JSON report of all failures (for heal/alert automation)",
    )
    ap.add_argument(
        "--report-md", metavar="FILE",
        help="write a Markdown failure summary (for PR/issue bodies)",
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

    results = [
        verify_one(s, log_digests, args.retries, args.retry_wait) for s in skills
    ]
    failures = [r for r in results if r["status"] != "ok"]

    if args.report:
        Path(args.report).write_text(json.dumps(
            {"total": len(results), "failures": failures}, indent=2,
        ) + "\n")
    if args.report_md:
        Path(args.report_md).write_text(report_markdown(results))

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
