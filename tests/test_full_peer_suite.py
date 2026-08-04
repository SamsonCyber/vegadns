#!/usr/bin/env python3
"""Scrutiny tests for full_peer_suite fairness helpers + real CLI scoring path.

NO TEST THEATER:
  - ranking uses real metric fields, not hardcoded winners
  - oracle scoring drives recall/precision from found vs known lists
  - negative: invented_score / incomplete timed rows fail fairness
  - shipped vegadns binary is exercised for mock enum scoring
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import full_peer_suite as fps  # noqa: E402


def test_rank_lane_wall_orders_by_wall_not_name():
    rows = [
        {
            "tool": "slow",
            "timed": True,
            "wall": 2.0,
            "recall": 1.0,
            "precision": 1.0,
        },
        {
            "tool": "fast",
            "timed": True,
            "wall": 0.5,
            "recall": 1.0,
            "precision": 1.0,
        },
        {
            "tool": "missing",
            "timed": False,
            "wall": None,
            "recall": None,
            "precision": None,
            "note": "not installed",
        },
    ]
    ranking = fps.rank_lane(rows, primary="wall")
    assert [r["tool"] for r in ranking] == ["fast", "slow"]
    assert ranking[0]["rank"] == 1
    assert "missing" not in {r["tool"] for r in ranking}


def test_rank_lane_skips_timed_without_oracle_metrics():
    rows = [
        {"tool": "half", "timed": True, "wall": 0.1, "recall": None, "precision": None},
        {"tool": "full", "timed": True, "wall": 0.2, "recall": 1.0, "precision": 1.0},
    ]
    ranking = fps.rank_lane(rows, primary="wall")
    assert [r["tool"] for r in ranking] == ["full"]


def test_efficiency_rate_and_none_safe():
    assert fps.efficiency_rate(1000, 2.0) == 500.0
    assert fps.efficiency_rate(1000, 0) is None
    assert fps.efficiency_rate(None, 1.0) is None


def test_score_cli_found_against_oracle_real_sets():
    known = ["www.lab.test", "api.lab.test", "mail.lab.test"]
    found = ["www.lab.test", "api.lab.test", "noise.lab.test"]
    s = fps.score_cli_found_against_oracle(found, known)
    assert s["hit"] == 2
    assert abs(s["recall"] - 2 / 3) < 1e-9
    assert abs(s["precision"] - 2 / 3) < 1e-9
    assert s["found_n"] == 3
    assert s["known_n"] == 3


def test_fairness_rejects_invented_and_incomplete_timed():
    report = {
        "claim_overall_fastest": False,
        "lanes": {
            "dns": {
                "rows": [
                    {
                        "tool": "fake",
                        "timed": True,
                        "wall": 0.01,
                        "recall": None,
                        "precision": None,
                        "invented_score": True,
                    }
                ],
                "ranking_wall": [],
            },
            "http": {"rows": [], "ranking_wall": []},
        },
    }
    errs = fps.assert_report_fairness(report)
    assert any("invented_score" in e for e in errs)
    assert any("timed but missing" in e for e in errs)


def test_fairness_rejects_overall_fastest_claim():
    report = {
        "claim_overall_fastest": True,
        "lanes": {"dns": {"rows": [], "ranking_wall": []}, "http": {"rows": [], "ranking_wall": []}},
    }
    errs = fps.assert_report_fairness(report)
    assert any("claim_overall_fastest" in e for e in errs)


def test_reject_lane_mixing():
    with pytest.raises(ValueError, match="lane-mixing"):
        fps.reject_cross_lane_merge(
            [{"tool": "x", "lane": "mixed"}],
            [],
        )


def test_shipped_vegadns_enum_scores_against_oracle():
    """Drive real release binary; score stdout/names file against fixture oracle."""
    vega = None
    for p in (
        ROOT / "target" / "release" / "vegadns.exe",
        ROOT / "target" / "release" / "vegadns",
    ):
        if p.exists():
            vega = p
            break
    if vega is None:
        pytest.skip("vegadns release binary missing")

    zone = ROOT / "fixtures" / "zone_bench.json"
    wl = ROOT / "fixtures" / "wordlist_bench.txt"
    known = ROOT / "fixtures" / "known_true.txt"
    assert zone.exists() and wl.exists() and known.exists()

    out_names = ROOT / "target" / "tmp_full_suite_enum_names.txt"
    cmd = [
        str(vega),
        "enum",
        "--mock-zone",
        str(zone),
        "-w",
        str(wl),
        "--known-true",
        str(known),
        "-o",
        str(out_names),
        "-q",
    ]
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    found = [
        ln.strip().lower().rstrip(".")
        for ln in out_names.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    known_lines = [
        ln.strip().lower().rstrip(".")
        for ln in known.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    s = fps.score_cli_found_against_oracle(found, known_lines)
    assert abs(s["recall"] - 1.0) < 1e-9
    assert abs(s["precision"] - 1.0) < 1e-9
    assert s["hit"] == s["known_n"]


def test_full_suite_module_imports_without_hardcoded_winner_table():
    """Structural: ranking function body must not hardcode tool order winners."""
    src = (SCRIPTS / "full_peer_suite.py").read_text(encoding="utf-8")
    assert "rank_lane" in src
    assert "claim_overall_fastest" in src
    # No fixed podium assignment like winner = "vegadns" as the ranking result
    assert 'ranking = [{"tool": "vegadns"' not in src
    assert "FORBIDDEN" in src or "never_merged" in src or "lanes_never_merged" in src
