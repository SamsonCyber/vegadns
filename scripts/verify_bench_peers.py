#!/usr/bin/env python3
"""Verify peer tools are real installable binaries (not empty stubs)."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def which(name: str) -> str | None:
    hit = shutil.which(name) or shutil.which(name + ".exe")
    if hit:
        return hit
    for p in (
        Path.home() / "go" / "bin" / name,
        Path.home() / "go" / "bin" / f"{name}.exe",
        Path("/usr/local/bin") / name,
        Path("/usr/bin") / name,
    ):
        if p.exists():
            return str(p)
    return None


def main() -> int:
    tools = ["massdns", "dnsx", "puredns", "shuffledns", "gobuster", "amass"]
    rows = []
    for t in tools:
        path = which(t)
        if not path:
            rows.append((t, "MISSING", "", 0))
            continue
        size = Path(path).stat().st_size
        # real binaries are large enough; reject empty stubs
        kind = "ok"
        if size < 10_000:
            kind = "SUSPECT_TINY"
        # try file(1)
        file_out = ""
        try:
            p = subprocess.run(["file", path], capture_output=True, text=True, timeout=5)
            file_out = (p.stdout or "").strip()
        except Exception:
            file_out = f"size={size}"
        if "ASCII text" in file_out or "empty" in file_out.lower():
            kind = "NOT_BINARY"
        rows.append((t, kind, path, size))

    print(f"{'tool':<12} {'status':<14} {'size':>10}  path")
    print("-" * 80)
    for t, kind, path, size in rows:
        print(f"{t:<12} {kind:<14} {size:>10}  {path}")

    # Require at least massdns + one wrapper OR dnsx for a meaningful multi-tool race
    ok_mass = any(r[0] == "massdns" and r[1] == "ok" for r in rows)
    ok_dnsx = any(r[0] == "dnsx" and r[1] == "ok" for r in rows)
    if not (ok_mass or ok_dnsx):
        print("\nFAIL: need at least real massdns or dnsx for peer bench", file=sys.stderr)
        return 1
    print("\nOK: peer inventory complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
