#!/usr/bin/env python3
"""Comprehensive lab suite for vegadns: large fixtures, recall/precision, multi-run.

Invokes the real vegadns binary only. Default: embedded mock-zone (safe, local).
Optional: --resolver host:port for standalone mock-serve on a private lab host.

Exit 0 only if both rigorous runs pass volume + recall + precision floors.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "fixtures" / "lab"
ZONE = LAB / "zone_lab.json"
KNOWN = LAB / "known_true_lab.txt"
WORDLIST = LAB / "wordlist_lab.txt"
URLS = LAB / "urls_lab.txt"


def find_vega() -> Path:
    for p in (
        ROOT / "target" / "release" / "vegadns.exe",
        ROOT / "target" / "release" / "vegadns",
        ROOT / "target" / "debug" / "vegadns.exe",
        ROOT / "target" / "debug" / "vegadns",
    ):
        if p.exists():
            return p
    raise SystemExit("vegadns binary missing; cargo build --release")


def load_lines(path: Path) -> list[str]:
    return [
        ln.strip().lower().rstrip(".")
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def recall_precision(found: list[str], known: list[str]) -> tuple[float, float]:
    fs, ks = set(found), set(known)
    r = 1.0 if not ks else len(fs & ks) / len(ks)
    p = 1.0 if not fs else len(fs & ks) / len(fs)
    return r, p


def run_enum(
    vega: Path,
    out_dir: Path,
    tag: str,
    *,
    resolver: str | None,
    concurrency: int,
    timeout_ms: int,
) -> dict:
    names = out_dir / f"{tag}_names.txt"
    stats = out_dir / f"{tag}_stats.json"
    log = out_dir / f"{tag}.log"
    cmd = [
        str(vega),
        "enum",
        "-w",
        str(WORDLIST),
        "--known-true",
        str(KNOWN),
        "-o",
        str(names),
        "--stats-json",
        str(stats),
        "--concurrency",
        str(concurrency),
        "--timeout-ms",
        str(timeout_ms),
        "--retries",
        "2",
        "--sockets",
        "2",
        "--wildcard-probes",
        "2",
        "-q",
    ]
    if resolver:
        # standalone: need resolvers file + domain
        res_file = out_dir / f"{tag}_resolvers.txt"
        res_file.write_text(resolver + "\n", encoding="utf-8")
        cmd.extend(["-d", "lab.test", "-r", str(res_file)])
    else:
        cmd.extend(["--mock-zone", str(ZONE)])

    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    harness = time.perf_counter() - t0
    log.write_text(
        f"cmd={' '.join(cmd)}\nexit={p.returncode}\nharness_wall={harness:.6f}\n"
        f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}\n",
        encoding="utf-8",
    )
    st = json.loads(stats.read_text()) if stats.exists() else {}
    found = load_lines(names) if names.exists() else []
    known = load_lines(KNOWN)
    r, pr = recall_precision(found, known)
    wall = float(st.get("wall_secs", harness))
    wild = [n for n in found if ".wild." in n or ".cdn-edge." in n]
    garbage = [n for n in found if n.startswith("junk") or "noise-" in n]
    return {
        "tag": tag,
        "exit": p.returncode,
        "wall": wall,
        "harness": harness,
        "qps": float(st.get("query_rate", 0)),
        "queries": st.get("queries_sent"),
        "found": len(found),
        "found_raw": st.get("found_raw"),
        "known_n": len(known),
        "recall": r,
        "precision": pr,
        "wild_fps": wild,
        "garbage_fps": garbage,
        "names": sorted(found),
        "ok": (
            p.returncode == 0
            and abs(r - 1.0) < 1e-9
            and abs(pr - 1.0) < 1e-9
            and len(found) == len(known)
            and not wild
            and not garbage
        ),
    }


def ensure_fixtures() -> None:
    if not ZONE.exists() or not KNOWN.exists() or not WORDLIST.exists():
        subprocess.check_call(
            [sys.executable, str(ROOT / "scripts" / "gen_lab_fixtures.py")],
            cwd=str(ROOT),
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="vegadns lab high-speed suite")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--resolver",
        default=None,
        help="optional host:port for remote/local mock-serve (private lab only)",
    )
    ap.add_argument("--concurrency", type=int, default=4000)
    ap.add_argument("--timeout-ms", type=int, default=300)
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args()

    ensure_fixtures()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    vega = find_vega()

    meta = {}
    if (LAB / "fixture_meta.json").exists():
        meta = json.loads((LAB / "fixture_meta.json").read_text(encoding="utf-8"))

    runs = []
    for i in range(1, args.runs + 1):
        tag = f"lab_suite_run{i}"
        r = run_enum(
            vega,
            out,
            tag,
            resolver=args.resolver,
            concurrency=args.concurrency,
            timeout_ms=args.timeout_ms,
        )
        runs.append(r)
        print(
            f"{tag}: ok={r['ok']} wall={r['wall']:.4f}s qps={r['qps']:.0f} "
            f"found={r['found']}/{r['known_n']} recall={r['recall']:.3f} prec={r['precision']:.3f}"
        )

    # URL list structural check (vegadns is DNS; URLs are companion inventory)
    urls_n = len(load_lines(URLS)) if URLS.exists() else 0
    known_n = len(load_lines(KNOWN))

    # One run: consistency N/A (pass). Two+: require identical sorted name sets.
    consistent = len(runs) < 2 or all(r["names"] == runs[0]["names"] for r in runs[1:])
    all_ok = all(r["ok"] for r in runs) and consistent

    lines = [
        "vegadns LAB SUITE METRICS",
        "=" * 60,
        f"scope: private lab / mock only (lab.test)",
        f"vegadns: {vega}",
        f"resolver: {args.resolver or 'embedded --mock-zone'}",
        f"known_true: {known_n}",
        f"wordlist: {meta.get('wordlist', '?')}",
        f"urls_companion: {urls_n}",
        f"name_set_identical: {consistent}",
        "",
    ]
    for r in runs:
        lines.append(
            f"{r['tag']}: exit={r['exit']} wall={r['wall']:.6f} harness={r['harness']:.6f} "
            f"qps={r['qps']:.1f} queries={r['queries']} found={r['found']} "
            f"recall={r['recall']:.4f} precision={r['precision']:.4f} "
            f"wild_fps={len(r['wild_fps'])} garbage_fps={len(r['garbage_fps'])} ok={r['ok']}"
        )
    lines.append("")
    lines.append(f"GATE_LAB_SUITE: {'PASS' if all_ok else 'FAIL'}")
    lines.append(
        "Definition: recall=1 precision=1, identical multi-run names, "
        "no wildcard/garbage flood, real vegadns CLI."
    )
    report = "\n".join(lines) + "\n"
    (out / "lab_suite_metrics.txt").write_text(report, encoding="utf-8")
    (out / "lab_suite_summary.json").write_text(
        json.dumps({"gate": all_ok, "runs": runs, "meta": meta}, indent=2, default=str),
        encoding="utf-8",
    )
    print(report)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
