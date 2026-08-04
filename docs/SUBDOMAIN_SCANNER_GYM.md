# Subdomain Scanner Gym — TRUE TEST

Multi-mode DNS enum harness. Measures **wall + recall + precision/F1** under declared conditions.

## What this is not

- Not “fastest on the market”
- Not massdns public-internet QPS supremacy
- Not a replacement for puredns+massdns on every large hunt
- Not passive OSINT / SaaS ASM coverage

Every `bench_report.json` embeds the same claim bounds.

## Modes

| Mode | What runs | What it proves |
|---|---|---|
| **mock-stress** (default) | Gym zone on 127.0.0.1 with **latency + SERVFAIL% + drop%** | Survival under noisy/slow recursive-like path; F1 vs peers on same zone |
| **mock-clean** | Instant mock answers | Regression / oracle correctness / CI |
| **live-resolve** | Public resolvers + fixed FQDN list (must-live + NX canaries) | Real network path agreement; requires `--authorized` |

Obscure + realistic known-true labels live in `fixtures/gym/` (generator: `scripts/gen_gym_fixtures.py`).

## CLI

```bash
cargo build --release
python scripts/gen_gym_fixtures.py

# True local test (default)
python scripts/gym_bench.py --mode mock-stress --out ./gym_out --wordlist-cap 5000 \
  --latency-ms 15 --servfail-pct 5 --drop-pct 2

# Instant regression
python scripts/gym_bench.py --mode mock-clean --out ./gym_out --wordlist-cap 5000

# Real public resolver path (fixed hostnames only)
python scripts/gym_bench.py --mode live-resolve --out ./gym_out --authorized
```

Peers timed when installed: **vegadns**, **massdns**, **dnsx**, **puredns**.

Mock stress flags on the DNS responder:

```bash
vegadns mock-serve --zone fixtures/gym/zone_gym.json --bind 127.0.0.1:53535 \
  --latency-ms 15 --servfail-pct 5 --drop-pct 2
```

## GUI

```bash
python scripts/gym_server.py --host 127.0.0.1 --port 9876 --out ./gym_out
# open http://127.0.0.1:9876/
```

Mode dropdown → **Run true test** → live wall-time graph + final table + claim bounds panel.

**Note:** port 8765 may already be used by other tools on this host. Prefer **9876**.

## Privacy

- GUI and mock bind **127.0.0.1** only.
- live-resolve uses public recursive resolvers for a **fixed** hostname list (not third-party subdomain hunting).
- Not authorization to scan arbitrary third-party domains.

## Steelman (defensible claims only)

- Active DNS brute + wildcard filter, single binary, Windows+Linux, no massdns dependency.
- On this harness, report wall/recall/precision per mode honestly.
- Peers that skip wildcard cleaning lose precision/F1 on the wildcarded gym zone.
- Breadth/depth (if used elsewhere) is candidate volume, not QPS glory.
