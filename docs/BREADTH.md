# Subdomain breadth (vs adjacent tools)

## Goal

Find **more real subdomains** per authorized target than resolve-only peers that only chew the wordlist you hand them. Speed and wildcard precision stay the other gates (`docs/DISCOVERY_COVERAGE.md`).

## Depth ladder (modular fast → final)

One flag picks list size and optional auto-permute. Main UX for scan depth.

| Depth | Flag | List pack | ~labels | Auto-permute |
|---|---|---|---|---|
| 1 | `--depth fast` / `-D 1` | `tiny` | ~270 | off |
| 2 | `--depth normal` / `-D 2` | `small` | ~620 | off |
| 3 | `--depth deep` / `-D 3` | `medium` | ~5k | off |
| 4 | `--depth deeper` / `-D 4` | `large` | ~20k | off |
| 5 | `--depth final` / `-D 5` | `final` | ~65k | on (top 300 seeds × alter, cap 250k) |

`final` is the super-long end of the ladder: SecLists 20k + n0kovo tiny + boost, then bounded altdns-class mutate of the top seeds so candidates do not explode unbounded.

Use `--no-permute` on final for max list only. Override knobs with `--permute-numbers`, `--permute-max`, `--permute-seed-cap`.

## What peers do

| Tool | Breadth built-in? | Notes |
|---|---|---|
| massdns / dnsx / gobuster dns | No | Resolve whatever FQDN list you feed |
| puredns / shuffledns | No list/permute | Accuracy + massdns; still external lists |
| altdns / gotator / alterx | Permute only | Separate binary; no resolve/wildcard pack |
| subfinder / amass passive | Passive only | Different lane (API/CT), not active brute |

Typical wild stack:

```
subfinder → alterx/gotator → puredns+massdns → dnsx
```

**vegadns packs active brute + depth ladder + altdns-class permute + wildcard filter in one binary.**

## Other breadth surfaces

1. **Presets** (`tiny` / `small` / `medium` / `large` / `final` / `alter`) — same packs as depth
2. **Multi-source merge** — `--depth` + `-w` / `--preset`; depth list first
3. **`permute` command** — seeds × alter words
4. **`enum --permute` / `--no-permute`** — force on/off over depth defaults
5. **Optional cache** — `scripts/fetch_wordlists.py --cache-extra`

## CLI

```bash
vegadns wordlist list
vegadns wordlist emit final -o wl.txt

vegadns enum -d example.com -D fast -r resolvers.txt -o hits.txt
vegadns enum -d example.com --depth deep -r resolvers.txt -o hits.txt
vegadns enum -d example.com --depth final -r resolvers.txt -o hits.txt
vegadns enum -d example.com -D final --no-permute -r resolvers.txt

vegadns enum -d example.com -D normal -w my_org.txt -r resolvers.txt

vegadns permute -i known.txt -w alter -d example.com -o muts.txt
```

## Honesty

- Breadth is not "most assets on the public internet." That needs passive sources + APIs.
- Lab F1 gates still use fixed fixtures and fair peer wordlists.
- Live wins come from better candidate generation and precision (wildcard filter). Peers that dump wildcard noise lose F1 even when raw hit count is higher.
