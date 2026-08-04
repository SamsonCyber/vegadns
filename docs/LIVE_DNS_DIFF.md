# Live authorized DNS differential (vegadns vs massdns)

Compares vegadns and massdns on **the same public recursive resolvers** and the same name list.

## Authorization

You must pass `--authorized`. Two modes:

| Mode | What it does | When to use |
|---|---|---|
| `resolve-list` | Fixed public FQDNs (Cloudflare, Google, example.com, …) + NX canaries | Default safe live check |
| `enum` | Label wordlist × `--domain` via public resolvers | Only for a domain **you control** or otherwise authorize |

This is not a public-internet “find all subdomains of strangers” suite.

## Run (Linux/Kali with massdns)

```bash
cargo build --release
python3 scripts/live_dns_diff.py --out /tmp/live_diff --authorized --mode resolve-list
python3 scripts/live_dns_diff.py --out /tmp/live_enum --authorized --mode enum \
  --domain example.com --wordlist fixtures/wordlist_small.txt
```

Resolvers default: `fixtures/resolvers_public.txt` (1.1.1.1, 8.8.8.8, …).

## CLI pieces

- vegadns `--fqdn-list`: wordlist lines are absolute FQDNs (no label×domain expand). Used by resolve-list mode.
- massdns: `-t A -o S` on the same FQDN list.

## Gates

- **resolve-list**: both tools exit 0, Jaccard ≥ 0.5, intersection ≥ 5.
- **enum**: vegadns exit 0; set agreement is informational (wildcard filter can shrink vegadns vs massdns).

## Sample results (Kali, public resolvers)

### resolve-list

- vegadns found 12, massdns found 12, Jaccard **1.0**, only_* empty, PASS.

### enum example.com + wordlist_small

- both found 1 (apex/www-class), Jaccard **1.0**, PASS.

Reports: `live_dns_diff_resolve.txt`, `live_dns_diff_enum.txt`.
