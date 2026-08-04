# vegadns QA procedures

Run from the repo root `C:\code\vegadns` (or `~/vegadns` on Linux).

## 1. Unit and integration tests

```bash
cargo test --release
```

Covers pure modules (expand, classify, dns_packet, wildcard, dedup), mock engine path, multi-run consistency, and stress wordlists. Tests call shipped library functions / mock-enum only.

## 2. Gherkin (Cucumber-style) scenarios

Feature files: `features/*.feature`  
Runner drives the **shipped** `vegadns` binary (`expand` and `enum --mock-zone`).

```bash
cargo build --release
python scripts/gherkin_run.py
```

Required scenarios: expand FQDNs; live vs absent labels; wildcard FP rejection; full fixture recall/precision.

## 3. Line coverage (library)

Requires `cargo-llvm-cov` and `rustup component add llvm-tools-preview`.

```bash
cargo llvm-cov --release --lib --tests --summary-only
cargo llvm-cov --release --lib --tests --lcov --output-path coverage/lcov.info
```

Floor: **≥ 55%** line coverage on measured `src/` library code (see `docs/QA_METRICS.md`). `main.rs` CLI glue is not the coverage focus; mock/engine hot I/O paths may lag pure modules.

## 4. Mutation testing (pure logic)

Requires `cargo-mutants`.

```bash
cargo mutants --test-tool=cargo --timeout 30 --jobs 2 \
  --file 'src/expand.rs' --file 'src/classify.rs' --file 'src/wildcard.rs' --file 'src/dedup.rs' \
  --file 'src/dns_packet.rs'
```

Floor: **≥ 40%** kill rate on those modules, or document tool-unavailable and rely on strengthened unit tests in `tests/pure_logic_extra.rs` and `tests/mutation_kill.rs` (boundary packets, Rcode arms, soft-404/empty FP, dedup len).

## 5. CLI sanity (fixture)

```bash
cargo build --release
./target/release/vegadns enum --mock-zone fixtures/zone_bench.json \
  -w fixtures/wordlist_bench.txt --known-true fixtures/known_true.txt \
  --stats-json /tmp/s1.json -o /tmp/n1.txt -q
# run twice; compare sorted names; require recall=1.0 precision=1.0 in stderr
```

## 6. Optional scrutinize / bench

```bash
python scripts/scrutinize.py --out ./scrutinize_out
python scripts/bench.py --out ./bench_report.txt
```

## Quality pack artifacts

| Artifact | Role |
|---|---|
| `docs/QA.md` | This procedure |
| `docs/QA_METRICS.md` | Floors + latest measured numbers |
| `features/` | Gherkin features |
| `scripts/gherkin_run.py` | Gherkin runner (CLI-backed) |
| `tests/pure_logic_extra.rs` | Mutation-hardening units |
| `tests/scrutinize_consistency.rs` | Multi-run + stress |
| `scripts/quality_run.py` | One-shot local quality pack driver |
