# Recon pipeline: DNS → paths

**vegadns** covers two lanes as separate commands:

| Command | Lane | Job |
|---|---|---|
| `vegadns enum` | DNS | Subdomain brute + resolve + wildcard clean |
| `vegadns paths` | HTTP | Path/content discovery (ferox/ffuf-class) |

## Private lab chain

```bash
# 1) Subdomains (DNS)
vegadns enum -d example.lab -w subs.txt -r resolvers.txt -o hosts.txt

# 2) Optional live HTTP check (external: httpx)

# 3) Paths on a known base URL (HTTP)
vegadns paths -u http://127.0.0.1:18080/ -w paths.txt -o hits.txt --status 200,301,302,401,403
```

## Fixture / offline paths suite

```bash
vegadns paths \
  --mock-paths fixtures/paths/hit_paths.txt \
  -w fixtures/paths/wordlist.txt \
  --known-true fixtures/paths/known_true.txt \
  -o path_hits.txt \
  --status 200 \
  --concurrency 20
```

Embedded mock HTTP serves only paths listed in `hit_paths.txt` as 200; everything else 404.

## Scope

Path brute is for **authorized** targets / private lab only. Same rule as DNS enum.
