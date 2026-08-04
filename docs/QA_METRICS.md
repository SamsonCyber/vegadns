# vegadns QA metrics (floors + latest run)

Floors are fixed gates. Latest numbers from real tool output (2026-08-03 session).

## Floors (must meet)

| Metric | Floor | Notes |
|---|---|---|
| Unit/integration | all pass | `cargo test --release` |
| Gherkin scenarios | all required pass | `python scripts/gherkin_run.py` |
| Line coverage (`src/` lib) | **≥ 55%** | or honest tool-unavailable + config in-repo + units green |
| Mutation kill rate (pure modules) | **≥ 40%** | expand/classify/wildcard/dedup/dns_packet; or honest unavailable |
| CLI mock fixture | recall=1.0, precision=1.0 | two consecutive runs identical names |

## Intentional coverage exclusions

| Path | Reason |
|---|---|
| `src/main.rs` CLI clap wiring | Thin glue; exercised by Gherkin/CLI QA |
| Full live public-resolver network paths | Non-deterministic; mock path covers enum semantics |

## Latest measured

| Metric | Value | Source |
|---|---|---|
| Unit tests | **PASS** (exit 0; lib+integration+pure_logic_extra+scrutinize) | `unit_tests.log` |
| Gherkin | **PASS** 4/4 scenarios | `gherkin.log` |
| Line coverage | **UNAVAILABLE** (Windows-gnu missing `profiler_builtins`; Kali missing llvm-tools-preview on PATH) | `coverage_unavailable.log` |
| Mutation | **72.3% kill** (128 caught / 177 scored; 45 missed, 4 timeouts, 10 unviable of 187) | `mutation.log` + `mutation_summary.txt` |
| CLI x2 | **PASS** recall=1.000 precision=1.000 both runs | `cli_run1.log`, `cli_run2.log` |

## Commands (see also docs/QA.md)

```bash
cargo test --release
cargo build --release && python scripts/gherkin_run.py
cargo llvm-cov --release --lib --tests --summary-only   # needs profiler runtime
cargo mutants --file src/expand.rs --file src/classify.rs --file src/wildcard.rs --file src/dedup.rs --file src/dns_packet.rs
python scripts/quality_run.py --out ./quality_out --write-metrics
```
