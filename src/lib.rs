//! vegadns: high-concurrency subdomain enum + HTTP path discovery.
//!
//! Pure units (expand, classify, wildcard, dedup, path join/classify) are network-free.
//! DNS engine: UDP resolve / mock zone. Paths engine: concurrent HTTP.

pub mod classify;
pub mod dedup;
pub mod dns_packet;
pub mod engine;
pub mod expand;
pub mod mock_dns;
// MockStress used by CLI mock-serve stress knobs
pub mod mock_http;
pub mod path_classify;
pub mod path_join;
pub mod paths_engine;
pub mod permute;
pub mod soft404;
pub mod wildcard;
pub mod wordlists;

pub use classify::{classify_response, ResponseClass};
pub use dedup::Deduper;
pub use expand::{expand_label, expand_wordlist_lines};
pub use path_classify::{classify_status, PathClass};
pub use path_join::{expand_paths, join_url};
pub use permute::{permute_labels, seed_to_label, PermuteConfig};
pub use wildcard::{is_wildcard_hit, WildcardFilter, WildcardFingerprint};
pub use wordlists::{
    build_scan_labels, cap_labels, load_wordlist_path, merge_labels, parse_wordlist_text,
    resolve_wordlist_sources, DepthPlan, Preset, ScanDepth,
};
