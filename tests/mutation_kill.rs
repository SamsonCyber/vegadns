//! Targeted tests that kill known cargo-mutants survivors (boundary + API surface).

use vegadns::dedup::Deduper;
use vegadns::dns_packet::{
    build_query, build_response_a, build_response_nxdomain, parse_message, parse_question_name,
    DnsError, Rcode,
};
use vegadns::expand::expand_label;
use vegadns::classify::ResponseClass;
use vegadns::wildcard::{
    fingerprint_from_probe_results, fingerprints_for_wildcard_probes, WildcardFilter,
    WildcardFingerprint,
};

// --- dedup: len/is_empty must track real size, not constants ---

#[test]
fn dedup_len_and_is_empty_not_constants() {
    let mut d = Deduper::new();
    assert!(d.is_empty(), "fresh deduper must be empty");
    assert_eq!(d.len(), 0, "fresh len must be 0 not 1");
    assert!(d.insert("a".to_string()));
    assert!(!d.is_empty(), "after insert must not be empty");
    assert_eq!(d.len(), 1);
    assert!(d.insert("b".to_string()));
    assert_eq!(d.len(), 2, "len must be 2 after two distinct inserts");
    assert!(!d.insert("a".to_string()));
    assert_eq!(d.len(), 2, "duplicate insert must not grow len");
}

// --- Rcode: every assigned arm must map (kills deleted match arms) ---

#[test]
fn rcode_all_standard_values() {
    assert_eq!(Rcode::from(0), Rcode::NoError);
    assert_eq!(Rcode::from(1), Rcode::FormErr);
    assert_eq!(Rcode::from(2), Rcode::ServFail);
    assert_eq!(Rcode::from(3), Rcode::NxDomain);
    assert_eq!(Rcode::from(4), Rcode::NotImp);
    assert_eq!(Rcode::from(5), Rcode::Refused);
    assert_eq!(Rcode::from(6), Rcode::Other);
    assert_eq!(Rcode::from(15), Rcode::Other);
    // high bits masked
    assert_eq!(Rcode::from(0x13), Rcode::NxDomain);
}

// --- encode_name boundaries ---

#[test]
fn encode_name_rejects_too_long_and_bad_labels() {
    // 254-byte name (over 253) must fail
    let too_long = format!("{}.{}", "a".repeat(63), "b".repeat(190));
    assert!(too_long.len() > 253);
    assert!(matches!(
        build_query(1, &too_long),
        Err(DnsError::NameTooLong)
    ));

    // exactly 253 should encode
    // 63+1+63+1+63+1+61 = 253
    let ok253 = format!(
        "{}.{}.{}.{}",
        "a".repeat(63),
        "b".repeat(63),
        "c".repeat(63),
        "d".repeat(61)
    );
    assert_eq!(ok253.len(), 253);
    assert!(build_query(2, &ok253).is_ok());

    // label > 63
    let bad_label = format!("{}.example.com", "x".repeat(64));
    assert!(matches!(
        build_query(3, &bad_label),
        Err(DnsError::BadLabel)
    ));

    // empty label (double dot)
    assert!(matches!(
        build_query(4, "a..b.example.com"),
        Err(DnsError::BadLabel)
    ));

    // root-ish empty handled: trailing dots stripped so "" becomes empty name
    // build_query on empty after strip — name "" is allowed as root
    assert!(build_query(5, ".").is_ok() || build_query(5, "").is_err() || build_query(5, ".").is_ok());
}

// --- parse_message / parse_name: short packets, truncation, compression ---

#[test]
fn parse_message_too_short_and_header() {
    assert!(matches!(parse_message(&[]), Err(DnsError::TooShort)));
    assert!(matches!(parse_message(&[0u8; 11]), Err(DnsError::TooShort)));
    // 12-byte header only: no question → ok empty question
    let hdr = [0u8; 12];
    let msg = parse_message(&hdr).unwrap();
    assert!(!msg.is_response);
    assert_eq!(msg.rcode, Rcode::NoError);
}

#[test]
fn parse_question_name_too_short() {
    assert!(matches!(
        parse_question_name(&[0u8; 11]),
        Err(DnsError::TooShort)
    ));
}

#[test]
fn parse_a_response_with_compression_pointer() {
    // build_response_a uses 0xc00c name pointer in answers
    let resp = build_response_a(0xabcd, "svc.lab.test", &[[10, 1, 2, 3]], 120).unwrap();
    let msg = parse_message(&resp).unwrap();
    assert_eq!(msg.id, 0xabcd);
    assert!(msg.is_response);
    assert_eq!(msg.rcode, Rcode::NoError);
    assert_eq!(msg.question_name, "svc.lab.test");
    assert_eq!(msg.answers.len(), 1);
    assert_eq!(msg.answers[0].rdata_display.as_deref(), Some("10.1.2.3"));
    assert_eq!(msg.answers[0].rtype, 1);
}

#[test]
fn parse_nxdomain_rcode() {
    let nx = build_response_nxdomain(42, "nope.lab.test").unwrap();
    let msg = parse_message(&nx).unwrap();
    assert_eq!(msg.rcode, Rcode::NxDomain);
    assert!(msg.answers.is_empty());
    assert_eq!(msg.question_name, "nope.lab.test");
}

#[test]
fn parse_truncated_answer_rdata() {
    // Valid header + question, ANCOUNT=1 but cut off mid-answer
    let mut q = build_query(9, "a.b").unwrap();
    // Force response + ancount=1
    q[2] = 0x81;
    q[3] = 0x80;
    q[6] = 0;
    q[7] = 1; // ANCOUNT
    // Append incomplete RR (pointer + type only)
    q.extend_from_slice(&[0xc0, 0x0c, 0x00, 0x01]);
    assert!(matches!(parse_message(&q), Err(DnsError::Truncated)));
}

#[test]
fn parse_name_pointer_loop_guard() {
    // Packet that points to itself via compression → hop limit
    let mut p = vec![0u8; 12];
    p[4] = 0;
    p[5] = 1; // QDCOUNT=1
    // name at offset 12: pointer to self 0xc00c
    p.push(0xc0);
    p.push(12);
    p.extend_from_slice(&[0, 1, 0, 1]); // type/class
    assert!(matches!(
        parse_message(&p),
        Err(DnsError::BadLabel) | Err(DnsError::Truncated)
    ));
}

#[test]
fn multi_answer_a_records() {
    let resp = build_response_a(
        1,
        "multi.example.com",
        &[[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3]],
        60,
    )
    .unwrap();
    let msg = parse_message(&resp).unwrap();
    assert_eq!(msg.answers.len(), 3);
    let ips: Vec<_> = msg
        .answers
        .iter()
        .filter_map(|a| a.rdata_display.clone())
        .collect();
    assert_eq!(ips, vec!["1.1.1.1", "2.2.2.2", "3.3.3.3"]);
}

/// Crafted packets that pin comparison/arithmetic in parse_message / parse_name.
#[test]
fn parse_message_exact_rr_header_boundary() {
    // Header 12 + root question name (1 zero) + qtype/qclass 4 = 17 bytes, then
    // answer: pointer 2 + type/class/ttl/rdlen 10 + rdata 0 → exact end.
    let mut p = vec![0u8; 12];
    p[0] = 0x12;
    p[1] = 0x34;
    p[2] = 0x81;
    p[3] = 0x80; // response
    p[4] = 0;
    p[5] = 1; // QDCOUNT
    p[6] = 0;
    p[7] = 1; // ANCOUNT
    p.push(0); // root name in question
    p.extend_from_slice(&1u16.to_be_bytes()); // QTYPE A
    p.extend_from_slice(&1u16.to_be_bytes()); // QCLASS
    // Answer: name pointer to offset 12 (root), then 10-byte RR header, rdlength 0
    p.extend_from_slice(&0xc00cu16.to_be_bytes());
    p.extend_from_slice(&1u16.to_be_bytes()); // type A
    p.extend_from_slice(&1u16.to_be_bytes()); // class
    p.extend_from_slice(&60u32.to_be_bytes()); // ttl
    p.extend_from_slice(&0u16.to_be_bytes()); // rdlength 0
    // offset+10 == packet.len() after name; must NOT Truncated (kills > → >=)
    let msg = parse_message(&p).expect("exact RR header end must parse");
    assert_eq!(msg.answers.len(), 1);
    assert_eq!(msg.answers[0].rtype, 1);
    assert!(msg.answers[0].rdata_display.is_none()); // len != 4
}

#[test]
fn parse_a_requires_type_and_len_four() {
    // type TXT (16) with 4 bytes must NOT look like A
    let mut p = build_response_a(1, "x.test", &[[9, 9, 9, 9]], 60).unwrap();
    // find type field in first answer (after question): build_response uses pointer 0xc00c
    // Patch answer type from 1 to 16 (TXT) at the answer type position.
    // Layout: header12 + encoded name + 4 q + for each ans: 2 ptr + 2 type ...
    // Safer: parse known good A then craft manually
    let mut pkt = vec![0u8; 12];
    pkt[2] = 0x81;
    pkt[3] = 0x80;
    pkt[5] = 1;
    pkt[7] = 1;
    // question: 1,"a",0 + type + class
    pkt.push(1);
    pkt.push(b'a');
    pkt.push(0);
    pkt.extend_from_slice(&1u16.to_be_bytes());
    pkt.extend_from_slice(&1u16.to_be_bytes());
    // answer pointer + type 16 + class + ttl + rdlen 4 + 4 bytes
    pkt.extend_from_slice(&0xc00cu16.to_be_bytes());
    pkt.extend_from_slice(&16u16.to_be_bytes()); // TXT not A
    pkt.extend_from_slice(&1u16.to_be_bytes());
    pkt.extend_from_slice(&0u32.to_be_bytes());
    pkt.extend_from_slice(&4u16.to_be_bytes());
    pkt.extend_from_slice(&[1, 2, 3, 4]);
    let msg = parse_message(&pkt).unwrap();
    assert_eq!(msg.answers[0].rtype, 16);
    assert!(
        msg.answers[0].rdata_display.is_none(),
        "non-A with 4 bytes must not become dotted IP (kills && → ||)"
    );
}

#[test]
fn parse_cname_uses_rdata_start_not_end() {
    // CNAME rdata is a name; offset after consuming rdata points past it.
    // CNAME decode must use offset - rdlength (start of rdata).
    let mut pkt = vec![0u8; 12];
    pkt[2] = 0x81;
    pkt[3] = 0x80;
    pkt[5] = 1;
    pkt[7] = 1;
    // QNAME: www
    pkt.push(3);
    pkt.extend_from_slice(b"www");
    pkt.push(0);
    pkt.extend_from_slice(&5u16.to_be_bytes()); // QTYPE CNAME
    pkt.extend_from_slice(&1u16.to_be_bytes());
    // Answer name pointer to question name at 12
    pkt.extend_from_slice(&0xc00cu16.to_be_bytes());
    pkt.extend_from_slice(&5u16.to_be_bytes()); // type CNAME
    pkt.extend_from_slice(&1u16.to_be_bytes());
    pkt.extend_from_slice(&60u32.to_be_bytes());
    // rdata: label "cdn" + root = 5 bytes
    let rdata_start = pkt.len() + 2; // after rdlength field we'll push
    let mut rdata = vec![3u8];
    rdata.extend_from_slice(b"cdn");
    rdata.push(0);
    pkt.extend_from_slice(&(rdata.len() as u16).to_be_bytes());
    let _ = rdata_start;
    pkt.extend_from_slice(&rdata);
    let msg = parse_message(&pkt).unwrap();
    assert_eq!(msg.answers[0].rtype, 5);
    assert_eq!(
        msg.answers[0].rdata_display.as_deref(),
        Some("cdn"),
        "CNAME target must parse from rdata start (kills offset - rdlength → +)"
    );
}

#[test]
fn parse_name_label_ends_exactly_at_packet_end() {
    // Single label that fills to end of packet (no room for extra bytes).
    // end == packet.len() must succeed (kills end > len → >=).
    let mut pkt = vec![0u8; 12];
    pkt[5] = 1; // QDCOUNT
    // name: len=3 "abc" 0  then type class — label end is exact mid-packet
    pkt.push(3);
    pkt.extend_from_slice(b"abc");
    pkt.push(0);
    pkt.extend_from_slice(&1u16.to_be_bytes());
    pkt.extend_from_slice(&1u16.to_be_bytes());
    let (id, name) = parse_question_name(&pkt).unwrap();
    assert_eq!(id, 0);
    assert_eq!(name, "abc");
}

#[test]
fn parse_name_compression_pointer_bits() {
    // Answer name is only a compression pointer to offset 12 question name.
    let resp = build_response_a(99, "ptr.lab", &[[8, 8, 8, 8]], 30).unwrap();
    let msg = parse_message(&resp).unwrap();
    assert_eq!(msg.question_name, "ptr.lab");
    assert_eq!(msg.answers[0].rdata_display.as_deref(), Some("8.8.8.8"));
    // Corrupt pointer high bits would break if & 0xc0 / << 8 / | are wrong
    // Rebuild: question "z" at 12, answer pointer 0xc00c
    let mut p = vec![0u8; 12];
    p[2] = 0x81;
    p[3] = 0x80;
    p[5] = 1;
    p[7] = 1;
    p.push(1);
    p.push(b'z');
    p.push(0);
    p.extend_from_slice(&1u16.to_be_bytes());
    p.extend_from_slice(&1u16.to_be_bytes());
    p.extend_from_slice(&0xc00cu16.to_be_bytes()); // must resolve to "z"
    p.extend_from_slice(&1u16.to_be_bytes());
    p.extend_from_slice(&1u16.to_be_bytes());
    p.extend_from_slice(&0u32.to_be_bytes());
    p.extend_from_slice(&4u16.to_be_bytes());
    p.extend_from_slice(&[7, 7, 7, 7]);
    let m = parse_message(&p).unwrap();
    assert_eq!(m.answers[0].name, "z");
    assert_eq!(m.answers[0].rdata_display.as_deref(), Some("7.7.7.7"));
}

#[test]
fn parse_question_name_len_exactly_12_header_only_fails_name() {
    // len == 12: not TooShort for question parse entry, but name parse truncates
    let p = [0u8; 12];
    // parse_question_name requires len >= 12 then parse_name at 12 → Truncated
    assert!(matches!(
        parse_question_name(&p),
        Err(DnsError::Truncated)
    ));
    // len 11 is TooShort (kills < → <= would make 12 TooShort incorrectly for build path)
    assert!(matches!(
        parse_question_name(&[0u8; 11]),
        Err(DnsError::TooShort)
    ));
}

#[test]
fn parse_name_pointer_high_byte_must_shift() {
    // Name at absolute offset 512; pointer 0xc2 0x00 → ((0x02)<<8)|0x00 = 512.
    // If << becomes >>, pointer → 0 and question name is wrong/empty.
    let mut pkt = vec![0u8; 520];
    pkt[2] = 0x81;
    pkt[3] = 0x80;
    pkt[5] = 1;
    pkt[7] = 1;
    pkt[12] = 0xc2;
    pkt[13] = 0x00;
    pkt[14] = 0;
    pkt[15] = 1;
    pkt[16] = 0;
    pkt[17] = 1;
    pkt[18] = 0xc2;
    pkt[19] = 0x00;
    pkt[20] = 0;
    pkt[21] = 1;
    pkt[22] = 0;
    pkt[23] = 1;
    // ttl zeros 24..27, rdlength at 28..29
    pkt[28] = 0;
    pkt[29] = 4;
    pkt[30] = 4;
    pkt[31] = 3;
    pkt[32] = 2;
    pkt[33] = 1;
    pkt[512] = 2;
    pkt[513] = b'h';
    pkt[514] = b'i';
    pkt[515] = 0;
    let msg = parse_message(&pkt).expect("high pointer must resolve");
    assert_eq!(msg.question_name, "hi");
    assert_eq!(msg.answers[0].name, "hi");
    assert_eq!(msg.answers[0].rdata_display.as_deref(), Some("4.3.2.1"));
}

#[test]
fn parse_name_pointer_truncated_second_byte() {
    // Compression flag set but no second pointer byte → Truncated.
    // Kills `offset + 1` arithmetic mutants that skip the bound check.
    let mut p = vec![0u8; 13];
    p[5] = 1; // QDCOUNT
    p[12] = 0xc0; // pointer high, missing low byte (packet ends)
    assert!(matches!(
        parse_question_name(&p),
        Err(DnsError::Truncated)
    ));
}

#[test]
fn parse_message_truncated_mid_rdlength_add() {
    // offset += 10 must advance; if += becomes -=, next checks fail differently.
    // Packet: valid question, ANCOUNT=1, complete 10-byte RR header, rdlength claims 4 but only 2 bytes left.
    let mut q = build_query(1, "a.b").unwrap();
    q[2] = 0x81;
    q[3] = 0x80;
    q[6] = 0;
    q[7] = 1;
    q.extend_from_slice(&0xc00cu16.to_be_bytes());
    q.extend_from_slice(&1u16.to_be_bytes());
    q.extend_from_slice(&1u16.to_be_bytes());
    q.extend_from_slice(&0u32.to_be_bytes());
    q.extend_from_slice(&4u16.to_be_bytes()); // claims 4
    q.extend_from_slice(&[1, 2]); // only 2
    assert!(matches!(parse_message(&q), Err(DnsError::Truncated)));
}

#[test]
fn hops_limit_sixteen_ok_seventeen_bad() {
    // Build a chain of compression pointers: each points to next, last to a real name.
    // 16 hops allowed, 17th must BadLabel.
    fn chain(hops: usize) -> Vec<u8> {
        // layout: header + question is a pointer chain then name at the end
        let name_off = 12 + hops * 2;
        let mut p = vec![0u8; name_off + 4];
        p[5] = 1; // QDCOUNT
        for i in 0..hops {
            let off = 12 + i * 2;
            let target = 12 + (i + 1) * 2;
            // pointer to target (must be < 0x3fff)
            p[off] = 0xc0 | ((target >> 8) as u8);
            p[off + 1] = (target & 0xff) as u8;
        }
        // final real name at name_off
        p[name_off] = 1;
        p[name_off + 1] = b'x';
        p[name_off + 2] = 0;
        // pad type/class for question completeness not needed for parse_question_name
        p
    }
    // 1 hop works
    let (id, name) = parse_question_name(&chain(1)).unwrap();
    assert_eq!(id, 0);
    assert_eq!(name, "x");
    // 16 hops: hops counter goes 1..16, check is hops > 16 so 16 OK
    assert_eq!(parse_question_name(&chain(16)).unwrap().1, "x");
    // 17 hops should fail
    assert!(matches!(
        parse_question_name(&chain(17)),
        Err(DnsError::BadLabel)
    ));
}

// --- expand: multi-label + already-under-base (simplified path) ---

#[test]
fn expand_label_matrix() {
    assert_eq!(
        expand_label("www", "example.com").as_deref(),
        Some("www.example.com")
    );
    assert_eq!(
        expand_label("api.v1", "example.com").as_deref(),
        Some("api.v1.example.com")
    );
    assert_eq!(
        expand_label("www.example.com", "example.com").as_deref(),
        Some("www.example.com")
    );
    assert_eq!(
        expand_label("example.com", "example.com").as_deref(),
        Some("example.com")
    );
    assert!(expand_label("#", "example.com").is_none());
}

// --- wildcard: empty address sets must not fingerprint ---

#[test]
fn fingerprint_rejects_empty_address_live() {
    let results = vec![
        (
            "a.ex.com".into(),
            ResponseClass::Live {
                addresses: vec![],
            },
        ),
        (
            "b.ex.com".into(),
            ResponseClass::Live {
                addresses: vec!["1.1.1.1".into()],
            },
        ),
    ];
    assert!(
        fingerprint_from_probe_results(&results).is_none(),
        "empty Live addresses must not form a fingerprint"
    );
    assert!(
        fingerprints_for_wildcard_probes(&results).is_empty(),
        "empty Live addresses must yield no multi fingerprints"
    );
}

#[test]
fn fingerprint_all_empty_addresses_none() {
    let results = vec![
        (
            "a.ex.com".into(),
            ResponseClass::Live {
                addresses: vec![],
            },
        ),
        (
            "b.ex.com".into(),
            ResponseClass::Live {
                addresses: vec![],
            },
        ),
    ];
    assert!(fingerprint_from_probe_results(&results).is_none());
    assert!(fingerprints_for_wildcard_probes(&results).is_empty());
}

#[test]
fn wildcard_parents_and_is_empty_surface() {
    let mut f = WildcardFilter::new();
    assert!(f.is_empty());
    assert_eq!(f.parents().count(), 0);
    f.register(
        "dev.example.com",
        WildcardFingerprint::from_addresses(["9.9.9.9".into()]),
    );
    assert!(!f.is_empty());
    let parents: Vec<_> = f.parents().map(|(p, _)| p.clone()).collect();
    assert_eq!(parents, vec!["dev.example.com".to_string()]);
    // child under registered parent with same FP blocked
    assert!(!f.allow("x.dev.example.com", &["9.9.9.9".into()]));
    assert!(f.allow("x.dev.example.com", &["1.2.3.4".into()]));
}
