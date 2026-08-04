#!/usr/bin/env python3
"""Minimal sequential DNS A-resolve baseline for black-box fixture comparison.

Not competitive; exists so the harness always has a second runnable tool on Windows
when massdns is absent. Same inputs: FQDN list + resolver host:port.
"""
from __future__ import annotations

import argparse
import random
import socket
import struct
import time
from pathlib import Path


def encode_name(name: str) -> bytes:
    out = bytearray()
    for label in name.strip(".").split("."):
        b = label.encode("ascii")
        out.append(len(b))
        out.extend(b)
    out.append(0)
    return bytes(out)


def build_query(qid: int, name: str) -> bytes:
    header = struct.pack("!HHHHHH", qid & 0xFFFF, 0x0100, 1, 0, 0, 0)
    return header + encode_name(name) + struct.pack("!HH", 1, 1)


def parse_response(data: bytes) -> tuple[int, int, list[str]]:
    if len(data) < 12:
        return 0, 255, []
    qid, flags, qd, an, _, _ = struct.unpack("!HHHHHH", data[:12])
    rcode = flags & 0x0F
    # skip question
    i = 12
    while i < len(data) and data[i] != 0:
        if data[i] & 0xC0 == 0xC0:
            i += 2
            break
        i += 1 + data[i]
    else:
        i += 1
    i += 4  # qtype qclass
    addrs: list[str] = []
    for _ in range(an):
        if i >= len(data):
            break
        if data[i] & 0xC0 == 0xC0:
            i += 2
        else:
            while i < len(data) and data[i] != 0:
                i += 1 + data[i]
            i += 1
        if i + 10 > len(data):
            break
        rtype, _, _, rdlen = struct.unpack("!HHIH", data[i : i + 10])
        i += 10
        rdata = data[i : i + rdlen]
        i += rdlen
        if rtype == 1 and rdlen == 4:
            addrs.append(".".join(str(b) for b in rdata))
    return qid, rcode, addrs


def resolve_one(name: str, resolver: tuple[str, int], timeout: float) -> bool:
    qid = random.randint(1, 65535)
    pkt = build_query(qid, name)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(pkt, resolver)
        data, _ = s.recvfrom(4096)
        rid, rcode, addrs = parse_response(data)
        return rid == qid and rcode == 0 and bool(addrs)
    except OSError:
        return False
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-l", "--list", type=Path, required=True, help="FQDN list")
    ap.add_argument("-r", "--resolver", required=True, help="ip or ip:port")
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--timeout", type=float, default=0.5)
    args = ap.parse_args()
    if ":" in args.resolver:
        host, port_s = args.resolver.rsplit(":", 1)
        resolver = (host, int(port_s))
    else:
        resolver = (args.resolver, 53)
    names = [
        ln.strip().lower().rstrip(".")
        for ln in args.list.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    t0 = time.perf_counter()
    found = []
    for n in names:
        if resolve_one(n, resolver, args.timeout):
            found.append(n)
    wall = time.perf_counter() - t0
    args.output.write_text("\n".join(found) + ("\n" if found else ""), encoding="utf-8")
    qps = len(names) / wall if wall > 0 else 0.0
    print(
        f"naive_resolve found={len(found)} candidates={len(names)} wall={wall:.4f} qps={qps:.1f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
