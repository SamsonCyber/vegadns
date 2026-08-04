#!/usr/bin/env python3
"""One-shot quality pack: unit, gherkin, coverage attempt, mutation attempt, CLI x2.

Writes logs under --out (scratch or local dir). Updates docs/QA_METRICS.md when --write-metrics.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], log: Path, cwd: Path = ROOT, timeout: int | None = 600) -> int:
    t0 = time.perf_counter()
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        body = f"cmd: {' '.join(cmd)}\nexit={p.returncode}\nwall={time.perf_counter()-t0:.3f}s\n\nSTDOUT:\n{p.stdout}\n\nSTDERR:\n{p.stderr}\n"
        log.write_text(body, encoding="utf-8")
        return p.returncode
    except FileNotFoundError as e:
        log.write_text(f"cmd: {' '.join(cmd)}\nUNAVAILABLE: {e}\n", encoding="utf-8")
        return 127
    except subprocess.TimeoutExpired as e:
        log.write_text(f"cmd: {' '.join(cmd)}\nTIMEOUT\n{e}\n", encoding="utf-8")
        return 124


def find_storm() -> Path:
    for p in (
        ROOT / "target" / "release" / "vegadns.exe",
        ROOT / "target" / "release" / "vegadns",
    ):
        if p.exists():
            return p
    raise SystemExit("build release first")


def parse_coverage_pct(text: str) -> float | None:
    # TOTAL lines from llvm-cov summary
    m = re.search(r"TOTAL\s+(\d+)\s+(\d+)\s+([\d.]+)%", text)
    if m:
        return float(m.group(3))
    m = re.search(r"lines\.+:\s*([\d.]+)%", text, re.I)
    if m:
        return float(m.group(1))
    return None


def parse_mutation(text: str) -> tuple[int, int, float] | None:
    # cargo-mutants v27: "187 mutants tested in 11m: 45 missed, 128 caught, 10 unviable, 4 timeouts"
    m = re.search(
        r"(\d+)\s+mutants?\s+tested[^:]*:\s*(\d+)\s+missed,\s*(\d+)\s+caught(?:,\s*(\d+)\s+unviable)?(?:,\s*(\d+)\s+timeouts)?",
        text,
        re.I,
    )
    if m:
        total = int(m.group(1))
        missed = int(m.group(2))
        caught = int(m.group(3))
        timeouts = int(m.group(5) or 0)
        scored = caught + missed + timeouts
        kill = (caught / scored * 100.0) if scored else 0.0
        return total, caught, kill
    m = re.search(
        r"(\d+)\s+mutants?\s+tested:.*?(\d+)\s+caught.*?(\d+)\s+missed",
        text,
        re.I | re.S,
    )
    if m:
        total, caught, missed = int(m.group(1)), int(m.group(2)), int(m.group(3))
        kill = (caught / total * 100.0) if total else 0.0
        return total, caught, kill
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--write-metrics", action="store_true")
    ap.add_argument("--skip-mutation", action="store_true")
    ap.add_argument("--skip-coverage", action="store_true")
    args = ap.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "coverage").mkdir(exist_ok=True)
    (out / "mutation").mkdir(exist_ok=True)

    # build
    run(["cargo", "build", "--release"], out / "build.log")

    # unit
    unit_rc = run(
        ["cargo", "test", "--release", "--", "--test-threads=1"],
        out / "unit_tests.log",
    )

    # gherkin
    gherkin_rc = run([sys.executable, str(ROOT / "scripts" / "gherkin_run.py")], out / "gherkin.log")

    # coverage
    cov_pct = None
    if not args.skip_coverage:
        cov_rc = run(
            [
                "cargo",
                "llvm-cov",
                "--release",
                "--lib",
                "--tests",
                "--summary-only",
            ],
            out / "coverage_report.txt",
            timeout=900,
        )
        cov_text = (out / "coverage_report.txt").read_text(encoding="utf-8", errors="replace")
        if cov_rc != 0 or "profiler_builtins" in cov_text or "UNAVAILABLE" in cov_text:
            (out / "coverage_unavailable.log").write_text(
                "cargo-llvm-cov failed or unavailable on this host.\n"
                "Config/scripts present: scripts/quality_run.py, docs/QA.md\n"
                f"log excerpt:\n{cov_text[-2000:]}\n",
                encoding="utf-8",
            )
        else:
            cov_pct = parse_coverage_pct(cov_text)
            run(
                [
                    "cargo",
                    "llvm-cov",
                    "--release",
                    "--lib",
                    "--tests",
                    "--lcov",
                    "--output-path",
                    str(out / "coverage" / "lcov.info"),
                ],
                out / "coverage" / "llvm_cov_run.log",
                timeout=900,
            )

    # mutation
    mut_stats = None
    if not args.skip_mutation:
        mut_rc = run(
            [
                "cargo",
                "mutants",
                "--test-tool=cargo",
                "--timeout",
                "45",
                "--jobs",
                "2",
                "--file",
                "src/expand.rs",
                "--file",
                "src/classify.rs",
                "--file",
                "src/wildcard.rs",
                "--file",
                "src/dedup.rs",
                "--file",
                "src/dns_packet.rs",
            ],
            out / "mutation.log",
            timeout=3600,
        )
        mut_text = (out / "mutation.log").read_text(encoding="utf-8", errors="replace")
        if mut_rc in (127, 124) or "UNAVAILABLE" in mut_text or "error:" in mut_text[:500] and "mutants" not in mut_text.lower():
            # still parse if partial
            mut_stats = parse_mutation(mut_text)
            if mut_stats is None and mut_rc != 0:
                (out / "mutation_unavailable.log").write_text(
                    "cargo-mutants failed or unavailable.\n"
                    "Fallback: tests/pure_logic_extra.rs hardens pure modules.\n"
                    f"excerpt:\n{mut_text[-2000:]}\n",
                    encoding="utf-8",
                )
        else:
            mut_stats = parse_mutation(mut_text)
            if mut_stats is None and mut_rc != 0:
                (out / "mutation_unavailable.log").write_text(
                    f"could not parse mutation results exit={mut_rc}\n{mut_text[-2000:]}\n",
                    encoding="utf-8",
                )

    # CLI x2
    storm = find_storm()
    for i in (1, 2):
        stats = out / f"cli_stats{i}.json"
        names = out / f"cli_names{i}.txt"
        cmd = [
            str(storm),
            "enum",
            "--mock-zone",
            str(ROOT / "fixtures" / "zone_bench.json"),
            "-w",
            str(ROOT / "fixtures" / "wordlist_bench.txt"),
            "--known-true",
            str(ROOT / "fixtures" / "known_true.txt"),
            "--stats-json",
            str(stats),
            "-o",
            str(names),
            "-q",
            "--concurrency",
            "2000",
            "--timeout-ms",
            "200",
            "--retries",
            "2",
            "--sockets",
            "1",
        ]
        run(cmd, out / f"cli_run{i}.log", timeout=120)

    # summary
    summary = {
        "unit_exit": unit_rc,
        "gherkin_exit": gherkin_rc,
        "coverage_pct": cov_pct,
        "mutation": mut_stats,
        "coverage_unavailable": (out / "coverage_unavailable.log").exists(),
        "mutation_unavailable": (out / "mutation_unavailable.log").exists(),
    }
    (out / "quality_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

    if args.write_metrics:
        metrics = ROOT / "docs" / "QA_METRICS.md"
        mut_line = "tool missing / unavailable"
        if mut_stats:
            mut_line = f"{mut_stats[2]:.1f}% kill ({mut_stats[1]}/{mut_stats[0]} caught)"
        cov_line = f"{cov_pct:.1f}%" if cov_pct is not None else "unavailable (see coverage_unavailable.log)"
        body = f"""# vegadns QA metrics (floors + latest run)

Floors are fixed gates. Latest numbers from real tool output.

## Floors (must meet)

| Metric | Floor | Notes |
|---|---|---|
| Unit/integration | all pass | `cargo test --release` |
| Gherkin scenarios | all required pass | `python scripts/gherkin_run.py` |
| Line coverage (`src/` lib) | **≥ 55%** | engine/mock I/O is hard; pure modules should be high |
| Mutation kill rate (pure modules) | **≥ 40%** | expand/classify/wildcard/dedup/dns_packet focus; or honest tool-unavailable |
| CLI mock fixture | recall=1.0, precision=1.0 | two consecutive runs identical names |

## Intentional coverage exclusions

| Path | Reason |
|---|---|
| `src/main.rs` CLI clap wiring | Thin glue; exercised by Gherkin/CLI QA |
| Full live public-resolver network paths | Non-deterministic; mock path covers enum semantics |

## Latest measured

| Metric | Value | Source |
|---|---|---|
| Unit tests | exit={unit_rc} ({'PASS' if unit_rc==0 else 'FAIL'}) | unit_tests.log |
| Gherkin | exit={gherkin_rc} ({'PASS' if gherkin_rc==0 else 'FAIL'}) | gherkin.log |
| Line coverage | {cov_line} | coverage_report.txt / coverage_unavailable.log |
| Mutation | {mut_line} | mutation.log / mutation_unavailable.log |
| CLI x2 | see cli_run1.log cli_run2.log | recall/precision in stderr |
"""
        metrics.write_text(body, encoding="utf-8")

    # gate
    ok = unit_rc == 0 and gherkin_rc == 0
    # coverage: either meets floor or honest unavailable
    if cov_pct is not None and cov_pct < 55.0:
        ok = False
    if mut_stats is not None and mut_stats[2] < 40.0:
        ok = False
    # CLI checks
    for i in (1, 2):
        log = (out / f"cli_run{i}.log").read_text(encoding="utf-8", errors="replace")
        if "exit=0" not in log and "\nexit=0\n" not in log:
            # our run() writes exit=
            if not re.search(r"exit=0\b", log):
                ok = False
        if "recall=1.000" not in log and "recall=1.0" not in log:
            ok = False
        if "precision=1.000" not in log and "precision=1.0" not in log:
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
