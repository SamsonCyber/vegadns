//! Wildcard detection and filtering (pure fingerprint logic).

use ahash::{AHashMap, AHashSet};
use rand::Rng;

use crate::classify::ResponseClass;

/// Fingerprint of a wildcard catch-all: the set of answer addresses (sorted, unique).
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct WildcardFingerprint {
    pub addresses: Vec<String>,
}

impl WildcardFingerprint {
    pub fn from_addresses(addrs: impl IntoIterator<Item = String>) -> Self {
        let mut addresses: Vec<String> = addrs.into_iter().collect();
        addresses.sort();
        addresses.dedup();
        Self { addresses }
    }

    pub fn matches_addresses(&self, addrs: &[String]) -> bool {
        let other = WildcardFingerprint::from_addresses(addrs.iter().cloned());
        self.addresses == other.addresses
    }
}

/// Stateful filter: known wildcard fingerprints by parent suffix.
#[derive(Debug, Default)]
pub struct WildcardFilter {
    /// key: parent FQDN that is a wildcard root (e.g. "dev.example.com" for *.dev.example.com)
    /// Empty string key means the apex base domain is a catch-all.
    by_parent: AHashMap<String, WildcardFingerprint>,
}

impl WildcardFilter {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn register(&mut self, parent: impl Into<String>, fp: WildcardFingerprint) {
        self.by_parent.insert(parent.into(), fp);
    }

    pub fn parents(&self) -> impl Iterator<Item = (&String, &WildcardFingerprint)> {
        self.by_parent.iter()
    }

    /// Drop positive hits that match a registered wildcard fingerprint for their parent chain.
    pub fn allow(&self, fqdn: &str, addresses: &[String]) -> bool {
        if self.by_parent.is_empty() {
            return true;
        }
        // Check every parent suffix: a.b.example.com → b.example.com, example.com, ""
        for parent in parent_chain(fqdn) {
            if let Some(fp) = self.by_parent.get(&parent) {
                if fp.matches_addresses(addresses) {
                    return false;
                }
            }
        }
        // Apex catch-all registered under ""
        if let Some(fp) = self.by_parent.get("") {
            if fp.matches_addresses(addresses) {
                return false;
            }
        }
        true
    }

    pub fn is_empty(&self) -> bool {
        self.by_parent.is_empty()
    }
}

/// True when a live hit should be treated as a wildcard FP for the given filter.
pub fn is_wildcard_hit(filter: &WildcardFilter, fqdn: &str, class: &ResponseClass) -> bool {
    match class {
        ResponseClass::Live { addresses } => !filter.allow(fqdn, addresses),
        _ => false,
    }
}

/// Parent chain without the left-most label. `a.b.example.com` → `b.example.com`, `example.com`.
pub fn parent_chain(fqdn: &str) -> Vec<String> {
    let name = fqdn.trim_end_matches('.');
    let mut out = Vec::new();
    let mut rest = name;
    while let Some(idx) = rest.find('.') {
        rest = &rest[idx + 1..];
        if !rest.is_empty() {
            out.push(rest.to_string());
        }
    }
    out
}

/// Generate a random DNS label for wildcard probes (lowercase alnum).
pub fn random_probe_label(rng: &mut impl Rng, len: usize) -> String {
    const CHARSET: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789";
    (0..len)
        .map(|_| {
            let i = rng.gen_range(0..CHARSET.len());
            CHARSET[i] as char
        })
        .collect()
}

/// Build N random FQDNs under `parent` for wildcard probing.
pub fn probe_names(parent: &str, n: usize, label_len: usize, rng: &mut impl Rng) -> Vec<String> {
    let parent = parent.trim_end_matches('.');
    let mut set = AHashSet::new();
    let mut out = Vec::new();
    while out.len() < n {
        let label = random_probe_label(rng, label_len);
        let fqdn = if parent.is_empty() {
            label
        } else {
            format!("{label}.{parent}")
        };
        if set.insert(fqdn.clone()) {
            out.push(fqdn);
        }
    }
    out
}

/// Derive a fingerprint if all probe results agree on the same non-empty address set.
pub fn fingerprint_from_probe_results(
    results: &[(String, ResponseClass)],
) -> Option<WildcardFingerprint> {
    let mut fps: Vec<WildcardFingerprint> = Vec::new();
    for (_, class) in results {
        match class {
            ResponseClass::Live { addresses } if !addresses.is_empty() => {
                fps.push(WildcardFingerprint::from_addresses(addresses.clone()));
            }
            _ => return None, // any non-live means not a clean catch-all
        }
    }
    if fps.is_empty() {
        return None;
    }
    let first = &fps[0];
    if fps.iter().all(|f| f == first) {
        Some(first.clone())
    } else {
        // Disagreeing answers: still treat as wildcard if every probe was live
        // (load-balanced wildcards). Union fingerprint would over-filter; use first
        // and also register each unique set.
        None
    }
}

/// Like fingerprint_from_probe_results but accepts load-balanced wildcards by
/// requiring all probes live and using the union of address sets as "any match"
/// via multiple fingerprints. Returns one FP if all identical; else all unique FPs.
pub fn fingerprints_for_wildcard_probes(
    results: &[(String, ResponseClass)],
) -> Vec<WildcardFingerprint> {
    let mut fps = Vec::new();
    for (_, class) in results {
        match class {
            ResponseClass::Live { addresses } if !addresses.is_empty() => {
                let fp = WildcardFingerprint::from_addresses(addresses.clone());
                if !fps.contains(&fp) {
                    fps.push(fp);
                }
            }
            _ => return Vec::new(),
        }
    }
    fps
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn filters_matching_wildcard_addresses() {
        let mut f = WildcardFilter::new();
        f.register(
            "example.com",
            WildcardFingerprint::from_addresses(["9.9.9.9".into()]),
        );
        assert!(!f.allow("random.example.com", &["9.9.9.9".into()]));
        assert!(f.allow("www.example.com", &["1.2.3.4".into()]));
    }

    #[test]
    fn parent_chain_works() {
        assert_eq!(
            parent_chain("a.b.example.com"),
            vec![
                "b.example.com".to_string(),
                "example.com".to_string(),
                "com".to_string(),
            ]
        );
    }

    #[test]
    fn fingerprint_agreement() {
        let results = vec![
            (
                "x.example.com".into(),
                ResponseClass::Live {
                    addresses: vec!["9.9.9.9".into()],
                },
            ),
            (
                "y.example.com".into(),
                ResponseClass::Live {
                    addresses: vec!["9.9.9.9".into()],
                },
            ),
        ];
        let fp = fingerprint_from_probe_results(&results).unwrap();
        assert_eq!(fp.addresses, vec!["9.9.9.9".to_string()]);
    }

    #[test]
    fn fingerprint_rejects_mixed_nx() {
        let results = vec![
            (
                "x.example.com".into(),
                ResponseClass::Live {
                    addresses: vec!["9.9.9.9".into()],
                },
            ),
            ("y.example.com".into(), ResponseClass::NxDomain),
        ];
        assert!(fingerprint_from_probe_results(&results).is_none());
    }
}
