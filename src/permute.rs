//! Subdomain permutation / alteration engine (altdns / gotator / alterx class).
//!
//! Input: known seed labels (or FQDNs under a base domain) + alter words.
//! Output: mutated candidate labels (not FQDNs) for expand/resolve.
//!
//! Modes (all optional, default on for prefix+suffix+numbers):
//! - prefix: `{word}-{seed}`, `{word}{seed}`, `{word}_{seed}`
//! - suffix: `{seed}-{word}`, …
//! - numbers: seed with trailing digits mutated / appended in 0..=N
//! - multi-label seeds: mutate left-most label only (`api.v1` → `dev-api.v1`)

use ahash::AHashSet;

/// Configuration for permutation generation.
#[derive(Debug, Clone)]
pub struct PermuteConfig {
    /// Emit `word` + sep + seed (and concat forms when sep is empty-capable).
    pub prefix: bool,
    /// Emit seed + sep + `word`.
    pub suffix: bool,
    /// Separators between seed and alter word. Empty string = glue (`devapi`).
    pub separators: Vec<String>,
    /// When true, also mutate / append numeric tails 0..=numbers.
    pub numbers: bool,
    /// Inclusive upper bound for number mutations (e.g. 5 → 0..5).
    pub number_max: u32,
    /// Hard cap on output size (None = unbounded). Stops after cap unique labels.
    pub max_out: Option<usize>,
    /// Include original seeds in the output (default true).
    pub include_seeds: bool,
}

impl Default for PermuteConfig {
    fn default() -> Self {
        Self {
            prefix: true,
            suffix: true,
            separators: vec!["-".into(), "_".into(), "".into()],
            numbers: true,
            number_max: 5,
            max_out: None,
            include_seeds: true,
        }
    }
}

/// Strip a base domain from a seed if present; return the left-hand label path.
///
/// `www.example.com` + base `example.com` → `www`
/// `api.v1.example.com` → `api.v1`
/// bare `www` → `www`
pub fn seed_to_label(seed: &str, base_domain: Option<&str>) -> Option<String> {
    let raw = seed.trim().trim_end_matches('.').to_ascii_lowercase();
    if raw.is_empty() || raw.starts_with('#') {
        return None;
    }
    if let Some(base) = base_domain {
        let base = base.trim().trim_end_matches('.').to_ascii_lowercase();
        if base.is_empty() {
            return Some(raw);
        }
        if raw == base {
            return None; // apex itself is not a subdomain seed
        }
        let suffix = format!(".{base}");
        if let Some(left) = raw.strip_suffix(&suffix) {
            if left.is_empty() {
                return None;
            }
            return Some(left.to_string());
        }
    }
    Some(raw)
}

fn valid_label_fragment(s: &str) -> bool {
    if s.is_empty() || s.len() > 63 {
        return false;
    }
    // DNS label: alnum and hyphen; we also allow multi-label left side with dots
    s.chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.')
        && !s.starts_with('.')
        && !s.ends_with('.')
}

fn split_leftmost(label: &str) -> (&str, &str) {
    match label.split_once('.') {
        Some((left, rest)) => (left, rest),
        None => (label, ""),
    }
}

fn join_left(left: &str, rest: &str) -> String {
    if rest.is_empty() {
        left.to_string()
    } else {
        format!("{left}.{rest}")
    }
}

fn push_unique(out: &mut Vec<String>, seen: &mut AHashSet<String>, s: String, max: Option<usize>) -> bool {
    if !valid_label_fragment(&s) {
        return true; // keep going, just skip bad
    }
    // reject double-dots / empty segments
    if s.split('.').any(|p| p.is_empty()) {
        return true;
    }
    if seen.insert(s.clone()) {
        out.push(s);
        if let Some(n) = max {
            if out.len() >= n {
                return false; // stop
            }
        }
    }
    true
}

/// Generate altered labels from seeds + alter words.
pub fn permute_labels(
    seeds: &[String],
    words: &[String],
    base_domain: Option<&str>,
    cfg: &PermuteConfig,
) -> Vec<String> {
    let mut out = Vec::new();
    let mut seen = AHashSet::new();

    let seed_labels: Vec<String> = seeds
        .iter()
        .filter_map(|s| seed_to_label(s, base_domain))
        .collect();

    if cfg.include_seeds {
        for s in &seed_labels {
            if !push_unique(&mut out, &mut seen, s.clone(), cfg.max_out) {
                return out;
            }
        }
    }

    let default_seps = vec!["-".to_string()];
    let seps: &[String] = if cfg.separators.is_empty() {
        &default_seps
    } else {
        &cfg.separators
    };

    let words: Vec<String> = words
        .iter()
        .map(|w| w.trim().to_ascii_lowercase())
        .filter(|w| !w.is_empty() && !w.starts_with('#') && valid_label_fragment(w) && !w.contains('.'))
        .collect();

    for seed in &seed_labels {
        let (left, rest) = split_leftmost(seed);

        // number mutations on left-most label
        if cfg.numbers {
            let base_no_num = strip_trailing_digits(left);
            for n in 0..=cfg.number_max {
                let cand_left = format!("{base_no_num}{n}");
                let full = join_left(&cand_left, rest);
                if !push_unique(&mut out, &mut seen, full, cfg.max_out) {
                    return out;
                }
                // also dash form: api-1
                if !base_no_num.is_empty() {
                    let cand_left = format!("{base_no_num}-{n}");
                    let full = join_left(&cand_left, rest);
                    if !push_unique(&mut out, &mut seen, full, cfg.max_out) {
                        return out;
                    }
                }
            }
        }

        for word in &words {
            for sep in seps {
                if cfg.prefix {
                    let cand_left = format!("{word}{sep}{left}");
                    let full = join_left(&cand_left, rest);
                    if !push_unique(&mut out, &mut seen, full, cfg.max_out) {
                        return out;
                    }
                }
                if cfg.suffix {
                    let cand_left = format!("{left}{sep}{word}");
                    let full = join_left(&cand_left, rest);
                    if !push_unique(&mut out, &mut seen, full, cfg.max_out) {
                        return out;
                    }
                }
            }
        }
    }

    out
}

fn strip_trailing_digits(s: &str) -> String {
    let b = s.as_bytes();
    let mut end = b.len();
    while end > 0 && b[end - 1].is_ascii_digit() {
        end -= 1;
    }
    // if entire string is digits, keep as-is for number append path
    if end == 0 {
        return s.to_string();
    }
    s[..end].to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn seed_strips_base() {
        assert_eq!(
            seed_to_label("www.example.com", Some("example.com")).as_deref(),
            Some("www")
        );
        assert_eq!(
            seed_to_label("api.v1.example.com", Some("example.com")).as_deref(),
            Some("api.v1")
        );
        assert_eq!(seed_to_label("www", Some("example.com")).as_deref(), Some("www"));
    }

    #[test]
    fn prefix_suffix_and_numbers() {
        let seeds = vec!["api".into()];
        let words = vec!["dev".into(), "staging".into()];
        let cfg = PermuteConfig {
            number_max: 2,
            separators: vec!["-".into()],
            max_out: None,
            ..Default::default()
        };
        let out = permute_labels(&seeds, &words, None, &cfg);
        assert!(out.contains(&"api".to_string()));
        assert!(out.contains(&"dev-api".to_string()));
        assert!(out.contains(&"api-dev".to_string()));
        assert!(out.contains(&"staging-api".to_string()));
        assert!(out.contains(&"api0".to_string()) || out.contains(&"api1".to_string()));
    }

    #[test]
    fn multi_label_mutates_leftmost() {
        let seeds = vec!["api.v1".into()];
        let words = vec!["dev".into()];
        let cfg = PermuteConfig {
            numbers: false,
            separators: vec!["-".into()],
            ..Default::default()
        };
        let out = permute_labels(&seeds, &words, None, &cfg);
        assert!(out.contains(&"dev-api.v1".to_string()));
        assert!(out.contains(&"api-dev.v1".to_string()));
    }

    #[test]
    fn max_out_caps() {
        let seeds = vec!["a".into(), "b".into(), "c".into()];
        let words = vec!["x".into(), "y".into(), "z".into()];
        let cfg = PermuteConfig {
            max_out: Some(5),
            numbers: false,
            ..Default::default()
        };
        let out = permute_labels(&seeds, &words, None, &cfg);
        assert_eq!(out.len(), 5);
    }
}
