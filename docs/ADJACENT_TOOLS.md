# vegadns vs peers and adjacent tools

## Lanes (do not mix)

| Lane | Job | Tools |
|---|---|---|
| **DNS brute / resolve** | Wordlist → live subdomains | **vegadns**, massdns, gobuster dns, dnsx, puredns/shuffledns |
| **Breadth / permute** | Candidate generation | **vegadns** (`--preset`, `permute`), altdns, gotator, alterx |
| **Passive OSINT** | APIs/CT → names (no wordlist brute) | subfinder, amass (passive) |
| **HTTP next hop** | Paths on known hosts | feroxbuster, ffuf, gobuster dir, httpx |

**vegadns is lane 1 + built-in breadth (lane 2).** feroxbuster is lane 3. See `docs/BREADTH.md`.

```
vegadns  →  httpx  →  feroxbuster/ffuf
 (subs)     (alive)     (content)
```

## Run the comparison (private lab)

On a host with the tools (Kali recommended for ferox/ffuf/gobuster/massdns):

```bash
cd ~/vegadns   # or C:\code\vegadns
cargo build --release
python scripts/gen_lab_fixtures.py --known 500 --wordlist 25000

# Full dual-lane suite (preferred): DNS brute + HTTP hard soft-404
python scripts/full_peer_suite.py --out ./full_suite --wordlist-cap 5000
cat full_suite/full_suite_report.txt

# DNS-only adjacent compare (legacy)
python scripts/compare_adjacents.py --out ./compare_out --wordlist-cap 5000
```

Fair DNS head-to-head uses the same capped wordlist + shared `mock-serve` resolver.
Fair HTTP uses `fixtures/paths/*_hard*` soft-404 suite for all tools.

## What “best” means here

- **DNS lane:** lower wall among timed tools; also rank by F1 (recall/precision on known-true).
- **HTTP lane:** separate ranking; soft-404 noise lowers precision for status-only tools.
- **Never** one overall fastest across DNS + HTTP + passive.
- Efficiency: `rate ≈ candidates / wall_s` (throughput), secondary to R/P.

Fairness tests: `python -m pytest tests/test_full_peer_suite.py -v`.
