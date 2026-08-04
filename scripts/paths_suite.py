#!/usr/bin/env python3
"""Run vegadns paths twice on fixtures; assert recall/precision/stability."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def find_vega() -> Path:
    for p in (
        ROOT / "target" / "release" / "vegadns.exe",
        ROOT / "target" / "release" / "vegadns",
    ):
        if p.exists():
            return p
    raise SystemExit("build release first")


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("paths_out")
    out.mkdir(parents=True, exist_ok=True)
    vega = find_vega()
    runs = []
    for i in (1, 2):
        names = out / f"paths_run{i}_urls.txt"
        stats = out / f"paths_run{i}_stats.json"
        log = out / f"paths_run{i}.log"
        cmd = [
            str(vega),
            "paths",
            "--mock-paths",
            str(ROOT / "fixtures" / "paths" / "hit_paths.txt"),
            "-w",
            str(ROOT / "fixtures" / "paths" / "wordlist.txt"),
            "--known-true",
            str(ROOT / "fixtures" / "paths" / "known_true.txt"),
            "-o",
            str(names),
            "--stats-json",
            str(stats),
            "--status",
            "200",
            "--concurrency",
            "20",
            "--timeout-ms",
            "3000",
        ]
        t0 = time.perf_counter()
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
        wall = time.perf_counter() - t0
        log.write_text(
            f"cmd={' '.join(cmd)}\nexit={p.returncode}\nharness={wall:.6f}\n"
            f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}\n",
            encoding="utf-8",
        )
        st = json.loads(stats.read_text()) if stats.exists() else {}
        urls = [
            ln.strip()
            for ln in names.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ] if names.exists() else []
        # Compare path suffixes (mock uses ephemeral ports each run)
        paths_only = sorted(
            u.split("://", 1)[-1].split("/", 1)[-1] if "://" in u else u for u in urls
        )
        rec = "recall=1.000" in (p.stderr or "") or "recall=1.0" in (p.stderr or "")
        prec = "precision=1.000" in (p.stderr or "") or "precision=1.0" in (p.stderr or "")
        ok = p.returncode == 0 and rec and prec and len(urls) >= 5
        runs.append(
            {
                "exit": p.returncode,
                "urls": sorted(urls),
                "paths_only": paths_only,
                "ok": ok,
                "stats": st,
                "wall": wall,
            }
        )
        print(f"run{i}: ok={ok} hits={len(urls)} wall={wall:.3f} rps={st.get('request_rate')}")

    stable = runs[0]["paths_only"] == runs[1]["paths_only"]
    gate = all(r["ok"] for r in runs) and stable
    metrics = out / "paths_metrics.txt"
    lines = [
        "vegadns paths suite",
        f"gate: {'PASS' if gate else 'FAIL'}",
        f"stable_path_set: {stable}",
        f"run1_wall: {runs[0]['wall']:.6f}",
        f"run2_wall: {runs[1]['wall']:.6f}",
        f"hits: {len(runs[0]['urls'])}",
        f"request_rate_run1: {runs[0]['stats'].get('request_rate')}",
        f"paths: {', '.join(runs[0]['paths_only'])}",
    ]
    metrics.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(metrics.read_text())
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
