# Benchmarks (measured, fixed private suites)

All numbers below come from harness runs on fixed fixtures. They are **not**
public-internet market claims. Prefer **wall + recall + precision + F1** together.
massdns can win pure wall while dumping wildcard noise (low precision).

## Claim bounds

**Proves**

- Wall / recall / precision / F1 on the declared suite and mode
- Wildcard filter and soft-404 filter improve precision/F1 vs noise-dump peers
- Single-binary Windows + Linux path without a massdns dependency

**Does not prove**

- Fastest tool on the market
- Public recursive massdns QPS supremacy at multi-million scale
- Full replacement of puredns + massdns for every large hunt
- Passive OSINT / SaaS ASM coverage

## 1. DNS lab coverage (Kali, wildcard zone)

| Constant | Value |
|---|---|
| Host | Linux (Kali) |
| Known-true | 500 (`fixtures/lab`) |
| Wordlist labels | 8000 (fair cap) |
| Source | `coverage_dns_kali.txt` |

| tool | timed | wall_s | found | recall | precision | F1 |
|---|---|---:|---:|---:|---:|---:|
| **vegadns** | yes | **0.176** | 500 | **1.000** | **1.000** | **1.000** |
| massdns | yes | 0.434 | 721 | 1.000 | 0.693 | 0.819 |
| gobuster-dns | yes | 161.3 | 0 | 0.000 | 1.000 | 0.000 |
| dnsx | no | — | 0 | — | — | — |

Gate: **PASS** (strict F1 surpass). vegadns full recall + precision 1.0; massdns keeps wildcard noise.

## 2. DNS adjacent compare (Kali, fair 5k wordlist)

| Constant | Value |
|---|---|
| Host | Linux |
| Known-true in cap | 500 |
| Wordlist cap | 5000 |
| Source | `docs/adjacent_compare_kali.txt` |

| tool | wall_s | found | recall | precision |
|---|---:|---:|---:|---:|
| **vegadns** | **0.093** | 500 | 1.000 | 1.000 |
| massdns | 0.578 | 775 | 1.000 | 0.645 |
| gobuster-dns | 101.2 | 0 | 0.000 | 1.000 |

## 3. Gym stress multi-tool (Kali, latency + loss)

| Constant | Value |
|---|---|
| Mode | mock-stress |
| Latency / SERVFAIL / drop | 10 ms / 5% / 2% |
| Known-true | 800 |
| Wordlist / candidates | 2000 |
| Source | `docs/peer_bench_kali.txt` |

| tool | timed | wall_s | found | recall | precision | F1 |
|---|---|---:|---:|---:|---:|---:|
| **vegadns** | yes | 3.31 | 800 | 1.000 | **1.000** | **1.000** |
| massdns | yes | **0.97** | 1699 | 1.000 | 0.471 | 0.640 |
| shuffledns | yes | 1.82 | 1700 | 1.000 | 0.471 | 0.640 |
| puredns | yes | 3.82 | 1700 | 1.000 | 0.471 | 0.640 |

massdns wins wall. vegadns wins F1 (wildcard filter). Prefer both columns, not wall alone.

## 4. Hot-path optimization campaign (Windows gym-stress)

| Constant | Value |
|---|---|
| Wordlist cap | 3000 |
| Known-true | 800 |
| Stress | latency 10 ms, SERVFAIL 5%, drop 2% |
| Sockets / retries | 1 / 3 |
| Source | `docs/OPTIMIZATION_BREAKTHROUGHS.md` |

| round | wall_s | recall | precision | F1 | candidates/s |
|---|---:|---:|---:|---:|---:|
| 0 baseline | 0.594 | 1.000 | 1.000 | 1.000 | 5047 |
| **1 hot path (best)** | **0.396** | 1.000 | 1.000 | 1.000 | **7583** |
| 2 adaptive recovery | 0.484 | 1.000 | 1.000 | 1.000 | 6205 |
| 3 fast classify | 0.427 | 1.000 | 1.000 | 1.000 | 7034 |

vs baseline: wall **~33% faster**, candidates/s **+50%**, R/P/F1 held at **1.0**.

Clean mock ceiling (instant answers, same host): wall **0.247s**, ~12k candidates/s, R/P/F1 = 1.0.

## 5. HTTP paths hard suite (Kali, soft-404 200 noise)

| Constant | Value |
|---|---|
| Known-true | 24 |
| Soft-404 | missing paths return HTTP 200 with fixed body |
| Source | `coverage_paths_kali.txt` |

| tool | timed | wall_s | found | recall | precision | F1 |
|---|---|---:|---:|---:|---:|---:|
| **vegadns-paths** | yes | 2.26 | 24 | **1.000** | **1.000** | **1.000** |
| feroxbuster | yes | **1.56** | 61 | 1.000 | 0.393 | 0.565 |
| ffuf | yes | 3.82 | 59 | 1.000 | 0.407 | 0.578 |

Gate: **PASS**. Peers keep soft-404 200s; vegadns drops them after probe fingerprints.

Embedded hard mock (Windows, in-process): wall **~8 ms**, 24 hits, R=1 P=1 F1=1, 36 soft-404 dropped.

## 6. Windows gym clean (dnsx peer)

| Constant | Value |
|---|---|
| Mode | mock-clean |
| Known-true | 800 |
| Candidates | 2000 |
| Source | `gym_out_clean/bench_report.txt` (local scratch; not shipped) |

| tool | wall_s | recall | precision | F1 |
|---|---:|---:|---:|---:|
| **vegadns** | **0.024** | 1.000 | 1.000 | 1.000 |
| dnsx | 0.719 | 1.000 | 0.471 | 0.640 |

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
