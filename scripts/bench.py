#!/usr/bin/env python3
"""Black-box benchmark harness for vegadns vs baselines.

Metrics: wall time, query/result rate, recall vs known-true.
Runs vegadns (mock-zone), naive sequential resolver + dnsx against a shared
mock-serve fixture when possible. Records massdns/puredns/shuffledns install state.
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
DEFAULT_WORDLIST = FIX / "wordlist_bench.txt"
DEFAULT_ZONE = FIX / "zone_bench.json"
DEFAULT_KNOWN = FIX / "known_true.txt"
SCRIPTS = ROOT / "scripts"


def which(name: str) -> str | None:
    return shutil.which(name) or shutil.which(name + ".exe")


def load_lines(path: Path) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s.lower().rstrip("."))
    return out


def expand_wordlist(wordlist: Path, base: str) -> list[str]:
    base = base.lower().rstrip(".")
    out = []
    for w in load_lines(wordlist):
        if w == base or w.endswith("." + base):
            out.append(w)
        else:
            out.append(f"{w}.{base}")
    return out


def recall_precision(found: list[str], known: list[str]) -> tuple[float, float]:
    fs = set(found)
    ks = set(known)
    r = 1.0 if not ks else len(fs & ks) / len(ks)
    p = 1.0 if not fs else len(fs & ks) / len(fs)
    return r, p


def run_cmd(
    cmd: list[str], cwd: Path | None = None, timeout: float = 300.0
) -> tuple[int, str, str, float]:
    t0 = time.perf_counter()
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        return p.returncode, p.stdout or "", p.stderr or "", time.perf_counter() - t0
    except FileNotFoundError:
        return 127, "", "not found", time.perf_counter() - t0
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", (e.stderr or "") + "\nTIMEOUT", time.perf_counter() - t0


def find_vegadns() -> Path:
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
    raise SystemExit("vegadns binary not found; build with: cargo build --release")


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_mock_serve(storm: Path, zone: Path, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [
            str(storm),
            "mock-serve",
            "--zone",
            str(zone),
            "--bind",
            f"127.0.0.1:{port}",
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def bench_vegadns(
    bin_path: Path, wordlist: Path, zone: Path, known: Path, out_dir: Path
) -> dict:
    stats_path = out_dir / "vegadns_stats.json"
    names_path = out_dir / "vegadns_names.txt"
    cmd = [
        str(bin_path),
        "enum",
        "--mock-zone",
        str(zone),
        "--wordlist",
        str(wordlist),
        "--output",
        str(names_path),
        "--stats-json",
        str(stats_path),
        "--concurrency",
        "4000",
        "--timeout-ms",
        "800",
        "--retries",
        "2",
        "--sockets",
        "4",
        "--wildcard-probes",
        "3",
        "--quiet",
        "--known-true",
        str(known),
    ]
    code, stdout, stderr, wall = run_cmd(cmd, cwd=ROOT, timeout=120)
    known_list = load_lines(known)
    found = load_lines(names_path) if names_path.exists() else []
    r, p = recall_precision(found, known_list)
    stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {}
    return {
        "tool": "vegadns",
        "available": True,
        "exit_code": code,
        "wall_secs": float(stats.get("wall_secs", wall)),
        "harness_wall_secs": wall,
        "query_rate": float(stats.get("query_rate", 0.0)),
        "queries_sent": stats.get("queries_sent"),
        "found": len(found),
        "recall": r,
        "precision": p,
        "names": found,
        "note": (stderr or "")[-80:].replace("\n", " "),
    }


def bench_naive(fqdn_list: Path, resolver: str, known: list[str], out_dir: Path) -> dict:
    script = SCRIPTS / "naive_resolve.py"
    names_path = out_dir / "naive_names.txt"
    cmd = [
        sys.executable,
        str(script),
        "--list",
        str(fqdn_list),
        "--resolver",
        resolver,
        "--output",
        str(names_path),
        "--timeout",
        "0.4",
    ]
    code, stdout, stderr, wall = run_cmd(cmd, cwd=ROOT, timeout=300)
    found = load_lines(names_path) if names_path.exists() else []
    r, p = recall_precision(found, known)
    # parse qps from stdout if present
    qps = len(load_lines(fqdn_list)) / wall if wall > 0 else 0.0
    for part in (stdout or "").split():
        if part.startswith("qps="):
            try:
                qps = float(part.split("=", 1)[1])
            except ValueError:
                pass
    return {
        "tool": "naive_resolve",
        "available": True,
        "exit_code": code,
        "wall_secs": wall,
        "query_rate": qps,
        "found": len(found),
        "recall": r,
        "precision": p,
        "note": "sequential UDP baseline (same mock fixture)",
        "names": found,
    }


def bench_dnsx(fqdn_list: Path, resolver: str, known: list[str], out_dir: Path) -> dict:
    path = which("dnsx")
    if not path:
        return {
            "tool": "dnsx",
            "available": False,
            "note": "not installed",
            "wall_secs": None,
            "query_rate": None,
            "recall": None,
            "precision": None,
        }
    names_path = out_dir / "dnsx_names.txt"
    # dnsx: -l list -r resolver -o out -silent -a
    # custom port: -r 127.0.0.1:port may work in recent dnsx
    cmd = [
        path,
        "-l",
        str(fqdn_list),
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
    ]
    code, stdout, stderr, wall = run_cmd(cmd, cwd=ROOT, timeout=180)
    # dnsx may print host or host [ip]; normalize to host
    found = []
    if names_path.exists():
        for ln in names_path.read_text(encoding="utf-8", errors="replace").splitlines():
            host = ln.strip().split()[0].lower().rstrip(".") if ln.strip() else ""
            if host:
                found.append(host)
    if not found and stdout.strip():
        for ln in stdout.splitlines():
            host = ln.strip().split()[0].lower().rstrip(".") if ln.strip() else ""
            if host:
                found.append(host)
    # unique preserve order
    seen = set()
    uniq = []
    for f in found:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    r, p = recall_precision(uniq, known)
    n = len(load_lines(fqdn_list))
    qps = n / wall if wall > 0 else 0.0
    return {
        "tool": "dnsx",
        "available": True,
        "binary": path,
        "exit_code": code,
        "wall_secs": wall,
        "query_rate": qps,
        "found": len(uniq),
        "recall": r,
        "precision": p,
        "note": "resolve-only vs shared mock-serve (no built-in wildcard filter)",
        "names": uniq,
        "stderr_tail": (stderr or "")[-200:],
    }


def baseline_massdns_stack(name: str) -> dict:
    path = which(name)
    if not path:
        return {
            "tool": name,
            "available": False,
            "note": "not installed",
            "wall_secs": None,
            "query_rate": None,
            "recall": None,
            "precision": None,
        }
    mass = which("massdns")
    if name in ("puredns", "shuffledns") and not mass:
        return {
            "tool": name,
            "available": True,
            "binary": path,
            "note": "installed but massdns binary missing (required dependency); no fabricated scores",
            "wall_secs": None,
            "query_rate": None,
            "recall": None,
            "precision": None,
        }
    return {
        "tool": name,
        "available": True,
        "binary": path,
        "note": "installed; not comparable on mock-zone without massdns wiring",
        "wall_secs": None,
        "query_rate": None,
        "recall": None,
        "precision": None,
    }


def internal_floor_ok(row: dict, known_n: int) -> tuple[bool, str]:
    if row.get("exit_code") not in (0,):
        return False, f"exit_code={row.get('exit_code')}"
    if (row.get("recall") or 0) < 1.0 - 1e-9:
        return False, f"recall={row.get('recall')} < 1.0"
    if known_n > 0 and (row.get("found") or 0) < known_n:
        return False, "found < known_true"
    qps = row.get("query_rate") or 0
    wall = row.get("wall_secs") or 999
    if qps >= 500 or wall <= 5.0:
        return True, f"floor met qps={qps:.0f} wall={wall:.3f}s recall={row.get('recall')}"
    return False, f"qps={qps} wall={wall} below floor"


def beats_available_baselines(storm: dict, others: list[dict]) -> tuple[bool, str]:
    """Primary speed: lower wall_secs at equal-or-better recall."""
    notes = []
    ok = True
    for o in others:
        if not o.get("available"):
            continue
        if o.get("wall_secs") is None or o.get("recall") is None:
            continue
        if (storm.get("recall") or 0) + 1e-9 < (o.get("recall") or 0):
            ok = False
            notes.append(f"recall loss vs {o['tool']}")
            continue
        if (storm.get("wall_secs") or 0) > (o.get("wall_secs") or 0) + 1e-6:
            ok = False
            notes.append(
                f"slower than {o['tool']}: {storm.get('wall_secs'):.4f}s > {o.get('wall_secs'):.4f}s"
            )
        else:
            notes.append(
                f"beats {o['tool']} wall {storm.get('wall_secs'):.4f}s <= {o.get('wall_secs'):.4f}s @ recall {storm.get('recall')}"
            )
    if not notes:
        return True, "no timed baselines; internal floor applies"
    return ok, "; ".join(notes)


def format_report(
    rows: list[dict], floor: tuple[bool, str], vs: tuple[bool, str], meta: dict
) -> str:
    lines = []
    lines.append("vegadns benchmark report")
    lines.append("=" * 60)
    lines.append(f"host_os: {meta.get('os')}")
    lines.append(f"wordlist: {meta.get('wordlist')} ({meta.get('wordlist_n')} labels)")
    lines.append(f"zone: {meta.get('zone')}")
    lines.append(f"known_true: {meta.get('known_n')} names")
    lines.append(f"vegadns_bin: {meta.get('vegadns_bin')}")
    lines.append(f"mock_resolver: {meta.get('mock_resolver')}")
    lines.append("")
    lines.append(
        f"{'tool':<14} {'avail':<8} {'wall_s':>10} {'qps':>12} {'recall':>8} {'prec':>8} {'found':>6}  notes"
    )
    lines.append("-" * 110)
    for row in rows:
        avail = "yes" if row.get("available") else "no"
        wall = row.get("wall_secs")
        qps = row.get("query_rate")
        rec = row.get("recall")
        prec = row.get("precision")
        found = row.get("found")
        wall_s = f"{wall:.4f}" if isinstance(wall, (int, float)) else "-"
        qps_s = f"{qps:.0f}" if isinstance(qps, (int, float)) else "-"
        rec_s = f"{rec:.3f}" if isinstance(rec, (int, float)) else "-"
        prec_s = f"{prec:.3f}" if isinstance(prec, (int, float)) else "-"
        found_s = str(found) if found is not None else "-"
        note = (row.get("note") or "")[:70]
        lines.append(
            f"{row.get('tool', '?'):<14} {avail:<8} {wall_s:>10} {qps_s:>12} {rec_s:>8} {prec_s:>8} {found_s:>6}  {note}"
        )
    lines.append("")
    lines.append(f"internal_floor: {'PASS' if floor[0] else 'FAIL'} — {floor[1]}")
    lines.append(f"vs_timed_baselines: {'PASS' if vs[0] else 'FAIL'} — {vs[1]}")
    lines.append("")
    lines.append("Primary speed metric: wall_secs (lower better) at equal-or-better recall.")
    lines.append("Efficiency definition: docs/RESEARCH.md")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="vegadns benchmark harness")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--wordlist", type=Path, default=DEFAULT_WORDLIST)
    ap.add_argument("--zone", type=Path, default=DEFAULT_ZONE)
    ap.add_argument("--known-true", type=Path, default=DEFAULT_KNOWN)
    ap.add_argument("--artifact-dir", type=Path, default=None)
    args = ap.parse_args()

    out_path: Path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = args.artifact_dir or out_path.parent / "bench_artifacts"
    artifact.mkdir(parents=True, exist_ok=True)

    storm_bin = find_vegadns()
    known = load_lines(args.known_true)
    zone_meta = json.loads(args.zone.read_text(encoding="utf-8"))
    base = zone_meta.get("base", "bench.test")
    wl_n = len(load_lines(args.wordlist))

    # Expand FQDNs for external tools
    fqdns = expand_wordlist(args.wordlist, base)
    fqdn_path = artifact / "expanded_fqdns.txt"
    fqdn_path.write_text("\n".join(fqdns) + "\n", encoding="utf-8")

    rows: list[dict] = []
    sd = bench_vegadns(storm_bin, args.wordlist, args.zone, args.known_true, artifact)
    rows.append(sd)

    port = free_port()
    mock = start_mock_serve(storm_bin, args.zone, port)
    time.sleep(0.25)
    resolver = f"127.0.0.1:{port}"
    try:
        if mock.poll() is not None:
            err = (mock.stderr.read() if mock.stderr else "") or "mock-serve exited"
            rows.append(
                {
                    "tool": "naive_resolve",
                    "available": False,
                    "note": f"mock-serve failed: {err[:120]}",
                }
            )
            rows.append(
                {
                    "tool": "dnsx",
                    "available": bool(which("dnsx")),
                    "note": "skipped; mock-serve failed",
                    "wall_secs": None,
                    "query_rate": None,
                    "recall": None,
                    "precision": None,
                }
            )
        else:
            rows.append(bench_naive(fqdn_path, resolver, known, artifact))
            rows.append(bench_dnsx(fqdn_path, resolver, known, artifact))
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=3)
        except Exception:
            mock.kill()

    for name in ("massdns", "puredns", "shuffledns", "subfinder"):
        rows.append(baseline_massdns_stack(name))

    floor = internal_floor_ok(sd, len(known))
    timed = [r for r in rows if r.get("tool") != "vegadns"]
    vs = beats_available_baselines(sd, timed)
    meta = {
        "os": sys.platform,
        "wordlist": str(args.wordlist),
        "wordlist_n": wl_n,
        "zone": str(args.zone),
        "known_n": len(known),
        "vegadns_bin": str(storm_bin),
        "mock_resolver": resolver,
    }
    report = format_report(rows, floor, vs, meta)
    out_path.write_text(report, encoding="utf-8")
    (artifact / "bench_summary.json").write_text(
        json.dumps(
            {
                "rows": rows,
                "floor": {"ok": floor[0], "detail": floor[1]},
                "vs_baselines": {"ok": vs[0], "detail": vs[1]},
                "meta": meta,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(report)
    return 0 if floor[0] and vs[0] and sd.get("exit_code") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
