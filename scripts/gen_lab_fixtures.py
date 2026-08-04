#!/usr/bin/env python3
"""Generate large lab-only DNS fixtures for vegadns high-speed testing.

Outputs under fixtures/lab/:
  zone_lab.json, known_true_lab.txt, wordlist_lab.txt, urls_lab.txt

Scope: private mock DNS only (lab.test). Not for public internet brute-force.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "lab"
BASE = "lab.test"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--known", type=int, default=500, help="known-true live hosts")
    ap.add_argument("--wordlist", type=int, default=25000, help="total wordlist labels")
    ap.add_argument("--wild-fillers", type=int, default=200, help="wildcard child labels")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)

    # Deterministic known-true labels
    special = [
        "www",
        "mail",
        "api",
        "vpn",
        "cdn",
        "admin",
        "git",
        "ci",
        "staging",
        "dev",
        "portal",
        "auth",
        "db",
        "ns1",
        "ns2",
        "ftp",
        "ssh",
        "grafana",
        "prometheus",
        "vault",
    ]
    known_labels: list[str] = []
    for s in special:
        if s not in known_labels:
            known_labels.append(s)
    i = 0
    while len(known_labels) < args.known:
        lab = f"host{i:04d}"
        if lab not in known_labels:
            known_labels.append(lab)
        i += 1
    known_labels = known_labels[: args.known]

    records: dict[str, list[str]] = {}
    known_fqdns: list[str] = []
    for n, lab in enumerate(known_labels):
        fqdn = f"{lab}.{BASE}"
        # Spread IPs in 10.200.x.y private range (documentation only; mock answers)
        a = 10 + (n % 200)
        b = 20 + (n // 200) % 200
        records[fqdn] = [f"10.200.{b}.{a}"]
        known_fqdns.append(fqdn)

    wildcards = {
        f"wild.{BASE}": ["9.9.9.9"],
        f"cdn-edge.{BASE}": ["8.8.8.8"],
    }

    zone = {
        "base": BASE,
        "records": records,
        "wildcards": wildcards,
        "meta": {
            "scope": "private lab mock only",
            "known_true_count": len(known_fqdns),
        },
    }
    (OUT / "zone_lab.json").write_text(json.dumps(zone, indent=2) + "\n", encoding="utf-8")
    (OUT / "known_true_lab.txt").write_text("\n".join(known_fqdns) + "\n", encoding="utf-8")

    # Wordlist: all known labels + garbage + wildcard children + extras
    wl: list[str] = list(known_labels)
    for j in range(args.wild_fillers):
        wl.append(f"w{j:04d}.wild")
        wl.append(f"e{j:04d}.cdn-edge")
    g = 0
    while len(wl) < args.wordlist:
        wl.append(f"junk{g:05d}")
        g += 1
        if len(wl) < args.wordlist:
            wl.append(f"noise-{g:05d}")
            g += 1
    wl = wl[: args.wordlist]
    # de-dupe preserve order
    seen = set()
    uniq = []
    for x in wl:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    (OUT / "wordlist_lab.txt").write_text("\n".join(uniq) + "\n", encoding="utf-8")

    # URL-style list for optional dummy HTTP checks (FQDN as host)
    urls = [f"https://{h}/" for h in known_fqdns]
    (OUT / "urls_lab.txt").write_text("\n".join(urls) + "\n", encoding="utf-8")

    summary = {
        "base": BASE,
        "known_true": len(known_fqdns),
        "wordlist": len(uniq),
        "records": len(records),
        "wild_parents": list(wildcards.keys()),
        "urls": len(urls),
        "paths": {
            "zone": str(OUT / "zone_lab.json"),
            "known_true": str(OUT / "known_true_lab.txt"),
            "wordlist": str(OUT / "wordlist_lab.txt"),
            "urls": str(OUT / "urls_lab.txt"),
        },
    }
    (OUT / "fixture_meta.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
