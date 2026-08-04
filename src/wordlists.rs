//! Built-in DNS wordlist presets, scan depths, and multi-source merge.
//!
//! Sources (curated into repo wordlists/, not live-fetched at runtime):
//! - SecLists Discovery/DNS top1m-5000 / top1m-20000
//! - n0kovo_subdomains tiny (final tier)
//! - altdns words.txt (alter/permutation dictionary)
//! - high-value modern labels boost-merged ahead of rank lists
//!
//! Depth ladder (fast → final) is the modular scan system: one flag picks
//! list size and optional auto-permute profile.

use std::path::Path;

/// Built-in list packs (also valid as `-w` / `--preset` names).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Preset {
    /// ~270 high-value labels.
    Tiny,
    /// ~620 labels.
    Small,
    /// ~5k labels (SecLists top1m-5000 + boost).
    Medium,
    /// ~20k labels (SecLists top1m-20000 + boost).
    Large,
    /// ~65k labels (large + n0kovo tiny). Deepest static pack.
    Final,
    /// Alteration dictionary for permute (altdns-class words).
    Alter,
}

impl Preset {
    pub fn as_str(self) -> &'static str {
        match self {
            Preset::Tiny => "tiny",
            Preset::Small => "small",
            Preset::Medium => "medium",
            Preset::Large => "large",
            Preset::Final => "final",
            Preset::Alter => "alter",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "tiny" => Some(Preset::Tiny),
            "small" => Some(Preset::Small),
            "medium" | "med" | "5k" => Some(Preset::Medium),
            "large" | "20k" | "big" => Some(Preset::Large),
            "final" | "maxlist" | "full" => Some(Preset::Final),
            "alter" | "altdns" | "perm" | "permutation" => Some(Preset::Alter),
            _ => None,
        }
    }

    pub fn all() -> &'static [Preset] {
        &[
            Preset::Tiny,
            Preset::Small,
            Preset::Medium,
            Preset::Large,
            Preset::Final,
            Preset::Alter,
        ]
    }

    /// Embedded raw text (one label per line).
    pub fn raw(self) -> &'static str {
        match self {
            Preset::Tiny => include_str!("../wordlists/dns_tiny.txt"),
            Preset::Small => include_str!("../wordlists/dns_small.txt"),
            Preset::Medium => include_str!("../wordlists/dns_medium.txt"),
            Preset::Large => include_str!("../wordlists/dns_large.txt"),
            Preset::Final => include_str!("../wordlists/dns_final.txt"),
            Preset::Alter => include_str!("../wordlists/alter_words.txt"),
        }
    }

    pub fn labels(self) -> Vec<String> {
        parse_wordlist_text_opts(self.raw(), true)
    }

    pub fn role(self) -> &'static str {
        match self {
            Preset::Tiny => "fast recon / sanity",
            Preset::Small => "light brute",
            Preset::Medium => "breadth pack (SecLists 5k + boost)",
            Preset::Large => "deep pack (SecLists 20k + boost)",
            Preset::Final => "max static pack (20k + n0kovo tiny)",
            Preset::Alter => "permutation dictionary (altdns-class)",
        }
    }
}

/// Modular scan depth: maps one flag to list pack + permute profile.
///
/// Ladder: fast → normal → deep → deeper → final (super long).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ScanDepth {
    /// Depth 1: tiny list, no auto-permute. Seconds on a lab zone.
    Fast = 1,
    /// Depth 2: small list.
    Normal = 2,
    /// Depth 3: medium (~5k).
    Deep = 3,
    /// Depth 4: large (~20k).
    Deeper = 4,
    /// Depth 5: final (~65k) + auto light permute on top seeds.
    Final = 5,
}

/// Resolved plan for a depth (list + optional auto-permute knobs).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DepthPlan {
    pub depth: ScanDepth,
    pub list: Preset,
    /// When true, enum applies alter-style permute unless user disables it.
    pub auto_permute: bool,
    pub permute_numbers: u32,
    /// Cap on total labels after permute (None = unbounded).
    pub permute_max: Option<usize>,
    /// Only the first N base labels are used as permute seeds (rest stay unmutated).
    pub permute_seed_cap: Option<usize>,
}

impl ScanDepth {
    pub fn level(self) -> u8 {
        self as u8
    }

    pub fn as_str(self) -> &'static str {
        match self {
            ScanDepth::Fast => "fast",
            ScanDepth::Normal => "normal",
            ScanDepth::Deep => "deep",
            ScanDepth::Deeper => "deeper",
            ScanDepth::Final => "final",
        }
    }

    pub fn all() -> &'static [ScanDepth] {
        &[
            ScanDepth::Fast,
            ScanDepth::Normal,
            ScanDepth::Deep,
            ScanDepth::Deeper,
            ScanDepth::Final,
        ]
    }

    /// Parse `1`..`5`, `d1`..`d5`, or names (`fast`, `quick`, `normal`, … `final`, `max`).
    pub fn parse(s: &str) -> Option<Self> {
        let t = s.trim().to_ascii_lowercase();
        match t.as_str() {
            "1" | "d1" | "fast" | "quick" | "q" | "sanity" => Some(ScanDepth::Fast),
            "2" | "d2" | "normal" | "default" | "std" | "standard" => Some(ScanDepth::Normal),
            "3" | "d3" | "deep" | "medium" => Some(ScanDepth::Deep),
            "4" | "d4" | "deeper" | "large" | "long" => Some(ScanDepth::Deeper),
            "5" | "d5" | "final" | "max" | "full" | "ultra" | "super" | "exhaustive" => {
                Some(ScanDepth::Final)
            }
            _ => None,
        }
    }

    pub fn plan(self) -> DepthPlan {
        match self {
            ScanDepth::Fast => DepthPlan {
                depth: self,
                list: Preset::Tiny,
                auto_permute: false,
                permute_numbers: 0,
                permute_max: None,
                permute_seed_cap: None,
            },
            ScanDepth::Normal => DepthPlan {
                depth: self,
                list: Preset::Small,
                auto_permute: false,
                permute_numbers: 0,
                permute_max: None,
                permute_seed_cap: None,
            },
            ScanDepth::Deep => DepthPlan {
                depth: self,
                list: Preset::Medium,
                auto_permute: false,
                permute_numbers: 0,
                permute_max: None,
                permute_seed_cap: None,
            },
            ScanDepth::Deeper => DepthPlan {
                depth: self,
                list: Preset::Large,
                auto_permute: false,
                permute_numbers: 0,
                permute_max: None,
                permute_seed_cap: None,
            },
            // Super-long: max static list + bounded alter of top seeds only.
            ScanDepth::Final => DepthPlan {
                depth: self,
                list: Preset::Final,
                auto_permute: true,
                permute_numbers: 5,
                permute_max: Some(250_000),
                permute_seed_cap: Some(300),
            },
        }
    }

    pub fn blurb(self) -> &'static str {
        match self {
            ScanDepth::Fast => "fast — tiny list, no permute",
            ScanDepth::Normal => "normal — small list, no permute",
            ScanDepth::Deep => "deep — medium ~5k, no permute",
            ScanDepth::Deeper => "deeper — large ~20k, no permute",
            ScanDepth::Final => "final — ~65k + auto permute (top seeds)",
        }
    }
}

/// Parse wordlist body: trim, skip blanks/comments, de-dupe order-preserving.
///
/// When `lowercase` is true (DNS presets), labels are lowercased. Generic file
/// loads keep case so path known-true lines can keep a `PORT` placeholder.
pub fn parse_wordlist_text(text: &str) -> Vec<String> {
    parse_wordlist_text_opts(text, false)
}

/// Like [`parse_wordlist_text`] with explicit lowercasing.
pub fn parse_wordlist_text_opts(text: &str, lowercase: bool) -> Vec<String> {
    let mut out = Vec::new();
    let mut seen = ahash::AHashSet::new();
    for line in text.lines() {
        let s = line.trim();
        if s.is_empty() || s.starts_with('#') {
            continue;
        }
        let s = s.trim_end_matches('.');
        if s.is_empty() {
            continue;
        }
        let s = if lowercase {
            s.to_ascii_lowercase()
        } else {
            s.to_string()
        };
        if seen.insert(s.clone()) {
            out.push(s);
        }
    }
    out
}

/// Load labels from a filesystem path (case preserved).
pub fn load_wordlist_path(path: impl AsRef<Path>) -> anyhow::Result<Vec<String>> {
    let text = std::fs::read_to_string(path.as_ref())?;
    Ok(parse_wordlist_text_opts(&text, false))
}

/// Merge many label lists: first-seen wins, order preserved.
/// Does not force lowercase (callers normalize DNS labels when needed).
pub fn merge_labels(lists: impl IntoIterator<Item = Vec<String>>) -> Vec<String> {
    let mut out = Vec::new();
    let mut seen = ahash::AHashSet::new();
    for list in lists {
        for s in list {
            let s = s.trim().trim_end_matches('.').to_string();
            if s.is_empty() || s.starts_with('#') {
                continue;
            }
            if seen.insert(s.clone()) {
                out.push(s);
            }
        }
    }
    out
}

/// Resolve a mix of preset names and file paths into one deduped label list.
///
/// Each token is tried as a preset name first, then as a file path.
pub fn resolve_wordlist_sources(sources: &[String]) -> anyhow::Result<Vec<String>> {
    if sources.is_empty() {
        anyhow::bail!("no wordlist sources (use --depth, --preset, and/or -w)");
    }
    let mut parts = Vec::with_capacity(sources.len());
    for src in sources {
        let src = src.trim();
        if src.is_empty() {
            continue;
        }
        if let Some(p) = Preset::parse(src) {
            parts.push(p.labels());
            continue;
        }
        let path = Path::new(src);
        if path.is_file() {
            parts.push(load_wordlist_path(path)?);
            continue;
        }
        anyhow::bail!(
            "unknown wordlist source '{src}' (not a preset: tiny|small|medium|large|final|alter, and not a file)"
        );
    }
    if parts.is_empty() {
        anyhow::bail!("no wordlist labels loaded");
    }
    Ok(merge_labels(parts))
}

/// Cap a list to the first `n` entries (after merge). `None` = no cap.
pub fn cap_labels(mut labels: Vec<String>, cap: Option<usize>) -> Vec<String> {
    if let Some(n) = cap {
        if labels.len() > n {
            labels.truncate(n);
        }
    }
    labels
}

/// Build the candidate label list for enum/expand from optional depth + sources.
///
/// Rules:
/// 1. If `depth` is set, start from that depth's list pack.
/// 2. Merge any extra `-w` / `--preset` sources (first-seen: depth list first).
/// 3. Apply `cap` if set.
/// 4. Permute when `do_permute` (explicit or depth auto) using alter words;
///    only first `permute_seed_cap` labels are seeds if set; full base list is kept.
pub fn build_scan_labels(
    depth: Option<ScanDepth>,
    extra_sources: &[String],
    cap: Option<usize>,
    do_permute: bool,
    alter_words: &[String],
    permute_numbers: u32,
    permute_max: Option<usize>,
    permute_seed_cap: Option<usize>,
    domain: Option<&str>,
) -> anyhow::Result<Vec<String>> {
    let plan = depth.map(|d| d.plan());
    let mut sources: Vec<String> = Vec::new();
    if let Some(ref p) = plan {
        sources.push(p.list.as_str().to_string());
    }
    sources.extend(extra_sources.iter().cloned());
    if sources.is_empty() {
        anyhow::bail!("provide --depth, -w <file|preset>, and/or --preset");
    }

    let mut labels = resolve_wordlist_sources(&sources)?;
    labels = cap_labels(labels, cap);

    if do_permute {
        let alter = resolve_wordlist_sources(alter_words)?;
        let seed_cap = permute_seed_cap.or_else(|| plan.as_ref().and_then(|p| p.permute_seed_cap));
        let seeds: Vec<String> = match seed_cap {
            Some(n) if n < labels.len() => labels.iter().take(n).cloned().collect(),
            _ => labels.clone(),
        };
        let numbers = if permute_numbers > 0 {
            permute_numbers
        } else {
            plan.as_ref().map(|p| p.permute_numbers).unwrap_or(0)
        };
        let max_out = permute_max.or_else(|| plan.as_ref().and_then(|p| p.permute_max));
        let cfg = crate::permute::PermuteConfig {
            prefix: true,
            suffix: true,
            separators: vec!["-".into(), "_".into(), "".into()],
            numbers: numbers > 0,
            number_max: numbers,
            max_out,
            include_seeds: true,
        };
        let mut muts = crate::permute::permute_labels(&seeds, &alter, domain, &cfg);
        // Keep the full base list (labels beyond seed_cap never became seeds).
        labels = merge_labels([labels, muts.drain(..).collect()]);
        if let Some(n) = max_out {
            labels = cap_labels(labels, Some(n));
        }
    }

    if labels.is_empty() {
        anyhow::bail!("wordlist resolved to zero labels");
    }
    Ok(labels)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn presets_grow_by_tier() {
        let tiny = Preset::Tiny.labels();
        let small = Preset::Small.labels();
        let medium = Preset::Medium.labels();
        let large = Preset::Large.labels();
        let final_p = Preset::Final.labels();
        let alter = Preset::Alter.labels();
        assert!(tiny.len() >= 100, "tiny={}", tiny.len());
        assert!(small.len() > tiny.len());
        assert!(medium.len() > small.len());
        assert!(large.len() > medium.len(), "large={}", large.len());
        assert!(final_p.len() > large.len(), "final={}", final_p.len());
        assert!(alter.len() >= 50);
        assert!(tiny.iter().any(|x| x == "www"));
        assert!(tiny.iter().any(|x| x == "api"));
    }

    #[test]
    fn depth_ladder_maps_lists() {
        assert_eq!(ScanDepth::Fast.plan().list, Preset::Tiny);
        assert_eq!(ScanDepth::Normal.plan().list, Preset::Small);
        assert_eq!(ScanDepth::Deep.plan().list, Preset::Medium);
        assert_eq!(ScanDepth::Deeper.plan().list, Preset::Large);
        let f = ScanDepth::Final.plan();
        assert_eq!(f.list, Preset::Final);
        assert!(f.auto_permute);
        assert!(f.permute_seed_cap.unwrap() > 0);
    }

    #[test]
    fn depth_parse_aliases() {
        assert_eq!(ScanDepth::parse("1"), Some(ScanDepth::Fast));
        assert_eq!(ScanDepth::parse("fast"), Some(ScanDepth::Fast));
        assert_eq!(ScanDepth::parse("d3"), Some(ScanDepth::Deep));
        assert_eq!(ScanDepth::parse("final"), Some(ScanDepth::Final));
        assert_eq!(ScanDepth::parse("max"), Some(ScanDepth::Final));
        assert_eq!(ScanDepth::parse("nope"), None);
    }

    #[test]
    fn build_scan_labels_depth_only() {
        let labels = build_scan_labels(
            Some(ScanDepth::Fast),
            &[],
            None,
            false,
            &["alter".into()],
            0,
            None,
            None,
            None,
        )
        .unwrap();
        assert_eq!(labels.len(), Preset::Tiny.labels().len());
    }

    #[test]
    fn build_scan_labels_final_auto_permute_grows() {
        let base = Preset::Final.labels().len();
        let labels = build_scan_labels(
            Some(ScanDepth::Final),
            &[],
            None,
            true,
            &["alter".into()],
            2,
            Some(80_000),
            Some(20),
            Some("example.com"),
        )
        .unwrap();
        assert!(labels.len() > base.min(80_000).min(base + 1));
        assert!(labels.len() <= 80_000);
    }

    #[test]
    fn merge_dedupes_order() {
        let a = vec!["www".into(), "mail".into()];
        let b = vec!["mail".into(), "api".into()];
        assert_eq!(merge_labels([a, b]), vec!["www", "mail", "api"]);
    }

    #[test]
    fn parse_skips_comments() {
        let t = "# hi\nwww\n\n#x\nAPI\n";
        assert_eq!(parse_wordlist_text(t), vec!["www", "API"]);
        assert_eq!(parse_wordlist_text_opts(t, true), vec!["www", "api"]);
    }

    #[test]
    fn resolve_preset_name() {
        let v = resolve_wordlist_sources(&["tiny".into()]).unwrap();
        assert!(!v.is_empty());
        assert!(v.contains(&"www".to_string()));
    }
}
