#!/usr/bin/env python3
"""Unbiased multi-tool DNS enum benchmark.

Same candidate list + same resolvers + same oracle for every tool.
Metrics: wall_s, recall, precision, F1, candidates_per_sec, found_per_sec.

Modes:
  gym-clean / gym-stress  — local gym zone (default gym-stress for honesty)
  htb-target              — record HTB IP from target JSON; DNS race still uses
                            shared gym oracle unless --domain is set for live labels

Claim bounds forbid market-supremacy wording in every report.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import gym_bench  # noqa: E402
from gym_metrics import f1  # noqa: E402

CLAIM = gym_bench.CLAIM_BOUNDS


def enrich_efficiency(report: dict) -> dict:
    """Add efficiency fields from wall + counts (mutates tools in place)."""
    cand = float(report.get("wordlist_n") or report.get("candidates_n") or 0)
    for t in report.get("tools", []):
        wall = t.get("wall_s")
        found = float(t.get("found") or 0)
        if t.get("timed") and wall and wall > 0:
            t["candidates_per_sec"] = round(cand / wall, 2) if cand else None
            t["found_per_sec"] = round(found / wall, 2)
            t["efficiency_score"] = round(
                (t.get("f1") or 0) * (cand / wall) / 1000.0, 4
            ) if cand else round(t.get("f1") or 0, 4)
        else:
            t["candidates_per_sec"] = None
            t["found_per_sec"] = None
            t["efficiency_score"] = None
    report["claim_bounds"] = CLAIM
    report["fairness"] = {
        "same_candidates": True,
        "same_resolvers": True,
        "same_oracle": True,
        "note": "All timed tools use the suite-generated candidate list and resolver file.",
    }
    return report


def load_htb_target(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Unbiased multi-tool DNS bench")
    ap.add_argument("--out", type=Path, default=ROOT / "bench_out")
    ap.add_argument(
        "--mode",
        choices=["gym-clean", "gym-stress", "htb-target"],
        default="gym-stress",
    )
    ap.add_argument("--wordlist-cap", type=int, default=5000)
    ap.add_argument("--concurrency", type=int, default=4000)
    ap.add_argument("--latency-ms", type=int, default=10)
    ap.add_argument("--servfail-pct", type=int, default=5)
    ap.add_argument("--drop-pct", type=int, default=2)
    ap.add_argument("--htb-target", type=Path, default=ROOT / "gym_out" / "htb_target.json")
    ap.add_argument(
        "--retries",
        type=int,
        default=None,
        help="Override vegadns retries in mock modes (optimization knob)",
    )
    ap.add_argument(
        "--sockets",
        type=int,
        default=1,
        help="vegadns UDP sockets (default 1: measured faster under stress mock)",
    )
    args = ap.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    htb_meta = None
    if args.mode == "htb-target":
        if not args.htb_target.exists():
            raise SystemExit(
                f"HTB target missing: {args.htb_target} "
                "(run: python scripts/htb_lab.py spawn ... --target-out ...)"
            )
        htb_meta = load_htb_target(args.htb_target)
        # DNS enum still needs a domain/oracle; use gym stress as fair core,
        # attach HTB target metadata for operator + optional IP probe.
        mode = "mock-stress"
    elif args.mode == "gym-clean":
        mode = "mock-clean"
    else:
        mode = "mock-stress"

    # Monkey-patch stress defaults via run_mock_mode kwargs
    if mode == "mock-clean":
        report = gym_bench.run_mock_mode(
            out,
            stress=False,
            wordlist_cap=args.wordlist_cap,
            concurrency=args.concurrency,
            latency_ms=0,
            servfail_pct=0,
            drop_pct=0,
            retries=args.retries,
            sockets=args.sockets,
        )
    else:
        report = gym_bench.run_mock_mode(
            out,
            stress=True,
            wordlist_cap=args.wordlist_cap,
            concurrency=args.concurrency,
            latency_ms=args.latency_ms,
            servfail_pct=args.servfail_pct,
            drop_pct=args.drop_pct,
            retries=args.retries if args.retries is not None else 6,
            timeout_ms=2000 if args.retries is None else None,
            sockets=args.sockets,
        )

    report = enrich_efficiency(report)
    report["suite"] = "unbiased_tool_bench"
    report["mode_requested"] = args.mode
    if htb_meta:
        report["htb_target"] = {
            "name": htb_meta.get("name"),
            "id": htb_meta.get("id"),
            "ip": htb_meta.get("ip"),
            "authorized": htb_meta.get("authorized"),
            "reachability": htb_meta.get("reachability"),
        }

    # rewrite reports with efficiency
    gym_bench.write_report(out, report)
    # also write efficiency table
    lines = [
        "",
        "EFFICIENCY",
        f"{'tool':<12} {'cand/s':>10} {'found/s':>10} {'eff_score':>10}",
        "-" * 48,
    ]
    for t in report["tools"]:
        cps = f"{t['candidates_per_sec']:.1f}" if t.get("candidates_per_sec") is not None else "-"
        fps = f"{t['found_per_sec']:.1f}" if t.get("found_per_sec") is not None else "-"
        es = f"{t['efficiency_score']:.4f}" if t.get("efficiency_score") is not None else "-"
        lines.append(f"{t['tool']:<12} {cps:>10} {fps:>10} {es:>10}")
    extra = "\n".join(lines) + "\n"
    with (out / "bench_report.txt").open("a", encoding="utf-8") as f:
        f.write(extra)
    print(extra)

    (out / "fairness.json").write_text(
        json.dumps(report.get("fairness"), indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
