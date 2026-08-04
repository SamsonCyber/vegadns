//! HTTP response classification for path discovery (pure).

/// Default status codes treated as "interesting hits" (ferox/ffuf-class).
pub const DEFAULT_HIT_STATUSES: &[u16] = &[200, 201, 204, 301, 302, 307, 308, 401, 403];

/// Classification of one path probe.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PathClass {
    Hit { status: u16 },
    Miss { status: u16 },
    Error,
}

/// Classify an HTTP status against the match list.
pub fn classify_status(status: u16, match_codes: &[u16]) -> PathClass {
    if match_codes.is_empty() {
        // empty list → treat only 2xx/3xx/401/403 as hits (same as default intent)
        if DEFAULT_HIT_STATUSES.contains(&status) {
            PathClass::Hit { status }
        } else {
            PathClass::Miss { status }
        }
    } else if match_codes.contains(&status) {
        PathClass::Hit { status }
    } else {
        PathClass::Miss { status }
    }
}

pub fn is_hit(class: &PathClass) -> bool {
    matches!(class, PathClass::Hit { .. })
}

/// Parse comma-separated status list like "200,301,302,401,403".
pub fn parse_status_list(s: &str) -> anyhow::Result<Vec<u16>> {
    let mut out = Vec::new();
    for part in s.split(',') {
        let t = part.trim();
        if t.is_empty() {
            continue;
        }
        let v: u16 = t
            .parse()
            .map_err(|_| anyhow::anyhow!("bad status code: {t}"))?;
        out.push(v);
    }
    if out.is_empty() {
        anyhow::bail!("empty status list");
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hit_on_200() {
        assert!(matches!(
            classify_status(200, DEFAULT_HIT_STATUSES),
            PathClass::Hit { status: 200 }
        ));
    }

    #[test]
    fn miss_on_404() {
        assert!(matches!(
            classify_status(404, DEFAULT_HIT_STATUSES),
            PathClass::Miss { status: 404 }
        ));
    }

    #[test]
    fn parse_list() {
        assert_eq!(parse_status_list("200, 301,403").unwrap(), vec![200, 301, 403]);
    }
}
