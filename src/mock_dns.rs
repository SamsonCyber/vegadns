//! Embedded mock DNS zone for offline correctness and benchmarks.
//!
//! Serves on a dedicated OS thread (std UDP) so the resolve hot path can
//! busy-poll without starving the responder on small VMs.

use std::collections::HashMap;
use std::net::{SocketAddr, UdpSocket};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::Duration;

use serde::Deserialize;

use crate::dns_packet::{
    build_response_a, build_response_nxdomain, build_response_servfail, parse_ipv4,
    parse_question_name,
};

#[derive(Debug, Clone, Deserialize)]
pub struct MockZone {
    /// Base domain (informational; records are absolute keys).
    pub base: String,
    /// Exact name → list of A records (dotted IPv4).
    #[serde(default)]
    pub records: HashMap<String, Vec<String>>,
    /// Parent suffix → A records for any label under that parent (wildcard).
    /// Key "dev.bench.test" means *.dev.bench.test answers with those IPs.
    #[serde(default)]
    pub wildcards: HashMap<String, Vec<String>>,
}

impl MockZone {
    pub fn from_path(path: impl AsRef<Path>) -> anyhow::Result<Self> {
        let text = std::fs::read_to_string(path)?;
        let zone: MockZone = serde_json::from_str(&text)?;
        Ok(zone)
    }

    pub fn known_true_names(&self) -> Vec<String> {
        let mut names: Vec<String> = self.records.keys().cloned().collect();
        names.sort();
        names
    }

    pub fn lookup_addrs(&self, qname: &str) -> Option<Vec<[u8; 4]>> {
        let name = qname.trim_end_matches('.').to_ascii_lowercase();
        if let Some(list) = self.records.get(&name) {
            return Some(list.iter().filter_map(|s| parse_ipv4(s)).collect());
        }
        // Wildcard: longest matching parent
        let mut best: Option<(usize, &Vec<String>)> = None;
        for (parent, addrs) in &self.wildcards {
            let parent = parent.trim_end_matches('.').to_ascii_lowercase();
            let suffix = format!(".{parent}");
            if name.ends_with(&suffix) && name.len() > suffix.len() {
                let score = parent.len();
                if best.map(|(s, _)| score > s).unwrap_or(true) {
                    best = Some((score, addrs));
                }
            }
        }
        best.map(|(_, addrs)| addrs.iter().filter_map(|s| parse_ipv4(s)).collect())
    }
}

/// Network-stress knobs for true-ish local tests (not a public internet model).
#[derive(Debug, Clone, Copy)]
pub struct MockStress {
    /// Fixed delay before each reply (simulates RTT / slow recursive).
    pub latency_ms: u64,
    /// 0..=100 chance to return SERVFAIL instead of the real answer.
    pub servfail_pct: u8,
    /// 0..=100 chance to drop the query (no reply).
    pub drop_pct: u8,
}

impl Default for MockStress {
    fn default() -> Self {
        Self {
            latency_ms: 0,
            servfail_pct: 0,
            drop_pct: 0,
        }
    }
}

pub struct MockServer {
    pub addr: SocketAddr,
    stop: Arc<AtomicBool>,
    handle: Option<JoinHandle<()>>,
}

impl MockServer {
    pub fn spawn(zone: MockZone) -> anyhow::Result<Self> {
        Self::spawn_on(zone, "127.0.0.1:0")
    }

    pub fn spawn_on(zone: MockZone, bind: &str) -> anyhow::Result<Self> {
        Self::spawn_on_with_stress(zone, bind, MockStress::default())
    }

    pub fn spawn_on_with_stress(
        zone: MockZone,
        bind: &str,
        stress: MockStress,
    ) -> anyhow::Result<Self> {
        let sock = UdpSocket::bind(bind)?;
        // Short timeout so shutdown is prompt; recv still blocks most of the time.
        sock.set_read_timeout(Some(Duration::from_millis(5)))?;
        let addr = sock.local_addr()?;
        let stop = Arc::new(AtomicBool::new(false));
        let stop_t = Arc::clone(&stop);
        let zone = Arc::new(zone);
        // Cloneable socket for parallel stress replies (UDP is fine concurrent).
        let sock = Arc::new(sock);
        let sock_loop = Arc::clone(&sock);
        let handle = std::thread::Builder::new()
            .name("vegadns-mock".into())
            .spawn(move || {
                use rand::Rng;
                let mut rng = rand::thread_rng();
                let mut buf = [0u8; 2048];
                while !stop_t.load(Ordering::Relaxed) {
                    match sock_loop.recv_from(&mut buf) {
                        Ok((n, peer)) => {
                            let pkt = buf[..n].to_vec();
                            let Ok((id, qname)) = parse_question_name(&pkt) else {
                                continue;
                            };
                            // Drop: silent (resolver sees timeout).
                            if stress.drop_pct > 0 && rng.gen_range(0..100u8) < stress.drop_pct {
                                continue;
                            }
                            let zone = Arc::clone(&zone);
                            let sock_r = Arc::clone(&sock_loop);
                            let do_servfail = stress.servfail_pct > 0
                                && rng.gen_range(0..100u8) < stress.servfail_pct;
                            let latency = stress.latency_ms;
                            // Parallel replies so latency does not serialize the whole gym.
                            std::thread::spawn(move || {
                                if latency > 0 {
                                    std::thread::sleep(Duration::from_millis(latency));
                                }
                                let reply = if do_servfail {
                                    build_response_servfail(id, &qname).ok()
                                } else {
                                    match zone.lookup_addrs(&qname) {
                                        Some(addrs) if !addrs.is_empty() => {
                                            build_response_a(id, &qname, &addrs, 60).ok()
                                        }
                                        _ => build_response_nxdomain(id, &qname).ok(),
                                    }
                                };
                                if let Some(reply) = reply {
                                    let _ = sock_r.send_to(&reply, peer);
                                }
                            });
                        }
                        Err(ref e)
                            if e.kind() == std::io::ErrorKind::WouldBlock
                                || e.kind() == std::io::ErrorKind::TimedOut =>
                        {
                            continue;
                        }
                        Err(_) => break,
                    }
                }
            })?;
        Ok(Self {
            addr,
            stop,
            handle: Some(handle),
        })
    }

    pub fn resolver_line(&self) -> String {
        format!("{}", self.addr.ip())
    }

    pub fn resolver_host_port(&self) -> String {
        format!("{}:{}", self.addr.ip(), self.addr.port())
    }

    pub fn shutdown(mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(h) = self.handle.take() {
            let _ = h.join();
        }
    }
}

impl Drop for MockServer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(h) = self.handle.take() {
            let _ = h.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wildcard_longest_match() {
        let mut zone = MockZone {
            base: "bench.test".into(),
            records: HashMap::new(),
            wildcards: HashMap::new(),
        };
        zone.records
            .insert("www.bench.test".into(), vec!["1.1.1.1".into()]);
        zone.wildcards
            .insert("dev.bench.test".into(), vec!["9.9.9.9".into()]);
        assert_eq!(
            zone.lookup_addrs("www.bench.test").unwrap()[0],
            [1, 1, 1, 1]
        );
        assert_eq!(
            zone.lookup_addrs("foo.dev.bench.test").unwrap()[0],
            [9, 9, 9, 9]
        );
        assert!(zone.lookup_addrs("nope.bench.test").is_none());
    }
}
