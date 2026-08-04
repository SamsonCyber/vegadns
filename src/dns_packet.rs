//! Minimal DNS encode/decode for A queries (stub resolver path).

use thiserror::Error;

#[derive(Debug, Error)]
pub enum DnsError {
    #[error("packet too short")]
    TooShort,
    #[error("invalid label")]
    BadLabel,
    #[error("name too long")]
    NameTooLong,
    #[error("truncated packet")]
    Truncated,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum Rcode {
    NoError = 0,
    FormErr = 1,
    ServFail = 2,
    NxDomain = 3,
    NotImp = 4,
    Refused = 5,
    Other = 255,
}

impl From<u8> for Rcode {
    fn from(v: u8) -> Self {
        match v & 0x0f {
            0 => Rcode::NoError,
            1 => Rcode::FormErr,
            2 => Rcode::ServFail,
            3 => Rcode::NxDomain,
            4 => Rcode::NotImp,
            5 => Rcode::Refused,
            _ => Rcode::Other,
        }
    }
}

#[derive(Debug, Clone)]
pub struct DnsAnswer {
    pub name: String,
    pub rtype: u16,
    pub rdata_display: Option<String>,
}

#[derive(Debug, Clone)]
pub struct DnsMessage {
    pub id: u16,
    pub is_response: bool,
    pub rcode: Rcode,
    pub question_name: String,
    pub answers: Vec<DnsAnswer>,
}

/// Build a standard recursive A query packet.
pub fn build_query(id: u16, name: &str) -> Result<Vec<u8>, DnsError> {
    let mut buf = Vec::with_capacity(64 + name.len());
    build_query_into(&mut buf, id, name)?;
    Ok(buf)
}

/// Write a recursive A query into `buf` (clears first). Hot-path helper.
pub fn build_query_into(buf: &mut Vec<u8>, id: u16, name: &str) -> Result<(), DnsError> {
    buf.clear();
    buf.extend_from_slice(&id.to_be_bytes());
    // flags: RD=1
    buf.extend_from_slice(&0x0100u16.to_be_bytes());
    buf.extend_from_slice(&1u16.to_be_bytes()); // QDCOUNT
    buf.extend_from_slice(&0u16.to_be_bytes()); // ANCOUNT
    buf.extend_from_slice(&0u16.to_be_bytes()); // NSCOUNT
    buf.extend_from_slice(&0u16.to_be_bytes()); // ARCOUNT
    encode_name(name, buf)?;
    buf.extend_from_slice(&1u16.to_be_bytes()); // QTYPE A
    buf.extend_from_slice(&1u16.to_be_bytes()); // QCLASS IN
    Ok(())
}

/// Patch TXID in an existing query packet (first 2 bytes). No realloc.
#[inline]
pub fn patch_query_id(pkt: &mut [u8], id: u16) {
    if pkt.len() >= 2 {
        let b = id.to_be_bytes();
        pkt[0] = b[0];
        pkt[1] = b[1];
    }
}

/// Read TXID from a packet without full parse.
#[inline]
pub fn peek_id(packet: &[u8]) -> Option<u16> {
    if packet.len() < 2 {
        None
    } else {
        Some(u16::from_be_bytes([packet[0], packet[1]]))
    }
}

/// Build a NOERROR A response (for mock server).
pub fn build_response_a(id: u16, name: &str, addrs: &[[u8; 4]], ttl: u32) -> Result<Vec<u8>, DnsError> {
    let mut buf = Vec::with_capacity(512);
    buf.extend_from_slice(&id.to_be_bytes());
    // QR=1, RD=1, RA=1
    buf.extend_from_slice(&0x8180u16.to_be_bytes());
    buf.extend_from_slice(&1u16.to_be_bytes()); // QDCOUNT
    buf.extend_from_slice(&(addrs.len() as u16).to_be_bytes()); // ANCOUNT
    buf.extend_from_slice(&0u16.to_be_bytes());
    buf.extend_from_slice(&0u16.to_be_bytes());
    encode_name(name, &mut buf)?;
    buf.extend_from_slice(&1u16.to_be_bytes());
    buf.extend_from_slice(&1u16.to_be_bytes());
    for addr in addrs {
        // pointer to name at offset 12
        buf.extend_from_slice(&0xc00cu16.to_be_bytes());
        buf.extend_from_slice(&1u16.to_be_bytes()); // type A
        buf.extend_from_slice(&1u16.to_be_bytes()); // class IN
        buf.extend_from_slice(&ttl.to_be_bytes());
        buf.extend_from_slice(&4u16.to_be_bytes());
        buf.extend_from_slice(addr);
    }
    Ok(buf)
}

/// Build NXDOMAIN response.
pub fn build_response_nxdomain(id: u16, name: &str) -> Result<Vec<u8>, DnsError> {
    let mut buf = Vec::with_capacity(256);
    buf.extend_from_slice(&id.to_be_bytes());
    // QR=1, RD=1, RA=1, RCODE=3
    buf.extend_from_slice(&0x8183u16.to_be_bytes());
    buf.extend_from_slice(&1u16.to_be_bytes());
    buf.extend_from_slice(&0u16.to_be_bytes());
    buf.extend_from_slice(&0u16.to_be_bytes());
    buf.extend_from_slice(&0u16.to_be_bytes());
    encode_name(name, &mut buf)?;
    buf.extend_from_slice(&1u16.to_be_bytes());
    buf.extend_from_slice(&1u16.to_be_bytes());
    Ok(buf)
}

/// Build SERVFAIL response (mock stress / recursive noise).
pub fn build_response_servfail(id: u16, name: &str) -> Result<Vec<u8>, DnsError> {
    let mut buf = Vec::with_capacity(256);
    buf.extend_from_slice(&id.to_be_bytes());
    // QR=1, RD=1, RA=1, RCODE=2 (ServFail)
    buf.extend_from_slice(&0x8182u16.to_be_bytes());
    buf.extend_from_slice(&1u16.to_be_bytes());
    buf.extend_from_slice(&0u16.to_be_bytes());
    buf.extend_from_slice(&0u16.to_be_bytes());
    buf.extend_from_slice(&0u16.to_be_bytes());
    encode_name(name, &mut buf)?;
    buf.extend_from_slice(&1u16.to_be_bytes());
    buf.extend_from_slice(&1u16.to_be_bytes());
    Ok(buf)
}

fn encode_name(name: &str, buf: &mut Vec<u8>) -> Result<(), DnsError> {
    let name = name.trim_end_matches('.');
    if name.len() > 253 {
        return Err(DnsError::NameTooLong);
    }
    if name.is_empty() {
        buf.push(0);
        return Ok(());
    }
    for label in name.split('.') {
        if label.is_empty() || label.len() > 63 {
            return Err(DnsError::BadLabel);
        }
        buf.push(label.len() as u8);
        buf.extend_from_slice(label.as_bytes());
    }
    buf.push(0);
    Ok(())
}

/// Skip a compressed/uncompressed DNS name without allocating.
fn skip_name(packet: &[u8], mut offset: usize) -> Result<usize, DnsError> {
    let mut hops = 0u8;
    loop {
        if offset >= packet.len() {
            return Err(DnsError::Truncated);
        }
        let len = packet[offset];
        if len == 0 {
            return Ok(offset + 1);
        }
        if len & 0xc0 == 0xc0 {
            // pointer: 2 bytes
            if offset + 1 >= packet.len() {
                return Err(DnsError::Truncated);
            }
            return Ok(offset + 2);
        }
        if len & 0xc0 != 0 {
            return Err(DnsError::BadLabel);
        }
        offset = offset
            .checked_add(1 + len as usize)
            .ok_or(DnsError::Truncated)?;
        hops = hops.saturating_add(1);
        if hops > 64 {
            return Err(DnsError::BadLabel);
        }
    }
}

/// Fast path: TXID + classify A answers without building question/answer name strings.
/// Used by the resolve hot path (allocations only for Live address list).
pub fn classify_response_packet(packet: &[u8]) -> Result<(u16, crate::classify::ResponseClass), DnsError> {
    use crate::classify::ResponseClass;
    if packet.len() < 12 {
        return Err(DnsError::TooShort);
    }
    let id = u16::from_be_bytes([packet[0], packet[1]]);
    let flags = u16::from_be_bytes([packet[2], packet[3]]);
    let is_response = (flags & 0x8000) != 0;
    if !is_response {
        return Ok((id, ResponseClass::Garbage));
    }
    let rcode = Rcode::from((flags & 0x000f) as u8);
    let qdcount = u16::from_be_bytes([packet[4], packet[5]]);
    let ancount = u16::from_be_bytes([packet[6], packet[7]]);

    let mut offset = 12usize;
    for _ in 0..qdcount {
        offset = skip_name(packet, offset)?;
        offset = offset.checked_add(4).ok_or(DnsError::Truncated)?; // qtype+qclass
    }

    match rcode {
        Rcode::NxDomain => return Ok((id, ResponseClass::NxDomain)),
        Rcode::NoError => {}
        other => return Ok((id, ResponseClass::Error { rcode: other as u8 })),
    }

    if ancount == 0 {
        return Ok((id, ResponseClass::NoErrorEmpty));
    }

    let mut addresses = Vec::with_capacity(ancount as usize);
    for _ in 0..ancount {
        if offset >= packet.len() {
            break;
        }
        offset = skip_name(packet, offset)?;
        if offset + 10 > packet.len() {
            return Err(DnsError::Truncated);
        }
        let rtype = u16::from_be_bytes([packet[offset], packet[offset + 1]]);
        let rdlength = u16::from_be_bytes([packet[offset + 8], packet[offset + 9]]) as usize;
        offset += 10;
        if offset + rdlength > packet.len() {
            return Err(DnsError::Truncated);
        }
        if rtype == 1 && rdlength == 4 {
            let b = &packet[offset..offset + 4];
            addresses.push(format!("{}.{}.{}.{}", b[0], b[1], b[2], b[3]));
        }
        offset += rdlength;
    }

    if addresses.is_empty() {
        Ok((id, ResponseClass::NoErrorEmpty))
    } else {
        Ok((id, ResponseClass::Live { addresses }))
    }
}

/// Parse a DNS message enough for A-answer enumeration.
pub fn parse_message(packet: &[u8]) -> Result<DnsMessage, DnsError> {
    if packet.len() < 12 {
        return Err(DnsError::TooShort);
    }
    let id = u16::from_be_bytes([packet[0], packet[1]]);
    let flags = u16::from_be_bytes([packet[2], packet[3]]);
    let is_response = (flags & 0x8000) != 0;
    let rcode = Rcode::from((flags & 0x000f) as u8);
    let qdcount = u16::from_be_bytes([packet[4], packet[5]]);
    let ancount = u16::from_be_bytes([packet[6], packet[7]]);

    let mut offset = 12usize;
    let mut question_name = String::new();
    if qdcount > 0 {
        let (name, next) = parse_name(packet, offset)?;
        question_name = name;
        offset = next;
        // skip qtype + qclass
        offset = offset.checked_add(4).ok_or(DnsError::Truncated)?;
    }

    let mut answers = Vec::new();
    for _ in 0..ancount {
        if offset >= packet.len() {
            break;
        }
        let (name, next) = parse_name(packet, offset)?;
        offset = next;
        if offset + 10 > packet.len() {
            return Err(DnsError::Truncated);
        }
        let rtype = u16::from_be_bytes([packet[offset], packet[offset + 1]]);
        // class at +2
        // ttl at +4
        let rdlength = u16::from_be_bytes([packet[offset + 8], packet[offset + 9]]) as usize;
        offset += 10;
        if offset + rdlength > packet.len() {
            return Err(DnsError::Truncated);
        }
        let rdata = &packet[offset..offset + rdlength];
        offset += rdlength;
        let rdata_display = if rtype == 1 && rdata.len() == 4 {
            Some(format!("{}.{}.{}.{}", rdata[0], rdata[1], rdata[2], rdata[3]))
        } else if rtype == 5 {
            // CNAME
            parse_name(packet, offset - rdlength)
                .ok()
                .map(|(n, _)| n)
        } else {
            None
        };
        answers.push(DnsAnswer {
            name,
            rtype,
            rdata_display,
        });
    }

    Ok(DnsMessage {
        id,
        is_response,
        rcode,
        question_name,
        answers,
    })
}

/// Parse question name only (for mock server).
pub fn parse_question_name(packet: &[u8]) -> Result<(u16, String), DnsError> {
    if packet.len() < 12 {
        return Err(DnsError::TooShort);
    }
    let id = u16::from_be_bytes([packet[0], packet[1]]);
    let (name, _) = parse_name(packet, 12)?;
    Ok((id, name))
}

fn parse_name(packet: &[u8], mut offset: usize) -> Result<(String, usize), DnsError> {
    let mut labels = Vec::new();
    let mut jumped = false;
    let mut end_offset = offset;
    let mut hops = 0;

    loop {
        if offset >= packet.len() {
            return Err(DnsError::Truncated);
        }
        let len = packet[offset];
        if len == 0 {
            if !jumped {
                end_offset = offset + 1;
            }
            break;
        }
        if (len & 0xc0) == 0xc0 {
            if offset + 1 >= packet.len() {
                return Err(DnsError::Truncated);
            }
            let ptr = (((len as usize) & 0x3f) << 8) | packet[offset + 1] as usize;
            if !jumped {
                end_offset = offset + 2;
            }
            offset = ptr;
            jumped = true;
            hops += 1;
            if hops > 16 {
                return Err(DnsError::BadLabel);
            }
            continue;
        }
        offset += 1;
        let end = offset + len as usize;
        if end > packet.len() {
            return Err(DnsError::Truncated);
        }
        let label = std::str::from_utf8(&packet[offset..end]).map_err(|_| DnsError::BadLabel)?;
        labels.push(label.to_ascii_lowercase());
        offset = end;
        if !jumped {
            end_offset = offset;
        }
    }
    Ok((labels.join("."), end_offset))
}

/// Parse IPv4 dotted string into octets.
pub fn parse_ipv4(s: &str) -> Option<[u8; 4]> {
    let parts: Vec<_> = s.split('.').collect();
    if parts.len() != 4 {
        return None;
    }
    let mut out = [0u8; 4];
    for (i, p) in parts.iter().enumerate() {
        out[i] = p.parse().ok()?;
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_query_name() {
        let q = build_query(0x1234, "www.example.com").unwrap();
        let (id, name) = parse_question_name(&q).unwrap();
        assert_eq!(id, 0x1234);
        assert_eq!(name, "www.example.com");
    }

    #[test]
    fn patch_and_peek_id_hot_path() {
        let mut buf = Vec::new();
        build_query_into(&mut buf, 0, "api.test").unwrap();
        assert_eq!(peek_id(&buf), Some(0));
        patch_query_id(&mut buf, 0xabcd);
        assert_eq!(peek_id(&buf), Some(0xabcd));
        let (id, name) = parse_question_name(&buf).unwrap();
        assert_eq!(id, 0xabcd);
        assert_eq!(name, "api.test");
    }

    #[test]
    fn classify_response_packet_a_and_nx() {
        let q = build_query(7, "www.example.com").unwrap();
        let a = build_response_a(7, "www.example.com", &[[1, 2, 3, 4]], 60).unwrap();
        let (id, class) = classify_response_packet(&a).unwrap();
        assert_eq!(id, 7);
        match class {
            crate::classify::ResponseClass::Live { addresses } => {
                assert_eq!(addresses, vec!["1.2.3.4".to_string()]);
            }
            other => panic!("expected Live, got {other:?}"),
        }
        let nx = build_response_nxdomain(9, "nope.example.com").unwrap();
        let (id2, class2) = classify_response_packet(&nx).unwrap();
        assert_eq!(id2, 9);
        assert!(matches!(class2, crate::classify::ResponseClass::NxDomain));
        let _ = q;
    }

    #[test]
    fn parse_a_response() {
        let resp = build_response_a(7, "www.example.com", &[[1, 2, 3, 4]], 60).unwrap();
        let msg = parse_message(&resp).unwrap();
        assert_eq!(msg.id, 7);
        assert!(msg.is_response);
        assert_eq!(msg.rcode, Rcode::NoError);
        assert_eq!(msg.answers.len(), 1);
        assert_eq!(
            msg.answers[0].rdata_display.as_deref(),
            Some("1.2.3.4")
        );
    }
}
