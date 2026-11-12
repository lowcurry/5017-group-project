#!/usr/bin/env python3
"""Compare freeze-time code hashes with the packaged source files.

Usage:
    python3 tools/check_code_provenance.py /path/to/MSBD5017_Public_Handoff
    python3 tools/check_code_provenance.py /path/to/MSBD5017_Public_Handoff --strict

The tool reads experiments/formal_v1/freeze.json and hashes each file listed
under "code" in curve_audit/. It reports OK/DIFF per file and a summary.

Exit status is 0 unless --strict is used and any file differs. The packaged
verifier is a later revision; a DIFF is expected for the files changed after
the freeze and is recorded in LIMITATIONS.md rather than hidden.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="extracted MSBD5017_Public_Handoff directory")
    parser.add_argument("--strict", action="store_true", help="exit 1 if any hash differs")
    args = parser.parse_args(argv)

    package = args.package.resolve()
    freeze_path = package / "experiments" / "formal_v1" / "freeze.json"
    if not freeze_path.is_file():
        print(f"missing freeze record: {freeze_path}", file=sys.stderr)
        return 2

    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    code = freeze.get("code")
    if not isinstance(code, dict) or not code:
        print("freeze.json has no code hash map", file=sys.stderr)
        return 2

    mismatches: list[str] = []
    missing: list[str] = []
    for name, expected in sorted(code.items()):
        path = package / "curve_audit" / name
        if not path.is_file():
            missing.append(name)
            print(f"MISS {name}")
            continue
        actual = sha256_file(path)
        if actual == expected:
            print(f"OK   {name}")
        else:
            mismatches.append(name)
            print(f"DIFF {name}  freeze={expected[:12]}  file={actual[:12]}")

    print()
    print(f"checked {len(code)} files: {len(code) - len(mismatches) - len(missing)} match, "
          f"{len(mismatches)} differ, {len(missing)} missing")
    if mismatches or missing:
        print("The packaged verifier is not byte-identical to the freeze-time code.")
        print("See LIMITATIONS.md section 4 for the provenance record.")
    if args.strict and (mismatches or missing):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
