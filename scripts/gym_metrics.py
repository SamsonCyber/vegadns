#!/usr/bin/env python3
"""Pure metric helpers for Subdomain Scanner Gym (unit-testable, no network)."""
from __future__ import annotations


def normalize_name(s: str) -> str:
    return s.strip().lower().rstrip(".")


def load_name_set(lines: list[str]) -> set[str]:
    out: set[str] = set()
    for ln in lines:
        t = ln.strip()
        if not t or t.startswith("#"):
            continue
        out.add(normalize_name(t))
    return out


def recall_precision(found: list[str], known: list[str]) -> tuple[float, float, int, int]:
    """Return (recall, precision, hit_count, known_n)."""
    fs = {normalize_name(x) for x in found if x.strip() and not x.strip().startswith("#")}
    ks = [normalize_name(k) for k in known if k.strip() and not k.strip().startswith("#")]
    if not ks:
        return 1.0, 1.0, 0, 0
    kset = set(ks)
    hit = sum(1 for k in ks if k in fs)
    r = hit / len(ks)
    if not fs:
        p = 1.0
    else:
        p = sum(1 for f in fs if f in kset) / len(fs)
    return r, p, hit, len(ks)


def f1(recall: float, precision: float) -> float:
    if recall + precision <= 0:
        return 0.0
    return 2.0 * recall * precision / (recall + precision)


def sample_point(tool: str, elapsed_s: float, qps: float | None = None, phase: str = "running") -> dict:
    """One live graph sample."""
    return {
        "tool": tool,
        "t": round(elapsed_s, 4),
        "qps": qps,
        "phase": phase,
    }


def final_row(
    tool: str,
    wall_s: float,
    found: int,
    recall: float,
    precision: float,
    timed: bool = True,
    note: str = "",
) -> dict:
    return {
        "tool": tool,
        "timed": timed,
        "wall_s": round(wall_s, 4) if timed else None,
        "found": found,
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "f1": round(f1(recall, precision), 4),
        "note": note,
    }
