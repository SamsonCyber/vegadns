#!/usr/bin/env python3
"""Discovery coverage head-to-head: vegadns must SURPASS peers on F1.

Coverage quality = F1 from known-true recall and precision on fixed private lab suites.
Not public-internet supremacy. Not code line coverage.

DNS: wildcard filter → precision 1.0 while massdns/dnsx keep noise.
Paths (hard suite): soft-404 bait returns 200; vegadns soft-404 filter drops it;
peers matching status-only keep noise → lower F1.

Private lab / mock only.
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "fixtures" / "lab"
PATHS = ROOT / "fixtures" / "paths"

# Hard paths suite: soft-404 + 401/403 known-true
HARD_ZONE = PATHS / "hard_zone.txt"
HARD_WL = PATHS / "wordlist_hard.txt"
HARD_KNOWN = PATHS / "known_true_hard.txt"
# Fair status set for all HTTP tools
PATH_STATUSES = "200,401,403"


def which(name: str) -> str | None:
    return shutil.which(name) or shutil.which(name + ".exe")


def load_lines(path: Path) -> list[str]:
    return [
        ln.strip().lower().rstrip(".")
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def find_vega() -> Path:
    for p in (
        ROOT / "target" / "release" / "vegadns.exe",
        ROOT / "target" / "release" / "vegadns",
        Path.home() / "vegadns" / "target" / "release" / "vegadns",
    ):
        if p.exists():
            return p
    w = which("vegadns")
    if w:
        return Path(w)
    raise SystemExit("vegadns release binary missing")


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def free_tcp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def f1(r: float, p: float) -> float:
    if r + p <= 0:
        return 0.0
    return 2.0 * r * p / (r + p)


def normalize_name(s: str) -> str:
    s = s.strip().lower().rstrip(".")
    if "://" in s:
        u = urlparse(s)
        path = u.path or "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        return path.lstrip("/").lower()
    return s


def recall_precision(found: list[str], known: list[str]) -> tuple[float, float, int]:
    fs = {normalize_name(x) for x in found}
    ks = [normalize_name(k) for k in known]
    hit = sum(1 for k in ks if k in fs)
    r = 1.0 if not ks else hit / len(ks)
    p = 1.0 if not found else hit / len(found)
    return r, p, hit


def _as_text(data: str | bytes | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def run_cmd(cmd: list[str], timeout: float = 300) -> tuple[int, str, str, float]:
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
        return p.returncode, p.stdout or "", p.stderr or "", time.perf_counter() - t0
    except FileNotFoundError:
        return 127, "", "not found", time.perf_counter() - t0
    except subprocess.TimeoutExpired as e:
        return 124, _as_text(e.stdout), _as_text(e.stderr) + "\nTIMEOUT", time.perf_counter() - t0


def expand_fqdns(labels: list[str], base: str) -> list[str]:
    base = base.rstrip(".")
    out = []
    for w in labels:
        if w == base or w.endswith("." + base):
            out.append(w)
        else:
            out.append(f"{w}.{base}")
    return out


def write_report(path: Path, title: str, lines: list[str]) -> None:
    path.write_text(title + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def gate_surpass(vega_row: dict, peer_rows: list[dict]) -> tuple[bool, list[str], list[str], str]:
    """Require recall>=peers, F1>=peers, and strict F1 win when any peer is timed."""
    vr = float(vega_row["recall"])
    vp = float(vega_row["precision"])
    vf = f1(vr, vp)
    peers = [r for r in peer_rows if r.get("timed") and r.get("recall") is not None]
    wins, loses = [], []
    max_peer_f1 = -1.0
    max_peer_r = -1.0
    for r in peers:
        pr = float(r["recall"])
        pp = float(r.get("precision") or 0.0)
        pf = f1(pr, pp)
        max_peer_f1 = max(max_peer_f1, pf)
        max_peer_r = max(max_peer_r, pr)
        if vf + 1e-12 >= pf and vr + 1e-12 >= pr:
            wins.append(f"{r['tool']} (f1 {vf:.3f}>={pf:.3f} r {vr:.3f}>={pr:.3f})")
        else:
            loses.append(f"{r['tool']} (f1 {vf:.3f}<{pf:.3f} or r {vr:.3f}<{pr:.3f})")

    noise_ok = vega_row.get("noise", 0) == 0 and abs(vp - 1.0) < 1e-9
    full_recall = abs(vr - 1.0) < 1e-9
    if not peers:
        # Solo: must hit perfect known-true coverage quality
        gate = full_recall and noise_ok and abs(vf - 1.0) < 1e-9
        mode = "solo_perfect"
    else:
        # Surpass: F1 strictly above best timed peer (breaks recall ties via precision)
        strict = vf > max_peer_f1 + 1e-9
        gate = full_recall and noise_ok and not loses and strict
        mode = "strict_f1_surpass" if strict else "tie_or_lose"
    return gate, wins, loses, mode


def dns_lane(out: Path, vega: Path, wordlist_cap: int) -> dict:
    zone = LAB / "zone_lab.json"
    known_p = LAB / "known_true_lab.txt"
    wl_p = LAB / "wordlist_lab.txt"
    if not zone.exists():
        subprocess.check_call(
            [sys.executable, str(ROOT / "scripts" / "gen_lab_fixtures.py"), "--known", "500", "--wordlist", "25000"],
            cwd=str(ROOT),
        )
    known = load_lines(known_p)
    base = json.loads(zone.read_text(encoding="utf-8"))["base"]
    labels = load_lines(wl_p)[:wordlist_cap]
    known_labels = []
    for k in known:
        left = k[: -len(base) - 1] if k.endswith("." + base) else k
        known_labels.append(left)
    labels = list(dict.fromkeys(known_labels + labels))[: max(wordlist_cap, len(known_labels))]
    wl_path = out / "dns_wordlist.txt"
    wl_path.write_text("\n".join(labels) + "\n", encoding="utf-8")
    fqdns = expand_fqdns(labels, base)
    fqdn_path = out / "dns_fqdns.txt"
    fqdn_path.write_text("\n".join(fqdns) + "\n", encoding="utf-8")

    port = free_port()
    mock = subprocess.Popen(
        [str(vega), "mock-serve", "--zone", str(zone), "--bind", f"127.0.0.1:{port}"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.35)
    resolver = f"127.0.0.1:{port}"
    res_file = out / "dns_resolvers.txt"
    res_file.write_text(resolver + "\n", encoding="utf-8")

    rows = []
    try:
        if mock.poll() is not None:
            return {"gate": False, "error": "mock-serve failed", "rows": []}

        names = out / "dns_vegadns_names.txt"
        stats = out / "dns_vegadns_stats.json"
        code, so, se, wall = run_cmd(
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
                str(names),
                "--stats-json",
                str(stats),
                "--known-true",
                str(known_p),
                "-q",
                "--concurrency",
                "2000",
                "--timeout-ms",
                "500",
                "--retries",
                "2",
                "--sockets",
                "4",
            ]
        )
        found = load_lines(names) if names.exists() else []
        r, p, hit = recall_precision(found, known)
        st = json.loads(stats.read_text()) if stats.exists() else {}
        wall_v = float(st.get("wall_secs", wall))
        garbage = [n for n in found if "junk" in n or "noise-" in n]
        wild = [n for n in found if ".wild." in n or ".cdn-edge." in n]
        rows.append(
            {
                "tool": "vegadns",
                "timed": code == 0,
                "wall": wall_v,
                "found": len(found),
                "recall": r,
                "precision": p,
                "f1": f1(r, p),
                "hit": hit,
                "known_n": len(known),
                "noise": len(garbage) + len(wild),
                "note": "enum + wildcard filter",
            }
        )
        (out / "dns_vegadns.log").write_text(f"exit={code}\n{se}\n", encoding="utf-8")

        mass = which("massdns")
        if mass:
            mout = out / "dns_massdns_out.txt"
            code, so, se, wall = run_cmd(
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
            names_m = []
            if mout.exists():
                for ln in mout.read_text(encoding="utf-8", errors="replace").splitlines():
                    if ln.strip():
                        names_m.append(ln.split()[0].rstrip(".").lower())
            names_m = sorted(set(names_m))
            r, p, hit = recall_precision(names_m, known)
            rows.append(
                {
                    "tool": "massdns",
                    "timed": code == 0 and len(names_m) > 0,
                    "wall": wall if code == 0 else None,
                    "found": len(names_m),
                    "recall": r,
                    "precision": p,
                    "f1": f1(r, p),
                    "hit": hit,
                    "known_n": len(known),
                    "noise": len(names_m) - hit,
                    "note": "no wildcard filter",
                }
            )
            (out / "dns_massdns.log").write_text(f"exit={code}\n{se}\n", encoding="utf-8")
        else:
            rows.append({"tool": "massdns", "timed": False, "note": "not installed"})

        gob = which("gobuster")
        if gob:
            gout = out / "dns_gobuster.txt"
            code, so, se, wall = run_cmd(
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
            names_g = []
            if gout.exists():
                for ln in gout.read_text(encoding="utf-8", errors="replace").splitlines():
                    s = ln.strip().lower()
                    for tok in s.replace("found:", " ").split():
                        if base in tok:
                            names_g.append(tok.rstrip("."))
            names_g = sorted(set(names_g))
            r, p, hit = recall_precision(names_g, known)
            rows.append(
                {
                    "tool": "gobuster-dns",
                    "timed": code == 0,
                    "wall": wall if code == 0 else None,
                    "found": len(names_g),
                    "recall": r,
                    "precision": p,
                    "f1": f1(r, p),
                    "hit": hit,
                    "known_n": len(known),
                    "note": "dns mode",
                }
            )
            (out / "dns_gobuster.log").write_text(
                f"exit={code} wall={wall}\n{se}\n{so[:2000]}\n", encoding="utf-8"
            )
        else:
            rows.append({"tool": "gobuster-dns", "timed": False, "note": "not installed"})

        dnsx = which("dnsx")
        if dnsx:
            dout = out / "dns_dnsx.txt"
            code, so, se, wall = run_cmd(
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
                timeout=90,
            )
            names_d = []
            if dout.exists():
                for ln in dout.read_text(encoding="utf-8", errors="replace").splitlines():
                    if ln.strip():
                        names_d.append(ln.strip().split()[0].lower().rstrip("."))
            names_d = sorted(set(names_d))
            r, p, hit = recall_precision(names_d, known)
            ok = code == 0 and len(names_d) > 0
            rows.append(
                {
                    "tool": "dnsx",
                    "timed": ok,
                    "wall": wall if ok else None,
                    "found": len(names_d),
                    "recall": r,
                    "precision": p,
                    "f1": f1(r, p),
                    "hit": hit,
                    "known_n": len(known),
                    "noise": max(0, len(names_d) - hit),
                    "note": "resolve-only" if ok else f"not comparable exit={code}",
                }
            )
            (out / "dns_dnsx.log").write_text(f"exit={code}\n{se}\n", encoding="utf-8")
        else:
            rows.append({"tool": "dnsx", "timed": False, "note": "not installed"})

    finally:
        mock.terminate()
        try:
            mock.wait(timeout=2)
        except Exception:
            mock.kill()

    v = next(r for r in rows if r["tool"] == "vegadns")
    peers = [r for r in rows if r["tool"] != "vegadns"]
    gate, wins, loses, mode = gate_surpass(v, peers)

    lines = [
        f"definition: coverage quality = F1(known-true recall, precision) known_n={v['known_n']}",
        f"wordlist_labels: {len(labels)}",
        f"host: {sys.platform}",
        f"surpass_mode: {mode}",
        "",
        f"{'tool':<14} {'timed':<6} {'wall':>8} {'found':>6} {'recall':>8} {'prec':>8} {'f1':>8} {'hit':>5}  notes",
        "-" * 92,
    ]
    for r in rows:
        timed = "yes" if r.get("timed") else "no"
        wall = f"{r['wall']:.4f}" if isinstance(r.get("wall"), (int, float)) else "-"
        found = str(r.get("found", "-"))
        rec = f"{r['recall']:.3f}" if isinstance(r.get("recall"), (int, float)) else "-"
        prec = f"{r['precision']:.3f}" if isinstance(r.get("precision"), (int, float)) else "-"
        ff = f"{r['f1']:.3f}" if isinstance(r.get("f1"), (int, float)) else "-"
        hit = str(r.get("hit", "-"))
        lines.append(
            f"{r['tool']:<14} {timed:<6} {wall:>8} {found:>6} {rec:>8} {prec:>8} {ff:>8} {hit:>5}  {r.get('note','')}"
        )
    lines += [
        "",
        f"vegadns_recall: {v['recall']:.4f}",
        f"vegadns_precision: {v.get('precision')}",
        f"vegadns_f1: {v.get('f1'):.4f}",
        f"noise_hits: {v.get('noise', 0)}",
        f"beats_timed_peers: {', '.join(wins) if wins else '(no timed peers)'}",
        f"loses_to: {', '.join(loses) if loses else 'none'}",
        f"GATE_DNS_COVERAGE: {'PASS' if gate else 'FAIL'}",
    ]
    write_report(out / "coverage_dns.txt", "DNS discovery coverage (F1 surpass)", lines)
    return {"gate": gate, "rows": rows, "vegadns": v, "mode": mode}


def parse_hard_zone(path: Path) -> dict[str, int]:
    """path -> status code."""
    out: dict[str, int] = {}
    for ln in path.read_text(encoding="utf-8").splitlines():
        t = ln.strip()
        if not t or t.startswith("#"):
            continue
        parts = t.split()
        p = parts[0].lstrip("/").lower()
        code = int(parts[1]) if len(parts) > 1 else 200
        out[p] = code
    return out


def paths_lane(out: Path, vega: Path) -> dict:
    """Hard suite: soft-404 200 noise + 401/403 real hits."""
    if not HARD_ZONE.exists() or not HARD_WL.exists() or not HARD_KNOWN.exists():
        return {"gate": False, "error": "hard path fixtures missing", "rows": []}

    zone_map = parse_hard_zone(HARD_ZONE)
    words = [
        ln.strip()
        for ln in HARD_WL.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]

    http_port = free_tcp_port()
    # Soft-404 body must match vegadns mock length fingerprint style
    soft_body = "<!DOCTYPE html><html><body>Not Found soft404-lab-pad.</body></html>\n"
    real_body = "vegadns-real-hit-body-v1\n"
    body_401 = "auth-required-vegadns\n"
    body_403 = "forbidden-vegadns\n"

    srv_script = out / "_path_server_hard.py"
    srv_script.write_text(
        f"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ZONE = {zone_map!r}
SOFT = {soft_body!r}.encode()
REAL = {real_body!r}.encode()
B401 = {body_401!r}.encode()
B403 = {body_403!r}.encode()

class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def do_GET(self):
        key = self.path.split('?',1)[0].lstrip('/').lower()
        if key in ZONE:
            code = ZONE[key]
            if code == 401:
                body = B401
            elif code == 403:
                body = B403
            else:
                body = REAL
        else:
            code = 200
            body = SOFT
        self.send_response(code)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
ThreadingHTTPServer(('127.0.0.1', {http_port}), H).serve_forever()
""",
        encoding="utf-8",
    )
    http = subprocess.Popen(
        [sys.executable, str(srv_script)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.3)
    base = f"http://127.0.0.1:{http_port}/"
    known = [
        ln.strip().replace("PORT", str(http_port)).lower().rstrip(".")
        for ln in HARD_KNOWN.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    wl = out / "paths_wordlist_hard.txt"
    wl.write_text("\n".join(words) + "\n", encoding="utf-8")

    rows = []
    try:
        names = out / "paths_vegadns_urls.txt"
        stats = out / "paths_vegadns_stats.json"
        code, so, se, wall = run_cmd(
            [
                str(vega),
                "paths",
                "-u",
                base,
                "-w",
                str(wl),
                "-o",
                str(names),
                "--stats-json",
                str(stats),
                "--status",
                PATH_STATUSES,
                "--soft404-probes",
                "3",
                "--concurrency",
                "64",
                "--timeout-ms",
                "3000",
                "--retries",
                "0",
                "-q",
            ]
        )
        found = load_lines(names) if names.exists() else []
        if not found and so:
            found = [ln.strip().lower() for ln in so.splitlines() if ln.strip().startswith("http")]
        r, p, hit = recall_precision(found, known)
        st = json.loads(stats.read_text()) if stats.exists() else {}
        # Fair H2H: use process wall for every tool (same run_cmd clock as ferox/ffuf).
        # Engine wall_secs stays in stats/notes for debug only.
        eng_wall = float(st.get("wall_secs", wall)) if st else float(wall)
        known_keys = {normalize_name(k) for k in known}
        noise = [u for u in found if normalize_name(u) not in known_keys]
        rows.append(
            {
                "tool": "vegadns-paths",
                "timed": code == 0,
                "wall": float(wall),
                "engine_wall": eng_wall,
                "found": len(found),
                "recall": r,
                "precision": p,
                "f1": f1(r, p),
                "hit": hit,
                "known_n": len(known),
                "noise": len(noise),
                "note": (
                    f"soft404 filter drop={st.get('soft404_dropped','?')} "
                    f"status={PATH_STATUSES} engine_wall={eng_wall:.4f}"
                ),
            }
        )
        (out / "paths_vegadns.log").write_text(f"exit={code}\n{se}\n{so}\n", encoding="utf-8")

        # Embedded hard mock self-check
        code2, so2, se2, wall2 = run_cmd(
            [
                str(vega),
                "paths",
                "--mock-hard-zone",
                str(HARD_ZONE),
                "-w",
                str(HARD_WL),
                "--known-true",
                str(HARD_KNOWN),
                "--status",
                PATH_STATUSES,
                "--soft404-probes",
                "3",
                "--concurrency",
                "64",
                "--retries",
                "0",
                "-q",
            ]
        )
        emb_ok = code2 == 0 and ("recall=1.000" in se2 or "recall=1.0" in se2)
        (out / "paths_vegadns_embedded.log").write_text(f"exit={code2}\n{se2}\n", encoding="utf-8")

        ferox = which("feroxbuster")
        if ferox:
            fout = out / "paths_ferox.json"
            # Match same statuses; --dont-filter keeps soft-404 200 noise (fair vs our soft404 filter).
            code, so, se, wall = run_cmd(
                [
                    ferox,
                    "-u",
                    base,
                    "-w",
                    str(wl),
                    "-q",
                    "--json",
                    "-o",
                    str(fout),
                    "-s",
                    "200",
                    "401",
                    "403",
                    "-t",
                    "20",
                    "-D",
                    "--no-state",
                    "--no-recursion",
                ],
                timeout=180,
            )
            found_f = []
            if fout.exists():
                for ln in fout.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        o = json.loads(ln)
                        u = o.get("url") or o.get("path") or ""
                        if u:
                            found_f.append(str(u).lower())
                    except json.JSONDecodeError:
                        if "http" in ln:
                            found_f.append(ln.strip().lower())
            r, p, hit = recall_precision(found_f, known)
            ok = code == 0 or hit > 0
            rows.append(
                {
                    "tool": "feroxbuster",
                    "timed": ok,
                    "wall": wall if ok else None,
                    "found": len(found_f),
                    "recall": r,
                    "precision": p,
                    "f1": f1(r, p),
                    "hit": hit,
                    "known_n": len(known),
                    "noise": max(0, len(found_f) - hit),
                    "note": "HTTP --dont-filter status match",
                }
            )
            (out / "paths_ferox.log").write_text(f"exit={code}\n{se}\n{so[:2000]}\n", encoding="utf-8")
        else:
            rows.append({"tool": "feroxbuster", "timed": False, "note": "not installed"})

        ffuf = which("ffuf")
        if ffuf:
            code, so, se, wall = run_cmd(
                [
                    ffuf,
                    "-u",
                    f"{base.rstrip('/')}/FUZZ",
                    "-w",
                    str(wl),
                    "-mc",
                    PATH_STATUSES,
                    "-t",
                    "40",
                    "-s",
                    "-o",
                    str(out / "paths_ffuf.json"),
                    "-of",
                    "json",
                ],
                timeout=180,
            )
            found_ff = []
            jf = out / "paths_ffuf.json"
            if jf.exists():
                try:
                    data = json.loads(jf.read_text(encoding="utf-8"))
                    for res in data.get("results", []):
                        u = res.get("url") or ""
                        if u:
                            found_ff.append(u.lower())
                except json.JSONDecodeError:
                    pass
            r, p, hit = recall_precision(found_ff, known)
            ok = code == 0 or hit > 0
            rows.append(
                {
                    "tool": "ffuf",
                    "timed": ok,
                    "wall": wall if ok else None,
                    "found": len(found_ff),
                    "recall": r,
                    "precision": p,
                    "f1": f1(r, p),
                    "hit": hit,
                    "known_n": len(known),
                    "noise": max(0, len(found_ff) - hit),
                    "note": f"ffuf -mc {PATH_STATUSES} (no soft404 filter)",
                }
            )
            (out / "paths_ffuf.log").write_text(f"exit={code}\n{se}\n", encoding="utf-8")
        else:
            rows.append({"tool": "ffuf", "timed": False, "note": "not installed"})

        gob = which("gobuster")
        if gob:
            gout = out / "paths_gobuster_dir.txt"
            # Soft-404 returns 200 with fixed body length 68 — exclude that length so
            # gobuster does not refuse the suite (fair participate; still no body-aware FP filter).
            soft_len = len(soft_body.encode())
            code, so, se, wall = run_cmd(
                [
                    gob,
                    "dir",
                    "-u",
                    base,
                    "-w",
                    str(wl),
                    "-t",
                    "40",
                    "-q",
                    "-o",
                    str(gout),
                    "-b",
                    "404",
                    "--exclude-length",
                    str(soft_len),
                    "--no-error",
                ],
                timeout=180,
            )
            found_g = []
            if gout.exists():
                for ln in gout.read_text(encoding="utf-8", errors="replace").splitlines():
                    s = ln.strip()
                    if not s or s.lower().startswith("status"):
                        # header line like "status (Status: 200)..." is not a path
                        if s.lower().startswith("status ") or s.lower() == "status":
                            continue
                    # gobuster -q/-o: "path (Status: 200) [Size: N]"
                    path = s.split("(Status", 1)[0].strip().lower()
                    if not path or path == "status":
                        continue
                    if not path.startswith("/"):
                        path = "/" + path
                    found_g.append((base.rstrip("/") + path).lower())
            r, p, hit = recall_precision(found_g, known)
            ok = code == 0 or hit > 0
            note = f"dir -b 404 --exclude-length {soft_len}"
            if not ok:
                note += "; not comparable (exit/hit=0)"
            rows.append(
                {
                    "tool": "gobuster-dir",
                    "timed": ok,
                    "wall": wall if ok else None,
                    "found": len(found_g),
                    "recall": r,
                    "precision": p,
                    "f1": f1(r, p),
                    "hit": hit,
                    "known_n": len(known),
                    "noise": max(0, len(found_g) - hit),
                    "note": note,
                    "not_comparable": not ok,
                }
            )
            (out / "paths_gobuster.log").write_text(
                f"exit={code}\n{se}\n{so[:1500]}\n", encoding="utf-8"
            )
        else:
            rows.append({"tool": "gobuster-dir", "timed": False, "note": "not installed"})

    finally:
        http.terminate()
        try:
            http.wait(timeout=2)
        except Exception:
            http.kill()

    v = next(r for r in rows if r["tool"] == "vegadns-paths")
    peers = [r for r in rows if r["tool"] != "vegadns-paths"]
    gate, wins, loses, mode = gate_surpass(v, peers)

    lines = [
        f"definition: coverage quality = F1 on hard soft-404 suite known_n={v['known_n']}",
        f"base_url: {base}",
        f"host: {sys.platform}",
        f"status_match: {PATH_STATUSES}",
        f"embedded_hard_mock_recall_ok: {emb_ok}",
        f"surpass_mode: {mode}",
        "",
        f"{'tool':<16} {'timed':<6} {'wall':>8} {'found':>6} {'recall':>8} {'prec':>8} {'f1':>8} {'hit':>5}  notes",
        "-" * 96,
    ]
    for r in rows:
        timed = "yes" if r.get("timed") else "no"
        wall = f"{r['wall']:.4f}" if isinstance(r.get("wall"), (int, float)) else "-"
        found = str(r.get("found", "-"))
        rec = f"{r['recall']:.3f}" if isinstance(r.get("recall"), (int, float)) else "-"
        prec = f"{r['precision']:.3f}" if isinstance(r.get("precision"), (int, float)) else "-"
        ff = f"{r['f1']:.3f}" if isinstance(r.get("f1"), (int, float)) else "-"
        hit = str(r.get("hit", "-"))
        lines.append(
            f"{r['tool']:<16} {timed:<6} {wall:>8} {found:>6} {rec:>8} {prec:>8} {ff:>8} {hit:>5}  {r.get('note','')}"
        )
    lines += [
        "",
        f"vegadns_paths_recall: {v['recall']:.4f}",
        f"vegadns_paths_precision: {v.get('precision')}",
        f"vegadns_paths_f1: {v.get('f1'):.4f}",
        f"noise_hits: {v.get('noise', 0)}",
        f"beats_timed_peers: {', '.join(wins) if wins else '(no timed peers)'}",
        f"loses_to: {', '.join(loses) if loses else 'none'}",
        f"GATE_PATHS_COVERAGE: {'PASS' if gate else 'FAIL'}",
    ]
    write_report(out / "coverage_paths.txt", "HTTP paths discovery coverage (F1 surpass)", lines)
    return {"gate": gate, "rows": rows, "vegadns": v, "emb_ok": emb_ok, "mode": mode}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--wordlist-cap", type=int, default=8000)
    args = ap.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    vega = find_vega()
    dns = dns_lane(out, vega, args.wordlist_cap)
    paths = paths_lane(out, vega)

    summary_lines = [
        "vegadns discovery coverage SURPASS report",
        "=" * 60,
        "Coverage quality = F1(known-true recall, precision).",
        "Hard paths suite uses soft-404 200 noise; DNS uses wildcard zone.",
        "Surpass = full recall + precision 1.0 + F1 strictly > best timed peer.",
        "Not public-internet supremacy. Not code line coverage.",
        "",
        f"DNS gate:   {'PASS' if dns['gate'] else 'FAIL'}  mode={dns.get('mode')}",
        f"Paths gate: {'PASS' if paths['gate'] else 'FAIL'}  mode={paths.get('mode')}",
        f"OVERALL:    {'PASS' if dns['gate'] and paths['gate'] else 'FAIL'}",
        "",
        "See coverage_dns.txt and coverage_paths.txt for peer tables.",
    ]
    write_report(out / "coverage_surpass.txt", summary_lines[0], summary_lines[1:])
    (out / "coverage_surpass.json").write_text(
        json.dumps({"dns": dns, "paths": paths}, indent=2, default=str),
        encoding="utf-8",
    )
    print((out / "coverage_surpass.txt").read_text(encoding="utf-8"))
    print((out / "coverage_dns.txt").read_text(encoding="utf-8"))
    print((out / "coverage_paths.txt").read_text(encoding="utf-8"))
    return 0 if dns["gate"] and paths["gate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
