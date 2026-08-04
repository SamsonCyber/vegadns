#!/usr/bin/env python3
"""Tiny lab-only HTTP dummy: 200 for hosts in a known list, 404 otherwise.

Bind to private interfaces only (default 127.0.0.1). Not for public exposure.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosts-file", type=Path, required=True, help="one hostname per line")
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18080)
    args = ap.parse_args()

    hosts = {
        ln.strip().lower().rstrip(".")
        for ln in args.hosts_file.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }
    # also allow bare labels if file is FQDNs
    print(f"[lab_http_dummy] {len(hosts)} hosts on http://{args.bind}:{args.port}/")

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            host = (self.headers.get("Host") or "").split(":")[0].lower().rstrip(".")
            ok = host in hosts or host.replace("www.", "") in hosts
            body = b"lab-ok\n" if ok else b"lab-miss\n"
            self.send_response(200 if ok else 404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *a):  # quieter
            pass

    srv = ThreadingHTTPServer((args.bind, args.port), H)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
