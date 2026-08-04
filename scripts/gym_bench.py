#!/usr/bin/env python3
"""Subdomain Scanner Gym — true multi-mode benchmark.

Modes:
  mock-clean   Instant loopback zone (regression / oracle correctness).
  mock-stress  Same zone + latency / SERVFAIL / drop (adversarial local net).
  live-resolve Public recursive resolvers + fixed FQDN list (real network path).

Honesty: every report embeds claim bounds. This is not "fastest on the market."
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from gym_metrics import final_row, normalize_name, recall_precision, sample_point  # noqa: E402

GYM = ROOT / "fixtures" / "gym"
PUBLIC_RES = ROOT / "fixtures" / "resolvers_public.txt"

CLAIM_BOUNDS = {
    "proves": [
        "Wall/recall/precision/F1 on this harness under the declared mode",
        "Wildcard filter improves precision/F1 vs peers that dump noise (mock modes)",
        "Single-binary Windows+Linux path without massdns dependency",
    ],
    "does_not_prove": [
        "Fastest tool on the market",
        "Fastest vs massdns on the public internet at multi-million scale",
        "Replaces puredns+massdns for every large hunt",
        "Passive OSINT / SaaS ASM coverage",
        "Public resolver QPS supremacy",
    ],
    "metric_note": (
        "Prefer wall + recall + precision/F1 together. "
        "F1 wins are not pure QPS wins. massdns raw dump can be faster and noisier."
    ),
}

# Live-resolve oracle: names expected to answer A (or known live) via public DNS.
LIVE_MUST_RESOLVE = [
    "cloudflare.com",
    "www.cloudflare.com",
    "one.one.one.one",
    "dns.google",
    "www.google.com",
    "github.com",
    "www.github.com",
    "example.com",
    "www.example.com",
    "iana.org",
    "quad9.net",
]
# Live-resolve canaries: must NOT appear as live A hits under honest tools.
LIVE_MUST_NX = [
    "never-exists-vegadns-probe-9f3a2c1b.example.com",
    "this-label-should-nxdomain-vegadns-000.invalid",
]


def which(name: str) -> str | None:
    """Find real peer binaries (PATH + common Go install dirs)."""
    hit = shutil.which(name) or shutil.which(name + ".exe")
    if hit:
        return hit
    extras = [
        Path.home() / "go" / "bin" / name,
        Path.home() / "go" / "bin" / f"{name}.exe",
        Path("/usr/local/bin") / name,
        Path("/usr/bin") / name,
        Path("C:/code/tools/massdns/bin") / name,
        Path("C:/code/tools/massdns/bin") / f"{name}.exe",
    ]
    for p in extras:
        if p.exists():
            return str(p)
    return None


def find_massdns() -> str | None:
    """massdns is required by puredns/shuffledns; prefer real binary."""
    return which("massdns")


def find_vega() -> Path:
    for p in (
        ROOT / "target" / "release" / "vegadns.exe",
        ROOT / "target" / "release" / "vegadns",
        Path.home() / "vegadns" / "target" / "release" / "vegadns",
        Path("/root/vegadns/target/release/vegadns"),
    ):
        if p.exists():
            return p
    w = which("vegadns")
    if w:
        return Path(w)
    raise SystemExit("vegadns release binary missing; run: cargo build --release")


def free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def load_lines(path: Path) -> list[str]:
    return [
        ln.strip()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def expand_fqdns(labels: list[str], base: str) -> list[str]:
    base = base.rstrip(".")
    out = []
    for w in labels:
        w = w.lower().rstrip(".")
        if w == base or w.endswith("." + base):
            out.append(w)
        else:
            out.append(f"{w}.{base}")
    return out


def append_jsonl(path: Path, obj: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj) + "\n")
        f.flush()


def sample_until_done(
    proc: subprocess.Popen,
    tool: str,
    samples_path: Path,
    stop: threading.Event,
    interval: float = 0.15,
) -> float:
    t0 = time.perf_counter()
    while not stop.is_set():
        if proc.poll() is not None:
            break
        elapsed = time.perf_counter() - t0
        append_jsonl(samples_path, sample_point(tool, elapsed, phase="running"))
        stop.wait(interval)
    wall = time.perf_counter() - t0
    append_jsonl(samples_path, sample_point(tool, wall, phase="done"))
    return wall


def ensure_gym_fixtures() -> None:
    if not (GYM / "zone_gym.json").exists():
        subprocess.check_call(
            [sys.executable, str(ROOT / "scripts" / "gen_gym_fixtures.py")],
            cwd=str(ROOT),
        )


def run_tool(
    cmd: list[str],
    tool: str,
    samples_path: Path,
    timeout: float,
    parse_found,
    known: list[str],
    note: str,
) -> dict:
    stop = threading.Event()
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    th = threading.Thread(
        target=sample_until_done, args=(proc, tool, samples_path, stop), daemon=True
    )
    th.start()
    try:
        so, se = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        so, se = proc.communicate()
        stop.set()
        th.join(timeout=2)
        return final_row(tool, time.perf_counter() - t0, 0, 0.0, 1.0, timed=False, note="TIMEOUT")
    stop.set()
    th.join(timeout=2)
    wall = time.perf_counter() - t0
    found = parse_found(so, se)
    r, p, hit, kn = recall_precision(found, known)
    ok = proc.returncode == 0 and (len(found) > 0 or r >= 0)
    # timed means process finished cleanly; zero-found with ok exit still timed
    timed = proc.returncode == 0
    return final_row(tool, wall, len(found), r, p, timed=timed, note=note)


def parse_names_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    return load_lines(path)


def parse_massdns_out(path: Path) -> list[str]:
    names = []
    if not path.exists():
        return names
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not ln.strip():
            continue
        upper = ln.upper()
        if "NXDOMAIN" in upper or "SERVFAIL" in upper:
            continue
        names.append(ln.split()[0].rstrip(".").lower())
    return sorted(set(names))


def write_report(out: Path, report: dict) -> None:
    (out / "bench_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = [
        "Subdomain Scanner Gym — TRUE TEST report",
        "=" * 56,
        f"mode: {report['mode']}",
        f"scope: {report['scope']}",
        f"base/target: {report.get('base', report.get('target', '-'))}",
        f"known_true/oracle: {report.get('known_n', '-')}",
        f"wordlist/candidates: {report.get('wordlist_n', report.get('candidates_n', '-'))}",
        f"resolver: {report.get('resolver', '-')}",
        "",
        "CLAIM BOUNDS (read this)",
        "  PROVES:",
    ]
    for p in report["claim_bounds"]["proves"]:
        lines.append(f"    - {p}")
    lines.append("  DOES NOT PROVE:")
    for p in report["claim_bounds"]["does_not_prove"]:
        lines.append(f"    - {p}")
    lines.append(f"  NOTE: {report['claim_bounds']['metric_note']}")
    lines += [
        "",
        f"{'tool':<12} {'timed':<6} {'wall_s':>10} {'found':>8} {'recall':>8} {'prec':>8} {'f1':>8}  notes",
        "-" * 92,
    ]
    for row in report["tools"]:
        timed = "yes" if row.get("timed") else "no"
        wall = f"{row['wall_s']:.4f}" if row.get("wall_s") is not None else "-"
        lines.append(
            f"{row['tool']:<12} {timed:<6} {wall:>10} {row.get('found', 0):>8} "
            f"{row.get('recall', 0):>8.3f} {row.get('precision', 0):>8.3f} {row.get('f1', 0):>8.3f}  {row.get('note', '')}"
        )
    text = "\n".join(lines) + "\n"
    (out / "bench_report.txt").write_text(text, encoding="utf-8")
    print(text)


def run_mock_mode(
    out: Path,
    *,
    stress: bool,
    wordlist_cap: int,
    concurrency: int,
    latency_ms: int,
    servfail_pct: int,
    drop_pct: int,
    retries: int | None = None,
    timeout_ms: int | None = None,
    sockets: int = 1,
) -> dict:
    ensure_gym_fixtures()
    samples_path = out / "samples.jsonl"
    if samples_path.exists():
        samples_path.unlink()

    zone_path = GYM / "zone_gym.json"
    known_path = GYM / "known_true_gym.txt"
    known = load_lines(known_path)
    zone = json.loads(zone_path.read_text(encoding="utf-8"))
    base = zone["base"]
    wl_full = load_lines(GYM / "wordlist_gym.txt")
    known_labels = []
    for k in known:
        kn = normalize_name(k)
        if kn.endswith("." + base):
            known_labels.append(kn[: -(len(base) + 1)])
        else:
            known_labels.append(kn)
    labels = list(dict.fromkeys(known_labels + wl_full))
    if wordlist_cap > 0:
        labels = labels[: max(wordlist_cap, len(known_labels))]
    wl_path = out / "bench_wordlist.txt"
    wl_path.write_text("\n".join(labels) + "\n", encoding="utf-8")
    fqdns = expand_fqdns(labels, base)
    fqdn_path = out / "bench_fqdns.txt"
    fqdn_path.write_text("\n".join(fqdns) + "\n", encoding="utf-8")

    vega = find_vega()
    port = free_udp_port()
    mock_cmd = [
        str(vega),
        "mock-serve",
        "--zone",
        str(zone_path),
        "--bind",
        f"127.0.0.1:{port}",
    ]
    if stress:
        mock_cmd += [
            "--latency-ms",
            str(latency_ms),
            "--servfail-pct",
            str(servfail_pct),
            "--drop-pct",
            str(drop_pct),
        ]
    mock = subprocess.Popen(
        mock_cmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
    )
    time.sleep(0.45)
    if mock.poll() is not None:
        err = mock.stderr.read() if mock.stderr else ""
        raise SystemExit(f"mock-serve failed: {err}")

    resolver = f"127.0.0.1:{port}"
    res_file = out / "resolvers.txt"
    res_file.write_text(resolver + "\n", encoding="utf-8")

    # Stress mode needs longer timeouts/retries (real recursive behavior).
    if timeout_ms is None:
        timeout_ms = 1500 if stress else 400
    if retries is None:
        retries = 4 if stress else 2
    conc = min(concurrency, 800) if stress else concurrency

    rows = []
    try:
        # vegadns
        names = out / "vegadns_names.txt"
        stats = out / "vegadns_stats.json"
        vcmd = [
            str(vega),
            "enum",
            "-d",
            base,
            "-w",
            str(wl_path),
            "-r",
            str(res_file),
            "-o",
            str(names),
            "--stats-json",
            str(stats),
            "--known-true",
            str(known_path),
            "-q",
            "--concurrency",
            str(conc),
            "--timeout-ms",
            str(timeout_ms),
            "--retries",
            str(retries),
            "--sockets",
            str(max(1, sockets)),
        ]
        stop = threading.Event()
        t0 = time.perf_counter()
        proc = subprocess.Popen(vcmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        th = threading.Thread(
            target=sample_until_done, args=(proc, "vegadns", samples_path, stop), daemon=True
        )
        th.start()
        try:
            proc.wait(timeout=900)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        stop.set()
        th.join(timeout=2)
        wall = time.perf_counter() - t0
        if stats.exists():
            wall = float(json.loads(stats.read_text(encoding="utf-8")).get("wall_secs", wall))
        found = parse_names_file(names)
        r, p, hit, kn = recall_precision(found, known)
        note = "enum + wildcard filter"
        if stress:
            note += f" | stress latency={latency_ms}ms servfail={servfail_pct}% drop={drop_pct}%"
        rows.append(final_row("vegadns", wall, len(found), r, p, timed=proc.returncode == 0, note=note))

        # massdns
        mass = which("massdns")
        if mass:
            mout = out / "massdns_out.txt"
            mcmd = [
                mass, "-r", str(res_file), "-t", "A", "-o", "S",
                "-s", "1000" if stress else "2000",
                "-c", "5" if stress else "3",
                "--interval", "200" if stress else "100",
                "-w", str(mout), str(fqdn_path),
            ]
            stop2 = threading.Event()
            t1 = time.perf_counter()
            mp = subprocess.Popen(mcmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            th2 = threading.Thread(
                target=sample_until_done, args=(mp, "massdns", samples_path, stop2), daemon=True
            )
            th2.start()
            try:
                mp.wait(timeout=900)
            except subprocess.TimeoutExpired:
                mp.kill()
                mp.wait()
            stop2.set()
            th2.join(timeout=2)
            mwall = time.perf_counter() - t1
            names_m = parse_massdns_out(mout)
            r, p, hit, kn = recall_precision(names_m, known)
            ok = mp.returncode == 0 and len(names_m) > 0
            rows.append(
                final_row(
                    "massdns", mwall, len(names_m), r, p, timed=ok,
                    note="no wildcard filter" if ok else f"exit={mp.returncode}",
                )
            )
        else:
            rows.append(final_row("massdns", 0, 0, 0.0, 1.0, timed=False, note="not installed"))

        # dnsx
        dnsx = which("dnsx")
        if dnsx:
            dout = out / "dnsx_out.txt"
            dcmd = [
                dnsx, "-l", str(fqdn_path), "-r", resolver, "-o", str(dout),
                "-silent", "-a", "-retry", "2" if stress else "1", "-t", "50" if stress else "100",
            ]
            stop3 = threading.Event()
            t2 = time.perf_counter()
            dp = subprocess.Popen(dcmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            th3 = threading.Thread(
                target=sample_until_done, args=(dp, "dnsx", samples_path, stop3), daemon=True
            )
            th3.start()
            try:
                dp.wait(timeout=300)
            except subprocess.TimeoutExpired:
                dp.kill()
                dp.wait()
            stop3.set()
            th3.join(timeout=2)
            dwall = time.perf_counter() - t2
            names_d = parse_names_file(dout)
            r, p, hit, kn = recall_precision(names_d, known)
            ok = dp.returncode == 0 and len(names_d) > 0
            rows.append(
                final_row(
                    "dnsx", dwall, len(names_d), r, p, timed=ok,
                    note="resolve-only" if ok else f"not comparable exit={dp.returncode}",
                )
            )
        else:
            rows.append(final_row("dnsx", 0, 0, 0.0, 1.0, timed=False, note="not installed"))

        mass_bin = find_massdns()

        # puredns (real tool; wraps massdns)
        puredns = which("puredns")
        if puredns and mass_bin:
            pout = out / "puredns_out.txt"
            pcmd = [
                puredns, "resolve", str(fqdn_path),
                "--resolvers", str(res_file),
                "--bin", mass_bin,
                "--skip-validation",  # local mock has no trusted recursive path
                "-w", str(pout),
                "-q",
            ]
            stop4 = threading.Event()
            t3 = time.perf_counter()
            pp = subprocess.Popen(pcmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            th4 = threading.Thread(
                target=sample_until_done, args=(pp, "puredns", samples_path, stop4), daemon=True
            )
            th4.start()
            try:
                so_p, se_p = pp.communicate(timeout=900)
            except subprocess.TimeoutExpired:
                pp.kill()
                so_p, se_p = pp.communicate()
            stop4.set()
            th4.join(timeout=2)
            pwall = time.perf_counter() - t3
            names_p = parse_names_file(pout)
            r, p, hit, kn = recall_precision(names_p, known)
            ok = pp.returncode == 0 and len(names_p) > 0
            note = "resolve+wildcard filter (massdns backend)" if ok else f"exit={pp.returncode} {(se_p or '')[:120]}"
            rows.append(final_row("puredns", pwall, len(names_p), r, p, timed=ok, note=note))
            (out / "puredns.log").write_text(f"exit={pp.returncode}\n{se_p}\n", encoding="utf-8")
        elif puredns and not mass_bin:
            rows.append(final_row("puredns", 0, 0, 0.0, 1.0, timed=False, note="needs real massdns binary"))
        else:
            rows.append(final_row("puredns", 0, 0, 0.0, 1.0, timed=False, note="not installed"))

        # shuffledns (real PD tool; wraps massdns)
        shuf = which("shuffledns")
        if shuf and mass_bin:
            sout = out / "shuffledns_out.txt"
            scmd = [
                shuf,
                "-mode", "resolve",
                "-list", str(fqdn_path),
                "-r", str(res_file),
                "-m", mass_bin,
                "-o", str(sout),
                "-t", "2000" if not stress else "500",
            ]
            stop5 = threading.Event()
            t4 = time.perf_counter()
            sp = subprocess.Popen(scmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            th5 = threading.Thread(
                target=sample_until_done, args=(sp, "shuffledns", samples_path, stop5), daemon=True
            )
            th5.start()
            try:
                so_s, se_s = sp.communicate(timeout=900)
            except subprocess.TimeoutExpired:
                sp.kill()
                so_s, se_s = sp.communicate()
            stop5.set()
            th5.join(timeout=2)
            swall = time.perf_counter() - t4
            names_s = parse_names_file(sout)
            r, p, hit, kn = recall_precision(names_s, known)
            ok = sp.returncode == 0 and len(names_s) > 0
            note = "resolve mode (massdns backend)" if ok else f"exit={sp.returncode} {(se_s or '')[:120]}"
            rows.append(final_row("shuffledns", swall, len(names_s), r, p, timed=ok, note=note))
            (out / "shuffledns.log").write_text(f"exit={sp.returncode}\n{se_s}\n", encoding="utf-8")
        elif shuf and not mass_bin:
            rows.append(final_row("shuffledns", 0, 0, 0.0, 1.0, timed=False, note="needs real massdns binary"))
        else:
            rows.append(final_row("shuffledns", 0, 0, 0.0, 1.0, timed=False, note="not installed"))

        # gobuster dns (real tool; slower, still timed when present)
        gob = which("gobuster")
        if gob:
            gout = out / "gobuster_dns.txt"
            gcmd = [
                gob, "dns",
                "--domain", base,
                "-w", str(wl_path),
                "--resolver", resolver,
                "-t", "50" if not stress else "20",
                "-o", str(gout),
                "-q",
                "--no-error",
                "--wildcard",
            ]
            stop6 = threading.Event()
            t5 = time.perf_counter()
            gp = subprocess.Popen(gcmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            th6 = threading.Thread(
                target=sample_until_done, args=(gp, "gobuster-dns", samples_path, stop6), daemon=True
            )
            th6.start()
            try:
                so_g, se_g = gp.communicate(timeout=600)
            except subprocess.TimeoutExpired:
                gp.kill()
                so_g, se_g = gp.communicate()
            stop6.set()
            th6.join(timeout=2)
            gwall = time.perf_counter() - t5
            names_g = []
            if gout.exists():
                for ln in gout.read_text(encoding="utf-8", errors="replace").splitlines():
                    s = ln.strip().lower()
                    for tok in s.replace("found:", " ").split():
                        if base in tok:
                            names_g.append(tok.rstrip("."))
            names_g = sorted(set(names_g))
            r, p, hit, kn = recall_precision(names_g, known)
            ok = gp.returncode == 0 and len(names_g) > 0
            rows.append(
                final_row(
                    "gobuster-dns", gwall, len(names_g), r, p, timed=ok,
                    note="dns mode" if ok else f"exit={gp.returncode} {(se_g or '')[:80]}",
                )
            )
        else:
            rows.append(final_row("gobuster-dns", 0, 0, 0.0, 1.0, timed=False, note="not installed"))

    finally:
        mock.terminate()
        try:
            mock.wait(timeout=2)
        except Exception:
            mock.kill()

    mode = "mock-stress" if stress else "mock-clean"
    scope = (
        f"127.0.0.1 stress mock (latency={latency_ms}ms servfail={servfail_pct}% drop={drop_pct}%)"
        if stress
        else "127.0.0.1 clean mock (instant answers)"
    )
    report = {
        "gym": "Subdomain Scanner Gym — TRUE TEST",
        "mode": mode,
        "scope": scope,
        "base": base,
        "known_n": len(known),
        "wordlist_n": len(labels),
        "resolver": resolver,
        "stress": {
            "enabled": stress,
            "latency_ms": latency_ms if stress else 0,
            "servfail_pct": servfail_pct if stress else 0,
            "drop_pct": drop_pct if stress else 0,
        },
        "claim_bounds": CLAIM_BOUNDS,
        "tools": rows,
        "samples_file": samples_path.name,
    }
    write_report(out, report)
    return report


def run_live_resolve(out: Path, *, authorized: bool, concurrency: int) -> dict:
    if not authorized:
        raise SystemExit(
            "live-resolve requires --authorized "
            "(public recursive DNS for fixed FQDN list only; still not third-party brute)"
        )
    samples_path = out / "samples.jsonl"
    if samples_path.exists():
        samples_path.unlink()

    resolvers = load_lines(PUBLIC_RES) if PUBLIC_RES.exists() else ["1.1.1.1", "8.8.8.8", "9.9.9.9"]
    res_file = out / "resolvers.txt"
    res_file.write_text("\n".join(resolvers) + "\n", encoding="utf-8")

    # Candidates = must-resolve + must-nx (full list for tools)
    candidates = list(dict.fromkeys(LIVE_MUST_RESOLVE + LIVE_MUST_NX))
    fqdn_path = out / "live_fqdns.txt"
    fqdn_path.write_text("\n".join(candidates) + "\n", encoding="utf-8")
    known = list(LIVE_MUST_RESOLVE)  # oracle for recall
    # precision: found should not include MUST_NX; we score vs known only for R/P classic,
    # plus nx_leak count for honesty
    nx_set = {normalize_name(x) for x in LIVE_MUST_NX}

    vega = find_vega()
    rows = []

    # vegadns --fqdn-list
    names = out / "vegadns_names.txt"
    stats = out / "vegadns_stats.json"
    vcmd = [
        str(vega), "enum",
        "-d", "example.com",
        "-w", str(fqdn_path),
        "-r", str(res_file),
        "-o", str(names),
        "--stats-json", str(stats),
        "--fqdn-list",
        "-q",
        "--concurrency", str(min(concurrency, 200)),
        "--timeout-ms", "2500",
        "--retries", "2",
        "--sockets", "2",
        "--wildcard-probes", "0",
    ]
    stop = threading.Event()
    t0 = time.perf_counter()
    proc = subprocess.Popen(vcmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    th = threading.Thread(target=sample_until_done, args=(proc, "vegadns", samples_path, stop), daemon=True)
    th.start()
    try:
        proc.wait(timeout=300)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    stop.set()
    th.join(timeout=2)
    wall = time.perf_counter() - t0
    if stats.exists():
        wall = float(json.loads(stats.read_text(encoding="utf-8")).get("wall_secs", wall))
    found = parse_names_file(names)
    r, p, hit, kn = recall_precision(found, known)
    nx_leak = sum(1 for f in found if normalize_name(f) in nx_set)
    rows.append(
        final_row(
            "vegadns", wall, len(found), r, p, timed=proc.returncode == 0,
            note=f"live public resolvers; nx_leak={nx_leak}",
        )
    )

    # massdns
    mass = which("massdns")
    if mass:
        mout = out / "massdns_out.txt"
        mcmd = [
            mass, "-r", str(res_file), "-t", "A", "-o", "S",
            "-s", "500", "-c", "5", "--interval", "200",
            "-w", str(mout), str(fqdn_path),
        ]
        stop2 = threading.Event()
        t1 = time.perf_counter()
        mp = subprocess.Popen(mcmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        th2 = threading.Thread(target=sample_until_done, args=(mp, "massdns", samples_path, stop2), daemon=True)
        th2.start()
        try:
            mp.wait(timeout=300)
        except subprocess.TimeoutExpired:
            mp.kill()
            mp.wait()
        stop2.set()
        th2.join(timeout=2)
        mwall = time.perf_counter() - t1
        names_m = parse_massdns_out(mout)
        r, p, hit, kn = recall_precision(names_m, known)
        nx_leak = sum(1 for f in names_m if normalize_name(f) in nx_set)
        ok = mp.returncode == 0 and len(names_m) > 0
        rows.append(
            final_row(
                "massdns", mwall, len(names_m), r, p, timed=ok,
                note=f"live; nx_leak={nx_leak}" if ok else f"exit={mp.returncode}",
            )
        )
    else:
        rows.append(final_row("massdns", 0, 0, 0.0, 1.0, timed=False, note="not installed"))

    # dnsx
    dnsx = which("dnsx")
    if dnsx:
        dout = out / "dnsx_out.txt"
        # dnsx wants resolvers file as IPs; use first or file
        dcmd = [
            dnsx, "-l", str(fqdn_path), "-r", str(res_file), "-o", str(dout),
            "-silent", "-a", "-retry", "2", "-t", "50",
        ]
        stop3 = threading.Event()
        t2 = time.perf_counter()
        dp = subprocess.Popen(dcmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        th3 = threading.Thread(target=sample_until_done, args=(dp, "dnsx", samples_path, stop3), daemon=True)
        th3.start()
        try:
            dp.wait(timeout=300)
        except subprocess.TimeoutExpired:
            dp.kill()
            dp.wait()
        stop3.set()
        th3.join(timeout=2)
        dwall = time.perf_counter() - t2
        names_d = parse_names_file(dout)
        r, p, hit, kn = recall_precision(names_d, known)
        nx_leak = sum(1 for f in names_d if normalize_name(f) in nx_set)
        ok = dp.returncode == 0 and len(names_d) > 0
        rows.append(
            final_row(
                "dnsx", dwall, len(names_d), r, p, timed=ok,
                note=f"live; nx_leak={nx_leak}" if ok else f"exit={dp.returncode}",
            )
        )
    else:
        rows.append(final_row("dnsx", 0, 0, 0.0, 1.0, timed=False, note="not installed"))

    # puredns
    puredns = which("puredns")
    if puredns:
        pout = out / "puredns_out.txt"
        pcmd = [puredns, "resolve", str(fqdn_path), "--resolvers", str(res_file), "-w", str(pout), "-q"]
        stop4 = threading.Event()
        t3 = time.perf_counter()
        pp = subprocess.Popen(pcmd, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        th4 = threading.Thread(target=sample_until_done, args=(pp, "puredns", samples_path, stop4), daemon=True)
        th4.start()
        try:
            pp.wait(timeout=300)
        except subprocess.TimeoutExpired:
            pp.kill()
            pp.wait()
        stop4.set()
        th4.join(timeout=2)
        pwall = time.perf_counter() - t3
        names_p = parse_names_file(pout)
        r, p, hit, kn = recall_precision(names_p, known)
        nx_leak = sum(1 for f in names_p if normalize_name(f) in nx_set)
        ok = pp.returncode == 0 and len(names_p) > 0
        rows.append(
            final_row(
                "puredns", pwall, len(names_p), r, p, timed=ok,
                note=f"live resolve; nx_leak={nx_leak}" if ok else f"exit={pp.returncode}",
            )
        )
    else:
        rows.append(final_row("puredns", 0, 0, 0.0, 1.0, timed=False, note="not installed"))

    report = {
        "gym": "Subdomain Scanner Gym — TRUE TEST",
        "mode": "live-resolve",
        "scope": "public recursive resolvers + fixed FQDN list (not third-party subdomain hunting)",
        "target": "fixed public hostnames + NX canaries",
        "known_n": len(known),
        "candidates_n": len(candidates),
        "resolver": ",".join(resolvers[:5]),
        "claim_bounds": CLAIM_BOUNDS,
        "tools": rows,
        "samples_file": samples_path.name,
        "authorized": True,
    }
    write_report(out, report)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Subdomain Scanner Gym TRUE TEST")
    ap.add_argument("--out", type=Path, default=ROOT / "gym_out")
    ap.add_argument(
        "--mode",
        choices=["mock-clean", "mock-stress", "live-resolve"],
        default="mock-stress",
        help="default mock-stress = adversarial local; live-resolve = public DNS path",
    )
    ap.add_argument("--wordlist-cap", type=int, default=8000)
    ap.add_argument("--concurrency", type=int, default=4000)
    ap.add_argument("--latency-ms", type=int, default=15, help="mock-stress only")
    ap.add_argument("--servfail-pct", type=int, default=5, help="mock-stress only")
    ap.add_argument("--drop-pct", type=int, default=2, help="mock-stress only")
    ap.add_argument(
        "--authorized",
        action="store_true",
        help="required for live-resolve (fixed FQDN list via public resolvers)",
    )
    args = ap.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    if args.mode == "live-resolve":
        report = run_live_resolve(out, authorized=args.authorized, concurrency=args.concurrency)
    elif args.mode == "mock-stress":
        report = run_mock_mode(
            out,
            stress=True,
            wordlist_cap=args.wordlist_cap,
            concurrency=args.concurrency,
            latency_ms=args.latency_ms,
            servfail_pct=args.servfail_pct,
            drop_pct=args.drop_pct,
        )
    else:
        report = run_mock_mode(
            out,
            stress=False,
            wordlist_cap=args.wordlist_cap,
            concurrency=args.concurrency,
            latency_ms=0,
            servfail_pct=0,
            drop_pct=0,
        )

    v = next((t for t in report["tools"] if t["tool"] == "vegadns"), None)
    if not v or not v.get("timed"):
        return 1
    # Live: require reasonable recall on must-resolve set
    if args.mode == "live-resolve" and v.get("recall", 0) < 0.5:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
