# Subdomain enumeration: language, architecture, tools

Research for **vegadns**. Host: Windows amd64. Date: 2026-08-02.

## Efficiency definition (used by the bench)

| Metric | Meaning | Primary? |
|---|---|---|
| **Wall time** | End-to-end seconds for fixed wordlist + zone | Primary speed |
| **Query rate** | Queries completed per second (sent and matched or timed out) | Primary throughput |
| **Recall** | `\|found ∩ known_true\| / \|known_true\|` | Primary accuracy |
| **Precision** | `\|found ∩ known_true\| / \|found\|` (garbage + wildcard FPs hurt) | Accuracy |
| **Resource use** | Peak RSS, open sockets, CPU% | Efficiency secondary |

"Fastest / most efficient" for this project means: **highest query rate and lowest wall time on the fixed in-repo suite at equal or better recall**, without unbounded wildcard false positives. Not global supremacy on every public target.

## Language choice

| Language | Strengths | Limits for this problem |
|---|---|---|
| **C (massdns)** | Lowest overhead, malloc-free DNS path, ~350k+ names/s reported | Linux-first; Windows needs Cygwin; no built-in wildcard filter; easy to flood resolvers |
| **Go (puredns, shuffledns, dnsx, subfinder)** | Fast CLI ecosystem, goroutines, ProjectDiscovery stack | GC under multi-million pending maps; wrappers often shell out to massdns for raw speed |
| **Rust** | Zero-cost async, no GC, safe concurrent maps, portable UDP | Ecosystem smaller than PD for passive sources; must implement DNS carefully |
| **Python** | Great for orchestration | Too slow for the hot UDP resolve path |

**Decision: Rust.** Goals need a native high-concurrency stub resolver that runs on Windows without massdns, plus pure units for offline tests. Rust matches massdns-class goals better than Go when massdns is absent, and beats Python on the hot path.

## Architecture patterns that win

1. **Stub resolver, not full recursive client.** Send queries to a list of recursive resolvers (or authoritative NS). Do not walk the DNS tree per name.
2. **UDP socket reuse.** One (or few) long-lived UDP sockets; many in-flight queries keyed by TXID. Creating a socket per lookup kills throughput (ZDNS/IMC findings).
3. **Hashmap concurrency control.** Cap in-flight lookups (massdns `-s` / hashmap size). Network is usually the bottleneck; CPU should stay light.
4. **Resolver rotation + retries.** Public resolvers lie, rate-limit, and SERVFAIL. Retry with another resolver; optional trusted re-validation pass (puredns).
5. **Stream wordlist → FQDN expand.** Do not load multi-million line lists fully when avoidable.
6. **Wildcard detection separate from resolve.** massdns does not filter wildcards (still on its todo). puredns: random-label probes + answer fingerprinting + trusted re-resolve.
7. **Split pure logic from I/O.** Expand, classify, wildcard filter, dedup are pure; tests drive them without live network. Controlled mock DNS path for recall/precision; live path for throughput.

## Existing tools (comparison matrix)

| Tool | Lang | Role | Strengths | Limits |
|---|---|---|---|---|
| **massdns** | C | Bulk stub resolve | Extreme QPS; simple; battle-tested for millions/billions of names | No wildcard filter; can overwhelm resolvers (ZDNS paper: high SERVFAIL without care); Windows friction |
| **puredns** | Go + massdns | Brute + resolve + clean | Best practical accuracy for large active lists; wildcard + poison filter; trusted re-resolve | Depends on massdns binary; rate limits on trusted path |
| **shuffledns** | Go + massdns | Brute/resolve wrapper | Easy PD-style UX; resolver shuffle; speed on large lists | massdns dependency; less accuracy focus than puredns |
| **dnsx** | Go | Multi-purpose DNS toolkit | Flexible records, templates, PD pipeline fit | Not a pure mass-brute engine; different product shape |
| **subfinder** | Go | Passive discovery | Many free/API sources; zero wordlist needed for surface map | Not active brute; needs keys for full coverage |
| **Amass** | Go | Full attack-surface graph | Deep passive + active + intel graph | Heavy; not "fastest resolver"; out of scope for pure enum speed |
| **ZDNS** | Go | Measurement toolkit | Academic rigor; socket reuse; modules | Measurement focus, not recon UX |
| **altdns / gotator / alterx** | Python/Go | Permutation | Finds mutations of known subs | Separate tools; vegadns now embeds altdns-class permute + presets for breadth |

### Stack used in the wild (recon)

```
subfinder (passive) → (optional altdns) → puredns/shuffledns+massdns (active) → dnsx → httpx
```

Active accuracy winners today: **puredns over raw massdns** (wildcards + validation). Raw speed winner: **massdns** (or massdns-backed tools). Passive winner: **subfinder** (coverage, not QPS).

## Design targets for vegadns

| Gap in the field | vegadns response |
|---|---|
| massdns has no wildcard filter | Built-in random-probe fingerprint filter |
| puredns/shuffledns need massdns | Native Rust UDP engine (Windows + Linux) |
| Hard to unit-test live DNS | Embedded mock zone + pure classify/expand/wildcard/dedup |
| Benchmarks are anecdote | Fixed fixture suite + black-box harness vs baselines |
| Breadth needs 3+ tools (list + alterx + resolve) | Built-in presets (SecLists-class) + `permute` + resolve + wildcard in one binary |

## Architecture of vegadns

```
wordlist ──► expand (label + base → FQDN, stream)
                │
                ▼
         concurrent UDP stub engine
         (multi-socket, TXID map, resolver rotate, timeout/retry)
                │
                ▼
         classify (NOERROR+answers / NXDOMAIN / error / timeout)
                │
                ▼
         wildcard filter (random probes → fingerprint → drop FPs)
                │
                ▼
         dedup → emit unique names
```

Optional: `--mock-zone` starts an in-process authoritative-style responder so correctness and recall are deterministic offline.

## What we will not claim without bench evidence

- "Best tool in the world" on every public internet domain.
- Beating massdns absolute Linux QPS numbers from a Windows desktop without measurement.
- Exhaustive passive provider parity with subfinder.

Bench report under the goal scratch dir is the ground truth for criterion 5.
