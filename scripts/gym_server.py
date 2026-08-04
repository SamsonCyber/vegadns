#!/usr/bin/env python3
"""Subdomain Scanner Gym web UI + metrics API + multi-mode true test launcher.

Binds 127.0.0.1 only.
  python scripts/gym_server.py --port 9876
  open http://127.0.0.1:9876/
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "gym" / "static"
SCRIPTS = ROOT / "scripts"

_state = {
    "state": "idle",
    "message": "",
    "out_dir": str(ROOT / "gym_out"),
    "mode": "mock-stress",
}
_lock = threading.Lock()


def set_state(state: str, message: str = "", mode: str | None = None) -> None:
    with _lock:
        _state["state"] = state
        _state["message"] = message
        if mode is not None:
            _state["mode"] = mode


def get_state() -> dict:
    with _lock:
        return dict(_state)


def run_bench_job(out_dir: Path, wordlist_cap: int, mode: str, authorized: bool) -> None:
    set_state("running", f"benchmark mode={mode}", mode=mode)
    try:
        samples = out_dir / "samples.jsonl"
        if samples.exists():
            samples.unlink()
        report_json = out_dir / "bench_report.json"
        if report_json.exists():
            report_json.unlink()
        cmd = [
            sys.executable,
            str(SCRIPTS / "gym_bench.py"),
            "--out",
            str(out_dir),
            "--wordlist-cap",
            str(wordlist_cap),
            "--mode",
            mode,
        ]
        if mode == "live-resolve":
            cmd.append("--authorized")
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=1200)
        log = out_dir / "server_bench.log"
        log.write_text(
            f"exit={p.returncode}\nmode={mode}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}\n",
            encoding="utf-8",
        )
        if p.returncode != 0:
            set_state("error", f"bench exit {p.returncode}", mode=mode)
        else:
            set_state("done", "ok", mode=mode)
    except Exception as e:
        set_state("error", str(e), mode=mode)


class Handler(BaseHTTPRequestHandler):
    out_dir: Path = ROOT / "gym_out"
    wordlist_cap: int = 8000

    def log_message(self, fmt: str, *args) -> None:
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self._json(200, get_state())
            return
        if path == "/api/samples":
            samples = self.out_dir / "samples.jsonl"
            data = samples.read_bytes() if samples.exists() else b""
            self._send(200, data, "application/x-ndjson; charset=utf-8")
            return
        if path == "/api/report":
            rep = self.out_dir / "bench_report.json"
            if rep.exists():
                self._send(200, rep.read_bytes(), "application/json; charset=utf-8")
            else:
                self._json(404, {"error": "no report yet"})
            return
        if path == "/api/meta":
            meta = ROOT / "fixtures" / "gym" / "fixture_meta.json"
            if meta.exists():
                self._send(200, meta.read_bytes(), "application/json; charset=utf-8")
            else:
                self._json(404, {"error": "generate fixtures first"})
            return
        if path == "/api/modes":
            self._json(
                200,
                {
                    "modes": [
                        {
                            "id": "mock-stress",
                            "label": "Mock stress (true local test)",
                            "desc": "Latency + SERVFAIL + drop on gym zone. Default.",
                        },
                        {
                            "id": "mock-clean",
                            "label": "Mock clean (regression)",
                            "desc": "Instant answers. CI / oracle correctness.",
                        },
                        {
                            "id": "live-resolve",
                            "label": "Live public resolvers",
                            "desc": "Real network path. Fixed FQDNs only. Not market QPS.",
                        },
                    ]
                },
            )
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/run":
            st = get_state()
            if st["state"] == "running":
                self._json(409, {"error": "already running"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            mode = "mock-stress"
            if body:
                try:
                    obj = json.loads(body.decode("utf-8"))
                    mode = obj.get("mode") or mode
                except json.JSONDecodeError:
                    pass
            # also allow ?mode=
            qs = parse_qs(urlparse(self.path).query)
            if "mode" in qs:
                mode = qs["mode"][0]
            if mode not in ("mock-clean", "mock-stress", "live-resolve"):
                self._json(400, {"error": f"bad mode {mode}"})
                return
            with _lock:
                _state["out_dir"] = str(self.out_dir)
            set_state("running", "starting", mode=mode)
            t = threading.Thread(
                target=run_bench_job,
                args=(self.out_dir, self.wordlist_cap, mode, mode == "live-resolve"),
                daemon=True,
            )
            t.start()
            self._json(200, {"ok": True, "state": "running", "mode": mode})
            return
        self._json(404, {"error": "not found"})


def main() -> int:
    ap = argparse.ArgumentParser(description="Subdomain Scanner Gym GUI (true test)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9876)
    ap.add_argument("--out", type=Path, default=ROOT / "gym_out")
    ap.add_argument("--wordlist-cap", type=int, default=8000)
    args = ap.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("REFUSED: gym server binds private loopback only", file=sys.stderr)
        return 2

    if not (ROOT / "fixtures" / "gym" / "zone_gym.json").exists():
        subprocess.check_call(
            [sys.executable, str(SCRIPTS / "gen_gym_fixtures.py")],
            cwd=str(ROOT),
        )

    args.out.mkdir(parents=True, exist_ok=True)
    Handler.out_dir = args.out
    Handler.wordlist_cap = args.wordlist_cap

    if not (STATIC / "index.html").exists():
        print(f"missing GUI: {STATIC / 'index.html'}", file=sys.stderr)
        return 1

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Subdomain Scanner Gym TRUE TEST UI: http://{args.host}:{args.port}/")
    print("modes: mock-stress (default) | mock-clean | live-resolve")
    print("claim bounds embedded in every report — not market fastest")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutdown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
