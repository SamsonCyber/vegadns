//! User-facing CLI / TUI-style status output (stderr).
//!
//! Names and machine data stay on stdout. Progress, banners, and summaries
//! go to stderr so pipes stay clean (`vegadns enum ... | httpx`).

use std::io::{self, IsTerminal, Write};
use std::time::Duration;

use crate::engine::EngineStats;
use crate::paths_engine::PathsStats;

/// Whether stderr is an interactive terminal (color + \r progress).
pub fn stderr_is_tty() -> bool {
    io::stderr().is_terminal()
}

fn use_color() -> bool {
    if !stderr_is_tty() {
        return false;
    }
    // Respect NO_COLOR (https://no-color.org/)
    std::env::var_os("NO_COLOR").is_none()
}

const RESET: &str = "\x1b[0m";
const BOLD: &str = "\x1b[1m";
const DIM: &str = "\x1b[2m";
const CYAN: &str = "\x1b[36m";
const GREEN: &str = "\x1b[32m";
const YELLOW: &str = "\x1b[33m";
const _RED: &str = "\x1b[31m";

fn paint(code: &str, s: &str) -> String {
    if use_color() {
        format!("{code}{s}{RESET}")
    } else {
        s.to_string()
    }
}

/// Brand tag for every human status line.
pub fn tag() -> String {
    paint(CYAN, "vegadns")
}

pub fn info(msg: &str) {
    let _ = writeln!(io::stderr(), "{}  {}", tag(), msg);
}

pub fn ok(msg: &str) {
    let _ = writeln!(
        io::stderr(),
        "{}  {} {}",
        tag(),
        paint(GREEN, "ok"),
        msg
    );
}

pub fn warn(msg: &str) {
    let _ = writeln!(
        io::stderr(),
        "{}  {} {}",
        tag(),
        paint(YELLOW, "warn"),
        msg
    );
}

/// Clear current progress line (TTY only).
pub fn clear_progress_line() {
    if stderr_is_tty() {
        let _ = write!(io::stderr(), "\r\x1b[2K");
        let _ = io::stderr().flush();
    }
}

/// In-place progress line for long enum runs (TTY). Falls back to rare newlines.
pub fn progress_resolve(done: u64, total: u64, queries: u64, found_hint: u64, elapsed: Duration) {
    if total == 0 {
        return;
    }
    let pct = (done as f64 * 100.0 / total as f64).min(100.0);
    let qps = if elapsed.as_secs_f64() > 0.0 {
        queries as f64 / elapsed.as_secs_f64()
    } else {
        0.0
    };
    let bar = progress_bar(pct, 18);
    let line = format!(
        "{}  resolve  {}  {:>5.1}%  {}/{}  q={}  ~found={}  {:>7.0} q/s  {:.1}s",
        tag(),
        bar,
        pct,
        done,
        total,
        queries,
        found_hint,
        qps,
        elapsed.as_secs_f64()
    );
    if stderr_is_tty() {
        let _ = write!(io::stderr(), "\r\x1b[2K{line}");
        let _ = io::stderr().flush();
    }
}

fn progress_bar(pct: f64, width: usize) -> String {
    let filled = ((pct / 100.0) * width as f64).round() as usize;
    let filled = filled.min(width);
    let empty = width.saturating_sub(filled);
    let body = format!("{}{}", "█".repeat(filled), "░".repeat(empty));
    if use_color() {
        format!("{CYAN}[{body}]{RESET}")
    } else {
        format!("[{body}]")
    }
}

/// Enum start banner (key knobs only).
pub fn enum_start(
    domain: &str,
    labels: usize,
    depth: Option<&str>,
    permute: bool,
    resolvers: usize,
    concurrency: usize,
    mock: bool,
) {
    let mode = if mock { "mock" } else { "live" };
    let depth_s = depth.unwrap_or("-");
    let perm = if permute { "on" } else { "off" };
    info(&format!(
        "{}  {}  labels={}  depth={}  permute={}  resolvers={}  concurrency={}",
        paint(BOLD, "enum"),
        paint(DIM, mode),
        labels,
        depth_s,
        perm,
        resolvers,
        concurrency
    ));
    if mock {
        info(&format!("domain  {}", paint(BOLD, domain)));
    } else {
        info(&format!("target  {}", paint(BOLD, domain)));
    }
}

/// Paths start banner.
pub fn paths_start(base: &str, candidates: usize, concurrency: usize, soft404: usize) {
    info(&format!(
        "{}  candidates={}  concurrency={}  soft404-probes={}",
        paint(BOLD, "paths"),
        candidates,
        concurrency,
        soft404
    ));
    info(&format!("base  {}", paint(BOLD, base)));
}

/// Final enum summary panel (aligned columns).
pub fn enum_done(s: &EngineStats, names_out: usize) {
    clear_progress_line();
    let wall = s.elapsed.as_secs_f64();
    let qps = s.query_rate();
    let lines = [
        format!("{}  {}", tag(), paint(GREEN, "done  enum")),
        format_kv("found", &format!("{}", s.found_after_wildcard)),
        format_kv("raw_hits", &format!("{}", s.found_raw)),
        format_kv("emitted", &format!("{names_out}")),
        format_kv("candidates", &format!("{}", s.candidates)),
        format_kv("queries", &format!("{}", s.queries_sent)),
        format_kv("qps", &format!("{qps:.0}")),
        format_kv("wall", &format!("{wall:.3}s")),
        format_kv("nxdomain", &format!("{}", s.nxdomain)),
        format_kv("timeouts", &format!("{}", s.timeouts)),
        format_kv(
            "wildcards",
            &if s.wildcard_parents > 0 {
                paint(YELLOW, &s.wildcard_parents.to_string())
            } else {
                s.wildcard_parents.to_string()
            },
        ),
    ];
    for line in lines {
        let _ = writeln!(io::stderr(), "{line}");
    }
}

pub fn paths_done(s: &PathsStats, urls_out: usize) {
    clear_progress_line();
    let wall = s.elapsed.as_secs_f64();
    let rps = s.request_rate();
    let lines = [
        format!("{}  {}", tag(), paint(GREEN, "done  paths")),
        format_kv("hits", &format!("{}", s.hits)),
        format_kv("emitted", &format!("{urls_out}")),
        format_kv("candidates", &format!("{}", s.candidates)),
        format_kv("requests", &format!("{}", s.requests)),
        format_kv("rps", &format!("{rps:.0}")),
        format_kv("wall", &format!("{wall:.3}s")),
        format_kv(
            "soft404_drop",
            &if s.soft404_dropped > 0 {
                paint(YELLOW, &s.soft404_dropped.to_string())
            } else {
                s.soft404_dropped.to_string()
            },
        ),
        format_kv("errors", &format!("{}", s.errors)),
    ];
    for line in lines {
        let _ = writeln!(io::stderr(), "{line}");
    }
}

pub fn recall_precision(r: f64, p: f64, known: usize, found: usize) {
    let ok = (r - 1.0).abs() < 1e-9 && (p - 1.0).abs() < 1e-9;
    let mark = if ok {
        paint(GREEN, "ok")
    } else {
        paint(YELLOW, "note")
    };
    let _ = writeln!(
        io::stderr(),
        "{}  {}  recall={:.3}  precision={:.3}  known={}  found={}",
        tag(),
        mark,
        r,
        p,
        known,
        found
    );
}

pub fn wrote(kind: &str, n: usize, path: &str) {
    info(&format!("wrote {n} {kind} → {path}"));
}

fn format_kv(key: &str, val: &str) -> String {
    format!("{}    {:<12} {}", tag(), paint(DIM, key), val)
}

/// Pretty depth/preset tables for `wordlist list`.
pub fn print_depth_table(
    rows: &[(String, u8, String, usize, String, String)],
) {
    // depth, level, list, labels, permute, notes
    println!(
        "{}  {}",
        tag(),
        paint(BOLD, "scan depths  (fast → final)")
    );
    println!(
        "  {:<8} {:>5}  {:<8} {:>8}  {:<28}  {}",
        "depth", "lvl", "list", "labels", "permute", "notes"
    );
    println!("  {}", "─".repeat(78));
    for (depth, lvl, list, labels, perm, notes) in rows {
        println!(
            "  {:<8} {:>5}  {:<8} {:>8}  {:<28}  {}",
            depth, lvl, list, labels, perm, notes
        );
    }
    println!();
}

pub fn print_preset_table(rows: &[(String, usize, String)]) {
    println!("{}  {}", tag(), paint(BOLD, "wordlist packs"));
    println!("  {:<10} {:>8}  {}", "preset", "labels", "role");
    println!("  {}", "─".repeat(56));
    for (name, n, role) in rows {
        println!("  {:<10} {:>8}  {}", name, n, role);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn progress_bar_bounds() {
        assert!(progress_bar(0.0, 10).contains('░') || progress_bar(0.0, 10).contains('['));
        assert!(progress_bar(100.0, 10).contains('█') || progress_bar(100.0, 10).contains('['));
    }

    #[test]
    fn paint_no_panic() {
        let _ = paint(YELLOW, "x");
        let _ = tag();
        let _ = paint(_RED, "y");
    }
}
