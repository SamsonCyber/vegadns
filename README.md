<p align="center">
  <img src="assets/banner.jpg" alt="vegadns — subdomain enum and path discovery" width="100%">
</p>

# vegadns

High-concurrency **subdomain enum** and **HTTP path discovery** in one Rust binary.

Sanskrit *vega* = impetus / velocity. Also the star.

| Lane | What it does |
|---|---|
| DNS | Wordlist expand → concurrent UDP resolve → wildcard filter → emit |
| Breadth | Built-in depth packs + altdns-class permute |
| HTTP paths | Concurrent path scan + soft-404 fingerprint filter |

Research pass covered massdns, puredns/shuffledns, dnsx, subfinder, alterx/gotator/altdns, and ZDNS. See [docs/RESEARCH.md](docs/RESEARCH.md).

## Claim bounds (read first)

Numbers in this README come from **fixed private lab / gym suites**.

**Proves:** wall + recall + precision + F1 on those suites.  
**Does not prove:** fastest tool on the public internet, massdns QPS supremacy, or full ASM replacement.

Prefer **F1 with wall**, not wall alone. massdns can finish faster and dump more noise.

Full tables and reproduce steps: **[docs/BENCHMARKS.md](docs/BENCHMARKS.md)**.

## Benchmarks at a glance

### DNS — Kali lab (wildcard zone, 500 known-true, 8k labels)

| tool | wall_s | recall | precision | F1 |
|---|---:|---:|---:|---:|
| **vegadns** | **0.176** | **1.000** | **1.000** | **1.000** |
| massdns | 0.434 | 1.000 | 0.693 | 0.819 |
| gobuster-dns | 161.3 | 0.000 | 1.000 | 0.000 |

### DNS — gym stress (Kali, 10 ms latency, 5% SERVFAIL, 2% drop)

| tool | wall_s | recall | precision | F1 |
|---|---:|---:|---:|---:|
| **vegadns** | 3.31 | 1.000 | **1.000** | **1.000** |
| massdns | **0.97** | 1.000 | 0.471 | 0.640 |
| shuffledns | 1.82 | 1.000 | 0.471 | 0.640 |
| puredns | 3.82 | 1.000 | 0.471 | 0.640 |

massdns wins wall. vegadns wins clean F1 (wildcard filter).

### DNS — hot-path campaign (Windows gym-stress, 3k candidates)

| round | wall_s | F1 | candidates/s |
|---|---:|---:|---:|
| baseline | 0.594 | 1.000 | 5047 |
| **best (burst/drain + retry hygiene)** | **0.396** | 1.000 | **7583** |

~33% wall cut, +50% candidates/s, R/P/F1 held at 1.0. Detail: [docs/OPTIMIZATION_BREAKTHROUGHS.md](docs/OPTIMIZATION_BREAKTHROUGHS.md).

### HTTP paths — Kali hard soft-404 suite (24 known-true)

| tool | wall_s | recall | precision | F1 |
|---|---:|---:|---:|---:|
| **vegadns-paths** | 2.26 | **1.000** | **1.000** | **1.000** |
| feroxbuster | **1.56** | 1.000 | 0.393 | 0.565 |
| ffuf | 3.82 | 1.000 | 0.407 | 0.578 |

Peers keep soft-404 200 responses. vegadns drops them after probes.

## Build

```bash
cargo build --release
```

Binary: `target/release/vegadns` (`.exe` on Windows).

Requires a recent Rust toolchain. No massdns dependency for the binary itself.

## Quick start

### Offline mock (fixture path)

```bash
vegadns enum \
  --mock-zone fixtures/zone_bench.json \
  --wordlist fixtures/wordlist_small.txt \
  --output hits.txt \
  --known-true fixtures/known_true.txt
```

### Live resolvers (authorized targets only)

```bash
vegadns enum \
  -d example.com \
  -w wordlist.txt \
  -r resolvers.txt \
  -o found.txt \
  --concurrency 4000 \
  --timeout-ms 1500
```

### Depth ladder

```bash
vegadns wordlist list

vegadns enum -d example.com -D fast   -r resolvers.txt -o found.txt
vegadns enum -d example.com -D deep   -r resolvers.txt -o found.txt
vegadns enum -d example.com -D final  -r resolvers.txt -o found.txt
vegadns enum -d example.com -D final --no-permute -r resolvers.txt
```

### HTTP paths

```bash
vegadns paths --mock-paths fixtures/paths/hit_paths.txt \
  -w fixtures/paths/wordlist.txt --known-true fixtures/paths/known_true.txt \
  -o hits.txt --status 200

# hard soft-404 mock
vegadns paths --mock-hard-zone fixtures/paths/hard_zone.txt \
  -w fixtures/paths/wordlist_hard.txt --known-true fixtures/paths/known_true_hard.txt \
  --status 200,401,403 --soft404-probes 10 -q

# live (authorized base URL only)
vegadns paths -u http://127.0.0.1:18080/ -w paths.txt -o hits.txt
```

## Subdomain Scanner Gym

Multi-mode harness: **mock-stress**, **mock-clean**, **live-resolve**. Reports wall + recall + precision/F1 with claim bounds baked into every report.

```bash
python scripts/gen_gym_fixtures.py
python scripts/gym_bench.py --mode mock-stress --out ./gym_out --wordlist-cap 5000
python scripts/gym_server.py --port 9876
# http://127.0.0.1:9876/  or start_gym.bat on Windows
```

Guide: [docs/SUBDOMAIN_SCANNER_GYM.md](docs/SUBDOMAIN_SCANNER_GYM.md).

## Unbiased multi-tool bench

Same candidates, resolvers, and oracle for every tool. Metrics: wall, recall, precision, F1, candidates/s, found/s, efficiency_score.

```bash
# Linux peers (massdns ELF, puredns/shuffledns/dnsx/gobuster)
bash scripts/install_bench_peers.sh
python scripts/verify_bench_peers.py

python scripts/unbiased_tool_bench.py --mode gym-stress --out ./bench_out --wordlist-cap 5000
python scripts/coverage_surpass.py --out ./coverage_out --wordlist-cap 8000
python scripts/full_peer_suite.py --out ./full_suite --wordlist-cap 5000
```

Docs: [docs/HTB_UNBIASED_BENCH.md](docs/HTB_UNBIASED_BENCH.md), [docs/DISCOVERY_COVERAGE.md](docs/DISCOVERY_COVERAGE.md), [docs/ADJACENT_TOOLS.md](docs/ADJACENT_TOOLS.md).

Optional HTB Labs control (token stays in `~/.secrets/htb_api_token.txt`, never in the repo):

```bash
python scripts/htb_lab.py status
python scripts/htb_lab.py spawn --name <box> --target-out ./htb_target.json
```

Spawn is not VPN reachability. Live probes against lab IPs need HTB VPN on the runner.

## Tests

```bash
cargo test --release
python scripts/gherkin_run.py
python scripts/quality_run.py --out ./quality_out --write-metrics
```

## Docs map

| Doc | Topic |
|---|---|
| [docs/BENCHMARKS.md](docs/BENCHMARKS.md) | Full comparison tables + reproduce |
| [docs/OPTIMIZATION_BREAKTHROUGHS.md](docs/OPTIMIZATION_BREAKTHROUGHS.md) | Measured hot-path campaign |
| [docs/DISCOVERY_COVERAGE.md](docs/DISCOVERY_COVERAGE.md) | F1 surpass definition |
| [docs/SUBDOMAIN_SCANNER_GYM.md](docs/SUBDOMAIN_SCANNER_GYM.md) | Gym modes + GUI |
| [docs/PIPELINE.md](docs/PIPELINE.md) | DNS → paths pipeline |
| [docs/BREADTH.md](docs/BREADTH.md) | Wordlist depth + permute |
| [docs/RESEARCH.md](docs/RESEARCH.md) | Peer landscape notes |
| [docs/QA.md](docs/QA.md) | Quality procedures |

## License

MIT. See [LICENSE](LICENSE).

Wordlist packs include SecLists / altdns snapshots under their upstream licenses. See [wordlists/README.md](wordlists/README.md).

## Ethics

Use only on systems you own or are authorized to test. The gym and lab suites are private mocks by default. Live enum and path modes are for authorized targets.
