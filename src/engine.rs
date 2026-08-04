//! High-concurrency DNS stub resolve engine.
//!
//! Hot path: massdns-style nonblocking UDP poll (send burst + recv drain)
//! on a blocking worker so the mock server stays schedulable on async threads.

use std::net::{SocketAddr, UdpSocket as StdUdpSocket};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use ahash::{AHashMap, AHashSet};

use crate::classify::{classify_response, is_positive_hit, ResponseClass};
use crate::dedup::Deduper;
use crate::dns_packet::{build_query_into, parse_message, patch_query_id, peek_id};
use crate::expand::expand_label;
use crate::mock_dns::{MockServer, MockZone};
use crate::wildcard::{
    fingerprints_for_wildcard_probes, probe_names, WildcardFilter, WildcardFingerprint,
};

#[derive(Debug, Clone)]
pub struct EngineConfig {
    pub domain: String,
    pub concurrency: usize,
    pub timeout: Duration,
    pub retries: u32,
    pub sockets: usize,
    pub wildcard_probes: usize,
    pub quiet: bool,
    /// When true, wordlist lines are absolute FQDNs (no label×domain expand).
    pub fqdn_list: bool,
}

impl Default for EngineConfig {
    fn default() -> Self {
        Self {
            domain: String::new(),
            concurrency: 8000,
            timeout: Duration::from_millis(500),
            retries: 1,
            sockets: 2,
            wildcard_probes: 2,
            quiet: false,
            fqdn_list: false,
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct EngineStats {
    pub queries_sent: u64,
    pub responses_ok: u64,
    pub nxdomain: u64,
    pub errors: u64,
    pub timeouts: u64,
    pub found_raw: u64,
    pub found_after_wildcard: u64,
    pub wildcard_parents: u64,
    pub elapsed: Duration,
    pub candidates: u64,
}

impl EngineStats {
    pub fn query_rate(&self) -> f64 {
        let secs = self.elapsed.as_secs_f64().max(1e-9);
        (self.queries_sent as f64) / secs
    }

    pub fn result_rate(&self) -> f64 {
        let secs = self.elapsed.as_secs_f64().max(1e-9);
        (self.found_after_wildcard as f64) / secs
    }
}

#[derive(Debug)]
pub struct EnumResult {
    pub names: Vec<String>,
    pub stats: EngineStats,
    pub wildcard_filter: WildcardFilter,
}

struct Pending {
    /// Index into the batch `names` slice.
    name_idx: usize,
    attempts: u32,
    sent_at: Instant,
    sock_idx: usize,
}

struct Counters {
    queries_sent: AtomicU64,
    responses_ok: AtomicU64,
    nxdomain: AtomicU64,
    errors: AtomicU64,
    timeouts: AtomicU64,
}

struct SyncResolver {
    socks: Vec<StdUdpSocket>,
    resolvers: Vec<SocketAddr>,
    cfg: EngineConfig,
    counters: Counters,
    next_id: u16,
    resolver_idx: usize,
    sock_rr: usize,
    buf: [u8; 4096],
}

impl SyncResolver {
    fn new(resolvers: Vec<SocketAddr>, cfg: EngineConfig) -> anyhow::Result<Self> {
        if resolvers.is_empty() {
            anyhow::bail!("no resolvers");
        }
        let n = cfg.sockets.max(1);
        let mut socks = Vec::with_capacity(n);
        for _ in 0..n {
            let s = StdUdpSocket::bind("0.0.0.0:0")?;
            s.set_nonblocking(true)?;
            // Large UDP buffers cut WouldBlock on send/recv under burst load (Unix).
            #[cfg(unix)]
            {
                let _ = s.set_recv_buffer_size(4 * 1024 * 1024);
                let _ = s.set_send_buffer_size(4 * 1024 * 1024);
            }
            socks.push(s);
        }
        Ok(Self {
            socks,
            resolvers,
            cfg,
            counters: Counters {
                queries_sent: AtomicU64::new(0),
                responses_ok: AtomicU64::new(0),
                nxdomain: AtomicU64::new(0),
                errors: AtomicU64::new(0),
                timeouts: AtomicU64::new(0),
            },
            next_id: 1,
            resolver_idx: 0,
            sock_rr: 0,
            buf: [0u8; 4096],
        })
    }

    #[inline]
    fn send_pkt(&mut self, sock_idx: usize, pkt: &[u8]) -> std::io::Result<usize> {
        let si = sock_idx % self.socks.len();
        let resolver = self.resolvers[self.resolver_idx % self.resolvers.len()];
        self.resolver_idx = self.resolver_idx.wrapping_add(1);
        self.socks[si].send_to(pkt, resolver)
    }

    fn alloc_id(&mut self, pending: &AHashMap<u16, Pending>) -> u16 {
        let mut id = self.next_id;
        if id == 0 {
            id = 1;
        }
        self.next_id = id.wrapping_add(1);
        if self.next_id == 0 {
            self.next_id = 1;
        }
        let mut tries = 0;
        while pending.contains_key(&id) && tries < 64 {
            id = self.next_id;
            if id == 0 {
                id = 1;
            }
            self.next_id = id.wrapping_add(1);
            tries += 1;
        }
        id
    }

    fn resolve(&mut self, names: &[String]) -> Vec<(String, ResponseClass)> {
        if names.is_empty() {
            return Vec::new();
        }
        // Pre-encode A-query templates (TXID patched per send). Avoids re-encoding
        // names on every attempt (massdns-class hot path).
        let mut templates: Vec<Option<Vec<u8>>> = Vec::with_capacity(names.len());
        for name in names {
            let mut pkt = Vec::with_capacity(64 + name.len());
            match build_query_into(&mut pkt, 0, name) {
                Ok(()) => templates.push(Some(pkt)),
                Err(_) => templates.push(None),
            }
        }

        // Inflight cap ~massdns -s 2000: higher floods loopback mock and causes drop storms.
        let concurrency = self.cfg.concurrency.max(1).min(2000);
        let mut pending: AHashMap<u16, Pending> =
            AHashMap::with_capacity(concurrency.min(names.len()).max(16));
        let mut classes: Vec<Option<ResponseClass>> = vec![None; names.len()];
        let mut done = 0usize;
        let mut name_idx = 0usize;

        // Short retry interval; full timeout only for give-up.
        // Second-guess: too-aggressive retransmit floods loopback and slows wall.
        let retry_after =
            Duration::from_millis(25).min(self.cfg.timeout / 3).max(Duration::from_millis(10));
        let give_up_after = self.cfg.timeout;
        // attempts starts at 1; allow `retries` retransmits (total sends = retries+1).
        let max_attempts = self.cfg.retries.saturating_add(1).max(1);
        let overall_deadline = Instant::now()
            + give_up_after.saturating_mul(self.cfg.retries.saturating_add(3));
        let mut last_progress = Instant::now();

        // Burst then drain (massdns-class). 384 balances fill vs drop.
        let burst = 384usize;
        let mut to_retry: Vec<(u16, usize, usize)> = Vec::with_capacity(256);
        let mut expired: Vec<u16> = Vec::with_capacity(64);

        loop {
            let mut sent_this_round = 0usize;
            while name_idx < names.len()
                && pending.len() < concurrency
                && sent_this_round < burst
            {
                let ni = name_idx;
                name_idx += 1;
                let Some(tpl) = templates[ni].as_mut() else {
                    classes[ni] = Some(ResponseClass::Garbage);
                    done += 1;
                    continue;
                };
                let id = self.alloc_id(&pending);
                patch_query_id(tpl, id);
                let si = self.sock_rr % self.socks.len();
                self.sock_rr = self.sock_rr.wrapping_add(1);
                match self.send_pkt(si, tpl) {
                    Ok(_) => {
                        self.counters.queries_sent.fetch_add(1, Ordering::Relaxed);
                        pending.insert(
                            id,
                            Pending {
                                name_idx: ni,
                                attempts: 1,
                                sent_at: Instant::now(),
                                sock_idx: si,
                            },
                        );
                        sent_this_round += 1;
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        name_idx -= 1;
                        break;
                    }
                    Err(_) => {
                        classes[ni] = Some(ResponseClass::Error { rcode: 254 });
                        self.counters.errors.fetch_add(1, Ordering::Relaxed);
                        done += 1;
                    }
                }
            }

            // recv drain — peek TXID, then fast classify (no name allocations)
            let mut got_any = false;
            for sock in &self.socks {
                // Drain hard: many replies per send burst.
                for _ in 0..1024 {
                    match sock.recv_from(&mut self.buf) {
                        Ok((n, _)) => {
                            got_any = true;
                            last_progress = Instant::now();
                            let slice = &self.buf[..n];
                            let Some(id) = peek_id(slice) else {
                                continue;
                            };
                            if !pending.contains_key(&id) {
                                continue;
                            }
                            if let Ok(msg) = parse_message(slice) {
                                if let Some(p) = pending.remove(&msg.id) {
                                    let class = classify_response(&msg);
                                    match &class {
                                        ResponseClass::Live { .. }
                                        | ResponseClass::NoErrorEmpty => {
                                            self.counters
                                                .responses_ok
                                                .fetch_add(1, Ordering::Relaxed);
                                        }
                                        ResponseClass::NxDomain => {
                                            self.counters.nxdomain.fetch_add(1, Ordering::Relaxed);
                                        }
                                        _ => {
                                            self.counters.errors.fetch_add(1, Ordering::Relaxed);
                                        }
                                    }
                                    if classes[p.name_idx].is_none() {
                                        classes[p.name_idx] = Some(class);
                                        done += 1;
                                    }
                                }
                            }
                        }
                        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                        Err(_) => break,
                    }
                }
            }

            // retries: one Instant::now; linear scan into to_retry / expired (no retain thrash)
            let now = Instant::now();
            to_retry.clear();
            expired.clear();
            for (id, p) in pending.iter_mut() {
                let age = now.duration_since(p.sent_at);
                if age < retry_after {
                    continue;
                }
                if p.attempts < max_attempts {
                    p.attempts += 1;
                    p.sent_at = now;
                    to_retry.push((*id, p.name_idx, p.sock_idx));
                } else if age >= give_up_after {
                    expired.push(*id);
                }
            }
            for id in &expired {
                if let Some(p) = pending.remove(id) {
                    if classes[p.name_idx].is_none() {
                        classes[p.name_idx] = Some(ResponseClass::Error { rcode: 255 });
                        done += 1;
                        self.counters.timeouts.fetch_add(1, Ordering::Relaxed);
                    }
                }
            }
            for (id, ni, si) in to_retry.drain(..) {
                if let Some(tpl) = templates[ni].as_mut() {
                    patch_query_id(tpl, id);
                    if self.send_pkt(si, tpl).is_ok() {
                        self.counters.queries_sent.fetch_add(1, Ordering::Relaxed);
                    }
                }
            }

            if done >= names.len() && pending.is_empty() {
                break;
            }
            if now >= overall_deadline {
                for (_, p) in pending.drain() {
                    if classes[p.name_idx].is_none() {
                        classes[p.name_idx] = Some(ResponseClass::Error { rcode: 255 });
                        self.counters.timeouts.fetch_add(1, Ordering::Relaxed);
                    }
                }
                break;
            }

            // Spin briefly when waiting on in-flight only; avoid long sleeps on hot path.
            if !got_any && name_idx >= names.len() && !pending.is_empty() {
                if last_progress.elapsed() > Duration::from_millis(1) {
                    std::hint::spin_loop();
                    std::thread::yield_now();
                }
            }
        }

        let mut results = Vec::with_capacity(names.len());
        for (i, name) in names.iter().enumerate() {
            let class = classes[i]
                .take()
                .unwrap_or(ResponseClass::Error { rcode: 255 });
            results.push((name.clone(), class));
        }
        results
    }
}

/// Load resolvers from a file. Lines: `ip` or `ip:port`.
pub fn load_resolvers(path: impl AsRef<Path>) -> anyhow::Result<Vec<SocketAddr>> {
    let text = std::fs::read_to_string(path)?;
    parse_resolver_lines(&text)
}

pub fn parse_resolver_lines(text: &str) -> anyhow::Result<Vec<SocketAddr>> {
    let mut out = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let addr = if line.contains(':') {
            line.parse::<SocketAddr>()?
        } else {
            format!("{line}:53").parse::<SocketAddr>()?
        };
        out.push(addr);
    }
    if out.is_empty() {
        anyhow::bail!("no resolvers loaded");
    }
    Ok(out)
}

/// Load wordlist labels from path (deduped, lowercased, comments skipped).
pub fn load_wordlist(path: impl AsRef<Path>) -> anyhow::Result<Vec<String>> {
    crate::wordlists::load_wordlist_path(path)
}

/// Infer nested wildcard parents from main results: ≥2 distinct labels under the
/// same parent with identical answer fingerprints ⇒ catch-all at that parent.
fn infer_wildcards_from_results(
    results: &[(String, ResponseClass)],
    domain: &str,
    filter: &mut WildcardFilter,
) {
    // parent -> fingerprint string -> set of left-most labels
    let mut by_parent: AHashMap<String, AHashMap<String, AHashSet<String>>> = AHashMap::new();
    for (name, class) in results {
        let ResponseClass::Live { addresses } = class else {
            continue;
        };
        if addresses.is_empty() {
            continue;
        }
        let Some((label, parent)) = name.split_once('.') else {
            continue;
        };
        if parent == domain {
            continue;
        }
        let fp = WildcardFingerprint::from_addresses(addresses.clone());
        let key = fp.addresses.join(",");
        by_parent
            .entry(parent.to_string())
            .or_default()
            .entry(key)
            .or_default()
            .insert(label.to_string());
    }
    for (parent, fps) in by_parent {
        for (key, labels) in fps {
            if labels.len() >= 2 {
                let addrs: Vec<String> = key.split(',').filter(|s| !s.is_empty()).map(|s| s.to_string()).collect();
                if !addrs.is_empty() {
                    filter.register(parent.clone(), WildcardFingerprint::from_addresses(addrs));
                }
            }
        }
    }
}

/// Run enumeration against provided resolvers.
pub async fn run_enum(
    cfg: EngineConfig,
    wordlist: &[String],
    resolvers: Vec<SocketAddr>,
) -> anyhow::Result<EnumResult> {
    if resolvers.is_empty() {
        anyhow::bail!("no resolvers");
    }
    let domain = cfg.domain.trim_end_matches('.').to_ascii_lowercase();
    let mut candidates = Vec::with_capacity(wordlist.len());
    if cfg.fqdn_list {
        for w in wordlist {
            let t = w.trim().trim_end_matches('.').to_ascii_lowercase();
            if t.is_empty() || t.starts_with('#') {
                continue;
            }
            candidates.push(t);
        }
    } else {
        for w in wordlist {
            if let Some(fqdn) = expand_label(w, &domain) {
                candidates.push(fqdn);
            }
        }
    }
    let candidate_count = candidates.len() as u64;

    // Sync resolve on a dedicated OS thread so the async runtime (if any) is free
    // and the OS-thread mock server can answer without scheduler contention.
    let cfg_c = cfg.clone();
    let domain_c = domain.clone();
    let (names, stats, wildcard_filter) = std::thread::spawn(move || {
        let mut resolver = SyncResolver::new(resolvers, cfg_c.clone())?;
        let start = Instant::now();

        // Phase 1: base-domain wildcard probes (apex catch-all)
        let mut wildcard_filter = WildcardFilter::new();
        {
            let mut rng = rand::thread_rng();
            let probes = probe_names(&domain_c, cfg_c.wildcard_probes.max(1), 12, &mut rng);
            let probe_results = resolver.resolve(&probes);
            let fps = fingerprints_for_wildcard_probes(&probe_results);
            for fp in fps {
                wildcard_filter.register(domain_c.clone(), fp);
            }
            if !cfg_c.quiet && !wildcard_filter.is_empty() {
                eprintln!(
                    "[vegadns] wildcard fingerprint registered for {domain_c} ({} parents)",
                    wildcard_filter.parents().count()
                );
            }
        }

        // Phase 2: main resolve
        let mut results = resolver.resolve(&candidates);

        // Phase 2b: recovery for UDP drops until clear or 3 passes (R=1 gate).
        {
            let old_c = resolver.cfg.concurrency;
            let old_t = resolver.cfg.timeout;
            let old_r = resolver.cfg.retries;
            for pass in 0..3 {
                let mut missing: Vec<String> = Vec::new();
                let mut missing_idx: Vec<usize> = Vec::new();
                for (i, (name, class)) in results.iter().enumerate() {
                    if matches!(class, ResponseClass::Error { .. } | ResponseClass::Garbage) {
                        missing.push(name.clone());
                        missing_idx.push(i);
                    }
                }
                if missing.is_empty() {
                    break;
                }
                let nmiss = missing.len();
                resolver.cfg.concurrency = match pass {
                    0 => (nmiss.min(800)).max(64),
                    1 => (nmiss.min(256)).max(32),
                    _ => (nmiss.min(64)).max(8),
                };
                resolver.cfg.timeout = old_t.max(Duration::from_millis(600));
                resolver.cfg.retries = old_r.max(3);
                let recovered = resolver.resolve(&missing);
                for (j, rec) in recovered.into_iter().enumerate() {
                    let i = missing_idx[j];
                    if !matches!(rec.1, ResponseClass::Error { .. }) {
                        results[i] = rec;
                    }
                }
            }
            // Single-name last resort for any remaining timeouts (usually 0–5 names).
            let leftovers: Vec<(usize, String)> = results
                .iter()
                .enumerate()
                .filter(|(_, (_, c))| matches!(c, ResponseClass::Error { .. } | ResponseClass::Garbage))
                .map(|(i, (n, _))| (i, n.clone()))
                .collect();
            if !leftovers.is_empty() {
                resolver.cfg.concurrency = 1;
                resolver.cfg.timeout = Duration::from_millis(1200);
                resolver.cfg.retries = 5;
                for (i, name) in leftovers {
                    let rec = resolver.resolve(std::slice::from_ref(&name));
                    if let Some(r) = rec.into_iter().next() {
                        if !matches!(r.1, ResponseClass::Error { .. }) {
                            results[i] = r;
                        }
                    }
                }
            }
            resolver.cfg.concurrency = old_c;
            resolver.cfg.timeout = old_t;
            resolver.cfg.retries = old_r;
        }

        // Phase 3: infer nested wildcards from multi-label answer agreement.
        infer_wildcards_from_results(&results, &domain_c, &mut wildcard_filter);

        let mut needs_probe: Vec<String> = Vec::new();
        let mut seen_parent = AHashSet::new();
        for (name, class) in &results {
            if !is_positive_hit(class) {
                continue;
            }
            let Some((_, parent)) = name.split_once('.') else {
                continue;
            };
            if parent == domain_c {
                continue;
            }
            let already = wildcard_filter.parents().any(|(p, _)| p == parent);
            if !already && seen_parent.insert(parent.to_string()) {
                needs_probe.push(parent.to_string());
            }
        }
        // Nested probes only for parents that already share an answer fingerprint
        // with ≥2 siblings (infer_wildcards already registered pure catch-alls).
        // Cap probes: precision without multi-second parent fan-out.
        needs_probe.truncate(16);
        if !needs_probe.is_empty() && cfg_c.wildcard_probes > 0 {
            let mut all_probes = Vec::new();
            let mut rng = rand::thread_rng();
            let probes_per = cfg_c.wildcard_probes.max(1).min(2);
            for parent in &needs_probe {
                all_probes.extend(probe_names(parent, probes_per, 12, &mut rng));
            }
            let probe_results = resolver.resolve(&all_probes);
            for parent in &needs_probe {
                let parent_results: Vec<(String, ResponseClass)> = probe_results
                    .iter()
                    .filter(|(n, _)| n.ends_with(&format!(".{parent}")) || n == parent)
                    .cloned()
                    .collect();
                let fps = fingerprints_for_wildcard_probes(&parent_results);
                for fp in fps {
                    wildcard_filter.register(parent.clone(), fp);
                }
            }
        }

        let mut dedup = Deduper::new();
        let mut found_raw = 0u64;
        for (name, class) in results {
            if let ResponseClass::Live { addresses } = class {
                found_raw += 1;
                if wildcard_filter.allow(&name, &addresses) {
                    dedup.insert(name);
                }
            }
        }

        let stats = EngineStats {
            queries_sent: resolver.counters.queries_sent.load(Ordering::Relaxed),
            responses_ok: resolver.counters.responses_ok.load(Ordering::Relaxed),
            nxdomain: resolver.counters.nxdomain.load(Ordering::Relaxed),
            errors: resolver.counters.errors.load(Ordering::Relaxed),
            timeouts: resolver.counters.timeouts.load(Ordering::Relaxed),
            found_raw,
            found_after_wildcard: dedup.len() as u64,
            wildcard_parents: wildcard_filter.parents().count() as u64,
            elapsed: start.elapsed(),
            candidates: candidate_count,
        };

        Ok::<_, anyhow::Error>((dedup.into_vec(), stats, wildcard_filter))
    })
    .join()
    .map_err(|_| anyhow::anyhow!("resolve thread panicked"))??;

    Ok(EnumResult {
        names,
        stats,
        wildcard_filter,
    })
}

/// Run with an embedded mock zone (offline path).
///
/// Mock DNS runs on a dedicated OS thread; resolve uses the sync poll loop
/// on a blocking worker so the two never fight over one async scheduler.
pub async fn run_enum_with_mock(
    mut cfg: EngineConfig,
    wordlist: &[String],
    zone: MockZone,
) -> anyhow::Result<(EnumResult, SocketAddr)> {
    if cfg.domain.is_empty() {
        cfg.domain = zone.base.clone();
    }
    let server = MockServer::spawn(zone)?;
    let addr = server.addr;
    let resolvers = vec![addr];
    // Yield once so the mock OS thread is scheduled before the first burst.
    tokio::task::yield_now().await;
    let result = run_enum(cfg, wordlist, resolvers).await;
    server.shutdown();
    Ok((result?, addr))
}

/// Compute recall against known-true set.
pub fn recall(found: &[String], known_true: &[String]) -> f64 {
    if known_true.is_empty() {
        return 1.0;
    }
    let set: AHashSet<&str> = found.iter().map(|s| s.as_str()).collect();
    let hit = known_true.iter().filter(|k| set.contains(k.as_str())).count();
    hit as f64 / known_true.len() as f64
}

pub fn precision(found: &[String], known_true: &[String]) -> f64 {
    if found.is_empty() {
        return 1.0;
    }
    let set: AHashSet<&str> = known_true.iter().map(|s| s.as_str()).collect();
    let hit = found.iter().filter(|f| set.contains(f.as_str())).count();
    hit as f64 / found.len() as f64
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[tokio::test]
    async fn mock_enum_finds_known_true_filters_wildcard() {
        let mut records = HashMap::new();
        records.insert("www.bench.test".into(), vec!["1.2.3.4".into()]);
        records.insert("mail.bench.test".into(), vec!["1.2.3.5".into()]);
        records.insert("api.bench.test".into(), vec!["1.2.3.6".into()]);
        let mut wildcards = HashMap::new();
        wildcards.insert("dev.bench.test".into(), vec!["9.9.9.9".into()]);
        let zone = MockZone {
            base: "bench.test".into(),
            records,
            wildcards,
        };
        let wordlist = vec![
            "www".into(),
            "mail".into(),
            "api".into(),
            "nope".into(),
            "garbage123".into(),
            "foo.dev".into(),
            "bar.dev".into(),
            "zzz".into(),
        ];
        let cfg = EngineConfig {
            domain: "bench.test".into(),
            concurrency: 100,
            timeout: Duration::from_millis(500),
            retries: 1,
            sockets: 2,
            wildcard_probes: 2,
            quiet: true,
            fqdn_list: false,
        };
        let (result, _) = run_enum_with_mock(cfg, &wordlist, zone).await.unwrap();
        assert!(result.names.contains(&"www.bench.test".into()));
        assert!(result.names.contains(&"mail.bench.test".into()));
        assert!(result.names.contains(&"api.bench.test".into()));
        assert!(!result.names.iter().any(|n| n.contains(".dev.bench.test")));
        assert!(!result.names.contains(&"nope.bench.test".into()));
    }

    #[tokio::test]
    async fn mock_bench_fixture_prec_and_recall() {
        let zone_path = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/zone_bench.json");
        let zone = MockZone::from_path(zone_path).unwrap();
        let known = zone.known_true_names();
        let wl_path = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/wordlist_bench.txt");
        let words = load_wordlist(wl_path).unwrap();
        let cfg = EngineConfig {
            domain: "bench.test".into(),
            concurrency: 8000,
            timeout: Duration::from_millis(500),
            retries: 1,
            sockets: 2,
            wildcard_probes: 2,
            quiet: true,
            fqdn_list: false,
        };
        let (result, _) = run_enum_with_mock(cfg, &words, zone).await.unwrap();
        assert_eq!(recall(&result.names, &known), 1.0);
        assert_eq!(precision(&result.names, &known), 1.0);
        assert!(!result.names.iter().any(|n| n.contains(".wild.bench.test")));
    }

    #[test]
    fn infer_wildcard_from_multi_label_same_fp() {
        let results = vec![
            (
                "foo.wild.bench.test".into(),
                ResponseClass::Live {
                    addresses: vec!["9.9.9.9".into()],
                },
            ),
            (
                "bar.wild.bench.test".into(),
                ResponseClass::Live {
                    addresses: vec!["9.9.9.9".into()],
                },
            ),
            (
                "www.bench.test".into(),
                ResponseClass::Live {
                    addresses: vec!["1.2.3.4".into()],
                },
            ),
        ];
        let mut filter = WildcardFilter::new();
        infer_wildcards_from_results(&results, "bench.test", &mut filter);
        assert!(!filter.allow("zzz.wild.bench.test", &["9.9.9.9".into()]));
        assert!(filter.allow("www.bench.test", &["1.2.3.4".into()]));
    }
}
