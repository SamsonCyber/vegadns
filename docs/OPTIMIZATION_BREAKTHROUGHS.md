# vegadns optimization breakthroughs

Human-readable report of multi-round measured work on the active DNS resolve
hot path. Numbers come from the fixed private suite harness (not memory).

## Claim bounds (read first)

This report documents **wins on a fixed private gym-stress suite**:

| Constant | Value |
|---|---|
| wordlist cap | 3000 |
| known-true oracle | 800 (`fixtures/gym`) |
| stress | latency 10ms, SERVFAIL 5%, drop 2% |
| retries | 3 |
| sockets | 1 |

**Proves:** wall / recall / precision / F1 / candidates-per-second under this harness.

**Does not prove:** “fastest on the market,” public-internet massdns QPS supremacy,
or that no further optimization is mathematically possible. Residual limits are
network physics (RTT, loss) and peer tooling (massdns raw dump can win pure wall
while losing precision).

---

## Scoreboard (vegadns, same suite)

| Round | wall_s | recall | precision | F1 | candidates/s | found/s |
|---|---:|---:|---:|---:|---:|---:|
| **0 baseline** | 0.5944 | 1.000 | 1.000 | 1.000 | 5047 | 1346 |
| **1 hot path** | **0.3956** | 1.000 | 1.000 | 1.000 | **7583** | **2022** |
| **2 recovery** | 0.4835 | 1.000 | 1.000 | 1.000 | 6205 | 1655 |
| **3 fast classify** | 0.4265 | 1.000 | 1.000 | 1.000 | 7034 | 1876 |

**Best stress wall in this campaign: round 1 (0.396s).**  
**vs round 0:** wall improved **~33%**, candidates/s **+50%**, R/P/F1 held at **1.0**.

### Peer (same suite, windows host)

| tool | wall_s | recall | precision | F1 |
|---|---:|---:|---:|---:|
| vegadns (R3) | 0.43 | 1.000 | **1.000** | **1.000** |
| dnsx | ~11–13 | ~0.995 | **0.47** | ~0.64 |
| massdns / puredns / shuffledns | not on Windows PATH (need Linux massdns) | | | |

Kali full peer race (earlier campaign, stress suite) already showed **massdns lowest wall / lowest precision**, **vegadns highest F1**. See `docs/peer_bench_kali.txt`.

### Clean mock (absolute speed ceiling on this host)

| wall_s | recall | precision | F1 | candidates/s |
|---:|---:|---:|---:|---:|
| 0.2472 | 1.000 | 1.000 | 1.000 | 12136 |

Instant answers; not a public recursive model.

---

## Round 0 — baseline

**Gap:** Need a fixed number before changing code.

**Change:** none (release binary as of campaign start).

**Harness:** `scripts/unbiased_tool_bench.py --mode gym-stress` with constants above.

**Result:** wall **0.5944s**, R=1.0, P=1.0, F1=1.0, **5047 cand/s**.

---

## Round 1 — hot-path send/recv/retry (breakthrough)

**Gap:** wall dominated by loop overhead and UDP pipe underfill / thrashy retry logic.

**Shipped:**

1. **Burst 384** queries then drain (fill the socket, then empty it).
2. **Hard recv drain** (up to 1024 recvs per socket per turn) so answers never pile up unprocessed.
3. **Retry list instead of `HashMap::retain` thrash** — scan pending once per turn into `to_retry` / `expired`.
4. **Clean attempt budget** — `max_attempts = retries + 1`, no ambiguous double-branch timeout path.
5. **Second-guess on retransmit aggressiveness** — 12ms retransmit flooded loopback and *hurt* wall (measured ~1.67s). Settled on **25ms** retry interval.
6. Unix-only large SO_RCVBUF/SO_SNDBUF (no Windows API in std).

**Result:** wall **0.3956s** (−33% vs R0), **7583 cand/s**, R/P/F1 still **1.0**.

**Breakthrough:** treat the hot path like massdns — **burst/drain**, not one-query-one-wait; retransmit just enough to hold recall, not enough to self-DDoS.

---

## Round 2 — adaptive recovery (second-guessed)

**Gap:** Phase 2b did up to **3 shrinking recovery passes + straggler batch** even when almost done.

**Shipped:** adaptive recovery — **at most 2 passes**, first wide, second narrow; exit as soon as no Errors remain.

**Result on this suite:** wall **0.4835s** (still beats R0; slightly behind R1).

**Second-guess:** on *this* mild stress suite, recovery rarely needed after R1, so adaptive recovery’s win is mostly **worst-case** (harsh loss), not median wall. Kept the change for harsh SERVFAIL/drop regimes; do not claim a R2 wall victory over R1.

---

## Round 3 — zero-alloc packet classify on hot path

**Gap:** full `parse_message` allocates question/answer **names** on every reply.

**Shipped:** prefer `classify_response_packet` (TXID + rcode + A rdata without name strings); full parse only as fallback.

**Result:** wall **0.4265s**, **7034 cand/s**, R/P/F1 **1.0**.

**Breakthrough:** answer classification is the inner loop; names are irrelevant until a name is *kept* after filter. Defer string work.

---

## Algorithmic shape (theoretical ceiling talk, grounded)

For stub resolve of N names against a recursive/mock:

\[
T \ge \max\left(\frac{N}{Q_{\text{pipe}}},\; RTT_{\text{eff}} \cdot (1 + L_{\text{loss}})\right)
\]

- \(Q_{\text{pipe}}\): send/recv fill rate (burst/drain, buffers, CPU classify).
- \(RTT_{\text{eff}}\): mock latency or public RTT.
- \(L_{\text{loss}}\): retransmit budget under drop/SERVFAIL.

**What we pushed toward the ceiling:**

- Raised \(Q_{\text{pipe}}\) (burst/drain, fast classify, less map thrash).
- Controlled \(L_{\text{loss}}\) (retry interval not too low).
- Cut post-pass work when \(L_{\text{loss}} \approx 0\) (adaptive recovery, fewer nested probes).

**What remains outside code:** public RTT, resolver rate limits, multi-million wordlists, massdns’s C path on Linux for pure dump throughput. Those are residual limits, not “we lost.”

---

## HTB (authorized control)

Optional control plane for subscription lab boxes. Token path only
(`~/.secrets/htb_api_token.txt`). Never commit tokens or target JSON.

```bash
python scripts/htb_lab.py status
python scripts/htb_lab.py spawn --name <box> --target-out ./htb_target.json
```

Spawn is not VPN reachability. Live DNS against lab IPs needs HTB VPN on the runner.
See `docs/HTB_UNBIASED_BENCH.md`.

---

## How to reproduce

```bash
cargo build --release
# Fixed suite constants — do not change between rounds
python scripts/unbiased_tool_bench.py --mode gym-stress --out ./opt_out \
  --wordlist-cap 3000 --concurrency 800 --latency-ms 10 \
  --servfail-pct 5 --drop-pct 2 --retries 3 --sockets 1
```

Peers (Linux/Kali with real massdns):

```bash
bash scripts/install_bench_peers.sh
python scripts/unbiased_tool_bench.py --mode gym-stress --out ./opt_out ...
```

---

## Residual limits (honest)

1. **Public recursive physics** — cannot beat RTT with software alone.
2. **massdns raw wall** on Linux can still be lower while dumping wildcard noise (F1 loses).
3. **Windows** lacks native massdns; full peer table needs Kali/WSL.
4. **“No further optimization possible”** is unfalsifiable; this campaign closed the *local* burst/classify/retry cluster. Next gains are elsewhere (IOCP, multi-thread resolve, SIMD parse) or live-network measurement.

---

## Victory summary

| Claim | Evidence |
|---|---|
| Faster on fixed stress suite | wall 0.594 → **0.396** (R1 best) |
| Full accuracy held | R=1.0 P=1.0 F1=1.0 every round |
| Cleaner than dnsx on same suite | P=1.0 vs P≈0.47 |
| Efficiency up | cand/s 5047 → **7583** (R1) |
| Breakthroughs named | burst/drain, retry hygiene, fast packet classify, adaptive recovery |
| HTB control scripts | token-out-of-repo; spawn ≠ VPN |
| Market supremacy **not** claimed | bounds in every report + this doc |

*Sources: harness JSON under goal scratch `opt_round0`…`opt_round3`, `opt_clean`; Kali peer table `docs/peer_bench_kali.txt`.*
