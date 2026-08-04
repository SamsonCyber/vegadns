#!/usr/bin/env python3
"""Full dual-lane peer suite: DNS brute + HTTP path discovery (unbiased).

Lanes are NEVER merged into one overall fastest score.

DNS: vegadns, massdns, gobuster dns, dnsx, puredns (when fair).
HTTP: vegadns paths, feroxbuster, ffuf, gobuster dir (hard soft-404 suite).

Fairness policy (enforced in report + pure helpers used by tests):
  - same mock resolver / same base URL
  - same capped wordlist per lane
  - same known-true oracle
  - same success definition (DNS: live names; HTTP: hits after tool policy)
  - missing tools: timed=no + reason; never invent scores
  - timed rows must carry wall + recall + precision

Private lab / mock only.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import coverage_surpass as cov  # noqa: E402


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested; no subprocess)
# ---------------------------------------------------------------------------


def f1(recall: float, precision: float) -> float:
    if recall + precision <= 0:
        return 0.0
    return 2.0 * recall * precision / (recall + precision)


def efficiency_rate(work_units: float | int | None, wall_s: float | None) -> float | None:
    """Throughput: work units per second (queries or requests or candidates)."""
    if work_units is None or wall_s is None:
        return None
    if wall_s <= 0:
        return None
    return float(work_units) / float(wall_s)


def is_timed_complete(row: dict) -> bool:
    """Timed and fair enough for ranking: wall + oracle metrics present."""
    if not row.get("timed"):
        return False
    if not isinstance(row.get("wall"), (int, float)):
        return False
    if row.get("wall") is None:
        return False
    if row.get("recall") is None or row.get("precision") is None:
        return False
    return True


def rank_lane(
    rows: list[dict],
    *,
    primary: str = "wall",
    require_recall_floor: float | None = None,
) -> list[dict]:
    """Rank timed-complete tools only. Lower wall is better; higher rate better.

    Returns list of {rank, tool, wall, recall, precision, f1, rate, note}.
    Does not invent tools. Empty if no timed-complete rows.
    """
    eligible = [r for r in rows if is_timed_complete(r)]
    if require_recall_floor is not None:
        eligible = [
            r
            for r in eligible
            if float(r["recall"]) + 1e-12 >= require_recall_floor
        ]
    if primary == "wall":
        eligible.sort(key=lambda r: (float(r["wall"]), -f1(float(r["recall"]), float(r["precision"]))))
    elif primary == "f1":
        eligible.sort(
            key=lambda r: (
                -f1(float(r["recall"]), float(r["precision"])),
                float(r["wall"]),
            )
        )
    elif primary == "rate":
        eligible.sort(
            key=lambda r: (
                -(float(r["rate"]) if r.get("rate") is not None else -1.0),
                float(r["wall"]),
            )
        )
    else:
        raise ValueError(f"unknown primary metric: {primary}")

    out = []
    for i, r in enumerate(eligible, 1):
        rr = float(r["recall"])
        pp = float(r["precision"])
        out.append(
            {
                "rank": i,
                "tool": r["tool"],
                "wall": float(r["wall"]),
                "recall": rr,
                "precision": pp,
                "f1": f1(rr, pp),
                "rate": r.get("rate"),
                "found": r.get("found"),
                "note": r.get("note", ""),
            }
        )
    return out


def reject_cross_lane_merge(dns_rows: list[dict], http_rows: list[dict]) -> None:
    """Raise if any tool appears as both DNS and HTTP timed winner claim blob."""
    dns_tools = {r["tool"] for r in dns_rows if r.get("lane") == "dns" or "dns" in r.get("tool", "")}
    # Explicit: never build a single overall ranking from mixed lanes
    mixed = [r for r in dns_rows + http_rows if r.get("lane") == "mixed"]
    if mixed:
        raise ValueError("lane-mixing forbidden: found lane=mixed rows")


def score_cli_found_against_oracle(found_lines: list[str], known_lines: list[str]) -> dict:
    """Score real CLI name/URL lines against oracle (no hardcoded winner)."""
    r, p, hit = cov.recall_precision(found_lines, known_lines)
    return {
        "recall": r,
        "precision": p,
        "hit": hit,
        "found_n": len(found_lines),
        "known_n": len(known_lines),
        "f1": f1(r, p),
    }


def enrich_rate(row: dict, candidates: int | None) -> dict:
    """Attach efficiency rate when wall is known."""
    r = dict(row)
    if r.get("rate") is not None:
        return r
    # Prefer explicit query/request counts from stats if present
    units = r.get("queries") or r.get("requests") or r.get("candidates") or candidates
    rate = efficiency_rate(units, r.get("wall"))
    if rate is not None:
        r["rate"] = rate
    return r


def assert_report_fairness(report: dict) -> list[str]:
    """Return list of fairness violations (empty = ok). Used by scrutiny tests."""
    errs: list[str] = []
    if report.get("claim_overall_fastest"):
        errs.append("forbidden claim_overall_fastest set")
    for lane_name in ("dns", "http"):
        lane = report.get("lanes", {}).get(lane_name) or {}
        rows = lane.get("rows") or []
        for r in rows:
            if r.get("timed") and not is_timed_complete(r):
                # allow timed=True with wall only if explicitly not_comparable
                if not r.get("not_comparable"):
                    errs.append(
                        f"{lane_name}/{r.get('tool')}: timed but missing wall or oracle metrics"
                    )
            if r.get("invented_score"):
                errs.append(f"{lane_name}/{r.get('tool')}: invented_score flag")
        ranking = lane.get("ranking_wall") or []
        ranked_tools = {x["tool"] for x in ranking}
        for r in rows:
            if is_timed_complete(r) and r["tool"] not in ranked_tools:
                # if recall floor filtered them out of ranking_wall, ok if documented
                if lane.get("rank_require_recall_floor") is None:
                    errs.append(f"{lane_name}: timed-complete {r['tool']} missing from ranking")
    reject_cross_lane_merge(
        (report.get("lanes", {}).get("dns") or {}).get("rows") or [],
        (report.get("lanes", {}).get("http") or {}).get("rows") or [],
    )
    return errs


# ---------------------------------------------------------------------------
# Suite runner
# ---------------------------------------------------------------------------


def normalize_dns_rows(cov_rows: list[dict], candidates: int) -> list[dict]:
    out = []
    for r in cov_rows:
        rr = {
            "tool": r["tool"],
            "lane": "dns",
            "timed": bool(r.get("timed")),
            "wall": r.get("wall"),
            "found": r.get("found"),
            "recall": r.get("recall"),
            "precision": r.get("precision"),
            "f1": r.get("f1"),
            "hit": r.get("hit"),
            "noise": r.get("noise"),
            "note": r.get("note", ""),
            "queries": r.get("queries"),
            "candidates": candidates,
            "not_comparable": bool(r.get("not_comparable")),
        }
        # dnsx/massdns may leave precision as float already
        out.append(enrich_rate(rr, candidates))
    return out


def normalize_http_rows(cov_rows: list[dict], candidates: int) -> list[dict]:
    out = []
    for r in cov_rows:
        rr = {
            "tool": r["tool"],
            "lane": "http",
            "timed": bool(r.get("timed")),
            "wall": r.get("wall"),
            "found": r.get("found"),
            "recall": r.get("recall"),
            "precision": r.get("precision"),
            "f1": r.get("f1"),
            "hit": r.get("hit"),
            "noise": r.get("noise"),
            "note": r.get("note", ""),
            "requests": r.get("requests") or r.get("candidates") or candidates,
            "candidates": candidates,
            "not_comparable": bool(r.get("not_comparable")),
        }
        out.append(enrich_rate(rr, candidates))
    return out


def format_lane_table(rows: list[dict], title: str) -> list[str]:
    lines = [
        title,
        f"{'tool':<16} {'timed':<6} {'wall_s':>10} {'rate':>10} {'found':>7} "
        f"{'recall':>8} {'prec':>8} {'f1':>8}  notes",
        "-" * 100,
    ]
    for r in rows:
        timed = "yes" if r.get("timed") else "no"
        wall = f"{r['wall']:.4f}" if isinstance(r.get("wall"), (int, float)) else "-"
        rate = f"{r['rate']:.0f}" if isinstance(r.get("rate"), (int, float)) else "-"
        found = str(r["found"]) if r.get("found") is not None else "-"
        rec = f"{r['recall']:.3f}" if isinstance(r.get("recall"), (int, float)) else "-"
        prec = f"{r['precision']:.3f}" if isinstance(r.get("precision"), (int, float)) else "-"
        ff = f"{r['f1']:.3f}" if isinstance(r.get("f1"), (int, float)) else "-"
        note = r.get("note", "")
        if not r.get("timed") and note:
            pass
        elif not r.get("timed"):
            note = note or "not installed / not comparable"
        lines.append(
            f"{r['tool']:<16} {timed:<6} {wall:>10} {rate:>10} {found:>7} "
            f"{rec:>8} {prec:>8} {ff:>8}  {note}"
        )
    return lines


def format_ranking(ranking: list[dict], metric: str) -> list[str]:
    if not ranking:
        return ["(no timed-complete tools in this lane)"]
    lines = [f"ranking by {metric} (timed-complete only; lower wall / higher f1 as labeled):"]
    for r in ranking:
        rate = f"{r['rate']:.0f}/s" if isinstance(r.get("rate"), (int, float)) else "-"
        lines.append(
            f"  #{r['rank']} {r['tool']}: wall={r['wall']:.4f}s rate={rate} "
            f"R={r['recall']:.3f} P={r['precision']:.3f} F1={r['f1']:.3f}"
        )
    return lines


def run_suite(out: Path, wordlist_cap: int) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    dns_dir = out / "dns"
    http_dir = out / "http"
    dns_dir.mkdir(parents=True, exist_ok=True)
    http_dir.mkdir(parents=True, exist_ok=True)
    vega = cov.find_vega()

    dns_raw = cov.dns_lane(dns_dir, vega, wordlist_cap)
    http_raw = cov.paths_lane(http_dir, vega)

    dns_candidates = wordlist_cap
    # hard wordlist size for HTTP
    http_wl = cov.HARD_WL
    http_candidates = len(
        [
            ln
            for ln in http_wl.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
    ) if http_wl.exists() else None

    dns_rows = normalize_dns_rows(dns_raw.get("rows") or [], dns_candidates)
    http_rows = normalize_http_rows(http_raw.get("rows") or [], http_candidates or 0)

    # Rankings: wall (speed) and f1 (quality-efficiency under fair oracle).
    # Quality-floor wall ranking: only tools with high recall+precision (incomplete
    # peers that race incomplete discovery are excluded from "efficient at quality").
    dns_rank_wall = rank_lane(dns_rows, primary="wall")
    dns_rank_f1 = rank_lane(dns_rows, primary="f1")
    http_rank_wall = rank_lane(http_rows, primary="wall")
    http_rank_f1 = rank_lane(http_rows, primary="f1")
    http_rank_wall_quality = rank_lane(
        http_rows, primary="wall", require_recall_floor=0.95
    )
    # Also require P≈1 for quality wall (filter in post)
    http_rank_wall_quality = [
        r for r in http_rank_wall_quality if float(r["precision"]) + 1e-9 >= 0.999
    ]
    for i, r in enumerate(http_rank_wall_quality, 1):
        r["rank"] = i

    report = {
        "scope": "private lab mock only",
        "host": platform.system().lower(),
        "platform": sys.platform,
        "fairness": {
            "dns": {
                "wordlist_cap": wordlist_cap,
                "oracle": "fixtures/lab/known_true_lab.txt",
                "resolver": "shared mock-serve on free UDP port",
                "success": "live A answers; vegadns applies wildcard filter (P=1.0 target)",
            },
            "http": {
                "wordlist": "fixtures/paths/wordlist_hard.txt",
                "oracle": "fixtures/paths/known_true_hard.txt",
                "base_url": "shared hard soft-404 mock HTTP",
                "status_match": cov.PATH_STATUSES,
                "success": "paths matching oracle; soft-404 200 noise hurts precision if unfiltered",
            },
            "lanes_never_merged": True,
        },
        "claim_overall_fastest": False,
        "lanes": {
            "dns": {
                "rows": dns_rows,
                "ranking_wall": dns_rank_wall,
                "ranking_f1": dns_rank_f1,
                "coverage_gate": dns_raw.get("gate"),
                "candidates": dns_candidates,
            },
            "http": {
                "rows": http_rows,
                "ranking_wall": http_rank_wall,
                "ranking_wall_quality": http_rank_wall_quality,
                "ranking_f1": http_rank_f1,
                "coverage_gate": http_raw.get("gate"),
                "candidates": http_candidates,
                "rank_require_recall_floor": 0.95,
            },
        },
    }

    fairness_errs = assert_report_fairness(report)
    report["fairness_check_errors"] = fairness_errs

    # Human report
    lines: list[str] = [
        "FULL PEER SUITE (unbiased dual-lane)",
        "=" * 72,
        f"host: {report['host']} ({report['platform']})",
        "Lanes are SEPARATE. No overall fastest across DNS+HTTP.",
        "",
        "## Fairness policy",
        "- Same mock resolver (DNS) / same base URL (HTTP) for all timed tools in a lane",
        "- Same capped wordlist and known-true oracle per lane",
        "- Missing tools: timed=no + reason; scores never invented",
        "- Timed ranking requires wall + recall + precision",
        "",
    ]
    lines += format_lane_table(dns_rows, "## DNS lane (subdomain brute/resolve)")
    lines.append("")
    lines += format_ranking(dns_rank_wall, "wall_s (fastest)")
    lines.append("")
    lines += format_ranking(dns_rank_f1, "F1 (quality under oracle)")
    lines.append("")
    lines += format_lane_table(http_rows, "## HTTP lane (path discovery; soft-404 hard suite)")
    lines.append("")
    lines += format_ranking(http_rank_wall, "wall_s (raw fastest, any timed R/P)")
    lines.append("")
    lines += format_ranking(
        http_rank_wall_quality,
        "wall_s at quality floor (R>=0.95 and P=1.0)",
    )
    lines.append("")
    lines += format_ranking(http_rank_f1, "F1 (quality under oracle)")
    lines.append("")
    lines.append("## Efficiency note")
    lines.append(
        "rate = candidates (or queries/requests when known) / wall_s. "
        "Higher is more efficient throughput; still secondary to R/P on the oracle."
    )
    lines.append("")
    lines.append("## Fairness check")
    if fairness_errs:
        lines.append("FAIL: " + "; ".join(fairness_errs))
    else:
        lines.append("PASS: no invented scores; no cross-lane merge; timed rows complete or not ranked")
    lines.append("")
    # Winners summary (per lane only)
    lines.append("## Per-lane winners (timed-complete only)")
    if dns_rank_wall:
        w = dns_rank_wall[0]
        lines.append(
            f"DNS fastest wall: {w['tool']} ({w['wall']:.4f}s) F1={w['f1']:.3f}"
        )
    else:
        lines.append("DNS fastest wall: (none timed-complete)")
    if dns_rank_f1:
        w = dns_rank_f1[0]
        lines.append(f"DNS best F1: {w['tool']} (F1={w['f1']:.3f}, wall={w['wall']:.4f}s)")
    if http_rank_wall:
        w = http_rank_wall[0]
        lines.append(
            f"HTTP fastest wall: {w['tool']} ({w['wall']:.4f}s) F1={w['f1']:.3f}"
        )
    else:
        lines.append("HTTP fastest wall: (none timed-complete)")
    if http_rank_f1:
        w = http_rank_f1[0]
        lines.append(f"HTTP best F1: {w['tool']} (F1={w['f1']:.3f}, wall={w['wall']:.4f}s)")
    lines.append("")
    lines.append("FORBIDDEN: single overall fastest across DNS+HTTP+passive.")

    text = "\n".join(lines) + "\n"
    (out / "full_suite_report.txt").write_text(text, encoding="utf-8")
    (out / "full_suite_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print(text)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--wordlist-cap", type=int, default=5000)
    args = ap.parse_args()
    report = run_suite(args.out, args.wordlist_cap)
    # exit 0 even if some peers missing; fairness errors are soft (still report)
    # hard fail only if vegadns itself missing (raised earlier)
    if report.get("fairness_check_errors"):
        print("fairness warnings:", report["fairness_check_errors"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
