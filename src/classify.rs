//! DNS response classification (pure; no network).

use crate::dns_packet::{DnsMessage, Rcode};

/// Coarse class of a DNS reply for enum decisions.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ResponseClass {
    /// NOERROR with at least one answer RR matching the question name (or any A in answer).
    Live { addresses: Vec<String> },
    /// NXDOMAIN: name does not exist.
    NxDomain,
    /// NOERROR with empty answer (often used with wildcards or CNAMEs stripped).
    NoErrorEmpty,
    /// SERVFAIL / REFUSED / FORMERR / other.
    Error { rcode: u8 },
    /// Truncated or unparseable.
    Garbage,
}

/// Classify a parsed DNS message for enumeration purposes.
pub fn classify_response(msg: &DnsMessage) -> ResponseClass {
    if !msg.is_response {
        return ResponseClass::Garbage;
    }
    match msg.rcode {
        Rcode::NoError => {
            if msg.answers.is_empty() {
                ResponseClass::NoErrorEmpty
            } else {
                let mut addresses = Vec::new();
                for a in &msg.answers {
                    if let Some(ip) = &a.rdata_display {
                        addresses.push(ip.clone());
                    }
                }
                if addresses.is_empty() {
                    ResponseClass::NoErrorEmpty
                } else {
                    ResponseClass::Live { addresses }
                }
            }
        }
        Rcode::NxDomain => ResponseClass::NxDomain,
        other => ResponseClass::Error { rcode: other as u8 },
    }
}

/// True when this class counts as a "found" host before wildcard filtering.
pub fn is_positive_hit(class: &ResponseClass) -> bool {
    matches!(class, ResponseClass::Live { .. })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dns_packet::{DnsAnswer, DnsMessage, Rcode};

    #[test]
    fn live_with_a() {
        let msg = DnsMessage {
            id: 1,
            is_response: true,
            rcode: Rcode::NoError,
            question_name: "www.example.com".into(),
            answers: vec![DnsAnswer {
                name: "www.example.com".into(),
                rtype: 1,
                rdata_display: Some("1.2.3.4".into()),
            }],
        };
        match classify_response(&msg) {
            ResponseClass::Live { addresses } => assert_eq!(addresses, vec!["1.2.3.4".to_string()]),
            other => panic!("expected Live, got {other:?}"),
        }
    }

    #[test]
    fn nxdomain() {
        let msg = DnsMessage {
            id: 1,
            is_response: true,
            rcode: Rcode::NxDomain,
            question_name: "nope.example.com".into(),
            answers: vec![],
        };
        assert_eq!(classify_response(&msg), ResponseClass::NxDomain);
    }
}
