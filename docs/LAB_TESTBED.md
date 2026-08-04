# vegadns lab testbed (private only)

**Scope:** authorized private lab / localhost only (`127.0.0.1`, `192.168.1.0/24` when you own the hosts).  
Do **not** point this suite at public internet domains you do not control.

## What it is

| Asset | Role |
|---|---|
| `fixtures/lab/zone_lab.json` | Large mock zone: hundreds of live A records + intentional wildcards |
| `fixtures/lab/known_true_lab.txt` | Oracle live FQDNs |
| `fixtures/lab/wordlist_lab.txt` | Large label list (known + garbage + wildcard children) |
| `fixtures/lab/urls_lab.txt` | Companion URL inventory (`https://fqdn/`) for optional HTTP dummy |
| `scripts/gen_lab_fixtures.py` | Regenerates fixtures |
| `scripts/lab_suite.py` | Rigorous multi-run suite (real `vegadns` CLI) |
| `scripts/lab_http_dummy.py` | Optional tiny HTTP 200/404 by Host header |
| `vegadns mock-serve` | Standalone UDP DNS for network-path tests |

vegadns discovers **DNS names**, not full web crawls. “Ton of URLs” here means a large FQDN/URL inventory derived from known-true subdomains, plus optional dummy HTTP.

## Generate fixtures

```bash
cd C:\code\vegadns   # or ~/vegadns
python scripts/gen_lab_fixtures.py --known 500 --wordlist 25000
```

## Local high-speed run (embedded mock)

```bash
cargo build --release
python scripts/lab_suite.py --out ./lab_out
```

This uses `--mock-zone fixtures/lab/zone_lab.json` inside vegadns (no external DNS process).

## Standalone mock DNS (localhost or Kali)

### Localhost

```bash
# terminal A
./target/release/vegadns mock-serve --zone fixtures/lab/zone_lab.json --bind 127.0.0.1:53535

# terminal B
python scripts/lab_suite.py --out ./lab_out --resolver 127.0.0.1:53535
```

### Kali (example free host: 192.168.1.183)

```bash
# on Kali (after syncing vegadns release binary + fixtures/lab)
./vegadns mock-serve --zone fixtures/lab/zone_lab.json --bind 0.0.0.0:53535
# only bind on private LAN; firewall if needed

# from desktop (private path)
python scripts/lab_suite.py --out ./lab_out --resolver 192.168.1.183:53535
```

Stop: Ctrl+C the mock-serve process.

## Optional dummy HTTP

```bash
python scripts/lab_http_dummy.py --hosts-file fixtures/lab/known_true_lab.txt --bind 127.0.0.1 --port 18080
# Host: www.lab.test  -> 200 lab-ok
```

## Capacity notes

| Host | Role | Notes |
|---|---|---|
| Desktop (Windows) | Build + suite + embedded mock | Default gate |
| Kali 192.168.1.183 | Optional remote mock-serve | ~8GB RAM free enough for DNS mock |
| Pi 192.168.1.170 | Optional | Skip if down / no capacity |
| Jetson / NUC | Optional | Same: private mock-serve only |

Concurrency default in suite is capped (4000). Lower it if the lab DNS host is weak:

```bash
python scripts/lab_suite.py --out ./lab_out --concurrency 1000
```

## Pass criteria (suite)

- Real `vegadns` exit 0 on each rigorous run  
- Recall = 1.0 on `known_true_lab.txt`  
- Precision = 1.0 (no junk*, no `*.wild` / `*.cdn-edge` flood)  
- Identical sorted primary name sets across runs  
- Metrics written to `lab_suite_metrics.txt`
