# Benchmarks (measured, fixed private suites)

All numbers below come from harness runs on fixed fixtures. They are **not**
public-internet market claims.

## How to read the tables

We plant a fixed oracle of **real** answers. Every tool gets the same wordlist and the same mock.

| Human column | Metric name | Meaning |
|---|---|---|
| Time | wall_s | Seconds until finish (lower is faster) |
| Real found | hit / recall × oracle | Planted answers recovered |
| Reported | found | Lines the tool printed as hits |
| Junk | found − hit | Extra noise to triage |
| Clean hit rate | precision | Real found / Reported (1.0 = 100% clean) |
| Combined score | F1 | Harmonic mean of recall and precision; 1.0 = full recall and zero junk |

**Rule of thumb:** full Real found + near-zero Junk first, then lowest Time. A tool can win Time and lose the race if it dumps noise.

## Claim bounds

**Proves**

- Time / real-found / junk / clean-hit-rate on the declared suite and mode
- Wildcard filter and soft-404 filter cut junk vs noise-dump peers
- Single-binary Windows + Linux path without a massdns dependency

**Does not prove**

- Fastest tool on the market
- Public recursive massdns QPS supremacy at multi-million scale
- Full replacement of puredns + massdns for every large hunt
- Passive OSINT / SaaS ASM coverage

## 1. DNS lab coverage (Kali, wildcard zone)

**Story:** find all 500 planted subs; ignore wildcard answers on random labels.

| Constant | Value |
|---|---|
| Host | Linux (Kali) |
| Real answers planted | 500 (`fixtures/lab`) |
| Wordlist labels | 8000 (fair cap) |
| Source | `coverage_dns_kali.txt` |

| tool | timed | Time | Real found | Reported | Junk | Clean hit rate | F1 |
|---|---|---:|---:|---:|---:|---:|---:|
| **vegadns** | yes | **0.176s** | **500** | **500** | **0** | **100%** | **1.000** |
| massdns | yes | 0.434s | 500 | 721 | 221 | 69% | 0.819 |
| gobuster-dns | yes | 161.3s | 0 | 0 | 0 | — | 0.000 |
| dnsx | no | — | 0 | 0 | — | — | — |

**Takeaway:** both vegadns and massdns recovered every real name. massdns added 221 wildcard lies. vegadns did not. Gate: **PASS**.

## 2. DNS adjacent compare (Kali, fair 5k wordlist)

**Story:** same idea as §1, smaller fair wordlist cap.

| Constant | Value |
|---|---|
| Host | Linux |
| Real answers in cap | 500 |
| Wordlist cap | 5000 |
| Source | `docs/adjacent_compare_kali.txt` |

| tool | Time | Real found | Reported | Junk | Clean hit rate |
|---|---:|---:|---:|---:|---:|
| **vegadns** | **0.093s** | 500 | 500 | 0 | 100% |
| massdns | 0.578s | 500 | 775 | 275 | 65% |
| gobuster-dns | 101.2s | 0 | 0 | 0 | — |

## 3. Gym stress multi-tool (Kali, latency + loss)

**Story:** flaky mock DNS. Find all 800 reals without printing wildcard junk.

| Constant | Value |
|---|---|
| Mode | mock-stress |
| Latency / SERVFAIL / drop | 10 ms / 5% / 2% |
| Real answers planted | 800 |
| Wordlist / candidates | 2000 |
| Source | `docs/peer_bench_kali.txt` |

| tool | timed | Time | Real found | Reported | Junk | Clean hit rate | F1 |
|---|---|---:|---:|---:|---:|---:|---:|
| massdns | yes | **0.97s** | 800 | 1699 | 899 | 47% | 0.640 |
| shuffledns | yes | 1.82s | 800 | 1700 | 900 | 47% | 0.640 |
| **vegadns** | yes | 3.31s | **800** | **800** | **0** | **100%** | **1.000** |
| puredns | yes | 3.82s | 800 | 1700 | 900 | 47% | 0.640 |

**Takeaway:** massdns wins pure speed. vegadns wins clean output (zero junk).

## 4. Hot-path optimization campaign (Windows gym-stress)

**Story:** vegadns only, before vs after engine work. Oracle and suite fixed.

| Constant | Value |
|---|---|
| Wordlist cap | 3000 |
| Real answers planted | 800 |
| Stress | latency 10 ms, SERVFAIL 5%, drop 2% |
| Sockets / retries | 1 / 3 |
| Source | `docs/OPTIMIZATION_BREAKTHROUGHS.md` |

| round | Time | Real found | Clean hit rate | Names / sec |
|---|---:|---:|---:|---:|
| 0 baseline | 0.594s | 800 / 800 | 100% | 5,047 |
| **1 hot path (best)** | **0.396s** | 800 / 800 | 100% | **7,583** |
| 2 adaptive recovery | 0.484s | 800 / 800 | 100% | 6,205 |
| 3 fast classify | 0.427s | 800 / 800 | 100% | 7,034 |

vs baseline: **~33%** faster, **+50%** names/sec, still zero junk.

Clean mock ceiling (instant answers, same host): Time **0.247s**, ~12k names/sec, 100% clean.

## 5. HTTP paths hard suite (Kali, soft-404 200 noise)

**Story:** site returns HTTP 200 even for missing paths (same body). Status-only
tools count those as hits. We planted 24 real paths; the rest of the wordlist is bait.

| Constant | Value |
|---|---|
| Real paths planted | 24 |
| Soft-404 | missing path → HTTP 200 + fixed body |
| Source | `coverage_paths_kali.txt` |

| tool | timed | Time (process wall) | Real found | Reported | Junk | Clean hit rate | F1 |
|---|---|---:|---:|---:|---:|---:|---:|
| **vegadns-paths** | yes | **0.032s** | **24** | **24** | **0** | **100%** | **1.000** |
| feroxbuster | yes | 1.03s | 24 | 61 | **37** | 39% | 0.565 |

**Takeaway:** both found every real path. vegadns is faster **and** clean
(process-wall H2H, 3 stable runs). ferox prints ~37 soft-404 200s.
Gate: **PASS** (F1 and wall).

Embedded hard mock (in-process): engine wall ~**14 ms**, 24 real / 24 reported,
0 junk, 36 soft-404s dropped before emit.

## 6. Windows gym clean (dnsx peer)

**Story:** instant mock answers (no latency). 800 reals, 2000 candidates.

| Constant | Value |
|---|---|
| Mode | mock-clean |
| Real answers planted | 800 |
| Candidates | 2000 |
| Source | `gym_out_clean/bench_report.txt` (local scratch; not shipped) |

| tool | Time | Real found | Reported | Junk | Clean hit rate |
|---|---:|---:|---:|---:|---:|
| **vegadns** | **0.024s** | 800 | 800 | 0 | 100% |
| dnsx | 0.719s | 800 | 1700 | 900 | 47% |

## Reproduce

```bash
cargo build --release

# DNS + paths F1 surpass (needs peer tools on PATH for full table)
python scripts/coverage_surpass.py --out ./coverage_out --wordlist-cap 8000

# Gym stress true test
python scripts/gen_gym_fixtures.py
python scripts/gym_bench.py --mode mock-stress --out ./gym_out --wordlist-cap 2000

# Unbiased multi-tool (Linux: install massdns first)
bash scripts/install_bench_peers.sh   # or scripts/verify_bench_peers.py
python scripts/unbiased_tool_bench.py --mode gym-stress --out ./bench_out \
  --wordlist-cap 3000 --concurrency 800 --latency-ms 10 \
  --servfail-pct 5 --drop-pct 2 --retries 3 --sockets 1
```

Raw peer tables that ship with the repo:

- `docs/peer_bench_kali.txt` / `.json`
- `docs/adjacent_compare_kali.txt` / `.json`
- `docs/OPTIMIZATION_BREAKTHROUGHS.md`
- `docs/DISCOVERY_COVERAGE.md`
