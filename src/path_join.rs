//! URL path candidate construction (pure).

/// Join a base URL and a path wordlist entry into a full request URL.
///
/// Returns None for empty/comment wordlist lines.
pub fn join_url(base: &str, path_word: &str) -> Option<String> {
    let raw = path_word.trim();
    if raw.is_empty() || raw.starts_with('#') {
        return None;
    }
    let base = base.trim().trim_end_matches('/');
    if base.is_empty() {
        return None;
    }
    // Strip leading slash from word so we always insert one
    let rel = raw.trim_start_matches('/');
    if rel.is_empty() {
        return Some(format!("{base}/"));
    }
    Some(format!("{base}/{rel}"))
}

/// Expand many path words against one base URL.
pub fn expand_paths(base: &str, words: &[String]) -> Vec<String> {
    let mut out = Vec::with_capacity(words.len());
    for w in words {
        if let Some(u) = join_url(base, w) {
            out.push(u);
        }
    }
    out
}

/// Normalize a discovered URL for dedup (lowercase scheme/host not required;
/// strip trailing slash except root).
pub fn normalize_url_key(url: &str) -> String {
    let u = url.trim();
    if u.ends_with('/') && u.matches('/').count() > 2 {
        // http://h/path/ -> http://h/path
        let mut s = u.trim_end_matches('/').to_string();
        if s.ends_with("://") {
            return u.to_string();
        }
        // keep root slash: http://host -> http://host/
        if let Some(idx) = s.rfind("://") {
            let rest = &s[idx + 3..];
            if !rest.contains('/') {
                s.push('/');
            }
        }
        s
    } else {
        u.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn joins_relative_path() {
        assert_eq!(
            join_url("http://127.0.0.1:8080", "admin").as_deref(),
            Some("http://127.0.0.1:8080/admin")
        );
        assert_eq!(
            join_url("http://127.0.0.1:8080/", "/api/v1").as_deref(),
            Some("http://127.0.0.1:8080/api/v1")
        );
    }

    #[test]
    fn skips_blank_and_comment() {
        assert!(join_url("http://x", "#c").is_none());
        assert!(join_url("http://x", "  ").is_none());
    }
}
