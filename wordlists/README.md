# DNS wordlist packs (breadth)

Built-in presets ship inside the binary via `include_str!`. Files here are the source of truth.

| File | Preset / depth | Role |
|---|---|---|
| `dns_tiny.txt` | `tiny` / depth 1 fast | High-value modern + top ranks (~270) |
| `dns_small.txt` | `small` / depth 2 normal | Light brute (~620) |
| `dns_medium.txt` | `medium` / depth 3 deep | Breadth pack (~5k) |
| `dns_large.txt` | `large` / depth 4 deeper | SecLists 20k + boost (~20k) |
| `dns_final.txt` | `final` / depth 5 final | 20k + n0kovo tiny (~65k) |
| `alter_words.txt` | `alter` | altdns-class permutation dictionary |
| `seclists_top5k.txt` | (raw) | rebuild input |
| `seclists_top20k.txt` | (raw) | rebuild input |
| `n0kovo_tiny.txt` | (raw) | rebuild input for final |

## Sources (Chrome bookmarks + field research)

1. **SecLists Discovery/DNS** — `subdomains-top1million-5000.txt` (rank list backbone for medium).
2. **altdns `words.txt`** — alter dictionary for prefix/suffix/number mutations.
3. **Boost labels** — modern stack names often late or missing in rank lists (`k8s`, `sso`, `oauth`, `grafana`, `minio`, …).
4. **Optional pull** — `scripts/fetch_wordlists.py` can refresh SecLists/trickest/assetnote-style lists into `wordlists/cache/` (not embedded; merge with `-w`).

Bookmarks that informed this pack: SecLists, trickest/wordlists, Bug-Bounty-Wordlists, altdns, bitquark/dnspop, Kali wordlists page.

## Rebuild tiers

```bash
python scripts/fetch_wordlists.py --rebuild-tiers
```

## License note

SecLists and altdns content remain under their upstream licenses. We ship a snapshot for offline/reproducible breadth, not a full SecLists mirror.
