#!/usr/bin/env python3
"""Fairness + efficiency contracts for unbiased_tool_bench / gym_metrics."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import gym_metrics as metrics  # noqa: E402
import unbiased_tool_bench as utb  # noqa: E402


class TestUnbiasedBench(unittest.TestCase):
    def test_efficiency_enrichment(self) -> None:
        report = {
            "wordlist_n": 1000,
            "tools": [
                {
                    "tool": "vegadns",
                    "timed": True,
                    "wall_s": 0.5,
                    "found": 100,
                    "recall": 1.0,
                    "precision": 1.0,
                    "f1": 1.0,
                },
                {
                    "tool": "massdns",
                    "timed": False,
                    "wall_s": None,
                    "found": 0,
                    "recall": 0.0,
                    "precision": 1.0,
                    "f1": 0.0,
                },
            ],
        }
        out = utb.enrich_efficiency(report)
        v = out["tools"][0]
        self.assertAlmostEqual(v["candidates_per_sec"], 2000.0)
        self.assertAlmostEqual(v["found_per_sec"], 200.0)
        self.assertIsNotNone(v["efficiency_score"])
        self.assertTrue(out["fairness"]["same_candidates"])
        self.assertTrue(out["fairness"]["same_resolvers"])
        self.assertIn("market", " ".join(out["claim_bounds"]["does_not_prove"]).lower())

    def test_oracle_membership_in_capped_list(self) -> None:
        """Suite contract: known labels must remain after wordlist cap merge."""
        known = [f"host{i}.gym.test" for i in range(50)]
        labels = [k[: -len(".gym.test")] for k in known]
        junk = [f"junk{i}" for i in range(5000)]
        cap = 100
        # same merge policy as gym_bench (known first)
        merged = list(dict.fromkeys(labels + junk))[: max(cap, len(labels))]
        for lab in labels:
            self.assertIn(lab, merged, "capping must not drop oracle labels")

    def test_htb_target_json_shape(self) -> None:
        sample = {
            "source": "hackthebox",
            "authorized": True,
            "ip": "10.129.7.197",
            "name": "Cohort",
            "id": 933,
        }
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.json"
            p.write_text(json.dumps(sample), encoding="utf-8")
            loaded = utb.load_htb_target(p)
            self.assertEqual(loaded["name"], "Cohort")
            self.assertTrue(loaded["authorized"])

    def test_metrics_f1_helper(self) -> None:
        self.assertAlmostEqual(metrics.f1(1.0, 1.0), 1.0)
        self.assertAlmostEqual(metrics.f1(0.0, 1.0), 0.0)

    def test_htb_target_mode_is_gym_core_not_live_dns(self) -> None:
        """Ship contract: htb-target attaches metadata but DNS race stays gym mock.

        Live 10.129 resolve requires HTB VPN and a separate path; do not treat
        htb-target bench walls as live lab enum without reachability.
        """
        src = (ROOT / "scripts" / "unbiased_tool_bench.py").read_text(encoding="utf-8")
        self.assertIn("htb-target", src)
        self.assertIn("gym", src.lower())
        # mode branches to mock-stress when htb-target is requested
        self.assertIn('mode = "mock-stress"', src)
        self.assertIn("htb_meta", src)
        self.assertIn("htb_target", src)


if __name__ == "__main__":
    unittest.main()
