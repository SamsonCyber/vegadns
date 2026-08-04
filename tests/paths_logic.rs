//! Path discovery pure units + mock HTTP integration (shipped code).

use std::time::Duration;

use vegadns::dedup::Deduper;
use vegadns::engine::load_wordlist;
use std::collections::HashMap;

use vegadns::mock_http::{load_hit_paths, MockHttp, MockHttpZone, PathBehavior};
use vegadns::path_classify::{classify_status, is_hit, parse_status_list, PathClass};
use vegadns::path_join::{expand_paths, join_url, normalize_url_key};
use vegadns::paths_engine::{
    path_f1, path_precision, path_recall, rewrite_port_template, run_paths, PathsConfig,
};

#[test]
fn join_and_expand_shipped() {
    let u = join_url("http://127.0.0.1:9", "admin").unwrap();
    assert_eq!(u, "http://127.0.0.1:9/admin");
    let words = vec!["api".into(), "#c".into(), "login".into()];
    let exp = expand_paths("http://h", &words);
    assert_eq!(exp, vec!["http://h/api".to_string(), "http://h/login".to_string()]);
}

#[test]
fn classify_and_dedup_shipped() {
    assert!(is_hit(&classify_status(200, &[200, 403])));
    assert!(!is_hit(&classify_status(404, &[200, 403])));
    assert_eq!(parse_status_list("200,404").unwrap(), vec![200, 404]);
    let mut d = Deduper::new();
    assert!(d.insert(normalize_url_key("http://x/a/")));
    assert!(!d.insert(normalize_url_key("http://x/a")));
}

#[tokio::test]
async fn mock_paths_recall_precision() {
    let root = env!("CARGO_MANIFEST_DIR");
    let hits = load_hit_paths(format!("{root}/fixtures/paths/hit_paths.txt")).unwrap();
    let mock = MockHttp::spawn(hits).await.unwrap();
    let port = mock.addr.port();
    let base = mock.base_url();
    let words = load_wordlist(format!("{root}/fixtures/paths/wordlist.txt")).unwrap();
    let kt_raw = load_wordlist(format!("{root}/fixtures/paths/known_true.txt")).unwrap();
    let known = rewrite_port_template(&kt_raw, port);

    let cfg = PathsConfig {
        base_url: base,
        concurrency: 16,
        timeout: Duration::from_secs(5),
        match_codes: vec![200],
        quiet: true,
        soft404_probes: 0,
        retries: 2,
    };
    let result = run_paths(cfg, &words).await.unwrap();
    mock.shutdown().await;

    let r = path_recall(&result.urls, &known);
    let p = path_precision(&result.urls, &known);
    assert!(
        r >= 1.0 - 1e-9,
        "recall {r} urls={:?} known={:?}",
        result.urls,
        known
    );
    assert!(
        p >= 1.0 - 1e-9,
        "precision {p} urls={:?}",
        result.urls
    );
    assert!(!result.urls.iter().any(|u| u.contains("notreal-junk")));
    assert!(matches!(
        classify_status(404, &[200]),
        PathClass::Miss { status: 404 }
    ));
}

#[tokio::test]
async fn hard_soft404_suite_perfect_f1() {
    let mut map = HashMap::new();
    for p in ["admin", "api", "login", "secret", "internal"] {
        let beh = if p == "secret" {
            PathBehavior::Hit401
        } else if p == "internal" {
            PathBehavior::Hit403
        } else {
            PathBehavior::Hit200
        };
        map.insert(p.to_string(), beh);
    }
    let mock = MockHttp::spawn_zone(MockHttpZone::hard_from_map(map))
        .await
        .unwrap();
    let port = mock.addr.port();
    let base = mock.base_url();
    let words = vec![
        "admin".into(),
        "api".into(),
        "login".into(),
        "secret".into(),
        "internal".into(),
        "noise-page-001".into(),
        "noise-page-002".into(),
        "fake-admin".into(),
        "does-not-exist-1".into(),
    ];
    let known = vec![
        format!("http://127.0.0.1:{port}/admin"),
        format!("http://127.0.0.1:{port}/api"),
        format!("http://127.0.0.1:{port}/login"),
        format!("http://127.0.0.1:{port}/secret"),
        format!("http://127.0.0.1:{port}/internal"),
    ];
    let cfg = PathsConfig {
        base_url: base,
        concurrency: 16,
        timeout: Duration::from_secs(5),
        match_codes: vec![200, 401, 403],
        quiet: true,
        soft404_probes: 8,
        retries: 2,
    };
    let result = run_paths(cfg, &words).await.unwrap();
    mock.shutdown().await;

    let r = path_recall(&result.urls, &known);
    let p = path_precision(&result.urls, &known);
    let f = path_f1(&result.urls, &known);
    assert!((r - 1.0).abs() < 1e-9, "recall {r} urls={:?}", result.urls);
    assert!((p - 1.0).abs() < 1e-9, "precision {p} urls={:?}", result.urls);
    assert!((f - 1.0).abs() < 1e-9, "f1 {f}");
    assert!(result.stats.soft404_dropped >= 3);
    assert!(!result.urls.iter().any(|u| u.contains("noise") || u.contains("fake")));
}
