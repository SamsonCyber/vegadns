# Mutation testing (pure modules)

## Scope

```bash
cargo mutants --test-tool=cargo --timeout 35 --jobs 6 \
  --file src/expand.rs --file src/classify.rs --file src/wildcard.rs \
  --file src/dedup.rs --file src/dns_packet.rs
```

Hardening tests: `tests/mutation_kill.rs`, `tests/pure_logic_extra.rs`.

## Results (2026-08-03)

Full pure-module run:

| Metric | Before cleanup | After cleanup |
|---|---|---|
| Tested | 187 | 183 |
| Caught | 128 | **163** |
| Missed | 45 | **5** |
| Unviable | 10 | 10 |
| Timeouts | 4 | 5 |

Kill rate caught/(caught+missed+timeout) ≈ **163/173 ≈ 94%** (was ~74%).

`dns_packet.rs` alone after extra pointer tests: **110 caught, 3 missed, 1 timeout**.

### Remaining missed (documented)

1. `parse_name` pointer `|` → `^` — **equivalent** (`(hi<<8)|lo` == `(hi<<8)^lo` for lo in 0..255).
2. `parse_name` `end > packet.len()` → `==` / `>=` — **equivalent** Truncated timing for complete names.
3. Timeouts (not “survived logic”): hop `+=`→`*=` loop; `parent_chain` arithmetic; fixed `random_probe_label` uniqueness hang.

## Cleanup actions taken

1. Removed dead branch in `expand_label` (equivalent multi-`&&` mutants gone).
2. Asserts on `Deduper::len` / `is_empty` not constants.
3. Full `Rcode` arm coverage.
4. Boundary packets: RR header exact end, CNAME rdata start, non-A 4-byte, compression high pointer, hop limit 16/17, truncated rdata.
5. Empty `Live` address fingerprints rejected.
6. `WildcardFilter::parents` / `is_empty` surface.
