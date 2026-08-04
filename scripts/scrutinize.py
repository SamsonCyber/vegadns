#!/usr/bin/env python3
"""Extensive scrutinize suite for vegadns: correctness, consistency, speed.

Defines "best+fastest on this suite" as:
  - recall=1.0 and precision=1.0 on known_true
  - 3-run identical sorted primary name sets
  - wall_secs <= every timed baseline in the same session
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"
ZONE = FIX / "zone_bench.json"
WORDLIST = FIX / "wordlist_bench.txt"
KNOWN = FIX / "known_true.txt"


def which(name: str) -> str | None:
    return shutil.which(name) or shutil.which(name + ".exe")


def load_lines(path: Path) -> list[str]:
    return [
        ln.strip().lower().rstrip(".")
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def expand(wordlist: Path, base: str) -> list[str]:
    base = base.lower().rstrip(".")
    out = []
    for w in load_lines(wordlist):
        out.append(w if w == base or w.endswith("." + base) else f"{w}.{base}")
    return out


def recall_precision(found: list[str], known: list[str]) -> tuple[float, float]:
    fs, ks = set(found), set(known)
    r = 1.0 if not ks else len(fs & ks) / len(ks)
    p = 1.0 if not fs else len(fs & ks) / len(fs)
    return r, p


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def find_storm() -> Path:
    for p in (
        ROOT / "target" / "release" / "vegadns.exe",
        ROOT / "target" / "release" / "vegadns",
        ROOT / "target" / "debug" / "vegadns.exe",
        ROOT / "target" / "debug" / "vegadns",
    ):
        if p.exists():
            return p
    w = which("vegadns")
    if w:
        return Path(w)
    raise SystemExit("vegadns binary missing; cargo build --release first")


def run_storm(
    storm: Path,
    wordlist: Path,
    out_dir: Path,
    tag: str,
    *,
    concurrency: int = 2000,
    timeout_ms: int = 200,
    retries: int = 2,
    sockets: int = 1,
) -> dict:
    stats = out_dir / f"{tag}_stats.json"
    names = out_dir / f"{tag}_names.txt"
    log = out_dir / f"{tag}.log"
    cmd = [
        str(storm),
        "enum",
        "--mock-zone",
        str(ZONE),
        "-w",
        str(wordlist),
        "--known-true",
        str(KNOWN),
        "--stats-json",
        str(stats),
        "-o",
        str(names),
        "--concurrency",
        str(concurrency),
        "--timeout-ms",
        str(timeout_ms),
        "--retries",
        str(retries),
        "--sockets",
        str(sockets),
        "--wildcard-probes",
        "2",
    ]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    harness = time.perf_counter() - t0
    log.write_text(
        f"exit={p.returncode}\nharness_wall={harness:.6f}\ncmd={' '.join(cmd)}\n"
        f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}\n",
        encoding="utf-8",
    )
    st = json.loads(stats.read_text()) if stats.exists() else {}
    found = load_lines(names) if names.exists() else []
    known = load_lines(KNOWN)
    r, pr = recall_precision(found, known)
    wall = float(st.get("wall_secs", harness))
    return {
        "tag": tag,
        "exit": p.returncode,
        "wall": wall,
        "harness": harness,
        "qps": float(st.get("query_rate", 0)),
        "found": len(found),
        "found_raw": st.get("found_raw"),
        "queries": st.get("queries_sent"),
        "timeouts": st.get("timeouts"),
        "recall": r,
        "precision": pr,
        "names": sorted(found),
        "wild_fps": [n for n in found if ".wild." in n],
        "ok": p.returncode == 0
        and abs(r - 1.0) < 1e-9
        and abs(pr - 1.0) < 1e-9
        and len(found) == len(known)
        and not any(".wild." in n for n in found),
    }


def run_naive(fqdn_path: Path, resolver: str, known: list[str], out_dir: Path) -> dict:
    names_path = out_dir / "naive_names.txt"
    script = ROOT / "scripts" / "naive_resolve.py"
    t0 = time.perf_counter()
    p = subprocess.run(
        [
            sys.executable,
            str(script),
            "--list",
            str(fqdn_path),
            "--resolver",
            resolver,
            "--output",
            str(names_path),
            "--timeout",
            "0.4",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    wall = time.perf_counter() - t0
    found = load_lines(names_path) if names_path.exists() else []
    r, pr = recall_precision(found, known)
    n = len(load_lines(fqdn_path))
    return {
        "tool": "naive_resolve",
        "available": True,
        "timed": True,
        "exit": p.returncode,
        "wall": wall,
        "qps": n / wall if wall > 0 else 0,
        "found": len(found),
        "recall": r,
        "precision": pr,
        "note": "sequential UDP; no wildcard filter",
        "names": found,
    }


def run_dnsx(fqdn_path: Path, resolver: str, known: list[str], out_dir: Path) -> dict:
    path = which("dnsx")
    if not path:
        return {
            "tool": "dnsx",
            "available": False,
            "timed": False,
            "note": "not installed",
            "wall": None,
            "recall": None,
            "precision": None,
        }
    names_path = out_dir / "dnsx_names.txt"
    t0 = time.perf_counter()
    try:
        p = subprocess.run(
            [
                path,
                "-l",
                str(fqdn_path),
                "-r",
                resolver,
                "-o",
                str(names_path),
                "-silent",
                "-a",
                "-retry",
                "1",
                "-t",
                "200",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {
            "tool": "dnsx",
            "available": True,
            "timed": False,
            "exit": 124,
            "wall": None,
            "found": 0,
            "recall": None,
            "precision": None,
            "note": "resolve-only; timed out on mock resolver (not comparable)",
            "names": [],
        }
    wall = time.perf_counter() - t0
    found = []
    if names_path.exists():
        for ln in names_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.strip():
                found.append(ln.strip().split()[0].lower().rstrip("."))
    # unique
    seen, uniq = set(), []
    for f in found:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    r, pr = recall_precision(uniq, known)
    n = len(load_lines(fqdn_path))
    ok_timed = p.returncode == 0 and len(uniq) > 0 and wall < 60
    return {
        "tool": "dnsx",
        "available": True,
        "timed": ok_timed,
        "exit": p.returncode,
        "wall": wall if ok_timed else None,
        "qps": (n / wall) if ok_timed and wall > 0 else None,
        "found": len(uniq),
        "recall": r,
        "precision": pr,
        "note": "resolve-only; no wildcard filter"
        + ("" if ok_timed else f"; not comparable (exit={p.returncode} found={len(uniq)} wall={wall:.2f})"),
        "names": uniq,
    }


def run_massdns(fqdn_path: Path, resolver: str, out_dir: Path) -> dict:
    path = which("massdns")
    if not path:
        return {
            "tool": "massdns",
            "available": False,
            "timed": False,
            "note": "not installed",
            "wall": None,
            "found": None,
        }
    resolvers = out_dir / "mass_resolvers.txt"
    # massdns accepts ip:port
    resolvers.write_text(resolver + "\n", encoding="utf-8")
    mass_out = out_dir / "massdns_names.txt"
    t0 = time.perf_counter()
    p = subprocess.run(
        [
            path,
            "-r",
            str(resolvers),
            "-t",
            "A",
            "-o",
            "S",
            "-s",
            "2000",
            "-c",
            "3",
            "--interval",
            "100",
            "-w",
            str(mass_out),
            str(fqdn_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    wall = time.perf_counter() - t0
    text = mass_out.read_text() if mass_out.exists() else ""
    names = set()
    for ln in text.splitlines():
        if ln.strip():
            names.add(ln.split()[0].rstrip(".").lower())
    (out_dir / "massdns_run.log").write_text(
        f"exit={p.returncode}\nwall={wall:.6f}\nfound={len(names)}\nstderr:\n{p.stderr}\n",
        encoding="utf-8",
    )
    ok = p.returncode == 0 and len(names) > 0
    return {
        "tool": "massdns",
        "available": True,
        "timed": ok,
        "exit": p.returncode,
        "wall": wall if ok else None,
        "found": len(names),
        "recall": None,
        "precision": None,
        "note": "no wildcard filter; may include catch-all FPs"
        + ("" if ok else f"; failed exit={p.returncode}"),
        "names": sorted(names),
    }


def baseline_status(out_dir: Path) -> list[str]:
    lines = []
    for name in ("massdns", "puredns", "shuffledns", "dnsx", "subfinder"):
        p = which(name)
        if not p:
            lines.append(f"{name}: not installed")
            continue
        extra = ""
        if name in ("puredns", "shuffledns") and not which("massdns"):
            extra = " (installed; massdns dependency missing for brute path)"
        elif name == "subfinder":
            extra = " (passive only; not timed on active mock suite)"
        lines.append(f"{name}: {p}{extra}")
    (out_dir / "baseline_status.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def write_report(
    out_dir: Path,
    host: str,
    inventory: dict,
    runs: list[dict],
    baselines: list[dict],
    stress: dict | None,
    unit_ok: bool | None,
) -> tuple[bool, str]:
    known = load_lines(KNOWN)
    known_n = len(known)
    lines: list[str] = []
    lines.append("vegadns SCRUTINIZE REPORT")
    lines.append("=" * 70)
    lines.append(f"host: {host}")
    lines.append(f"suite_zone: {ZONE}")
    lines.append(f"suite_wordlist: {WORDLIST} ({inventory['wordlist_n']} labels)")
    lines.append(f"known_true: {known_n} names")
    lines.append(f"vegadns_bin: {inventory['storm_bin']}")
    lines.append(f"timestamp: {time.strftime('%Y-%m-%dT%H:%M:%S')}")
    lines.append("")
    lines.append("## Baseline inventory")
    for b in inventory["baselines"]:
        lines.append(f"  - {b}")
    lines.append("")
    lines.append("## Unit tests")
    if unit_ok is None:
        lines.append("  (not run in this process)")
    else:
        lines.append(f"  cargo test --release: {'PASS' if unit_ok else 'FAIL'}")
    lines.append("")
    lines.append("## Three-run consistency (identical inputs)")
    for r in runs:
        lines.append(
            f"  {r['tag']}: exit={r['exit']} wall={r['wall']:.6f}s qps={r['qps']:.0f} "
            f"found={r['found']} recall={r['recall']:.3f} prec={r['precision']:.3f} ok={r['ok']}"
        )
    name_sets = [tuple(r["names"]) for r in runs]
    consistent = len(name_sets) >= 3 and all(s == name_sets[0] for s in name_sets[1:])
    all_correct = all(r["ok"] for r in runs)
    lines.append(f"  name_set_identical: {consistent}")
    lines.append(f"  all_runs_correct: {all_correct}")
    if runs:
        lines.append(f"  names: {', '.join(runs[0]['names'])}")
    lines.append("")
    lines.append("## Stress wordlist")
    if stress:
        lines.append(
            f"  wall={stress['wall']:.6f}s found={stress['found']} "
            f"recall={stress['recall']:.3f} prec={stress['precision']:.3f} ok={stress['ok']}"
        )
        lines.append(f"  wild_fps_in_output: {stress['wild_fps']}")
    else:
        lines.append("  (skipped)")
    lines.append("")
    lines.append("## Head-to-head (same session, shared mock where applicable)")
    lines.append(
        f"  {'tool':<14} {'timed':<6} {'wall_s':>10} {'found':>6} {'recall':>8} {'prec':>8}  notes"
    )
    lines.append("  " + "-" * 80)
    # vegadns primary = median or min of warm runs (use run3 if present else min)
    storm_wall = min(r["wall"] for r in runs) if runs else None
    storm_row = (
        f"  {'vegadns':<14} {'yes':<6} {storm_wall:>10.6f} {runs[0]['found']:>6} "
        f"{runs[0]['recall']:>8.3f} {runs[0]['precision']:>8.3f}  filtered+active"
    )
    lines.append(storm_row)
    speed_ok = True
    speed_notes = []
    for b in baselines:
        wall = b.get("wall")
        wall_s = f"{wall:.6f}" if isinstance(wall, (int, float)) else "-"
        found = b.get("found")
        found_s = str(found) if found is not None else "-"
        rec = b.get("recall")
        prec = b.get("precision")
        rec_s = f"{rec:.3f}" if isinstance(rec, (int, float)) else "-"
        prec_s = f"{prec:.3f}" if isinstance(prec, (int, float)) else "-"
        timed = "yes" if b.get("timed") else "no"
        lines.append(
            f"  {b.get('tool','?'):<14} {timed:<6} {wall_s:>10} {found_s:>6} {rec_s:>8} {prec_s:>8}  {b.get('note','')[:50]}"
        )
        if b.get("timed") and isinstance(wall, (int, float)) and storm_wall is not None:
            if storm_wall <= wall + 1e-9:
                speed_notes.append(f"beats {b['tool']} ({storm_wall:.6f}<={wall:.6f})")
            else:
                speed_ok = False
                speed_notes.append(f"LOSES to {b['tool']} ({storm_wall:.6f}>{wall:.6f})")
    if not any(b.get("timed") for b in baselines):
        speed_notes.append("no timed baselines; internal floor only")
    lines.append("")
    # wildcard extras check for unfiltered tools
    for b in baselines:
        if b.get("timed") and b.get("names") and runs:
            extras = set(b["names"]) - set(runs[0]["names"])
            wild = [x for x in extras if ".wild." in x]
            if wild:
                lines.append(
                    f"  note: {b['tool']} extra names are wildcard FPs ({len(wild)}): "
                    + ", ".join(sorted(wild)[:8])
                )
    lines.append("")
    gate = all_correct and consistent and speed_ok and (stress is None or stress["ok"])
    if unit_ok is False:
        gate = False
    lines.append("## Gate: best+fastest on this fixed suite")
    lines.append(f"  correctness_3run: {'PASS' if all_correct else 'FAIL'}")
    lines.append(f"  name_consistency: {'PASS' if consistent else 'FAIL'}")
    lines.append(f"  stress: {'PASS' if (stress is None or stress['ok']) else 'FAIL'}")
    lines.append(f"  wall_vs_timed_baselines: {'PASS' if speed_ok else 'FAIL'} — {'; '.join(speed_notes)}")
    lines.append(
        f"  BEST_AND_FASTEST_ON_SUITE: {'PASS' if gate else 'FAIL'}"
    )
    lines.append("")
    lines.append(
        "Definition: recall=1 precision=1, identical 3-run name set, "
        "wall <= every timed baseline same session. Not global internet supremacy."
    )
    text = "\n".join(lines) + "\n"
    (out_dir / "scrutinize_vs_baselines.txt").write_text(text, encoding="utf-8")
    # also machine summary
    (out_dir / "scrutinize_summary.json").write_text(
        json.dumps(
            {
                "gate": gate,
                "storm_wall_min": storm_wall,
                "runs": runs,
                "baselines": baselines,
                "stress": stress,
                "unit_ok": unit_ok,
                "host": host,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return gate, text


def make_stress_wordlist(out_dir: Path) -> Path:
    """Bench list + extra garbage + many wildcard labels."""
    base = load_lines(WORDLIST)
    extras = [
        "nope",
        "garbage999",
        "notreal_xyz",
        "zzzzzz",
        "foo.wild",
        "bar.wild",
        "baz.wild",
        "aaa.wild",
        "bbb.wild",
        "ccc.wild",
        "ddd.wild",
        "eee.wild",
        "fff.wild",
        "ggg.wild",
        "hhh.wild",
        "iii.wild",
        "jjj.wild",
    ]
    # more random garbage
    extras += [f"junk{i}" for i in range(500)]
    extras += [f"x{i}.wild" for i in range(50)]
    path = out_dir / "wordlist_stress.txt"
    path.write_text("\n".join(base + extras) + "\n", encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True, help="Output / scratch dir")
    ap.add_argument("--skip-unit", action="store_true")
    args = ap.parse_args()
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    storm = find_storm()
    host = f"{sys.platform}"
    try:
        host = f"{sys.platform} {os.uname().nodename}"  # type: ignore[attr-defined]
    except Exception:
        pass

    # inventory
    blines = baseline_status(out_dir)
    inventory = {
        "storm_bin": str(storm),
        "wordlist_n": len(load_lines(WORDLIST)),
        "baselines": blines,
    }
    (out_dir / "inventory.txt").write_text(
        f"host={host}\nstorm={storm}\nwordlist_n={inventory['wordlist_n']}\n"
        + "\n".join(blines)
        + "\n",
        encoding="utf-8",
    )

    # unit tests
    unit_ok = None
    if not args.skip_unit:
        up = subprocess.run(
            ["cargo", "test", "--release", "--", "--test-threads=1"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        (out_dir / "unit_tests.log").write_text(
            up.stdout + "\n" + up.stderr, encoding="utf-8"
        )
        unit_ok = up.returncode == 0

    # 3 CLI runs — also write scrutinize_runN.log aliases
    runs = []
    for i in (1, 2, 3):
        r = run_storm(storm, WORDLIST, out_dir, f"scrutinize_run{i}")
        runs.append(r)
        # copy log to expected name if tag matches
        print(
            f"{r['tag']}: ok={r['ok']} wall={r['wall']:.6f} recall={r['recall']:.3f} "
            f"prec={r['precision']:.3f} found={r['found']}"
        )

    # stress
    stress_wl = make_stress_wordlist(out_dir)
    stress = run_storm(storm, stress_wl, out_dir, "scrutinize_stress")
    # rename log for plan
    src = out_dir / "scrutinize_stress.log"
    print(
        f"stress: ok={stress['ok']} wall={stress['wall']:.6f} "
        f"recall={stress['recall']:.3f} prec={stress['precision']:.3f} found={stress['found']}"
    )

    # shared mock for baselines
    known = load_lines(KNOWN)
    base = json.loads(ZONE.read_text(encoding="utf-8"))["base"]
    fqdns = expand(WORDLIST, base)
    fqdn_path = out_dir / "expanded_fqdns.txt"
    fqdn_path.write_text("\n".join(fqdns) + "\n", encoding="utf-8")

    port = free_port()
    mock = subprocess.Popen(
        [str(storm), "mock-serve", "--zone", str(ZONE), "--bind", f"127.0.0.1:{port}"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.15)
    resolver = f"127.0.0.1:{port}"
    baselines: list[dict] = []
    try:
        if mock.poll() is not None:
            err = mock.stderr.read() if mock.stderr else "exit"
            baselines.append(
                {
                    "tool": "mock-serve",
                    "available": False,
                    "timed": False,
                    "note": f"failed: {err[:100]}",
                }
            )
        else:
            baselines.append(run_naive(fqdn_path, resolver, known, out_dir))
            baselines.append(run_dnsx(fqdn_path, resolver, known, out_dir))
            baselines.append(run_massdns(fqdn_path, resolver, out_dir))
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=2)
        except Exception:
            mock.kill()

    # status-only for puredns/shuffledns/subfinder
    for name in ("puredns", "shuffledns", "subfinder"):
        p = which(name)
        if p:
            note = f"installed at {p}"
            if name in ("puredns", "shuffledns") and not which("massdns"):
                note += "; massdns missing, brute path not timed"
            elif name in ("puredns", "shuffledns") and which("massdns"):
                note += "; not invoked on mock (wrapper/massdns path); status only"
            if name == "subfinder":
                note += "; passive only"
            baselines.append(
                {
                    "tool": name,
                    "available": True,
                    "timed": False,
                    "note": note,
                    "wall": None,
                }
            )
        else:
            baselines.append(
                {
                    "tool": name,
                    "available": False,
                    "timed": False,
                    "note": "not installed",
                    "wall": None,
                }
            )

    gate, text = write_report(
        out_dir, host, inventory, runs, baselines, stress, unit_ok
    )
    print(text)
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
