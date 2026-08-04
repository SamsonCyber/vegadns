# Discovery coverage (known-true quality)

**Coverage** here means: **F1 from known-true recall and precision** on fixed private lab suites.

It is **not** code line coverage and **not** “every asset on the public internet.”

## Win condition (surpass)

1. Recall = 1.0 on the known-true oracle.
2. Precision = 1.0 (no wildcard / soft-404 noise as hits).
3. F1 **strictly greater** than every timed peer on the same suite and wordlist.

Tie on recall alone is not enough. Peers that dump noise lose on precision and F1.

## Lanes

| Lane | Tool | Oracle | Peer set |
|---|---|---|---|
| DNS | `vegadns enum` | `fixtures/lab/known_true_lab.txt` (500) | massdns, gobuster dns, dnsx |
| HTTP paths | `vegadns paths` | `fixtures/paths/known_true_hard.txt` (24) | feroxbuster, ffuf, gobuster dir |

### DNS hard factors

- Lab zone includes wildcard parents.
- massdns / dnsx keep wildcard answers → lower precision.
- vegadns wildcard filter → precision 1.0 at full recall.

### Paths hard suite

- Soft-404: missing paths return **HTTP 200** with a fixed body.
- Real hits: 200 with a different body; 401 / 403 auth walls also count.
- vegadns: random soft-404 probes → status+length fingerprint → drop noise.
- Peers matching status only keep soft-404 200s → lower precision / F1.

Fixtures:

- `fixtures/paths/hard_zone.txt` — path + status
- `fixtures/paths/wordlist_hard.txt` — hits + soft-404 bait
- `fixtures/paths/known_true_hard.txt` — oracle URLs (`PORT` placeholder)

## Run

```bash
cargo build --release
python scripts/coverage_surpass.py --out ./coverage_out --wordlist-cap 8000
```

Outputs: `coverage_dns.txt`, `coverage_paths.txt`, `coverage_surpass.txt` (+ JSON).

## Measured results (private lab)

### Windows (dnsx present; HTTP peers not installed)

| Lane | vegadns F1 | Best peer F1 | Gate |
|---|---|---|---|
| DNS | 1.000 | dnsx 0.714 | PASS (strict) |
| Paths | 1.000 | (solo) | PASS |

### Kali (all peers present)

| Lane | vegadns | Peer F1 (timed) | Gate |
|---|---|---|---|
| DNS | R=1.0 P=1.0 F1=1.0 | massdns F1≈0.82 (noise) | PASS |
| Paths | R=1.0 P=1.0 F1=1.0 | ferox≈0.57, ffuf≈0.58 (soft-404 noise) | PASS |

See `coverage_*_kali.txt` for full peer tables from the last Kali run.

## CLI knobs (paths)

| Flag | Role |
|---|---|
| `--soft404-probes N` | Calibrate soft-404 fingerprints (0 = off) |
| `--status 200,401,403` | Status match list |
| `--retries N` | Transport retries |
| `--mock-hard-zone FILE` | Embedded soft-404 mock from hard zone file |

## Honesty bounds

- Fixed private mocks / lab only.
- Same wordlist and resolver / base URL for peer head-to-heads.
- Live public-internet “most assets ever” is out of scope for this gate.
