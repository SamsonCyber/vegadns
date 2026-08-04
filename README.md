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

## How to read the numbers

We plant a fixed set of **real** answers (oracle). Every tool gets the same wordlist and the same mock server.

| Column | Plain English |
|---|---|
| Time | Seconds until the tool finishes (lower is faster) |
| Real found | How many planted answers it recovered (higher is better; max = oracle size) |
| Reported | How many names/URLs it printed as hits |
| Junk | Reported − Real found (noise you still have to triage) |
| Clean hit rate | Real found / Reported. **100%** means every printed hit was real |

**Faster is not always better.** A tool can finish first and still bury you in junk. We care about **all real answers, almost no junk**, then speed.

These are private lab / gym suites only. Not “fastest on the public internet.”  
Full raw tables: **[docs/BENCHMARKS.md](docs/BENCHMARKS.md)**.

## Benchmarks at a glance

### 1. DNS lab — find subdomains, ignore wildcard noise

**Setup:** 500 real subdomains planted. Zone also answers random junk labels (wildcard). Wordlist: 8000 labels. Host: Kali.

| tool | Time | Real found (of 500) | Reported | Junk | Clean hit rate |
|---|---:|---:|---:|---:|---:|
| **vegadns** | **0.18s** | **500** | **500** | **0** | **100%** |
| massdns | 0.43s | 500 | 721 | 221 | 69% |
| gobuster-dns | 161s | 0 | 0 | 0 | — |

**Takeaway:** vegadns and massdns both found every real name. massdns also printed **221 wildcard lies**. vegadns filtered those and finished faster on this suite.

### 2. DNS stress gym — flaky resolver (latency + packet loss)

**Setup:** 800 real names. Mock DNS adds 10 ms delay, 5% SERVFAIL, 2% drop. Wordlist: 2000. Host: Kali.

| tool | Time | Real found (of 800) | Reported | Junk | Clean hit rate |
|---|---:|---:|---:|---:|---:|
| massdns | **0.97s** | 800 | 1699 | 899 | 47% |
| shuffledns | 1.82s | 800 | 1700 | 900 | 47% |
| **vegadns** | 3.31s | **800** | **800** | **0** | **100%** |
| puredns | 3.82s | 800 | 1700 | 900 | 47% |

**Takeaway:** massdns is the speed king here but ~half its output is noise. vegadns is slower and prints **only** real hits.

### 3. Same tool, before vs after hot-path work

**Setup:** Windows gym-stress, 3000 candidates, same 800 oracle. No peer race. We only compare vegadns to itself.

| build | Time | Real found | Clean hit rate | Names checked / sec |
|---|---:|---:|---:|---:|
| before | 0.59s | 800 / 800 | 100% | 5,047 |
| **after (best)** | **0.40s** | 800 / 800 | 100% | **7,583** |

**Takeaway:** ~**33%** faster, ~**50%** more names per second, still zero junk. Detail: [docs/OPTIMIZATION_BREAKTHROUGHS.md](docs/OPTIMIZATION_BREAKTHROUGHS.md).

### 4. HTTP paths — server lies with “200 OK” on missing pages

**Setup:** 24 real paths planted (`/admin`, `/api`, …). **Soft-404:** missing paths still return HTTP **200** with a fixed “not found” body. Status-only tools treat those as hits. Wordlist mixes real paths + bait. Same process-wall clock for every tool.

| tool | Time | Real found (of 24) | Reported | Junk | Clean hit rate |
|---|---:|---:|---:|---:|---:|
| **vegadns paths** | **0.032s** | **24** | **24** | **0** | **100%** |
| feroxbuster | 1.03s | 24 | 61 | **37** | 39% |

**What this means**

1. Every timed tool found all 24 real paths.
2. ferox also reported **37 fake pages** (soft-404 200s).
3. vegadns fingerprints the lie, drops fakes, prints **exactly the 24 real URLs**, and finishes **faster**.

**Takeaway:** vegadns wins clean output **and** wall on this fixed hard suite (body drain + keep-alive reuse; process-wall H2H).

## Build

```bash
cargo build --release
```

Binary: `target/release/vegadns` (`.exe` on Windows).

Requires a recent Rust toolchain. No massdns dependency for the binary itself.

**Output model:** stdout = results only (pipe-safe). stderr = ferox-class human UI (ASCII banner, scan-config panel, `[INF]`/`[OK ]`/`[WRN]` tags, live `[####>---]` progress on TTY, boxed complete stats). Color when TTY and `NO_COLOR` unset. Use `-q` / `--quiet-names` to silence.

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
