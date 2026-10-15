#!/usr/bin/env python3
"""Re-register artifacts the drift monitor classified as benign re-signs.

Reads a `verify_registry.py --report` JSON and, for every failure with status
"resigned" (live artifact VERIFIED under the registered kid, digest moved),
runs `add_entry.py --reregister` against it. Any other status (invalid,
kid-mismatch, unlogged, unreachable) is left alone — those must stay red for
a human. The caller (CI) commits the result as a PR; human merge remains the
publish gate.

Usage: heal_resigned.py <report.json>
Exits 0 whether or not anything was healed; check `git status` for changes.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    report = json.loads(Path(sys.argv[1]).read_text())
    resigned = [f for f in report["failures"] if f["status"] == "resigned"]
    skipped = [f for f in report["failures"] if f["status"] != "resigned"]
    for f in skipped:
        print(f"skip  {f['name']}  status={f['status']} (not auto-healable)")
    if not resigned:
        print("no benign re-signs to heal")
        return
    for f in resigned:
        print(f"heal  {f['name']}  {f['url']}")
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "add_entry.py"),
             "--url", f["url"], "--reregister"],
            check=True, timeout=300,
        )
    print(f"healed {len(resigned)} re-signed entr"
          f"{'y' if len(resigned) == 1 else 'ies'}")


if __name__ == "__main__":
    main()
