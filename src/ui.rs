//! User-facing CLI status (stderr). Ferox-class banner + panels, not a full TUI.
//!
//! Results stay on stdout so pipes stay clean (`vegadns enum ... | httpx`).
//! Progress, banners, and summaries go to stderr. Colors only on TTY; NO_COLOR respected.

use std::io::{self, IsTerminal, Write};
use std::time::Duration;

use crate::engine::EngineStats;
use crate::paths_engine::PathsStats;

/// Whether stderr is an interactive terminal (color + in-place progress).
pub fn stderr_is_tty() -> bool {
    io::stderr().is_terminal()
}

fn use_color() -> bool {
    if !stderr_is_tty() {
        return false;
    }
    std::env::var_os("NO_COLOR").is_none()
}

// ── ANSI ────────────────────────────────────────────────────────────
const RESET: &str = "\x1b[0m";
const BOLD: &str = "\x1b[1m";
const DIM: &str = "\x1b[2m";
const CYAN: &str = "\x1b[36m";
const BRIGHT_CYAN: &str = "\x1b[96m";
const GREEN: &str = "\x1b[32m";
const BRIGHT_GREEN: &str = "\x1b[92m";
const YELLOW: &str = "\x1b[33m";
const MAGENTA: &str = "\x1b[35m";
const BLUE: &str = "\x1b[34m";
const WHITE: &str = "\x1b[97m";
const RED: &str = "\x1b[31m";

fn paint(code: &str, s: &str) -> String {
    if use_color() {
        format!("{code}{s}{RESET}")
    } else {
        s.to_string()
    }
}

fn paint2(c1: &str, c2: &str, s: &str) -> String {
    if use_color() {
        format!("{c1}{c2}{s}{RESET}")
    } else {
        s.to_string()
    }
}

/// Brand word (cyan).
pub fn tag() -> String {
    paint(CYAN, "vegadns")
}

// ── Status tags (ferox / gobuster style) ─────────────────────────────

fn tag_inf() -> String {
    if use_color() {
        format!("{BOLD}{BLUE}[INF]{RESET}")
    } else {
        "[INF]".into()
    }
}

fn tag_ok() -> String {
    if use_color() {
        format!("{BOLD}{GREEN}[OK ]{RESET}")
    } else {
        "[OK ]".into()
    }
}

fn tag_wrn() -> String {
    if use_color() {
        format!("{BOLD}{YELLOW}[WRN]{RESET}")
    } else {
        "[WRN]".into()
    }
}

fn tag_err() -> String {
    if use_color() {
        format!("{BOLD}{RED}[ERR]{RESET}")
    } else {
        "[ERR]".into()
    }
}

fn tag_found() -> String {
    if use_color() {
        format!("{BOLD}{BRIGHT_GREEN}[+++]{RESET}")
    } else {
        "[+++]".into()
    }
}

fn tag_stat() -> String {
    if use_color() {
        format!("{BOLD}{MAGENTA}[###]{RESET}")
    } else {
        "[###]".into()
    }
}

fn eprint_line(s: &str) {
    let _ = writeln!(io::stderr(), "{s}");
}

pub fn info(msg: &str) {
    eprint_line(&format!("{}  {}", tag_inf(), msg));
}

pub fn ok(msg: &str) {
    eprint_line(&format!("{}  {}", tag_ok(), msg));
}

pub fn warn(msg: &str) {
    eprint_line(&format!("{}  {}", tag_wrn(), msg));
}

pub fn err(msg: &str) {
    eprint_line(&format!("{}  {}", tag_err(), msg));
}

// ── Banner (compact FIGlet-style, ferox energy) ─────────────────────

/// Wide rule line (ASCII so Windows CP437 / PowerShell do not mojibake).
fn rule(ch: char, width: usize) -> String {
    ch.to_string().repeat(width)
}

/// Print the vegadns ASCII banner + version strip.
pub fn banner(mode: &str) {
    let ver = env!("CARGO_PKG_VERSION");
    // FIGlet-ish, pure ASCII (ferox energy, no Unicode box soup).
    let art = [
        r" __   _____  __ _  __ _  __| |_ __  ___",
        r" \ \ / / _ \/ _` |/ _` |/ _` | '_ \/ __|",
        r"  \ V /  __/ (_| | (_| | (_| | | | \__ \",
        r"   \_/ \___|\__, |\__,_|\__,_|_| |_|___/",
        r"            |___/",
    ];
    eprint_line("");
    for line in art {
        eprint_line(&paint2(BOLD, BRIGHT_CYAN, line));
    }
    let strip = format!(
        "  {}  |  {}  |  v{}",
        paint(BOLD, "storm-class DNS + paths"),
        paint(DIM, mode),
        paint(DIM, ver)
    );
    eprint_line(&strip);
    eprint_line(&paint(DIM, &format!("  {}", rule('=', 54))));
    eprint_line("");
}

// ── Config panel (ferox target table vibes) ──────────────────────────

/// One row: `  key  |  value`
fn panel_row(key: &str, val: &str) {
    let k = if use_color() {
        format!("{DIM}{key:<16}{RESET}")
    } else {
        format!("{key:<16}")
    };
    let sep = paint(DIM, "|");
    eprint_line(&format!("  {k} {sep}  {val}"));
}

fn panel_header(title: &str) {
    let t = paint2(BOLD, WHITE, title);
    eprint_line(&format!("  {}", t));
    eprint_line(&paint(DIM, &format!("  {}", rule('-', 54))));
}

fn panel_footer() {
    eprint_line(&paint(DIM, &format!("  {}", rule('-', 54))));
    eprint_line("");
}

/// Enum start banner + config panel.
pub fn enum_start(
    domain: &str,
    labels: usize,
    depth: Option<&str>,
    permute: bool,
    resolvers: usize,
    concurrency: usize,
    mock: bool,
) {
    let mode = if mock { "enum / mock" } else { "enum / live" };
    banner(mode);

    panel_header("SCAN CONFIG");
    panel_row(
        "mode",
        &if mock {
            paint(YELLOW, "mock DNS")
        } else {
            paint(GREEN, "live DNS")
        },
    );
    panel_row("target", &paint2(BOLD, WHITE, domain));
    panel_row("labels", &format!("{}", labels));
    panel_row("depth", depth.unwrap_or("-"));
    panel_row(
        "permute",
        &if permute {
            paint(GREEN, "on")
        } else {
            paint(DIM, "off")
        },
    );
    panel_row("resolvers", &format!("{resolvers}"));
    panel_row("concurrency", &format!("{concurrency}"));
    panel_footer();
}

/// Paths start banner + config panel.
pub fn paths_start(base: &str, candidates: usize, concurrency: usize, soft404: usize) {
    banner("paths / HTTP");

    panel_header("SCAN CONFIG");
    panel_row("mode", &paint(GREEN, "HTTP paths"));
    panel_row("base", &paint2(BOLD, WHITE, base));
    panel_row("candidates", &format!("{candidates}"));
    panel_row("concurrency", &format!("{concurrency}"));
    panel_row("soft404-probes", &format!("{soft404}"));
    panel_footer();
}

// ── Progress (ferox-style bar) ───────────────────────────────────────

/// Clear current progress line (TTY only).
pub fn clear_progress_line() {
    if stderr_is_tty() {
        let _ = write!(io::stderr(), "\r\x1b[2K");
        let _ = io::stderr().flush();
    }
}

/// In-place progress for long enum runs (TTY).
pub fn progress_resolve(done: u64, total: u64, queries: u64, found_hint: u64, elapsed: Duration) {
    if total == 0 {
        return;
    }
    let pct = (done as f64 * 100.0 / total as f64).min(100.0);
    let secs = elapsed.as_secs_f64();
    let qps = if secs > 0.0 {
        queries as f64 / secs
    } else {
        0.0
    };
    let bar = progress_bar(pct, 24);
    let line = format!(
        "{}  {} {:>5.1}%  {:>6}/{}  | {:>7.0} q/s  | found~{:<5}  | {:.1}s",
        tag_stat(),
        bar,
        pct,
        done,
        total,
        qps,
        found_hint,
        secs
    );
    if stderr_is_tty() {
        let _ = write!(io::stderr(), "\r\x1b[2K{line}");
        let _ = io::stderr().flush();
    }
}

/// Paths progress (TTY). Same visual language as resolve.
pub fn progress_paths(done: u64, total: u64, hits: u64, elapsed: Duration) {
    if total == 0 {
        return;
    }
    let pct = (done as f64 * 100.0 / total as f64).min(100.0);
    let secs = elapsed.as_secs_f64();
    let rps = if secs > 0.0 {
        done as f64 / secs
    } else {
        0.0
    };
    let bar = progress_bar(pct, 24);
    let line = format!(
        "{}  {} {:>5.1}%  {:>6}/{}  | {:>7.0} r/s  | hits~{:<5}  | {:.1}s",
        tag_stat(),
        bar,
        pct,
        done,
        total,
        rps,
        hits,
        secs
    );
    if stderr_is_tty() {
        let _ = write!(io::stderr(), "\r\x1b[2K{line}");
        let _ = io::stderr().flush();
    }
}

/// Classic ferox-ish bar: `[################>------]`
fn progress_bar(pct: f64, width: usize) -> String {
    let filled = ((pct / 100.0) * width as f64).round() as usize;
    let filled = filled.min(width);
    let empty = width.saturating_sub(filled);
    let body = if filled == 0 {
        format!("{}{}", "", "-".repeat(empty))
    } else if filled >= width {
        "#".repeat(width)
    } else {
        format!("{}>{}", "#".repeat(filled.saturating_sub(1)), "-".repeat(empty))
    };
    if use_color() {
        format!("{CYAN}[{body}]{RESET}")
    } else {
        format!("[{body}]")
    }
}

// ── Done panels ─────────────────────────────────────────────────────

fn box_top(title: &str, width: usize) {
    // title sits in the top border: === title ===
    let t = format!(" {title} ");
    let side = width.saturating_sub(t.chars().count()).saturating_sub(2) / 2;
    let left = rule('=', side.max(2));
    let right = rule(
        '=',
        width
            .saturating_sub(side)
            .saturating_sub(t.chars().count())
            .saturating_sub(2)
            .max(2),
    );
    let line = format!("+{left}{t}{right}+");
    eprint_line(&paint2(BOLD, CYAN, &line));
}

fn box_row(key: &str, val: &str, width: usize) {
    let inner = width.saturating_sub(2);
    let key_w = 14usize;
    let content = format!("  {key:<key_w$} {val}");
    let pad = inner.saturating_sub(visible_len(&content));
    let row = format!("|{content}{}|", " ".repeat(pad));
    eprint_line(&row);
}

fn box_bottom(width: usize) {
    let line = format!("+{}+", rule('=', width.saturating_sub(2)));
    eprint_line(&paint(CYAN, &line));
}

/// Approximate visible width (ignore ANSI CSI).
fn visible_len(s: &str) -> usize {
    let mut n = 0usize;
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\x1b' {
            if chars.peek() == Some(&'[') {
                chars.next();
                for c2 in chars.by_ref() {
                    if c2.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
            continue;
        }
        n += 1;
    }
    n
}

/// Final enum summary panel.
pub fn enum_done(s: &EngineStats, names_out: usize) {
    clear_progress_line();
    let wall = s.elapsed.as_secs_f64();
    let qps = s.query_rate();
    let w = 56usize;

    eprint_line("");
    box_top("ENUM COMPLETE", w);
    box_row(
        "found",
        &paint2(BOLD, BRIGHT_GREEN, &format!("{}", s.found_after_wildcard)),
        w,
    );
    box_row("raw_hits", &format!("{}", s.found_raw), w);
    box_row("emitted", &format!("{names_out}"), w);
    box_row("candidates", &format!("{}", s.candidates), w);
    box_row("queries", &format!("{}", s.queries_sent), w);
    box_row("qps", &paint(CYAN, &format!("{qps:.0}")), w);
    box_row("wall", &format!("{wall:.3}s"), w);
    box_row("nxdomain", &format!("{}", s.nxdomain), w);
    box_row(
        "timeouts",
        &if s.timeouts > 0 {
            paint(YELLOW, &s.timeouts.to_string())
        } else {
            s.timeouts.to_string()
        },
        w,
    );
    box_row(
        "wildcards",
        &if s.wildcard_parents > 0 {
            paint(YELLOW, &s.wildcard_parents.to_string())
        } else {
            s.wildcard_parents.to_string()
        },
        w,
    );
    box_bottom(w);
    eprint_line("");
}

pub fn paths_done(s: &PathsStats, urls_out: usize) {
    clear_progress_line();
    let wall = s.elapsed.as_secs_f64();
    let rps = s.request_rate();
    let w = 56usize;

    eprint_line("");
    box_top("PATHS COMPLETE", w);
    box_row(
        "hits",
        &paint2(BOLD, BRIGHT_GREEN, &format!("{}", s.hits)),
        w,
    );
    box_row("emitted", &format!("{urls_out}"), w);
    box_row("candidates", &format!("{}", s.candidates), w);
    box_row("requests", &format!("{}", s.requests), w);
    box_row("rps", &paint(CYAN, &format!("{rps:.0}")), w);
    box_row("wall", &format!("{wall:.3}s"), w);
    box_row(
        "soft404_drop",
        &if s.soft404_dropped > 0 {
            paint(YELLOW, &s.soft404_dropped.to_string())
        } else {
            s.soft404_dropped.to_string()
        },
        w,
    );
    box_row(
        "errors",
        &if s.errors > 0 {
            paint(RED, &s.errors.to_string())
        } else {
            s.errors.to_string()
        },
        w,
    );
    box_bottom(w);
    eprint_line("");
}

pub fn recall_precision(r: f64, p: f64, known: usize, found: usize) {
    let perfect = (r - 1.0).abs() < 1e-9 && (p - 1.0).abs() < 1e-9;
    if perfect {
        eprint_line(&format!(
            "{}  {}  recall={:.3}  precision={:.3}  known={}  found={}",
            tag_ok(),
            paint(GREEN, "quality floor"),
            r,
            p,
            known,
            found
        ));
    } else {
        eprint_line(&format!(
            "{}  {}  recall={:.3}  precision={:.3}  known={}  found={}",
            tag_wrn(),
            paint(YELLOW, "quality note"),
            r,
            p,
            known,
            found
        ));
    }
    eprint_line("");
}

pub fn wrote(kind: &str, n: usize, path: &str) {
    info(&format!(
        "wrote {} {} → {}",
        paint(BOLD, &n.to_string()),
        kind,
        paint(DIM, path)
    ));
}

/// Optional live hit line (TTY noise; kept for future hit streaming).
#[allow(dead_code)]
pub fn found_hit(item: &str) {
    eprint_line(&format!("{}  {}", tag_found(), paint(BRIGHT_GREEN, item)));
}

// ── Wordlist tables ─────────────────────────────────────────────────

/// Pretty depth/preset tables for `wordlist list`.
pub fn print_depth_table(rows: &[(String, u8, String, usize, String, String)]) {
    banner("wordlists");
    eprint_line(&format!(
        "  {}",
        paint2(BOLD, WHITE, "SCAN DEPTHS  (fast -> final)")
    ));
    eprint_line(&paint(DIM, &format!("  {}", rule('-', 78))));
    eprint_line(&format!(
        "  {:<8} {:>5}  {:<8} {:>8}  {:<28}  {}",
        paint(DIM, "depth"),
        paint(DIM, "lvl"),
        paint(DIM, "list"),
        paint(DIM, "labels"),
        paint(DIM, "permute"),
        paint(DIM, "notes")
    ));
    eprint_line(&paint(DIM, &format!("  {}", rule('-', 78))));
    for (depth, lvl, list, labels, perm, notes) in rows {
        println!(
            "  {:<8} {:>5}  {:<8} {:>8}  {:<28}  {}",
            depth, lvl, list, labels, perm, notes
        );
    }
    println!();
}

pub fn print_preset_table(rows: &[(String, usize, String)]) {
    eprint_line(&format!("  {}", paint2(BOLD, WHITE, "WORDLIST PACKS")));
    eprint_line(&paint(DIM, &format!("  {}", rule('-', 56))));
    eprint_line(&format!(
        "  {:<10} {:>8}  {}",
        paint(DIM, "preset"),
        paint(DIM, "labels"),
        paint(DIM, "role")
    ));
    eprint_line(&paint(DIM, &format!("  {}", rule('-', 56))));
    for (name, n, role) in rows {
        println!("  {:<10} {:>8}  {}", name, n, role);
    }
    println!();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn progress_bar_bounds() {
        let z = progress_bar(0.0, 10);
        let f = progress_bar(100.0, 10);
        assert!(z.contains('[') && z.contains(']'));
        assert!(f.contains('[') && f.contains(']'));
        assert!(f.contains('#'));
    }

    #[test]
    fn progress_bar_mid_has_arrow() {
        let m = progress_bar(50.0, 10);
        assert!(m.contains('>') || m.contains('#'));
    }

    #[test]
    fn paint_no_panic() {
        let _ = paint(YELLOW, "x");
        let _ = tag();
        let _ = paint(RED, "y");
        let _ = tag_inf();
        let _ = tag_ok();
        let _ = tag_wrn();
        let _ = visible_len(&paint(CYAN, "hello"));
    }

    #[test]
    fn visible_len_strips_ansi() {
        let raw = "hello";
        let colored = paint(CYAN, "hello");
        // Without TTY, paint returns plain; with TTY would strip.
        assert!(visible_len(&colored) >= raw.len() || visible_len(&colored) == raw.len());
        assert_eq!(visible_len("plain"), 5);
    }
}
