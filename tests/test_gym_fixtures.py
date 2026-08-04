#!/usr/bin/env python3
"""Gym fixture generator contract tests — drive real gen_gym_fixtures.generate."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import gen_gym_fixtures as gen  # noqa: E402
import gym_metrics as metrics  # noqa: E402


class TestGymFixtures(unittest.TestCase):
    def test_generate_emits_obscure_and_realistic(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            summary = gen.generate(
                known=200,
                wordlist=2000,
                obscure_fraction=0.4,
                wild_fillers=20,
                out_dir=out,
            )
            self.assertEqual(summary["base"], "gym.test")
            self.assertGreaterEqual(summary["obscure_true"], 50)
            self.assertGreaterEqual(summary["realistic_true"], 40)
            self.assertEqual(
                summary["known_true"],
                summary["obscure_true"] + summary["realistic_true"],
            )

            zone = json.loads((out / "zone_gym.json").read_text(encoding="utf-8"))
            self.assertIn("wild.gym.test", zone["wildcards"])
            self.assertIn("catch-all.gym.test", zone["wildcards"])
            self.assertTrue(zone["meta"]["not_public_internet"])

            known = (out / "known_true_gym.txt").read_text(encoding="utf-8").splitlines()
            obscure = (out / "obscure_true.txt").read_text(encoding="utf-8").splitlines()
            self.assertTrue(all(k.endswith(".gym.test") for k in known if k))
            self.assertGreaterEqual(len([x for x in obscure if x]), 50)

            # Every obscure FQDN must be in zone records (oracle resolves)
            for fqdn in obscure:
                if not fqdn:
                    continue
                self.assertIn(fqdn, zone["records"], f"obscure missing from zone: {fqdn}")

            # Generator contract: at least N labels pass obscure-style heuristic
            labels = [f[: -len(".gym.test")] for f in known if f.endswith(".gym.test")]
            obscure_style = [lab for lab in labels if gen.is_obscure_style(lab)]
            self.assertGreaterEqual(
                len(obscure_style),
                50,
                "generator must emit enough low-frequency/obscure labels",
            )

            wl = (out / "wordlist_gym.txt").read_text(encoding="utf-8").splitlines()
            for lab in labels:
                self.assertIn(lab, wl, f"known label missing from wordlist: {lab}")

    def test_metrics_recall_precision_f1(self) -> None:
        known = ["www.gym.test", "api.gym.test", "x.obscure.gym.test"]
        found = ["www.gym.test", "junk.gym.test"]
        r, p, hit, kn = metrics.recall_precision(found, known)
        self.assertEqual(kn, 3)
        self.assertEqual(hit, 1)
        self.assertAlmostEqual(r, 1 / 3)
        self.assertAlmostEqual(p, 0.5)
        self.assertAlmostEqual(metrics.f1(r, p), 2 * (1 / 3) * 0.5 / ((1 / 3) + 0.5))

    def test_sample_and_final_row_shape(self) -> None:
        s = metrics.sample_point("vegadns", 1.25, qps=100.0)
        self.assertEqual(s["tool"], "vegadns")
        self.assertEqual(s["t"], 1.25)
        self.assertEqual(s["phase"], "running")
        row = metrics.final_row("vegadns", 0.5, 10, 1.0, 1.0, timed=True)
        self.assertTrue(row["timed"])
        self.assertEqual(row["f1"], 1.0)
        self.assertEqual(row["wall_s"], 0.5)

    def test_claim_bounds_present_in_bench_module(self) -> None:
        import gym_bench

        cb = gym_bench.CLAIM_BOUNDS
        self.assertIn("proves", cb)
        self.assertIn("does_not_prove", cb)
        joined = " ".join(cb["does_not_prove"]).lower()
        self.assertIn("market", joined)
        self.assertIn("massdns", joined)


if __name__ == "__main__":
    unittest.main()
