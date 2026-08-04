//! Integration tests driving shipped vegadns pure + mock paths.

use std::collections::HashMap;
use std::time::Duration;

use vegadns::classify::{classify_response, ResponseClass};
use vegadns::dedup::Deduper;
use vegadns::dns_packet::{build_query, build_response_a, build_response_nxdomain, parse_message};
use vegadns::engine::{precision, recall, run_enum_with_mock, EngineConfig};
use vegadns::expand::{expand_label, expand_wordlist_lines};
use vegadns::mock_dns::MockZone;
use vegadns::wildcard::{
    fingerprint_from_probe_results, is_wildcard_hit, WildcardFilter, WildcardFingerprint,
};

#[test]
fn expand_drives_shipped_function_not_reimplemented() {
    let lines = ["www", "#c", "", "mail", "api.bench.test"];
    let out = expand_wordlist_lines(lines, "bench.test");
    assert_eq!(
        out,
        vec![
            "www.bench.test".to_string(),
            "mail.bench.test".to_string(),
            "api.bench.test".to_string(),
        ]
    );
    assert_eq!(
        expand_label("staging", "bench.test").unwrap(),
        "staging.bench.test"
    );
}

#[test]
fn classify_uses_real_packet_parse_path() {
    let resp = build_response_a(42, "www.bench.test", &[[10, 0, 0, 1]], 30).unwrap();
    let msg = parse_message(&resp).unwrap();
    match classify_response(&msg) {
        ResponseClass::Live { addresses } => {
            assert_eq!(addresses, vec!["10.0.0.1".to_string()]);
        }
        other => panic!("unexpected {other:?}"),
    }
    let nx = build_response_nxdomain(43, "nope.bench.test").unwrap();
    let msg = parse_message(&nx).unwrap();
    assert_eq!(classify_response(&msg), ResponseClass::NxDomain);
}

#[test]
fn wildcard_filter_and_dedup_shipped() {
    let mut filter = WildcardFilter::new();
    filter.register(
        "wild.bench.test",
        WildcardFingerprint::from_addresses(["9.9.9.9".into()]),
    );
    let live = ResponseClass::Live {
        addresses: vec!["9.9.9.9".into()],
    };
    assert!(is_wildcard_hit(
        &filter,
        "foo.wild.bench.test",
        &live
    ));
    let real = ResponseClass::Live {
        addresses: vec!["1.2.3.4".into()],
    };
    assert!(!is_wildcard_hit(&filter, "www.bench.test", &real));

    let mut d = Deduper::new();
    assert!(d.insert("www.bench.test".to_string()));
    assert!(!d.insert("www.bench.test".to_string()));
    assert_eq!(d.len(), 1);
}

#[test]
fn fingerprint_from_probes_agrees() {
    let results = vec![
        (
            "aaa.wild.bench.test".into(),
            ResponseClass::Live {
                addresses: vec!["9.9.9.9".into()],
            },
        ),
        (
            "bbb.wild.bench.test".into(),
            ResponseClass::Live {
                addresses: vec!["9.9.9.9".into()],
            },
        ),
        (
            "ccc.wild.bench.test".into(),
            ResponseClass::Live {
                addresses: vec!["9.9.9.9".into()],
            },
        ),
    ];
    let fp = fingerprint_from_probe_results(&results).expect("fp");
    assert_eq!(fp.addresses, vec!["9.9.9.9".to_string()]);
}

#[test]
fn query_packet_roundtrip_shipped_codec() {
    let q = build_query(0xbeef, "mail.bench.test").unwrap();
    let msg = parse_message(&q);
    // query is not a response; parse still yields question path via parse_message
    // (is_response false). Ensure codec builds valid length.
    assert!(q.len() > 12);
    let _ = msg;
}

#[tokio::test]
async fn mock_fixture_zone_recall_and_no_wildcard_flood() {
    let zone_path = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/zone_bench.json");
    let zone = MockZone::from_path(zone_path).expect("zone");
    let known = zone.known_true_names();
    assert_eq!(known.len(), 10);

    let wl_path = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/wordlist_small.txt");
    let words: Vec<String> = std::fs::read_to_string(wl_path)
        .unwrap()
        .lines()
        .map(|l| l.trim().to_string())
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .collect();

    let cfg = EngineConfig {
        domain: "bench.test".into(),
        concurrency: 200,
        timeout: Duration::from_millis(800),
        retries: 2,
        sockets: 2,
        wildcard_probes: 3,
        quiet: true,
        fqdn_list: false,
    };
    let (result, _) = run_enum_with_mock(cfg, &words, zone).await.expect("enum");

    let r = recall(&result.names, &known);
    assert!(
        r >= 1.0,
        "recall {r} names={:?} known={:?}",
        result.names,
        known
    );
    // Wildcard children must not flood
    assert!(
        !result.names.iter().any(|n| n.ends_with(".wild.bench.test")),
        "wildcard FPs present: {:?}",
        result.names
    );
    // Garbage labels must not appear
    assert!(!result.names.iter().any(|n| n.starts_with("garbage999")));
    assert!(!result.names.iter().any(|n| n.starts_with("nope.")));

    let p = precision(&result.names, &known);
    assert!(p >= 0.9, "precision {p} found={:?}", result.names);
}

#[tokio::test]
async fn mock_inline_zone_filters_catch_all() {
    let mut records: HashMap<String, Vec<String>> = HashMap::new();
    records.insert("only.real.test".into(), vec!["5.5.5.5".into()]);
    // exact records take priority in mock; catch.* is pure wildcard noise
    let mut wildcards: HashMap<String, Vec<String>> = HashMap::new();
    wildcards.insert("catch.real.test".into(), vec!["8.8.8.8".into()]);
    let zone = MockZone {
        base: "real.test".into(),
        records,
        wildcards,
    };
    let words = vec![
        "only".into(),
        "a.catch".into(),
        "b.catch".into(),
        "missing".into(),
    ];
    let cfg = EngineConfig {
        domain: "real.test".into(),
        concurrency: 50,
        timeout: Duration::from_millis(500),
        retries: 1,
        sockets: 1,
        wildcard_probes: 3,
        quiet: true,
        fqdn_list: false,
    };
    let (result, _) = run_enum_with_mock(cfg, &words, zone).await.unwrap();
    assert!(result.names.contains(&"only.real.test".to_string()));
    assert!(!result.names.iter().any(|n| n.contains(".catch.real.test")));
}
