#!/usr/bin/env python3
"""Compare vegadns to DNS peers + document HTTP-adjacent tools (ferox/ffuf/gobuster dir).

DNS-class (fair head-to-head on lab.mock DNS):
  vegadns, massdns, gobuster dns, dnsx

HTTP-class (adjacent next hop, NOT subdomain enum peers):
  feroxbuster, ffuf, gobuster dir — run only against lab HTTP dummy after DNS names known

Passive (status only on lab.test; no public targets):
  subfinder, puredns (if massdns-backed brute cannot use mock port cleanly)

Private lab only.
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
LAB = ROOT / "fixtures" / "lab"
ZONE = LAB / "zone_lab.json"
KNOWN = LAB / "known_true_lab.txt"
WORDLIST = LAB / "wordlist_lab.txt"


def which(name: str) -> str | None:
    return shutil.which(name)


def load_lines(path: Path) -> list[str]:
    return [
        ln.strip().lower().rstrip(".")
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def recall_precision(found: list[str], known: list[str]) -> tuple[float, float, int]:
    fs, ks = set(found), set(known)
    hit = len(fs & ks)
    r = 1.0 if not ks else hit / len(ks)
    p = 1.0 if not fs else hit / len(fs)
    return r, p, hit


def find_vega() -> Path | None:
    for p in (
        ROOT / "target" / "release" / "vegadns",
        ROOT / "target" / "release" / "vegadns.exe",
        Path.home() / "vegadns" / "target" / "release" / "vegadns",
    ):
        if p.exists():
            return p
    w = which("vegadns")
    return Path(w) if w else None


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def expand_fqdns(wordlist: Path, base: str) -> list[str]:
    base = base.rstrip(".")
    out = []
    for w in load_lines(wordlist):
        if w == base or w.endswith("." + base):
            out.append(w)
        else:
            out.append(f"{w}.{base}")
    return out


def _as_text(data: str | bytes | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def run_timed(cmd: list[str], timeout: float = 300) -> tuple[int, str, str, float]:
    t0 = time.perf_counter()
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT)
        )
        return p.returncode, p.stdout or "", p.stderr or "", time.perf_counter() - t0
    except FileNotFoundError:
        return 127, "", "not found", time.perf_counter() - t0
    except subprocess.TimeoutExpired as e:
        so = _as_text(e.stdout)
        se = _as_text(e.stderr) + "\nTIMEOUT"
        return 124, so, se, time.perf_counter() - t0


def row(
    tool: str,
    *,
    available: bool,
    timed: bool = False,
    wall: float | None = None,
    found: int | None = None,
    recall: float | None = None,
    precision: float | None = None,
    note: str = "",
    names: list[str] | None = None,
    lane: str = "dns",
) -> dict:
    return {
        "tool": tool,
        "lane": lane,
        "available": available,
        "timed": timed,
        "wall": wall,
        "found": found,
        "recall": recall,
        "precision": precision,
        "note": note,
        "names": names or [],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--wordlist-cap", type=int, default=5000, help="cap labels for slower tools")
    args = ap.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    if not ZONE.exists():
        subprocess.check_call(
            [sys.executable, str(ROOT / "scripts" / "gen_lab_fixtures.py"), "--known", "500", "--wordlist", "25000"],
            cwd=str(ROOT),
        )

    known = load_lines(KNOWN)
    base = json.loads(ZONE.read_text(encoding="utf-8"))["base"]
    full_wl = load_lines(WORDLIST)
    # Cap wordlist for gobuster etc (thread-limited); vegadns/massdns get full or capped consistently
    # Fair DNS compare: same capped list for all timed DNS tools
    labels = full_wl[: args.wordlist_cap]
    wl_path = out / "compare_wordlist.txt"
    wl_path.write_text("\n".join(labels) + "\n", encoding="utf-8")
    fqdns = expand_fqdns(wl_path, base)
    fqdn_path = out / "compare_fqdns.txt"
    fqdn_path.write_text("\n".join(fqdns) + "\n", encoding="utf-8")

    # known-true that appear in this capped wordlist only (labels that expand to known)
    known_set = set(known)
    known_in_cap = [f for f in known if f.split(".")[0] in set(labels) or f in labels]
    # better: known FQDNs whose left label is in wordlist
    label_set = set(labels)
    known_in_cap = []
    for k in known:
        left = k[: -len(base) - 1] if k.endswith("." + base) else k
        if left in label_set or k in label_set:
            known_in_cap.append(k)
    if not known_in_cap:
        known_in_cap = known  # fallback

    rows: list[dict] = []
    port = free_port()
    vega = find_vega()
    if not vega:
        print("vegadns binary missing", file=sys.stderr)
        return 2

    mock = subprocess.Popen(
        [str(vega), "mock-serve", "--zone", str(ZONE), "--bind", f"127.0.0.1:{port}"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.4)
    resolver = f"127.0.0.1:{port}"
    res_file = out / "resolvers.txt"
    res_file.write_text(resolver + "\n", encoding="utf-8")

    try:
        if mock.poll() is not None:
            err = mock.stderr.read() if mock.stderr else "exit"
            (out / "mock_failed.log").write_text(err, encoding="utf-8")
            print("mock-serve failed", err)
            return 3

        # --- vegadns ---
        names_v = out / "vegadns_names.txt"
        stats_v = out / "vegadns_stats.json"
        code, so, se, wall = run_timed(
            [
                str(vega),
                "enum",
                "-d",
                base,
                "-w",
                str(wl_path),
                "-r",
                str(res_file),
                "-o",
                str(names_v),
                "--stats-json",
                str(stats_v),
                "--known-true",
                str(KNOWN),
                "-q",
                "--concurrency",
                "4000",
                "--timeout-ms",
                "400",
                "--retries",
                "2",
                "--sockets",
                "4",
            ]
        )
        found = load_lines(names_v) if names_v.exists() else []
        r, p, hit = recall_precision(found, known_in_cap)
        st = json.loads(stats_v.read_text()) if stats_v.exists() else {}
        wall = float(st.get("wall_secs", wall))
        rows.append(
            row(
                "vegadns",
                available=True,
                timed=code == 0,
                wall=wall,
                found=len(found),
                recall=r,
                precision=p,
                note=f"active DNS brute+wildcard filter hit={hit}/{len(known_in_cap)}",
                names=found,
            )
        )
        (out / "vegadns.log").write_text(f"exit={code}\n{se}\n{so}\n", encoding="utf-8")

        # --- massdns ---
        mass = which("massdns")
        if mass:
            mout = out / "massdns_out.txt"
            code, so, se, wall = run_timed(
                [
                    mass,
                    "-r",
                    str(res_file),
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
                    str(mout),
                    str(fqdn_path),
                ]
            )
            names = set()
            if mout.exists():
                for ln in mout.read_text(encoding="utf-8", errors="replace").splitlines():
                    if ln.strip():
                        names.add(ln.split()[0].rstrip(".").lower())
            found = sorted(names)
            r, p, hit = recall_precision(found, known_in_cap)
            rows.append(
                row(
                    "massdns",
                    available=True,
                    timed=code == 0 and len(found) > 0,
                    wall=wall if code == 0 else None,
                    found=len(found),
                    recall=r,
                    precision=p,
                    note="bulk resolve; no wildcard filter",
                    names=found,
                )
            )
            (out / "massdns.log").write_text(f"exit={code} wall={wall}\n{se}\n", encoding="utf-8")
        else:
            rows.append(row("massdns", available=False, note="not installed"))

        # --- gobuster dns ---
        gob = which("gobuster")
        if gob:
            gout = out / "gobuster_dns.txt"
            code, so, se, wall = run_timed(
                [
                    gob,
                    "dns",
                    "--domain",
                    base,
                    "-w",
                    str(wl_path),
                    "--resolver",
                    resolver,
                    "-t",
                    "50",
                    "-o",
                    str(gout),
                    "-q",
                    "--no-error",
                    "--wildcard",
                ],
                timeout=600,
            )
            found = []
            if gout.exists():
                for ln in gout.read_text(encoding="utf-8", errors="replace").splitlines():
                    # formats vary: "Found: x.lab.test" or just name
                    s = ln.strip().lower()
                    if not s:
                        continue
                    for tok in s.replace("found:", " ").split():
                        if base in tok:
                            found.append(tok.rstrip("."))
                            break
            found = sorted(set(found))
            r, p, hit = recall_precision(found, known_in_cap)
            rows.append(
                row(
                    "gobuster-dns",
                    available=True,
                    timed=code == 0,
                    wall=wall if code == 0 else None,
                    found=len(found),
                    recall=r,
                    precision=p,
                    note=f"DNS mode threads=50 hit={hit}",
                    names=found,
                )
            )
            (out / "gobuster_dns.log").write_text(
                f"exit={code} wall={wall}\n{se}\n{so[:2000]}\n", encoding="utf-8"
            )
        else:
            rows.append(row("gobuster-dns", available=False, note="not installed"))

        # --- dnsx resolve ---
        dnsx = which("dnsx")
        if dnsx:
            dout = out / "dnsx_names.txt"
            code, so, se, wall = run_timed(
                [
                    dnsx,
                    "-l",
                    str(fqdn_path),
                    "-r",
                    resolver,
                    "-o",
                    str(dout),
                    "-silent",
                    "-a",
                    "-retry",
                    "1",
                    "-t",
                    "100",
                ],
                timeout=120,
            )
            found = []
            if dout.exists():
                for ln in dout.read_text(encoding="utf-8", errors="replace").splitlines():
                    if ln.strip():
                        found.append(ln.strip().split()[0].lower().rstrip("."))
            found = sorted(set(found))
            r, p, hit = recall_precision(found, known_in_cap)
            ok = code == 0 and len(found) > 0 and wall < 100
            rows.append(
                row(
                    "dnsx",
                    available=True,
                    timed=ok,
                    wall=wall if ok else None,
                    found=len(found),
                    recall=r,
                    precision=p,
                    note="resolve-only" + ("" if ok else f"; not comparable exit={code} found={len(found)}"),
                    names=found,
                )
            )
            (out / "dnsx.log").write_text(f"exit={code} wall={wall}\n{se}\n", encoding="utf-8")
        else:
            rows.append(row("dnsx", available=False, note="not installed"))

        # --- puredns status ---
        pure = which("puredns")
        mass = which("massdns")
        if pure and mass:
            rows.append(
                row(
                    "puredns",
                    available=True,
                    timed=False,
                    note="installed; massdns-backed brute not wired to custom :port mock in this harness",
                )
            )
        elif pure:
            rows.append(
                row("puredns", available=True, timed=False, note="installed but massdns missing")
            )
        else:
            rows.append(row("puredns", available=False, note="not installed"))

        # --- subfinder passive (lab.test is not public; short timeout, status only) ---
        sub = which("subfinder")
        if sub:
            code, so, se, wall = run_timed(
                [sub, "-d", base, "-silent", "-timeout", "3"], timeout=12
            )
            found = [ln.strip().lower() for ln in so.splitlines() if ln.strip()]
            rows.append(
                row(
                    "subfinder",
                    available=True,
                    timed=False,
                    wall=wall,
                    found=len(found),
                    note="passive OSINT; lab.test not a public domain — not a DNS-brute peer",
                    names=found,
                    lane="passive",
                )
            )
        else:
            rows.append(row("subfinder", available=False, note="not installed", lane="passive"))

        # --- HTTP adjacent: tiny wordlist against dummy HTTP ---
        # Use first 20 known hosts as vhost-ish paths via Host header is hard; instead
        # serve simple files and brute paths — shows ferox is post-enum.
        http_port = 18080
        hosts_file = out / "http_hosts.txt"
        # path wordlist for content discovery
        path_wl = out / "paths.txt"
        path_wl.write_text("\n".join(["/", "admin", "api", "login", "robots.txt", "health"]) + "\n")
        # start http dummy for known hosts
        http_proc = None
        ferox = which("feroxbuster")
        ffuf = which("ffuf")
        if ferox or ffuf or which("gobuster"):
            http_proc = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "lab_http_dummy.py"),
                    "--hosts-file",
                    str(KNOWN),
                    "--bind",
                    "127.0.0.1",
                    "--port",
                    str(http_port),
                ],
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.3)

        # ferox against plain URL (no vhost) — lab dummy only checks Host
        if ferox and http_proc:
            # Use Host header with a known host
            sample_host = known_in_cap[0] if known_in_cap else f"www.{base}"
            fout = out / "ferox_out.txt"
            code, so, se, wall = run_timed(
                [
                    ferox,
                    "-u",
                    f"http://127.0.0.1:{http_port}/",
                    "-w",
                    str(path_wl),
                    "-H",
                    f"Host: {sample_host}",
                    "-q",
                    "-o",
                    str(fout),
                    "--json",
                ],
                timeout=60,
            )
            hits = 0
            if fout.exists():
                hits = sum(1 for _ in fout.read_text(encoding="utf-8", errors="replace").splitlines() if _.strip())
            rows.append(
                row(
                    "feroxbuster",
                    available=True,
                    timed=code == 0 or hits > 0,
                    wall=wall,
                    found=hits,
                    note="HTTP content discovery (adjacent next hop), NOT subdomain enum",
                    lane="http",
                )
            )
            (out / "ferox.log").write_text(f"exit={code} wall={wall}\n{se}\n{so[:1500]}\n", encoding="utf-8")
        else:
            rows.append(
                row(
                    "feroxbuster",
                    available=bool(ferox),
                    timed=False,
                    note="not run or not installed — HTTP path brute, not DNS peer",
                    lane="http",
                )
            )

        if ffuf and http_proc:
            sample_host = known_in_cap[0] if known_in_cap else f"www.{base}"
            code, so, se, wall = run_timed(
                [
                    ffuf,
                    "-u",
                    f"http://127.0.0.1:{http_port}/FUZZ",
                    "-w",
                    str(path_wl),
                    "-H",
                    f"Host: {sample_host}",
                    "-mc",
                    "200,301,302,401,403",
                    "-t",
                    "20",
                    "-s",
                ],
                timeout=60,
            )
            rows.append(
                row(
                    "ffuf",
                    available=True,
                    timed=code == 0,
                    wall=wall,
                    found=None,
                    note="HTTP fuzzer (adjacent next hop), NOT subdomain enum",
                    lane="http",
                )
            )
            (out / "ffuf.log").write_text(f"exit={code} wall={wall}\n{se}\n{so[:1500]}\n", encoding="utf-8")
        else:
            rows.append(
                row(
                    "ffuf",
                    available=bool(ffuf),
                    timed=False,
                    note="not run or not installed — HTTP, not DNS peer",
                    lane="http",
                )
            )

        gob = which("gobuster")
        if gob and http_proc:
            sample_host = known_in_cap[0] if known_in_cap else f"www.{base}"
            gout = out / "gobuster_dir.txt"
            code, so, se, wall = run_timed(
                [
                    gob,
                    "dir",
                    "-u",
                    f"http://127.0.0.1:{http_port}/",
                    "-w",
                    str(path_wl),
                    "-t",
                    "20",
                    "-q",
                    "-o",
                    str(gout),
                    "-H",
                    f"Host: {sample_host}",
                ],
                timeout=60,
            )
            rows.append(
                row(
                    "gobuster-dir",
                    available=True,
                    timed=code == 0,
                    wall=wall,
                    found=None,
                    note="HTTP dir mode (adjacent next hop), NOT DNS peer",
                    lane="http",
                )
            )
            (out / "gobuster_dir.log").write_text(
                f"exit={code} wall={wall}\n{se}\n{so[:1000]}\n", encoding="utf-8"
            )
        else:
            rows.append(
                row(
                    "gobuster-dir",
                    available=bool(gob),
                    timed=False,
                    note="not run or not installed — HTTP, not DNS peer",
                    lane="http",
                )
            )

        if http_proc:
            http_proc.terminate()
            try:
                http_proc.wait(timeout=2)
            except Exception:
                http_proc.kill()

    finally:
        mock.terminate()
        try:
            mock.wait(timeout=2)
        except Exception:
            mock.kill()

    # report
    dns_rows = [r for r in rows if r["lane"] == "dns" and r.get("timed") and r.get("wall") is not None]
    vega_wall = next((r["wall"] for r in rows if r["tool"] == "vegadns"), None)
    lines = []
    lines.append("vegadns vs DNS peers + HTTP-adjacent tools")
    lines.append("=" * 72)
    lines.append(f"scope: private lab mock only ({base})")
    lines.append(f"wordlist_cap: {len(labels)} labels (fair compare)")
    lines.append(f"known_true_in_cap: {len(known_in_cap)}")
    lines.append(f"host: {sys.platform}")
    lines.append("")
    lines.append("## DNS lane (subdomain enum peers)")
    lines.append(
        f"{'tool':<14} {'wall_s':>10} {'found':>7} {'recall':>8} {'prec':>8}  notes"
    )
    lines.append("-" * 72)
    for r in rows:
        if r["lane"] != "dns":
            continue
        wall = f"{r['wall']:.4f}" if isinstance(r.get("wall"), (int, float)) else "-"
        found = str(r["found"]) if r.get("found") is not None else "-"
        rec = f"{r['recall']:.3f}" if isinstance(r.get("recall"), (int, float)) else "-"
        prec = f"{r['precision']:.3f}" if isinstance(r.get("precision"), (int, float)) else "-"
        avail = "" if r["available"] else " [missing]"
        timed = "" if r.get("timed") or not r["available"] else " [not timed]"
        lines.append(
            f"{r['tool']:<14} {wall:>10} {found:>7} {rec:>8} {prec:>8}  {r.get('note','')}{avail}{timed}"
        )
    lines.append("")
    if vega_wall is not None and dns_rows:
        lines.append("### vegadns vs timed DNS peers (wall lower is better)")
        for r in dns_rows:
            if r["tool"] == "vegadns":
                continue
            if r["wall"] is None:
                continue
            beat = vega_wall <= r["wall"] + 1e-9
            lines.append(
                f"  vs {r['tool']}: vegadns {vega_wall:.4f}s vs {r['wall']:.4f}s → "
                f"{'WIN' if beat else 'LOSE'}"
            )
    lines.append("")
    lines.append("## HTTP lane (adjacent next hop — not competitors for subdomain enum)")
    lines.append(
        "Pipeline: vegadns (subs) → httpx (live HTTP) → feroxbuster/ffuf/gobuster dir (paths)"
    )
    for r in rows:
        if r["lane"] != "http":
            continue
        wall = f"{r['wall']:.4f}s" if isinstance(r.get("wall"), (int, float)) else "-"
        lines.append(f"  {r['tool']}: wall={wall}  {r.get('note','')}")
    lines.append("")
    lines.append("## Passive lane")
    for r in rows:
        if r["lane"] != "passive":
            continue
        lines.append(f"  {r['tool']}: {r.get('note','')}")
    lines.append("")
    lines.append("## Verdict")
    lines.append(
        "vegadns competes in DNS brute/resolve. ferox/ffuf/gobuster-dir sit AFTER hosts exist."
    )
    report = "\n".join(lines) + "\n"
    (out / "adjacent_compare.txt").write_text(report, encoding="utf-8")
    (out / "adjacent_compare.json").write_text(
        json.dumps({"rows": rows, "vega_wall": vega_wall, "cap": len(labels)}, indent=2, default=str),
        encoding="utf-8",
    )
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
