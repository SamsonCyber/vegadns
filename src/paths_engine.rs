//! Concurrent HTTP path discovery engine with optional soft-404 filter.
//!
//! Worker-pool design (not one task per URL): bounds spawn cost and reuses
//! connection pool under high concurrency.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use ahash::AHashSet;
use rand::{distributions::Alphanumeric, Rng};
use reqwest::Client;
use std::sync::atomic::AtomicUsize;

use crate::dedup::Deduper;
use crate::path_classify::{classify_status, is_hit, DEFAULT_HIT_STATUSES};
use crate::path_join::{expand_paths, normalize_url_key};
use crate::soft404::{allow_hit, Soft404Filter};

#[derive(Debug, Clone)]
pub struct PathsConfig {
    pub base_url: String,
    pub concurrency: usize,
    pub timeout: Duration,
    pub match_codes: Vec<u16>,
    pub quiet: bool,
    /// Probe this many random missing paths to learn soft-404 fingerprints (0 = off).
    pub soft404_probes: usize,
    /// Retries after transport error (not after HTTP status).
    pub retries: u32,
}

impl Default for PathsConfig {
    fn default() -> Self {
        Self {
            base_url: String::new(),
            concurrency: 128,
            timeout: Duration::from_secs(3),
            match_codes: DEFAULT_HIT_STATUSES.to_vec(),
            quiet: false,
            soft404_probes: 4,
            retries: 1,
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct PathsStats {
    pub candidates: u64,
    pub requests: u64,
    pub hits: u64,
    pub misses: u64,
    pub errors: u64,
    pub soft404_dropped: u64,
    pub soft404_fps: u64,
    pub elapsed: Duration,
}

impl PathsStats {
    pub fn request_rate(&self) -> f64 {
        let s = self.elapsed.as_secs_f64().max(1e-9);
        self.requests as f64 / s
    }
}

#[derive(Debug)]
pub struct PathsResult {
    /// Full hit URLs (deduped), after soft-404 filter.
    pub urls: Vec<String>,
    /// status per url for optional detailed output
    pub statuses: Vec<(String, u16)>,
    pub stats: PathsStats,
    pub soft404: Soft404Filter,
}

fn random_probe_path() -> String {
    let mut rng = rand::thread_rng();
    let s: String = (0..24)
        .map(|_| rng.sample(Alphanumeric) as char)
        .collect();
    format!("vegadns-soft404-probe-{s}")
}

/// Learn soft-404 fingerprints from random missing paths (parallel).
pub async fn calibrate_soft404(
    client: &Client,
    base_url: &str,
    probes: usize,
) -> Soft404Filter {
    let mut filter = Soft404Filter::new();
    if probes == 0 {
        return filter;
    }
    let base = base_url.trim_end_matches('/');
    let mut handles = Vec::with_capacity(probes);
    for _ in 0..probes {
        let client = client.clone();
        let url = format!("{base}/{}", random_probe_path());
        handles.push(tokio::spawn(async move {
            match client.get(&url).send().await {
                Ok(resp) => {
                    let status = resp.status().as_u16();
                    let len = match resp.content_length() {
                        Some(n) => n,
                        None => resp.bytes().await.map(|b| b.len() as u64).unwrap_or(0),
                    };
                    Some((status, len))
                }
                Err(_) => None,
            }
        }));
    }
    for h in handles {
        if let Ok(Some((status, len))) = h.await {
            filter.register(status, len);
        }
    }
    filter
}

async fn probe_one(
    client: &Client,
    url: String,
    match_codes: &[u16],
    soft: &Soft404Filter,
    retries: u32,
) -> Option<(String, u16, bool, bool)> {
    // returns (url, status, is_hit, was_soft404)
    let attempts = retries.saturating_add(1).max(1);
    for attempt in 0..attempts {
        match client.get(&url).send().await {
            Ok(resp) => {
                let status = resp.status().as_u16();
                let len = match resp.content_length() {
                    Some(n) => n,
                    None => resp.bytes().await.map(|b| b.len() as u64).unwrap_or(0),
                };
                let class = classify_status(status, match_codes);
                if !is_hit(&class) {
                    return Some((url, status, false, false));
                }
                if !allow_hit(soft, status, len) {
                    return Some((url, status, false, true));
                }
                return Some((url, status, true, false));
            }
            Err(_) => {
                if attempt + 1 < attempts {
                    tokio::time::sleep(Duration::from_millis(5 + 10 * attempt as u64)).await;
                    continue;
                }
                return None;
            }
        }
    }
    None
}

/// Run concurrent path discovery against `base_url` with path wordlist lines.
pub async fn run_paths(cfg: PathsConfig, words: &[String]) -> anyhow::Result<PathsResult> {
    let candidates = expand_paths(&cfg.base_url, words);
    let n = candidates.len();
    let match_codes = if cfg.match_codes.is_empty() {
        DEFAULT_HIT_STATUSES.to_vec()
    } else {
        cfg.match_codes.clone()
    };

    let conc = cfg.concurrency.max(1);
    let client = Client::builder()
        .timeout(cfg.timeout)
        .redirect(reqwest::redirect::Policy::none())
        .pool_max_idle_per_host(conc)
        .pool_idle_timeout(Duration::from_secs(30))
        .tcp_nodelay(true)
        .http1_only()
        .build()?;

    let start = Instant::now();
    let soft404 = calibrate_soft404(&client, &cfg.base_url, cfg.soft404_probes).await;
    if !cfg.quiet && !soft404.is_empty() {
        eprintln!(
            "[vegadns] soft-404 fingerprints={} (probes={})",
            soft404.len(),
            cfg.soft404_probes
        );
    }

    let urls = Arc::new(candidates);
    let next = Arc::new(AtomicUsize::new(0));
    let requests = Arc::new(AtomicU64::new(0));
    let hits_c = Arc::new(AtomicU64::new(0));
    let misses_c = Arc::new(AtomicU64::new(0));
    let errors_c = Arc::new(AtomicU64::new(0));
    let soft_drop = Arc::new(AtomicU64::new(0));
    let soft_arc = Arc::new(soft404.clone());
    let match_arc = Arc::new(match_codes);
    let retries = cfg.retries;
    let workers = conc.min(n.max(1));

    let mut handles = Vec::with_capacity(workers);
    for _ in 0..workers {
        let client = client.clone();
        let urls = Arc::clone(&urls);
        let next = Arc::clone(&next);
        let match_codes = Arc::clone(&match_arc);
        let requests = Arc::clone(&requests);
        let hits_c = Arc::clone(&hits_c);
        let misses_c = Arc::clone(&misses_c);
        let errors_c = Arc::clone(&errors_c);
        let soft_drop = Arc::clone(&soft_drop);
        let soft = Arc::clone(&soft_arc);
        let total = n;
        handles.push(tokio::spawn(async move {
            let mut local_hits: Vec<(String, u16)> = Vec::new();
            loop {
                let i = next.fetch_add(1, Ordering::Relaxed);
                if i >= total {
                    break;
                }
                let url = urls[i].clone();
                requests.fetch_add(1, Ordering::Relaxed);
                match probe_one(&client, url, &match_codes, &soft, retries).await {
                    Some((url, status, true, _)) => {
                        hits_c.fetch_add(1, Ordering::Relaxed);
                        local_hits.push((url, status));
                    }
                    Some((_url, _status, false, was_soft)) => {
                        if was_soft {
                            soft_drop.fetch_add(1, Ordering::Relaxed);
                        }
                        misses_c.fetch_add(1, Ordering::Relaxed);
                    }
                    None => {
                        errors_c.fetch_add(1, Ordering::Relaxed);
                    }
                }
            }
            local_hits
        }));
    }

    let mut dedup = Deduper::new();
    let mut statuses = Vec::new();
    let mut seen_keys = AHashSet::new();
    for h in handles {
        if let Ok(local) = h.await {
            for (url, status) in local {
                let key = normalize_url_key(&url);
                if seen_keys.insert(key) {
                    statuses.push((url.clone(), status));
                    dedup.insert(url);
                }
            }
        }
    }

    let stats = PathsStats {
        candidates: n as u64,
        requests: requests.load(Ordering::Relaxed),
        hits: hits_c.load(Ordering::Relaxed),
        misses: misses_c.load(Ordering::Relaxed),
        errors: errors_c.load(Ordering::Relaxed),
        soft404_dropped: soft_drop.load(Ordering::Relaxed),
        soft404_fps: soft_arc.len() as u64,
        elapsed: start.elapsed(),
    };

    Ok(PathsResult {
        urls: dedup.into_vec(),
        statuses,
        stats,
        soft404: (*soft_arc).clone(),
    })
}

/// Recall/precision helpers for path URL sets (reuse engine naming).
pub fn path_recall(found: &[String], known_true: &[String]) -> f64 {
    if known_true.is_empty() {
        return 1.0;
    }
    let set: AHashSet<String> = found.iter().map(|u| normalize_url_key(u)).collect();
    let hit = known_true
        .iter()
        .filter(|k| set.contains(&normalize_url_key(k)))
        .count();
    hit as f64 / known_true.len() as f64
}

pub fn path_precision(found: &[String], known_true: &[String]) -> f64 {
    if found.is_empty() {
        return 1.0;
    }
    let set: AHashSet<String> = known_true.iter().map(|u| normalize_url_key(u)).collect();
    let hit = found
        .iter()
        .filter(|f| set.contains(&normalize_url_key(f)))
        .count();
    hit as f64 / found.len() as f64
}

pub fn path_f1(found: &[String], known_true: &[String]) -> f64 {
    let r = path_recall(found, known_true);
    let p = path_precision(found, known_true);
    if r + p <= 0.0 {
        0.0
    } else {
        2.0 * r * p / (r + p)
    }
}

/// Rewrite known-true template PORT placeholder with actual port.
pub fn rewrite_port_template(lines: &[String], port: u16) -> Vec<String> {
    lines
        .iter()
        .map(|s| s.replace("PORT", &port.to_string()))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mock_http::{load_hit_paths, MockHttp, MockHttpZone, PathBehavior};
    use std::collections::HashMap;
    use std::path::PathBuf;

    #[tokio::test]
    async fn finds_known_paths_on_mock_http() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let hits = load_hit_paths(root.join("fixtures/paths/hit_paths.txt")).unwrap();
        let mock = MockHttp::spawn(hits).await.unwrap();
        let base = mock.base_url();
        let words = crate::engine::load_wordlist(root.join("fixtures/paths/wordlist.txt")).unwrap();
        let cfg = PathsConfig {
            base_url: base.clone(),
            concurrency: 16,
            timeout: Duration::from_secs(5),
            match_codes: vec![200],
            quiet: true,
            soft404_probes: 0,
            retries: 2,
        };
        let result = run_paths(cfg, &words).await.unwrap();
        mock.shutdown().await;

        assert!(
            result.stats.requests > 0,
            "no requests completed: {:?}",
            result.stats
        );
        assert!(
            result.urls.iter().any(|u| u.ends_with("/admin")),
            "missing admin in {:?} errs={}",
            result.urls,
            result.stats.errors
        );
    }

    #[tokio::test]
    async fn soft404_filter_drops_noise_keeps_real() {
        let mut map = HashMap::new();
        map.insert("admin".into(), PathBehavior::Hit200);
        map.insert("api".into(), PathBehavior::Hit200);
        map.insert("secret".into(), PathBehavior::Hit401);
        let zone = MockHttpZone::hard_from_map(map);
        let mock = MockHttp::spawn_zone(zone).await.unwrap();
        let base = mock.base_url();
        let words = vec![
            "admin".into(),
            "api".into(),
            "secret".into(),
            "noise1".into(),
            "noise2".into(),
            "noise3".into(),
        ];
        let cfg = PathsConfig {
            base_url: base,
            concurrency: 8,
            timeout: Duration::from_secs(5),
            match_codes: vec![200, 401, 403],
            quiet: true,
            soft404_probes: 6,
            retries: 2,
        };
        let result = run_paths(cfg, &words).await.unwrap();
        mock.shutdown().await;

        assert!(result.soft404.len() >= 1, "expected soft404 fp");
        assert!(
            result.urls.iter().any(|u| u.ends_with("/admin")),
            "missing admin {:?}",
            result.urls
        );
        assert!(result.stats.soft404_dropped >= 2);
        assert_eq!(path_precision(&result.urls, &result.urls), 1.0);
    }
}
