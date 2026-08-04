//! Candidate generation: wordlist labels → FQDNs.

/// Expand a single wordlist label against a base domain.
///
/// Strips surrounding whitespace. Empty / comment lines return None.
/// If the label already ends with the base domain (or is absolute under it),
/// it is returned as a cleaned FQDN without double-appending.
pub fn expand_label(label: &str, base_domain: &str) -> Option<String> {
    let raw = label.trim();
    if raw.is_empty() || raw.starts_with('#') {
        return None;
    }
    let base = base_domain
        .trim()
        .trim_end_matches('.')
        .to_ascii_lowercase();
    if base.is_empty() {
        return None;
    }

    let name = raw.trim_end_matches('.').to_ascii_lowercase();
    // Full FQDN already under base (or equals apex)
    if name == base || name.ends_with(&format!(".{base}")) {
        return Some(name);
    }
    // Bare label or multi-label prefix (e.g. "api.v1") under base
    Some(format!("{name}.{base}"))
}

/// Expand many wordlist lines into FQDNs. Preserves order; skips empties/comments.
pub fn expand_wordlist_lines<'a, I>(lines: I, base_domain: &'a str) -> Vec<String>
where
    I: IntoIterator<Item = &'a str>,
{
    let mut out = Vec::new();
    for line in lines {
        if let Some(fqdn) = expand_label(line, base_domain) {
            out.push(fqdn);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn expands_simple_label() {
        assert_eq!(
            expand_label("www", "example.com").as_deref(),
            Some("www.example.com")
        );
    }

    #[test]
    fn skips_comments_and_blank() {
        assert!(expand_label("# foo", "example.com").is_none());
        assert!(expand_label("  ", "example.com").is_none());
    }

    #[test]
    fn does_not_double_append() {
        assert_eq!(
            expand_label("www.example.com", "example.com").as_deref(),
            Some("www.example.com")
        );
    }
}
