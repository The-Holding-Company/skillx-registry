#!/usr/bin/env python3
"""CI gate for registry PRs: every skills.json entry must verify.

For each entry: run skillx verify against the live URL, check the registry
sha256 matches the signature payload, and require a transparency-log line
with the same digest. Exits nonzero on any failure.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
registry = json.loads((ROOT / "registry" / "skills.json").read_text())
log_digests = {
    json.loads(line)["sha256"]
    for line in (ROOT / "log" / "transparency.jsonl").read_text().splitlines()
    if line.strip()
}

failures = 0
for skill in registry.get("skills", []):
    name, url = skill.get("name"), skill.get("url")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "skillx.py"), "verify", url],
        capture_output=True, text=True, timeout=120,
    )
    verified = proc.returncode == 0 and "VERIFIED" in proc.stdout
    digest_ok = skill.get("sha256") in proc.stdout
    logged = skill.get("sha256") in log_digests
    status = "OK" if (verified and digest_ok and logged) else "FAIL"
    print(f"{status}  {name}  verified={verified} digest={digest_ok} logged={logged}")
    if status == "FAIL":
        print(proc.stdout or proc.stderr)
        failures += 1

sys.exit(1 if failures else 0)
