# HTB lab + unbiased multi-tool DNS bench

Authorized **HackTheBox Dedicated/VIP** control and a **fair** multi-tool enum race.

## Claim bounds

Every bench report embeds:

- **Proves:** wall / recall / precision / F1 / efficiency on this harness under a declared mode.
- **Does not prove:** fastest on the market, public-internet massdns QPS supremacy, full puredns+massdns replacement, passive OSINT coverage.

## HTB token

```text
%USERPROFILE%\.secrets\htb_api_token.txt
```

One line JWT (no `Bearer ` prefix). Subscription gates lab spawn.

```bash
python scripts/htb_lab.py user
python scripts/htb_lab.py list --per-page 20
python scripts/htb_lab.py spawn --name <box> --target-out ./htb_target.json
python scripts/htb_lab.py active
python scripts/htb_lab.py status
```

Spawn uses `POST /api/v4/vm/spawn` with `machine_id`.  
**Active instance is not the same as reachable from this host** — you need the HTB VPN (OpenVPN / HTB Connect) for traffic to `10.129.x.x`.

## Unbiased tool bench

Same candidates + same resolvers + same oracle for every tool.

```bash
# Stress gym (latency/SERVFAIL/drop) + efficiency metrics
python scripts/unbiased_tool_bench.py --mode gym-stress --out ./bench_out \
  --wordlist-cap 5000 --sockets 4

# Clean regression
python scripts/unbiased_tool_bench.py --mode gym-clean --out ./bench_out_clean

# Attach HTB target metadata (still fair gym DNS core)
python scripts/unbiased_tool_bench.py --mode htb-target \
  --htb-target gym_out/htb_target.json --out ./bench_htb
```

Metrics per timed tool:

| Metric | Meaning |
|---|---|
| wall_s | Wall clock |
| recall | Known-true hit rate |
| precision / F1 | Noise / cleanliness |
| candidates_per_sec | Suite size / wall |
| found_per_sec | Hits / wall |
| efficiency_score | F1 × (candidates/s) / 1000 |

Peers timed when installed (must be **real** binaries, not stubs):

| Tool | Role | Backend |
|---|---|---|
| vegadns | native enum + wildcard filter | — |
| massdns | raw bulk resolve (C, blechschmidt) | — |
| puredns | resolve + wildcard/poison filter | **requires massdns** |
| shuffledns | PD resolve/bruteforce wrapper | **requires massdns** |
| dnsx | PD multi-probe DNS toolkit | — |
| gobuster dns | Go DNS mode | — |

### Install real peers (Linux / Kali / WSL)

```bash
bash scripts/install_bench_peers.sh
# verifies with file(1): massdns ELF, puredns/shuffledns Go bins
```

**Windows note:** massdns is Linux-first (needs `sys/socket.h`). Run the full peer race on **Kali** (or WSL with massdns installed). Native Windows already has dnsx/puredns/shuffledns/gobuster from `go install`, but puredns/shuffledns stay **not comparable** until massdns is on PATH.

Verified Kali sample report: `docs/peer_bench_kali.txt`.

## Optimization note

Later campaign re-measured sockets=1 as default under mild stress (sockets=4 was not always faster). See `docs/OPTIMIZATION_BREAKTHROUGHS.md`.

## VPN note

If `ping 10.129.x.x` fails from the runner, spawn still counts for control-plane success. Live accuracy against the box needs HTB VPN on that host.
