//! Embedded lab HTTP fixture.
//!
//! Modes:
//! - simple: known paths → 200 short body; else 404
//! - hard: known paths → 200 unique body; missing → soft-404 200 fixed body
//!   Optional protected paths → 401 / 403 with distinct bodies

use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::sync::Arc;

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::oneshot;
use tokio::task::JoinHandle;

/// Fixed soft-404 body (stable length fingerprint for calibration).
pub const SOFT404_BODY: &[u8] =
    b"<!DOCTYPE html><html><body>Not Found soft404-lab-pad.</body></html>\n";

/// Behavior for one relative path.
#[derive(Debug, Clone)]
pub enum PathBehavior {
    /// 200 with unique short body (real hit).
    Hit200,
    /// 401 with distinct body (auth wall — still interesting).
    Hit401,
    /// 403 with distinct body.
    Hit403,
}

/// Full mock zone for hard coverage suites.
#[derive(Debug, Clone, Default)]
pub struct MockHttpZone {
    /// path (no leading slash) → behavior
    pub paths: HashMap<String, PathBehavior>,
    /// When true, unknown paths return soft-404 200 instead of 404.
    pub soft404: bool,
}

impl MockHttpZone {
    pub fn simple_hits(hits: HashSet<String>) -> Self {
        let mut paths = HashMap::new();
        for h in hits {
            let key = h.trim_start_matches('/').to_string();
            if !key.is_empty() {
                paths.insert(key, PathBehavior::Hit200);
            }
        }
        Self {
            paths,
            soft404: false,
        }
    }

    pub fn hard_from_map(paths: HashMap<String, PathBehavior>) -> Self {
        Self {
            paths,
            soft404: true,
        }
    }
}

/// Serves fixture paths; optional soft-404 on miss.
pub struct MockHttp {
    pub addr: std::net::SocketAddr,
    shutdown: Option<oneshot::Sender<()>>,
    handle: JoinHandle<()>,
}

impl MockHttp {
    pub async fn spawn(hit_paths: HashSet<String>) -> anyhow::Result<Self> {
        Self::spawn_zone(MockHttpZone::simple_hits(hit_paths)).await
    }

    pub async fn spawn_zone(zone: MockHttpZone) -> anyhow::Result<Self> {
        Self::spawn_zone_on("127.0.0.1:0", zone).await
    }

    pub async fn spawn_zone_on(bind: &str, zone: MockHttpZone) -> anyhow::Result<Self> {
        let listener = TcpListener::bind(bind).await?;
        let addr = listener.local_addr()?;
        let (tx, mut rx) = oneshot::channel::<()>();
        let zone = Arc::new(zone);
        let handle = tokio::spawn(async move {
            loop {
                tokio::select! {
                    _ = &mut rx => break,
                    acc = listener.accept() => {
                        match acc {
                            Ok((stream, _)) => {
                                let zone = Arc::clone(&zone);
                                tokio::spawn(async move {
                                    let _ = handle_client(stream, &zone).await;
                                });
                            }
                            Err(_) => break,
                        }
                    }
                }
            }
        });
        tokio::task::yield_now().await;
        Ok(Self {
            addr,
            shutdown: Some(tx),
            handle,
        })
    }

    pub fn base_url(&self) -> String {
        format!("http://{}/", self.addr)
    }

    pub async fn shutdown(mut self) {
        if let Some(tx) = self.shutdown.take() {
            let _ = tx.send(());
        }
        let _ = self.handle.await;
    }
}

async fn handle_client(
    mut stream: tokio::net::TcpStream,
    zone: &MockHttpZone,
) -> anyhow::Result<()> {
    let mut acc = Vec::with_capacity(1024);
    let mut buf = [0u8; 1024];
    loop {
        let n = tokio::time::timeout(
            std::time::Duration::from_secs(2),
            stream.read(&mut buf),
        )
        .await??;
        if n == 0 {
            break;
        }
        acc.extend_from_slice(&buf[..n]);
        if acc.windows(4).any(|w| w == b"\r\n\r\n") {
            break;
        }
        if acc.len() > 16_384 {
            break;
        }
    }
    if acc.is_empty() {
        return Ok(());
    }
    let req = String::from_utf8_lossy(&acc);
    let path = parse_request_path(&req).unwrap_or("/");
    let key = path.trim_start_matches('/').to_string();

    let (code, reason, body): (u16, &str, &[u8]) = match zone.paths.get(&key) {
        Some(PathBehavior::Hit200) => {
            // Distinct length vs soft-404 so calibration drops noise only.
            (200, "OK", b"vegadns-real-hit-body-v1\n")
        }
        Some(PathBehavior::Hit401) => (401, "Unauthorized", b"auth-required-vegadns\n"),
        Some(PathBehavior::Hit403) => (403, "Forbidden", b"forbidden-vegadns\n"),
        None if zone.soft404 => (200, "OK", SOFT404_BODY),
        None => (404, "Not Found", b"nope\n"),
    };
    let resp = format!(
        "HTTP/1.1 {code} {reason}\r\nContent-Length: {}\r\nConnection: close\r\nContent-Type: text/plain\r\n\r\n",
        body.len()
    );
    stream.write_all(resp.as_bytes()).await?;
    stream.write_all(body).await?;
    stream.shutdown().await.ok();
    Ok(())
}

fn parse_request_path(req: &str) -> Option<&str> {
    let line = req.lines().next()?;
    let mut parts = line.split_whitespace();
    let _method = parts.next()?;
    let path = parts.next()?;
    Some(path.split('?').next().unwrap_or(path))
}

/// Load hit paths file (one relative path per line) → simple Hit200 set.
pub fn load_hit_paths(path: impl AsRef<Path>) -> anyhow::Result<HashSet<String>> {
    let text = std::fs::read_to_string(path)?;
    let mut set = HashSet::new();
    for line in text.lines() {
        let t = line.trim();
        if t.is_empty() || t.starts_with('#') {
            continue;
        }
        set.insert(t.trim_start_matches('/').to_string());
    }
    Ok(set)
}

/// Load hard zone file: `path STATUS` per line (STATUS = 200|401|403). Default 200.
pub fn load_hard_zone(path: impl AsRef<Path>) -> anyhow::Result<MockHttpZone> {
    let text = std::fs::read_to_string(path)?;
    let mut paths = HashMap::new();
    for line in text.lines() {
        let t = line.trim();
        if t.is_empty() || t.starts_with('#') {
            continue;
        }
        let mut parts = t.split_whitespace();
        let p = parts.next().unwrap().trim_start_matches('/').to_string();
        let beh = match parts.next().unwrap_or("200") {
            "401" => PathBehavior::Hit401,
            "403" => PathBehavior::Hit403,
            _ => PathBehavior::Hit200,
        };
        paths.insert(p, beh);
    }
    Ok(MockHttpZone::hard_from_map(paths))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_get_path() {
        let req = "GET /admin HTTP/1.1\r\nHost: x\r\n\r\n";
        assert_eq!(parse_request_path(req), Some("/admin"));
    }

    #[test]
    fn soft404_body_stable_len() {
        // Distinct from real-hit body len (vegadns-real-hit-body-v1\n = 24).
        assert!(SOFT404_BODY.len() > 40);
        assert_ne!(SOFT404_BODY.len(), b"vegadns-real-hit-body-v1\n".len());
    }
}
