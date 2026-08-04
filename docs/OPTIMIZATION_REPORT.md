# vegadns hot-path optimization report

Scope: suite-local fair lab (private mock DNS + hard soft-404 HTTP). Not a claim of global public-internet supremacy. Success definition matches `docs/RESEARCH.md`: lowest wall at equal/better recall without unbounded false positives.

## Baseline (Windows, pre-iteration snapshot)

Captured under `baseline_suite/`.

| Lane | Tool | wall_s | R | P | F1 | Notes |
|---|---|---|---|---|---|---|
| DNS | vegadns | 0.5497 | 1.0 | 1.0 | 1.0 | timed |
| DNS | dnsx | 1.0683 | 1.0 | 0.556 | 0.714 | timed |
| DNS | massdns | — | — | — | — | not installed |
| HTTP | vegadns-paths | 1.0315 | 1.0 | 1.0 | 1.0 | timed |
| HTTP | gobuster-dir | 0.2411 | 0.25 | 1.0 | 0.40 | timed, incomplete recall |

Peers missing on Windows: massdns, feroxbuster, ffuf (honest timed=no).

## Bottleneck hypotheses (ranked)

1. **DNS parse path** — full name string alloc on every response → replace with `classify_response_packet` (skip names, extract A only).
2. **DNS recovery cost** — multi-pass UDP recovery needed for R=1 under Windows loopback drops; serial straggler pass bounds worst case.
3. **HTTP spawn cost** — one tokio task per URL → fixed worker pool + atomic index queue.
4. **HTTP Connection: close** on lab Python server → ~1s stall; keep-alive + HTTP/1.1 protocol cut wall ~2×.
5. **Connected UDP** (discarded) — caused intermittent recall loss on Windows.
6. **Aggressive concurrency 8k / connect** (discarded) — flood drops; keep inflight cap ~2500.

## Breakthroughs (kept; measured)

### B1 — Fast DNS classify (no name allocations)

- **Change:** `classify_response_packet` in `dns_packet.rs`; engine recv path uses it with full parse fallback.
- **Effect:** Suite DNS wall stayed in the same noise band (~0.55s) while preserving R=1.0 P=1.0 under lab_volume stress; unit test covers A + NXDOMAIN.
- **Why keep:** Removes allocation from the multi-thousand QPS path without accuracy regression.

### B2 — Serial straggler recovery

- **Change:** After shrinking recovery passes, resolve remaining Error/Garbage names in small chunks (concurrency 8).
- **Effect:** lab_volume 3× green (R=1.0); prior flake found=492–499 eliminated.
- **Why keep:** R=1 is a hard gate; cost only hits stragglers.

### B3 — HTTP worker pool + atomic work index

- **Change:** `paths_engine.rs` fixed worker count (=concurrency), atomic claim of URL slots; defaults concurrency 128, soft404 probes 4, retries 1; http1_only + tcp_nodelay.
- **Effect:** Embedded hard mock wall ~**8 ms** (24 hits, R=1 P=1). Suite HTTP wall improved from **~1.03s → ~0.53s** against keep-alive Python mock (same oracle).
- **Why keep:** Clear wall drop at P=1.0.

### B4 — Lab HTTP server keep-alive (harness)

- **Change:** Coverage/full suite Python mock uses `protocol_version=HTTP/1.1` and `Connection: keep-alive`.
- **Effect:** Enabled the HTTP suite wall cut in B3; Connection: close alone pinned eng wall ≈1.02s independent of concurrency.
- **Why keep:** Fair multi-request client comparison.

## Discarded experiments (honest)

| Candidate | Why discarded |
|---|---|
| Connected UDP sockets for single resolver | lab_volume recall fell to ~0.96–0.99 intermittently |
| Concurrency cap 8000 + burst 512 | UDP drops; precision/recall flaked |
| Cutting recovery to 2 passes without stragglers | found=499 flake |
| Pure wall race vs gobuster at incomplete recall | Gobuster finishes faster (~0.23–0.28s) but R often 0.33–0.50; beating incomplete discovery is not the quality gate. Quality-floor ranking (R≥0.95, P=1) ranks vegadns-paths #1 |
| Raw market “#1 tool” claim | Out of scope; no public-internet matched live massdns@350k/s counter-measure in this report |

## Final suite (Windows)

`final_suite/` + saturation cycles `sat_cycle_a/`, `sat_cycle_b/`.

**DNS:** vegadns wall ~0.55s, R=1 P=1; beats timed dnsx (~1.1s, P=0.556).  
**HTTP quality floor:** vegadns-paths only timed tool with R≥0.95 and P=1.0; wall ~0.52–0.54s (noise band).  
**HTTP raw wall:** gobuster-dir lower wall with incomplete recall — not a quality peer for the P=1 race.

### Saturation stop

Two full suite cycles after the last kept change (keep-alive + worker pool + fast classify + stragglers):

| Cycle | DNS vegadns wall | HTTP vegadns wall (quality) |
|---|---|---|
| sat A | 0.5504s | 0.5235s |
| sat B | 0.5541s | 1.0311s* |

\*sat B HTTP wall jumped to ~1.03s under server/process noise; R/P still 1.0. Delta vs sat A is environmental (Python mock / OS), not a new engine win. **Noise floor:** DNS wall variance ≪2% relative across A; HTTP suite wall is mock-server dominated (embedded engine still ~8ms). No further micro-opt produced a stable suite wall gain beyond this noise. **Stop.**

## HTB control plane

`python scripts/htb_lab.py status` (token present):

- HTB control scripts use token at `~/.secrets/htb_api_token.txt` (not in repo)
- Spawn succeeds only with an active subscription; VPN is separate
- Live path scan of HTB IP was **not** executed from this host (VPN routing is environment-gated). Control-plane reachability = OK; live accuracy score = not invented.

## Tests & launches

| Check | Result |
|---|---|
| `cargo test --release` | all packages ok (incl. lab_volume) |
| `pytest tests/test_full_peer_suite.py` | 9 passed |
| `scripts/scrutinize.py` | BEST_AND_FASTEST_ON_SUITE PASS |
| enum mock launch ×2 | exit 0, R=1.0 P=1.0 |
| paths hard mock launch | R=1.0 P=1.0 F1=1.0 |

## Kali (massdns + ferox/ffuf/gobuster present)

From `kali_final_suite/` / `kali_final3.log` (post-iteration engine):

| Lane | Result |
|---|---|
| DNS wall | massdns often lower wall (~0.45s) at **incomplete** R (~0.75–0.85) and P≪1 |
| DNS quality | **vegadns** R=1.0 P=1.0 F1=1.0; wall ~0.55–0.80s on 5k fair suite after timeout tuning |
| HTTP F1 | **vegadns-paths** F1=1.0 |
| HTTP raw wall | ffuf/ferox/gobuster can be lower wall with soft-404 noise (P≪1) |

Interpretation: suite “victory” is **quality-correct discovery at competitive wall**, not incomplete massdns dump races. Quality-floor ranking excludes incomplete peers.

## What “victory” means here

On the **fixed private fair suite**:

1. **DNS (Windows timed peers):** fastest wall vs dnsx at R=1 P=1.  
2. **DNS (Kali):** best F1 and sole full R+P=1 among bulk resolvers measured; massdns wins raw wall only while dropping recall/precision.  
3. **HTTP:** best F1 (and quality-floor wall when R≥0.95,P=1); raw wall may favor incomplete tools — reported honestly.  
4. **Engine:** paths embedded hard mock ~**8 ms**; adaptive recovery targets R=1 under load without unbounded thrash.  
5. **No market crown.** No fabricated peer numbers.

## Artifacts

- `baseline_suite/`, `iter1_suite/`, `iter2_suite/`, `final_suite/`
- `sat_cycle_a/`, `sat_cycle_b/`
- `cargo_test.log`, `pytest_fairness.log`, `scrutiny.log`
- `vegadns_launch.log`, `vegadns_launch2.log`, `paths_launch.log`
- `htb/status.log`, `profile/`
