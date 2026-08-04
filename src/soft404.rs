//! Soft-404 fingerprinting: status + body length of random missing paths.
//!
//! Pure match logic is network-free; probe collection lives in paths_engine.

use ahash::AHashSet;

/// One soft-404 fingerprint: HTTP status plus content length (bytes).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Soft404Fp {
    pub status: u16,
    pub length: u64,
}

/// Set of fingerprints observed on random non-existent paths.
#[derive(Debug, Clone, Default)]
pub struct Soft404Filter {
    fps: AHashSet<Soft404Fp>,
}

impl Soft404Filter {
    pub fn new() -> Self {
        Self {
            fps: AHashSet::new(),
        }
    }

    pub fn register(&mut self, status: u16, length: u64) {
        self.fps.insert(Soft404Fp { status, length });
    }

    pub fn is_empty(&self) -> bool {
        self.fps.is_empty()
    }

    pub fn len(&self) -> usize {
        self.fps.len()
    }

    /// True when this response looks like a soft-404 (should not count as a hit).
    pub fn is_soft404(&self, status: u16, length: u64) -> bool {
        self.fps.contains(&Soft404Fp { status, length })
    }

    pub fn fingerprints(&self) -> impl Iterator<Item = Soft404Fp> + '_ {
        self.fps.iter().copied()
    }
}

/// Decide if a status-matched response is a real hit after soft-404 filter.
pub fn allow_hit(filter: &Soft404Filter, status: u16, length: u64) -> bool {
    if filter.is_empty() {
        return true;
    }
    !filter.is_soft404(status, length)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_filter_allows_all() {
        let f = Soft404Filter::new();
        assert!(allow_hit(&f, 200, 12));
    }

    #[test]
    fn matching_fp_blocks() {
        let mut f = Soft404Filter::new();
        f.register(200, 42);
        assert!(!allow_hit(&f, 200, 42));
        assert!(allow_hit(&f, 200, 3));
        assert!(allow_hit(&f, 401, 42));
    }
}
