//! vegadns CLI: subdomain enum + HTTP path discovery + breadth (depth / presets / permute).

use std::path::PathBuf;
use std::time::Duration;

use clap::{Parser, Subcommand};
use vegadns::engine::{
    load_resolvers, load_wordlist, precision, recall, run_enum, run_enum_with_mock, EngineConfig,
};
use vegadns::expand::expand_label;
use vegadns::mock_dns::MockZone;
use vegadns::mock_http::{load_hard_zone, load_hit_paths, MockHttp};
use vegadns::path_classify::parse_status_list;
use vegadns::paths_engine::{
    path_f1, path_precision, path_recall, rewrite_port_template, run_paths, PathsConfig,
};
use vegadns::permute::{permute_labels, PermuteConfig};
use vegadns::ui;
use vegadns::wordlists::{
    build_scan_labels, cap_labels, resolve_wordlist_sources, Preset, ScanDepth,
};

#[derive(Parser, Debug)]
#[command(
    name = "vegadns",
    about = "Subdomain enum (DNS) + path discovery + modular depth (fast→final)",
    version
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

/// Parse `--depth` value or bail with a clear message.
fn parse_depth_flag(s: &str) -> anyhow::Result<ScanDepth> {
    ScanDepth::parse(s).ok_or_else(|| {
        anyhow::anyhow!(
            "unknown --depth '{s}' (use 1-5 or fast|normal|deep|deeper|final)"
        )
    })
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Serve a fixture zone over UDP DNS (for baseline comparison on the same answers).
    MockServe {
        /// Zone JSON path.
        #[arg(long = "zone")]
        zone: PathBuf,
        /// Bind address (default 127.0.0.1:5353).
        #[arg(long = "bind", default_value = "127.0.0.1:5353")]
        bind: String,
        /// Reply latency in ms (gym stress: slow recursive path).
        #[arg(long = "latency-ms", default_value_t = 0)]
        latency_ms: u64,
        /// Percent chance of SERVFAIL (0-100). Gym stress only.
        #[arg(long = "servfail-pct", default_value_t = 0)]
        servfail_pct: u8,
        /// Percent chance of silent drop (0-100). Gym stress only.
        #[arg(long = "drop-pct", default_value_t = 0)]
        drop_pct: u8,
    },
    /// Expand wordlist labels to FQDNs (no network). Used by pipelines and Gherkin.
    Expand {
        /// Base domain.
        #[arg(short = 'd', long = "domain")]
        domain: String,
        /// Scan depth: 1-5 or fast|normal|deep|deeper|final.
        #[arg(short = 'D', long = "depth")]
        depth: Option<String>,
        /// Wordlist file(s) and/or preset names (repeatable). Omit if --depth/--preset only.
        #[arg(short = 'w', long = "wordlist")]
        wordlist: Vec<String>,
        /// Built-in preset(s): tiny|small|medium|large|final|alter (merged with -w).
        #[arg(long = "preset")]
        preset: Vec<String>,
        /// Cap labels after merge.
        #[arg(long = "cap")]
        cap: Option<usize>,
        /// Optional output file (default stdout).
        #[arg(short = 'o', long = "output")]
        output: Option<PathBuf>,
    },
    /// Emit / inspect built-in wordlist presets and depth ladder (no network).
    Wordlist {
        #[command(subcommand)]
        action: WordlistCmd,
    },
    /// Generate altdns/gotator-class subdomain permutations (no network).
    Permute {
        /// Seed labels or FQDNs (known subs). File path, one per line.
        #[arg(short = 'i', long = "seeds")]
        seeds: PathBuf,
        /// Alter words file and/or preset name (default: built-in alter).
        #[arg(short = 'w', long = "words", default_value = "alter")]
        words: Vec<String>,
        /// Base domain: strip from FQDN seeds (optional).
        #[arg(short = 'd', long = "domain")]
        domain: Option<String>,
        /// Prefix mutations (dev-api). Default on.
        #[arg(long = "prefix", default_value_t = true, action = clap::ArgAction::Set)]
        prefix: bool,
        /// Suffix mutations (api-dev). Default on.
        #[arg(long = "suffix", default_value_t = true, action = clap::ArgAction::Set)]
        suffix: bool,
        /// Number mutations 0..=N (default 5). Set 0 to disable.
        #[arg(long = "numbers", default_value_t = 5)]
        numbers: u32,
        /// Separators (repeatable). Default: -, _, empty glue.
        #[arg(long = "sep")]
        sep: Vec<String>,
        /// Hard cap on output labels.
        #[arg(long = "max")]
        max: Option<usize>,
        /// Do not include original seeds.
        #[arg(long = "no-seeds")]
        no_seeds: bool,
        /// Output file (default stdout).
        #[arg(short = 'o', long = "output")]
        output: Option<PathBuf>,
    },
    /// Active brute-force enumeration against resolvers (or embedded mock zone).
    Enum {
        /// Base domain (e.g. example.com). With --mock-zone, defaults to zone.base.
        #[arg(short = 'd', long = "domain")]
        domain: Option<String>,

        /// Scan depth ladder: 1-5 or fast|normal|deep|deeper|final.
        /// Sets base wordlist pack; final also enables bounded auto-permute.
        #[arg(short = 'D', long = "depth")]
        depth: Option<String>,

        /// Wordlist file(s) and/or preset names (repeatable). Merged with depth pack.
        #[arg(short = 'w', long = "wordlist")]
        wordlist: Vec<String>,

        /// Built-in preset(s): tiny|small|medium|large|final|alter (merged with -w).
        #[arg(long = "preset")]
        preset: Vec<String>,

        /// Cap candidate labels after merge (before optional permute).
        #[arg(long = "cap")]
        cap: Option<usize>,

        /// After loading wordlist, also permute against alter words (breadth).
        /// Depth `final` enables this automatically unless --no-permute.
        #[arg(long = "permute")]
        permute: bool,

        /// Disable auto-permute from depth final (or ignore --permute).
        #[arg(long = "no-permute")]
        no_permute: bool,

        /// Alter words for --permute (file or preset; default alter).
        #[arg(long = "alter-words", default_value = "alter")]
        alter_words: Vec<String>,

        /// Number mutations when permuting. Default: 3, or depth plan value.
        #[arg(long = "permute-numbers")]
        permute_numbers: Option<u32>,

        /// Cap after permute expansion (default: depth plan, or unbounded).
        #[arg(long = "permute-max")]
        permute_max: Option<usize>,

        /// Only permute the first N base labels (rest stay plain). Depth final default 300.
        #[arg(long = "permute-seed-cap")]
        permute_seed_cap: Option<usize>,

        /// Resolvers file (ip or ip:port per line). Required unless --mock-zone is set.
        #[arg(short = 'r', long = "resolvers")]
        resolvers: Option<PathBuf>,

        /// Embedded mock zone JSON for offline/fixture runs (starts local DNS).
        #[arg(long = "mock-zone")]
        mock_zone: Option<PathBuf>,

        /// Write discovered names to this file (also prints to stdout unless --quiet-names).
        #[arg(short = 'o', long = "output")]
        output: Option<PathBuf>,

        /// Max in-flight queries.
        #[arg(long = "concurrency", default_value_t = 2000)]
        concurrency: usize,

        /// Per-attempt timeout in milliseconds.
        #[arg(long = "timeout-ms", default_value_t = 1500)]
        timeout_ms: u64,

        /// Retries after timeout.
        #[arg(long = "retries", default_value_t = 2)]
        retries: u32,

        /// Number of UDP sockets.
        #[arg(long = "sockets", default_value_t = 4)]
        sockets: usize,

        /// Random labels per wildcard probe set.
        #[arg(long = "wildcard-probes", default_value_t = 3)]
        wildcard_probes: usize,

        /// Treat wordlist lines as absolute FQDNs (skip label×domain expand).
        #[arg(long = "fqdn-list")]
        fqdn_list: bool,

        /// Suppress progress on stderr.
        #[arg(short = 'q', long = "quiet")]
        quiet: bool,

        /// Print only names (no stats footer on stderr).
        #[arg(long = "quiet-names")]
        quiet_names: bool,

        /// Optional known-true file (one FQDN per line) to print recall/precision on stderr.
        #[arg(long = "known-true")]
        known_true: Option<PathBuf>,

        /// Write JSON stats to this path.
        #[arg(long = "stats-json")]
        stats_json: Option<PathBuf>,
    },
    /// HTTP path/content discovery (ferox/ffuf-class): wordlist × base URL → hits.
    Paths {
        /// Base URL (e.g. http://127.0.0.1:18080/). With --mock-paths, optional (auto).
        #[arg(short = 'u', long = "url")]
        url: Option<String>,

        /// Path wordlist (one path segment/path per line).
        #[arg(short = 'w', long = "wordlist")]
        wordlist: PathBuf,

        /// Hit-path file for embedded mock HTTP (one relative path per line).
        #[arg(long = "mock-paths")]
        mock_paths: Option<PathBuf>,

        /// Hard mock zone: `path STATUS` lines + soft-404 on miss (overrides --mock-paths).
        #[arg(long = "mock-hard-zone")]
        mock_hard_zone: Option<PathBuf>,

        /// Write hit URLs to this file.
        #[arg(short = 'o', long = "output")]
        output: Option<PathBuf>,

        /// Concurrent requests (default 64; capped in-engine per host).
        #[arg(long = "concurrency", default_value_t = 64)]
        concurrency: usize,

        /// Per-request timeout ms.
        #[arg(long = "timeout-ms", default_value_t = 3000)]
        timeout_ms: u64,

        /// Status codes treated as hits (default 200,201,204,301,302,307,308,401,403).
        #[arg(long = "status", default_value = "200,201,204,301,302,307,308,401,403")]
        status: String,

        /// Soft-404 probe count (0 disables). Default 3.
        #[arg(long = "soft404-probes", default_value_t = 3)]
        soft404_probes: usize,

        /// Retries after transport error. Default 0 (lab-fast); raise for flaky targets.
        #[arg(long = "retries", default_value_t = 0)]
        retries: u32,

        /// Emit "URL STATUS" lines instead of URL only.
        #[arg(long = "show-status")]
        show_status: bool,

        #[arg(short = 'q', long = "quiet")]
        quiet: bool,

        /// Known-true URL list (PORT placeholder expanded when using --mock-paths).
        #[arg(long = "known-true")]
        known_true: Option<PathBuf>,

        #[arg(long = "stats-json")]
        stats_json: Option<PathBuf>,
    },
}

#[derive(Subcommand, Debug)]
enum WordlistCmd {
    /// List depth ladder + built-in presets and sizes.
    List,
    /// Emit a depth pack, preset, or merged sources to stdout or -o.
    Emit {
        /// Depth name/number, preset name(s), and/or file paths (merged).
        /// Example: `final` or `deep` or `medium org.txt`
        #[arg(required = true)]
        sources: Vec<String>,
        #[arg(long = "cap")]
        cap: Option<usize>,
        #[arg(short = 'o', long = "output")]
        output: Option<PathBuf>,
    },
}

/// Collect -w and --preset into extra sources (depth is separate).
fn collect_extra_sources(wordlist: &[String], preset: &[String]) -> Vec<String> {
    let mut sources = Vec::new();
    sources.extend(preset.iter().cloned());
    sources.extend(wordlist.iter().cloned());
    sources
}

/// Resolve emit tokens: depth names expand to their list pack name.
fn resolve_emit_sources(tokens: &[String]) -> anyhow::Result<Vec<String>> {
    let mut out = Vec::new();
    for t in tokens {
        if let Some(d) = ScanDepth::parse(t) {
            out.push(d.plan().list.as_str().to_string());
        } else {
            out.push(t.clone());
        }
    }
    Ok(out)
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Commands::MockServe {
            zone,
            bind,
            latency_ms,
            servfail_pct,
            drop_pct,
        } => {
            let zone = MockZone::from_path(&zone)?;
            let stress = vegadns::mock_dns::MockStress {
                latency_ms,
                servfail_pct: servfail_pct.min(100),
                drop_pct: drop_pct.min(100),
            };
            let server =
                vegadns::mock_dns::MockServer::spawn_on_with_stress(zone, &bind, stress)?;
            ui::info(&format!(
                "mock-serve  {}  latency_ms={}  servfail={}%%  drop={}%%  (Ctrl+C stop)",
                server.addr, stress.latency_ms, stress.servfail_pct, stress.drop_pct
            ));
            loop {
                tokio::time::sleep(std::time::Duration::from_secs(3600)).await;
            }
        }
        Commands::Expand {
            domain,
            depth,
            wordlist,
            preset,
            cap,
            output,
        } => {
            let depth = depth.as_deref().map(parse_depth_flag).transpose()?;
            let extra = collect_extra_sources(&wordlist, &preset);
            let words = build_scan_labels(
                depth,
                &extra,
                cap,
                false,
                &["alter".into()],
                0,
                None,
                None,
                Some(domain.as_str()),
            )?;
            let mut names = Vec::new();
            for w in &words {
                if let Some(fqdn) = expand_label(w, &domain) {
                    names.push(fqdn);
                }
            }
            for n in &names {
                println!("{n}");
            }
            if let Some(path) = output {
                std::fs::write(&path, names.join("\n") + "\n")?;
            }
        }
        Commands::Wordlist { action } => match action {
            WordlistCmd::List => {
                let mut depth_rows = Vec::new();
                for d in ScanDepth::all() {
                    let p = d.plan();
                    let n = p.list.labels().len();
                    let perm = if p.auto_permute {
                        format!(
                            "auto(seed={},n={},max={})",
                            p.permute_seed_cap
                                .map(|x| x.to_string())
                                .unwrap_or_else(|| "-".into()),
                            p.permute_numbers,
                            p.permute_max
                                .map(|x| x.to_string())
                                .unwrap_or_else(|| "-".into()),
                        )
                    } else {
                        "off".into()
                    };
                    depth_rows.push((
                        d.as_str().to_string(),
                        d.level(),
                        p.list.as_str().to_string(),
                        n,
                        perm,
                        d.blurb().to_string(),
                    ));
                }
                ui::print_depth_table(&depth_rows);
                let preset_rows: Vec<_> = Preset::all()
                    .iter()
                    .map(|p| {
                        (
                            p.as_str().to_string(),
                            p.labels().len(),
                            p.role().to_string(),
                        )
                    })
                    .collect();
                ui::print_preset_table(&preset_rows);
            }
            WordlistCmd::Emit {
                sources,
                cap,
                output,
            } => {
                let sources = resolve_emit_sources(&sources)?;
                let labels = cap_labels(resolve_wordlist_sources(&sources)?, cap);
                for l in &labels {
                    println!("{l}");
                }
                if let Some(path) = output {
                    std::fs::write(&path, labels.join("\n") + "\n")?;
                    ui::wrote("labels", labels.len(), &path.display().to_string());
                } else {
                    ui::info(&format!("{} labels (stdout)", labels.len()));
                }
            }
        },
        Commands::Permute {
            seeds,
            words,
            domain,
            prefix,
            suffix,
            numbers,
            sep,
            max,
            no_seeds,
            output,
        } => {
            let seed_lines = load_wordlist(&seeds)?;
            let alter = resolve_wordlist_sources(&words)?;
            let seps = if sep.is_empty() {
                vec!["-".into(), "_".into(), "".into()]
            } else {
                sep
            };
            let cfg = PermuteConfig {
                prefix,
                suffix,
                separators: seps,
                numbers: numbers > 0,
                number_max: numbers,
                max_out: max,
                include_seeds: !no_seeds,
            };
            let out = permute_labels(&seed_lines, &alter, domain.as_deref(), &cfg);
            for l in &out {
                println!("{l}");
            }
            if let Some(path) = output {
                std::fs::write(&path, out.join("\n") + "\n")?;
                ui::wrote("labels", out.len(), &path.display().to_string());
            } else {
                ui::info(&format!("permute  {} labels (stdout)", out.len()));
            }
        }
        Commands::Enum {
            domain,
            depth,
            wordlist,
            preset,
            cap,
            permute,
            no_permute,
            alter_words,
            permute_numbers,
            permute_max,
            permute_seed_cap,
            resolvers,
            mock_zone,
            output,
            concurrency,
            timeout_ms,
            retries,
            sockets,
            wildcard_probes,
            fqdn_list,
            quiet,
            quiet_names,
            known_true,
            stats_json,
        } => {
            let depth = depth.as_deref().map(parse_depth_flag).transpose()?;
            let plan = depth.map(|d| d.plan());
            let extra = collect_extra_sources(&wordlist, &preset);
            let do_permute = if no_permute {
                false
            } else {
                permute || plan.as_ref().map(|p| p.auto_permute).unwrap_or(false)
            };
            let numbers = permute_numbers.unwrap_or_else(|| {
                if do_permute {
                    plan.as_ref()
                        .map(|p| p.permute_numbers)
                        .filter(|n| *n > 0)
                        .unwrap_or(3)
                } else {
                    0
                }
            });
            let domain_hint = domain.clone();
            let words = build_scan_labels(
                depth,
                &extra,
                cap,
                do_permute,
                &alter_words,
                numbers,
                permute_max,
                permute_seed_cap,
                domain_hint.as_deref(),
            )?;
            let mut cfg = EngineConfig {
                domain: domain.clone().unwrap_or_default(),
                concurrency,
                timeout: Duration::from_millis(timeout_ms),
                retries,
                sockets,
                wildcard_probes,
                quiet,
                fqdn_list,
            };

            let result = if let Some(zone_path) = mock_zone {
                let zone = MockZone::from_path(&zone_path)?;
                if cfg.domain.is_empty() {
                    cfg.domain = zone.base.clone();
                }
                if !quiet {
                    ui::enum_start(
                        &cfg.domain,
                        words.len(),
                        depth.map(|d| d.as_str()),
                        do_permute,
                        1,
                        concurrency,
                        true,
                    );
                    ui::info(&format!("zone   {}", zone_path.display()));
                }
                let (res, addr) = run_enum_with_mock(cfg, &words, zone).await?;
                if !quiet {
                    ui::info(&format!("mock   {addr}"));
                }
                res
            } else {
                if cfg.domain.is_empty() && !cfg.fqdn_list {
                    anyhow::bail!("--domain is required without --mock-zone (or pass --fqdn-list)");
                }
                if cfg.domain.is_empty() {
                    cfg.domain = "_".to_string();
                }
                let resolvers_path = resolvers
                    .ok_or_else(|| anyhow::anyhow!("--resolvers is required without --mock-zone"))?;
                let resolvers = load_resolvers(&resolvers_path)?;
                if !quiet {
                    ui::enum_start(
                        &cfg.domain,
                        words.len(),
                        depth.map(|d| d.as_str()),
                        do_permute,
                        resolvers.len(),
                        concurrency,
                        false,
                    );
                }
                run_enum(cfg, &words, resolvers).await?
            };

            for name in &result.names {
                println!("{name}");
            }

            if let Some(path) = output {
                let body = result.names.join("\n") + "\n";
                std::fs::write(&path, body)?;
                if !quiet {
                    ui::wrote("names", result.names.len(), &path.display().to_string());
                }
            }

            if !quiet && !quiet_names {
                ui::enum_done(&result.stats, result.names.len());
            }

            if let Some(kt_path) = known_true {
                let kt = load_wordlist(&kt_path)?;
                let r = recall(&result.names, &kt);
                let p = precision(&result.names, &kt);
                ui::recall_precision(r, p, kt.len(), result.names.len());
            }

            if let Some(path) = stats_json {
                let s = &result.stats;
                let json = serde_json::json!({
                    "found": s.found_after_wildcard,
                    "found_raw": s.found_raw,
                    "candidates": s.candidates,
                    "queries_sent": s.queries_sent,
                    "query_rate": s.query_rate(),
                    "result_rate": s.result_rate(),
                    "wall_secs": s.elapsed.as_secs_f64(),
                    "nxdomain": s.nxdomain,
                    "timeouts": s.timeouts,
                    "errors": s.errors,
                    "wildcard_parents": s.wildcard_parents,
                    "names": result.names,
                });
                std::fs::write(path, serde_json::to_string_pretty(&json)?)?;
            }
        }
        Commands::Paths {
            url,
            wordlist,
            mock_paths,
            mock_hard_zone,
            output,
            concurrency,
            timeout_ms,
            status,
            soft404_probes,
            retries,
            show_status,
            quiet,
            known_true,
            stats_json,
        } => {
            let words = load_wordlist(&wordlist)?;
            let match_codes = parse_status_list(&status)?;

            let mock = if let Some(zone_path) = mock_hard_zone {
                let zone = load_hard_zone(&zone_path)?;
                let mock = MockHttp::spawn_zone(zone).await?;
                if !quiet {
                    ui::info(&format!(
                        "hard mock HTTP  {}  (soft404 zone)",
                        mock.addr
                    ));
                }
                Some(mock)
            } else if let Some(paths_file) = mock_paths {
                let hits = load_hit_paths(&paths_file)?;
                let mock = MockHttp::spawn(hits).await?;
                if !quiet {
                    ui::info(&format!("mock HTTP  {}", mock.addr));
                }
                Some(mock)
            } else {
                None
            };

            let base_url = if let Some(ref m) = mock {
                m.base_url()
            } else {
                url.ok_or_else(|| {
                    anyhow::anyhow!("--url is required without --mock-paths/--mock-hard-zone")
                })?
            };

            if !quiet {
                ui::paths_start(&base_url, words.len(), concurrency, soft404_probes);
            }

            let cfg = PathsConfig {
                base_url: base_url.clone(),
                concurrency,
                timeout: Duration::from_millis(timeout_ms),
                match_codes,
                soft404_probes,
                retries,
                quiet,
            };

            let result = run_paths(cfg, &words).await?;

            if show_status {
                for (u, st) in &result.statuses {
                    println!("{u} {st}");
                }
            } else {
                for u in &result.urls {
                    println!("{u}");
                }
            }

            if let Some(path) = output {
                let body = if show_status {
                    result
                        .statuses
                        .iter()
                        .map(|(u, st)| format!("{u} {st}"))
                        .collect::<Vec<_>>()
                        .join("\n")
                        + "\n"
                } else {
                    result.urls.join("\n") + "\n"
                };
                std::fs::write(&path, body)?;
                if !quiet {
                    ui::wrote("hits", result.urls.len(), &path.display().to_string());
                }
            }

            if !quiet {
                ui::paths_done(&result.stats, result.urls.len());
            }

            if let Some(kt_path) = known_true {
                let mut kt = load_wordlist(&kt_path)?;
                if let Some(ref m) = mock {
                    kt = rewrite_port_template(&kt, m.addr.port());
                }
                let r = path_recall(&result.urls, &kt);
                let p = path_precision(&result.urls, &kt);
                let _f = path_f1(&result.urls, &kt);
                ui::recall_precision(r, p, kt.len(), result.urls.len());
            }

            if let Some(path) = stats_json {
                let s = &result.stats;
                let json = serde_json::json!({
                    "hits": s.hits,
                    "candidates": s.candidates,
                    "requests": s.requests,
                    "request_rate": s.request_rate(),
                    "wall_secs": s.elapsed.as_secs_f64(),
                    "soft404_dropped": s.soft404_dropped,
                    "errors": s.errors,
                    "urls": result.urls,
                });
                std::fs::write(path, serde_json::to_string_pretty(&json)?)?;
            }

            if let Some(m) = mock {
                m.shutdown().await;
            }
        }
    }
    Ok(())
}
