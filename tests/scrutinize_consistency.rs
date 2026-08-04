//! Scrutinizing integration tests: multi-run consistency + stress via shipped engine.

use std::time::Duration;

use vegadns::engine::{load_wordlist, precision, recall, run_enum_with_mock, EngineConfig};
use vegadns::mock_dns::MockZone;

fn cfg() -> EngineConfig {
    EngineConfig {
        domain: "bench.test".into(),
        concurrency: 2000,
        timeout: Duration::from_millis(200),
        retries: 2,
        sockets: 1,
        wildcard_probes: 2,
        quiet: true,
        fqdn_list: false,
    }
}

#[tokio::test]
async fn three_runs_identical_names_recall_prec() {
    let zone_path = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/zone_bench.json");
    let wl_path = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/wordlist_bench.txt");
    let known_path = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/known_true.txt");
    let known = load_wordlist(known_path).unwrap();
    let words = load_wordlist(wl_path).unwrap();

    let mut sets = Vec::new();
    for _ in 0..3 {
        let zone = MockZone::from_path(zone_path).unwrap();
        let (result, _) = run_enum_with_mock(cfg(), &words, zone).await.unwrap();
        assert_eq!(recall(&result.names, &known), 1.0);
        assert_eq!(precision(&result.names, &known), 1.0);
        assert_eq!(result.names.len(), known.len());
        assert!(!result.names.iter().any(|n| n.contains(".wild.")));
        let mut sorted = result.names.clone();
        sorted.sort();
        sets.push(sorted);
    }
    assert_eq!(sets[0], sets[1]);
    assert_eq!(sets[1], sets[2]);
}

#[tokio::test]
async fn stress_wordlist_keeps_prec_and_recall() {
    let zone_path = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/zone_bench.json");
    let wl_path = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/wordlist_bench.txt");
    let known_path = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/known_true.txt");
    let known = load_wordlist(known_path).unwrap();
    let mut words = load_wordlist(wl_path).unwrap();
    // adversarial extras: garbage + many wildcard children
    for i in 0..100 {
        words.push(format!("junk{i}"));
        words.push(format!("w{i}.wild"));
    }
    words.extend([
        "nope".into(),
        "garbage999".into(),
        "foo.wild".into(),
        "bar.wild".into(),
    ]);

    let zone = MockZone::from_path(zone_path).unwrap();
    let (result, _) = run_enum_with_mock(cfg(), &words, zone).await.unwrap();
    assert_eq!(
        recall(&result.names, &known),
        1.0,
        "missing true names: {:?}",
        result.names
    );
    assert_eq!(
        precision(&result.names, &known),
        1.0,
        "false positives: {:?}",
        result.names
    );
    assert!(!result.names.iter().any(|n| n.contains(".wild.")));
}
