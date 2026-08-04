//! Large lab fixture path: shipped engine against fixtures/lab zone.

use std::time::Duration;

use vegadns::engine::{load_wordlist, precision, recall, run_enum_with_mock, EngineConfig};
use vegadns::mock_dns::MockZone;

fn lab_paths() -> Option<(String, String, String)> {
    let root = env!("CARGO_MANIFEST_DIR");
    let zone = format!("{root}/fixtures/lab/zone_lab.json");
    let wl = format!("{root}/fixtures/lab/wordlist_lab.txt");
    let known = format!("{root}/fixtures/lab/known_true_lab.txt");
    if std::path::Path::new(&zone).exists()
        && std::path::Path::new(&wl).exists()
        && std::path::Path::new(&known).exists()
    {
        Some((zone, wl, known))
    } else {
        None
    }
}

#[tokio::test]
async fn lab_fixture_recall_precision_and_volume() {
    let Some((zone_path, wl_path, known_path)) = lab_paths() else {
        eprintln!("fixtures/lab missing; run python scripts/gen_lab_fixtures.py");
        return;
    };
    let zone = MockZone::from_path(&zone_path).expect("zone");
    let known = load_wordlist(&known_path).expect("known");
    let words = load_wordlist(&wl_path).expect("wl");
    assert!(
        known.len() >= 100,
        "lab known_true too small: {}",
        known.len()
    );
    assert!(
        words.len() >= 5000,
        "lab wordlist too small: {}",
        words.len()
    );

    let cfg = EngineConfig {
        domain: zone.base.clone(),
        concurrency: 4000,
        timeout: Duration::from_millis(300),
        retries: 2,
        sockets: 2,
        wildcard_probes: 2,
        quiet: true,
        fqdn_list: false,
    };
    let (result, _) = run_enum_with_mock(cfg, &words, zone).await.expect("enum");
    let r = recall(&result.names, &known);
    let p = precision(&result.names, &known);
    assert_eq!(r, 1.0, "recall fail found={}", result.names.len());
    assert_eq!(p, 1.0, "precision fail {:?}", result.names.iter().take(20));
    assert_eq!(result.names.len(), known.len());
    assert!(!result.names.iter().any(|n| n.contains(".wild.")));
    assert!(!result.names.iter().any(|n| n.contains(".cdn-edge.")));
    assert!(!result.names.iter().any(|n| n.contains("junk")));
}

#[tokio::test]
async fn lab_two_runs_identical() {
    let Some((zone_path, wl_path, known_path)) = lab_paths() else {
        return;
    };
    let known = load_wordlist(&known_path).unwrap();
    let words = load_wordlist(&wl_path).unwrap();
    let mut sets = Vec::new();
    for _ in 0..2 {
        let zone = MockZone::from_path(&zone_path).unwrap();
        let cfg = EngineConfig {
            domain: zone.base.clone(),
            concurrency: 4000,
            timeout: Duration::from_millis(300),
            retries: 2,
            sockets: 2,
            wildcard_probes: 2,
            quiet: true,
            fqdn_list: false,
        };
        let (result, _) = run_enum_with_mock(cfg, &words, zone).await.unwrap();
        assert_eq!(recall(&result.names, &known), 1.0);
        assert_eq!(precision(&result.names, &known), 1.0);
        let mut s = result.names;
        s.sort();
        sets.push(s);
    }
    assert_eq!(sets[0], sets[1]);
}
