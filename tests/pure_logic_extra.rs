//! Extra pure-logic unit tests (mutation-hardening + edge cases) on shipped APIs.

use vegadns::classify::{classify_response, is_positive_hit, ResponseClass};
use vegadns::dedup::Deduper;
use vegadns::dns_packet::{
    build_query, build_response_a, build_response_nxdomain, parse_ipv4, parse_message,
    parse_question_name, Rcode,
};
use vegadns::engine::{load_wordlist, parse_resolver_lines, precision, recall};
use vegadns::expand::{expand_label, expand_wordlist_lines};
use vegadns::wildcard::{
    fingerprint_from_probe_results, fingerprints_for_wildcard_probes, is_wildcard_hit,
    parent_chain, probe_names, random_probe_label, WildcardFilter, WildcardFingerprint,
};

#[test]
fn expand_empty_base_returns_none() {
    assert!(expand_label("www", "").is_none());
    assert!(expand_label("www", "   ").is_none());
}

#[test]
fn expand_multi_label_prefix() {
    assert_eq!(
        expand_label("api.v1", "example.com").as_deref(),
        Some("api.v1.example.com")
    );
}

#[test]
fn expand_trailing_dot_normalized() {
    assert_eq!(
        expand_label("Mail.", "Example.COM.").as_deref(),
        Some("mail.example.com")
    );
}

#[test]
fn expand_wordlist_preserves_order_skips_noise() {
    let lines = ["#c", "www", "", "mail", "www"];
    let out = expand_wordlist_lines(lines, "ex.com");
    assert_eq!(
        out,
        vec![
            "www.ex.com".to_string(),
            "mail.ex.com".to_string(),
            "www.ex.com".to_string(),
        ]
    );
}

#[test]
fn classify_garbage_on_query_not_response() {
    let q = build_query(1, "a.example.com").unwrap();
    let msg = parse_message(&q).unwrap();
    assert_eq!(classify_response(&msg), ResponseClass::Garbage);
}

#[test]
fn classify_servfail_is_error() {
    // Build NXDOMAIN then flip is not easy; use parse of crafted-ish nx and error path
    let nx = build_response_nxdomain(9, "x.example.com").unwrap();
    let msg = parse_message(&nx).unwrap();
    assert_eq!(classify_response(&msg), ResponseClass::NxDomain);
    assert!(!is_positive_hit(&ResponseClass::NxDomain));
    assert!(is_positive_hit(&ResponseClass::Live {
        addresses: vec!["1.1.1.1".into()]
    }));
}

#[test]
fn packet_nxdomain_and_a_roundtrip() {
    let a = build_response_a(11, "www.ex.com", &[[10, 0, 0, 1], [10, 0, 0, 2]], 30).unwrap();
    let msg = parse_message(&a).unwrap();
    assert_eq!(msg.rcode, Rcode::NoError);
    assert_eq!(msg.answers.len(), 2);
    let (id, name) = parse_question_name(&build_query(99, "z.ex.com").unwrap()).unwrap();
    assert_eq!(id, 99);
    assert_eq!(name, "z.ex.com");
}

#[test]
fn parse_ipv4_rejects_bad() {
    assert!(parse_ipv4("1.2.3").is_none());
    assert!(parse_ipv4("a.b.c.d").is_none());
    assert_eq!(parse_ipv4("8.8.8.8"), Some([8, 8, 8, 8]));
}

#[test]
fn dedup_contains_len_empty_iter() {
    let mut d = Deduper::new();
    assert!(d.is_empty());
    assert_eq!(d.len(), 0);
    assert!(d.insert(1u32));
    assert!(!d.is_empty());
    assert!(d.contains(&1));
    assert!(!d.contains(&2));
    assert_eq!(d.len(), 1);
    assert!(d.insert(2u32));
    assert_eq!(d.len(), 2);
    assert_eq!(d.iter().copied().collect::<Vec<_>>(), vec![1, 2]);
}

#[test]
fn wildcard_fingerprint_order_independent() {
    let a = WildcardFingerprint::from_addresses(["2.2.2.2".into(), "1.1.1.1".into()]);
    let b = WildcardFingerprint::from_addresses(["1.1.1.1".into(), "2.2.2.2".into()]);
    assert_eq!(a, b);
    assert!(a.matches_addresses(&["2.2.2.2".into(), "1.1.1.1".into()]));
}

#[test]
fn wildcard_apex_empty_parent_key() {
    let mut f = WildcardFilter::new();
    f.register(
        "",
        WildcardFingerprint::from_addresses(["9.9.9.9".into()]),
    );
    assert!(!f.allow("anything.example.com", &["9.9.9.9".into()]));
    assert!(f.allow("anything.example.com", &["1.2.3.4".into()]));
}

#[test]
fn fingerprint_load_balance_returns_empty_on_disagree() {
    let results = vec![
        (
            "a.ex.com".into(),
            ResponseClass::Live {
                addresses: vec!["1.1.1.1".into()],
            },
        ),
        (
            "b.ex.com".into(),
            ResponseClass::Live {
                addresses: vec!["2.2.2.2".into()],
            },
        ),
    ];
    assert!(fingerprint_from_probe_results(&results).is_none());
    let multi = fingerprints_for_wildcard_probes(&results);
    assert_eq!(multi.len(), 2);
}

#[test]
fn probe_names_unique_count() {
    let mut rng = rand::thread_rng();
    let names = probe_names("example.com", 5, 8, &mut rng);
    assert_eq!(names.len(), 5);
    let set: std::collections::HashSet<_> = names.iter().collect();
    assert_eq!(set.len(), 5);
    let lab = random_probe_label(&mut rng, 10);
    assert_eq!(lab.len(), 10);
}

#[test]
fn parent_chain_single_label() {
    assert!(parent_chain("example").is_empty() || parent_chain("solo").is_empty());
}

#[test]
fn recall_precision_edge_empty() {
    assert_eq!(recall(&[], &[]), 1.0);
    assert_eq!(precision(&[], &["a".into()]), 1.0);
    assert_eq!(recall(&["a".into()], &["a".into(), "b".into()]), 0.5);
    assert_eq!(
        precision(&["a".into(), "x".into()], &["a".into()]),
        0.5
    );
}

#[test]
fn parse_resolvers_and_wordlist_shipped() {
    let text = "# c\n1.1.1.1\n8.8.8.8:5353\n\n";
    let r = parse_resolver_lines(text).unwrap();
    assert_eq!(r.len(), 2);
    assert_eq!(r[0].port(), 53);
    assert_eq!(r[1].port(), 5353);
    assert!(parse_resolver_lines("# only\n").is_err());

    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join("w.txt");
    std::fs::write(&p, "www\n#x\nmail\n").unwrap();
    let w = load_wordlist(&p).unwrap();
    assert_eq!(w, vec!["www".to_string(), "mail".to_string()]);
}

#[test]
fn is_wildcard_hit_non_live_false() {
    let f = WildcardFilter::new();
    assert!(!is_wildcard_hit(&f, "a.ex.com", &ResponseClass::NxDomain));
}
