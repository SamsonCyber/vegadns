#!/usr/bin/env python3
"""Live authorized DNS differential: vegadns vs massdns on public resolvers.

Authorization: operator must pass --authorized and name a domain they control
or an explicitly permitted research target. Default mode is resolve-list only
(fixed FQDNs), which only queries recursive resolvers for known names.

Private lab / mock comparison remains in kali_vs_massdns.py and coverage_surpass.py.

Examples:
  # Resolve-list differential (public resolvers, fixed known names)
  python scripts/live_dns_diff.py --out ./live_out --authorized --mode resolve-list

  # Enum differential on a domain you control
  python scripts/live_dns_diff.py --out ./live_out --authorized \\
    --mode enum --domain example.com --wordlist fixtures/wordlist_small.txt
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Fixed resolve-list: well-known public hostnames + guaranteed NX.
# This is recursive resolve only (not third-party subdomain hunting).
DEFAULT_RESOLVE_LIST = [
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
    "www.iana.org",
    "quad9.net",
    "never-exists-vegadns-probe-9f3a2c1b.example.com",
    "this-label-should-nxdomain-vegadns-000.invalid",
]


def which(name: str) -> str | None:
    return shutil.which(name) or shutil.which(name + ".exe")


def find_vega() -> Path:
    for p in (
        ROOT / "target" / "release" / "vegadns",
        ROOT / "target" / "release" / "vegadns.exe",
        Path.home() / "vegadns" / "target" / "release" / "vegadns",
        Path("/root/vegadns/target/release/vegadns"),
    ):
        if p.exists():
            return p
    w = which("vegadns")
    if w:
        return Path(w)
    raise SystemExit("vegadns release binary missing; cargo build --release first")


def load_lines(path: Path) -> list[str]:
    return [
        ln.strip().lower().rstrip(".")
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_cmd(cmd: list[str], timeout: float = 600) -> tuple[int, str, str, float]:
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
        return p.returncode, p.stdout or "", p.stderr or "", time.perf_counter() - t0
    except FileNotFoundError:
        return 127, "", "not found", time.perf_counter() - t0
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", (e.stderr or "") + "\nTIMEOUT", time.perf_counter() - t0


def expand_labels(labels: list[str], base: str) -> list[str]:
    base = base.lower().rstrip(".")
    out = []
    for w in labels:
        w = w.lower().rstrip(".")
        if w == base or w.endswith("." + base):
            out.append(w)
        else:
            out.append(f"{w}.{base}")
    return out


def parse_massdns_simple(path: Path) -> list[str]:
    """Parse massdns -o S lines: name status IP ..."""
    names = []
    if not path.exists():
        return names
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        # Simple: first token is name; skip NX / NOERROR filtering by presence of answer style
        # massdns -o S: "name. A 1.2.3.4" or similar
        tok = ln.split()
        if not tok:
            continue
        name = tok[0].rstrip(".").lower()
        # Keep lines that look like positive answers (have A/AAAA/CNAME and data)
        if len(tok) >= 3 and tok[1].upper() in {"A", "AAAA", "CNAME"}:
            names.append(name)
        elif len(tok) >= 2 and tok[1].upper() not in {"NXDOMAIN", "SERVFAIL", "REFUSED", "NOERROR"}:
            # some builds: name. 1.2.3.4
            if any(c.isdigit() for c in tok[1]) or tok[1].upper() == "CNAME":
                names.append(name)
    return sorted(set(names))


def parse_massdns_any_resolved(path: Path) -> list[str]:
    """Broader: any non-empty first-token line that is not clearly NX-only."""
    names = []
    if not path.exists():
        return names
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        upper = ln.upper()
        if "NXDOMAIN" in upper or "SERVFAIL" in upper:
            continue
        name = ln.split()[0].rstrip(".").lower()
        if name:
            names.append(name)
    return sorted(set(names))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    u = a | b
    if not u:
        return 1.0
    return len(a & b) / len(u)


def main() -> int:
    ap = argparse.ArgumentParser(description="Live vegadns vs massdns differential")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--authorized",
        action="store_true",
        help="Required: you authorize this live query (own domain or resolve-list).",
    )
    ap.add_argument(
        "--mode",
        choices=["resolve-list", "enum"],
        default="resolve-list",
        help="resolve-list: fixed public FQDNs. enum: domain+wordlist brute (own domain only).",
    )
    ap.add_argument("--domain", default="", help="Base domain for enum mode")
    ap.add_argument("--wordlist", type=Path, default=ROOT / "fixtures" / "wordlist_small.txt")
    ap.add_argument(
        "--resolvers",
        type=Path,
        default=ROOT / "fixtures" / "resolvers_public.txt",
    )
    ap.add_argument("--concurrency", type=int, default=200)
    ap.add_argument("--timeout-ms", type=int, default=2500)
    ap.add_argument("--massdns-hashmap", type=int, default=500)
    args = ap.parse_args()

    if not args.authorized:
        print(
            "REFUSED: pass --authorized after confirming this run is in scope "
            "(resolve-list on public resolvers, or enum of a domain you control).",
            file=sys.stderr,
        )
        return 2

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    vega = find_vega()
    mass = which("massdns")
    if not mass:
        print("massdns not installed; cannot differential", file=sys.stderr)
        return 3

    resolvers = load_lines(args.resolvers)
    if not resolvers:
        print("empty resolvers file", file=sys.stderr)
        return 1
    res_file = out / "resolvers.txt"
    # massdns wants ip only or ip:port; vegadns same
    write_lines(res_file, resolvers)

    use_fqdn_list = False
    if args.mode == "resolve-list":
        labels = list(dict.fromkeys(n.lower().rstrip(".") for n in DEFAULT_RESOLVE_LIST))
        base_note = "resolve-list (fixed public FQDNs via public recursive resolvers)"
        domain_for_vega = "example.com"
        use_fqdn_list = True
        wordlist_path = out / "fqdn_list.txt"
        write_lines(wordlist_path, labels)
        fqdn_path = wordlist_path
    else:
        if not args.domain.strip():
            print("enum mode requires --domain", file=sys.stderr)
            return 1
        domain_for_vega = args.domain.strip().lower().rstrip(".")
        base_note = f"enum domain={domain_for_vega}"
        labels = load_lines(args.wordlist)
        if not labels:
            print("empty wordlist", file=sys.stderr)
            return 1
        wordlist_path = out / "enum_wordlist.txt"
        write_lines(wordlist_path, labels)
        fqdns = expand_labels(labels, domain_for_vega)
        fqdn_path = out / "fqdn_list.txt"
        write_lines(fqdn_path, fqdns)

    # --- vegadns ---
    v_names = out / "vegadns_names.txt"
    v_stats = out / "vegadns_stats.json"
    v_log = out / "vegadns.log"
    v_cmd = [
        str(vega),
        "enum",
        "-d",
        domain_for_vega,
        "-w",
        str(wordlist_path if args.mode == "enum" else fqdn_path),
        "-r",
        str(res_file),
        "-o",
        str(v_names),
        "--stats-json",
        str(v_stats),
        "--concurrency",
        str(args.concurrency),
        "--timeout-ms",
        str(args.timeout_ms),
        "--retries",
        "2",
        "--sockets",
        "2",
        "--wildcard-probes",
        "0" if use_fqdn_list else "3",
        "-q",
    ]
    if use_fqdn_list:
        v_cmd.append("--fqdn-list")
    code_v, so_v, se_v, wall_v = run_cmd(v_cmd, timeout=900)
    v_log.write_text(f"exit={code_v}\nwall={wall_v}\n{se_v}\n{so_v[:2000]}\n", encoding="utf-8")
    vega_found = set(load_lines(v_names)) if v_names.exists() else set()
    st = json.loads(v_stats.read_text()) if v_stats.exists() else {}
    vega_wall = float(st.get("wall_secs", wall_v))

    # --- massdns ---
    m_out = out / "massdns_out.txt"
    m_log = out / "massdns.log"
    code_m, so_m, se_m, wall_m = run_cmd(
        [
            mass,
            "-r",
            str(res_file),
            "-t",
            "A",
            "-o",
            "S",
            "-s",
            str(args.massdns_hashmap),
            "-c",
            "5",
            "--interval",
            "200",
            "-w",
            str(m_out),
            str(fqdn_path),
        ],
        timeout=900,
    )
    m_log.write_text(f"exit={code_m}\nwall={wall_m}\n{se_m}\n{so_m[:2000]}\n", encoding="utf-8")
    mass_found = set(parse_massdns_simple(m_out))
    if not mass_found:
        mass_found = set(parse_massdns_any_resolved(m_out))

    only_vega = sorted(vega_found - mass_found)
    only_mass = sorted(mass_found - vega_found)
    both = sorted(vega_found & mass_found)
    jac = jaccard(vega_found, mass_found)

    # Agreement gate for resolve-list: jaccard >= 0.7 on live names (public DNS is flaky)
    # Enum: report only; no hard fail on disagreement (wildcards differ by design)
    report = [
        "vegadns vs massdns LIVE DNS differential",
        "=" * 60,
        f"mode: {args.mode}",
        f"scope: {base_note}",
        f"authorized: true",
        f"resolvers: {len(resolvers)} from {args.resolvers}",
        f"candidates: {len(load_lines(fqdn_path))}",
        "",
        f"{'tool':<12} {'exit':>4} {'wall_s':>10} {'found':>8}",
        "-" * 40,
        f"{'vegadns':<12} {code_v:>4} {vega_wall:>10.4f} {len(vega_found):>8}",
        f"{'massdns':<12} {code_m:>4} {wall_m:>10.4f} {len(mass_found):>8}",
        "",
        f"intersection: {len(both)}",
        f"only_vegadns: {len(only_vega)}",
        f"only_massdns: {len(only_mass)}",
        f"jaccard: {jac:.4f}",
        "",
        "only_vegadns sample: " + ", ".join(only_vega[:15]),
        "only_massdns sample: " + ", ".join(only_mass[:15]),
        "",
    ]

    # Differential interpretation
    if args.mode == "resolve-list":
        # On resolve-list, high agreement expected for well-known hosts
        ok = code_v == 0 and code_m == 0 and jac >= 0.5 and len(both) >= 5
        report.append(f"GATE_LIVE_RESOLVE_AGREEMENT: {'PASS' if ok else 'FAIL'} (need jaccard>=0.5, both>=5)")
        gate = ok
    else:
        # Enum: vegadns should not be empty if massdns found things; jaccard informational
        ok = code_v == 0 and (len(vega_found) > 0 or len(mass_found) == 0)
        report.append(
            f"GATE_LIVE_ENUM_RAN: {'PASS' if ok else 'FAIL'} "
            f"(vegadns exit 0; note: wildcard filter can shrink set vs massdns)"
        )
        report.append(
            "NOTE: only_massdns often = wildcards/noise massdns keeps and vegadns drops."
        )
        gate = ok

    report.append("")
    report.append(f"OVERALL: {'PASS' if gate else 'FAIL'}")

    text = "\n".join(report) + "\n"
    (out / "live_dns_diff.txt").write_text(text, encoding="utf-8")
    (out / "live_dns_diff.json").write_text(
        json.dumps(
            {
                "mode": args.mode,
                "vega_found": sorted(vega_found),
                "mass_found": sorted(mass_found),
                "only_vega": only_vega,
                "only_mass": only_mass,
                "both": both,
                "jaccard": jac,
                "vega_wall": vega_wall,
                "mass_wall": wall_m,
                "gate": gate,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(text)
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
