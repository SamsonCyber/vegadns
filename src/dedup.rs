//! Result deduplication.

use ahash::AHashSet;
use std::hash::Hash;

/// Streaming unique collector (order-preserving first-seen).
#[derive(Debug, Default)]
pub struct Deduper<T: Eq + Hash + Clone> {
    seen: AHashSet<T>,
    order: Vec<T>,
}

impl<T: Eq + Hash + Clone> Deduper<T> {
    pub fn new() -> Self {
        Self {
            seen: AHashSet::new(),
            order: Vec::new(),
        }
    }

    /// Insert if new. Returns true when the value was not seen before.
    pub fn insert(&mut self, value: T) -> bool {
        if self.seen.insert(value.clone()) {
            self.order.push(value);
            true
        } else {
            false
        }
    }

    pub fn contains(&self, value: &T) -> bool {
        self.seen.contains(value)
    }

    pub fn len(&self) -> usize {
        self.order.len()
    }

    pub fn is_empty(&self) -> bool {
        self.order.is_empty()
    }

    pub fn into_vec(self) -> Vec<T> {
        self.order
    }

    pub fn iter(&self) -> impl Iterator<Item = &T> {
        self.order.iter()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preserves_first_seen_order() {
        let mut d = Deduper::new();
        assert!(d.insert("a".to_string()));
        assert!(d.insert("b".to_string()));
        assert!(!d.insert("a".to_string()));
        assert_eq!(d.into_vec(), vec!["a".to_string(), "b".to_string()]);
    }
}
