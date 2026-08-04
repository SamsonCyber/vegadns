#!/usr/bin/env python3
"""HackTheBox lab control for authorized VIP/Dedicated accounts.

Token: ~/.secrets/htb_api_token.txt (single line JWT).
API base: https://labs.hackthebox.com/api/v4

Commands:
  user              Who am I
  list [--per-page N] [--search NAME]
  active            Show currently spawned machine
  spawn --id ID | --name NAME
  reset             Reset active machine (if supported)
  stop              Stop/terminate active machine (if supported)
  status            user + active summary
  write-target OUT  Write target JSON for bench wiring

Subscription-authorized only. Not for unauthorized scanning.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://labs.hackthebox.com/api/v4"
DEFAULT_TOKEN = Path.home() / ".secrets" / "htb_api_token.txt"


def load_token(path: Path | None = None) -> str:
    p = path or DEFAULT_TOKEN
    if not p.exists():
        raise SystemExit(f"HTB token missing: {p}")
    t = p.read_text(encoding="utf-8").strip()
    if not t or "PASTE" in t:
        raise SystemExit(f"HTB token empty/placeholder: {p}")
    return t


def api(
    method: str,
    path: str,
    token: str,
    body: dict | None = None,
    timeout: float = 45,
) -> tuple[int, dict | list | str]:
    url = path if path.startswith("http") else f"{API}{path}"
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (vegadns-htb-lab)",
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            code = resp.getcode()
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        code = e.code
    except urllib.error.URLError as e:
        raise SystemExit(f"HTB network error: {e}") from e
    try:
        parsed: dict | list | str = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = raw
    return code, parsed


def cmd_user(token: str) -> dict:
    code, data = api("GET", "/user/info", token)
    if code != 200:
        raise SystemExit(f"user/info failed HTTP {code}: {data}")
    info = data.get("info", data) if isinstance(data, dict) else data
    return info if isinstance(info, dict) else {"raw": info}


def cmd_list(token: str, per_page: int = 30, search: str = "") -> list[dict]:
    q = urllib.parse.urlencode({"per_page": per_page, "page": 1})
    code, data = api("GET", f"/machine/paginated?{q}", token)
    if code != 200:
        raise SystemExit(f"machine/paginated failed HTTP {code}: {data}")
    rows = data.get("data", []) if isinstance(data, dict) else []
    if search:
        s = search.lower()
        rows = [r for r in rows if s in str(r.get("name", "")).lower()]
    return rows


def cmd_active(token: str) -> dict | None:
    code, data = api("GET", "/machine/active", token)
    if code != 200:
        raise SystemExit(f"machine/active failed HTTP {code}: {data}")
    if not isinstance(data, dict):
        return None
    info = data.get("info")
    return info if isinstance(info, dict) else None


def cmd_profile(token: str, machine_id: int | str) -> dict:
    code, data = api("GET", f"/machine/profile/{machine_id}", token)
    if code != 200:
        raise SystemExit(f"machine/profile failed HTTP {code}: {data}")
    info = data.get("info", data) if isinstance(data, dict) else {}
    return info if isinstance(info, dict) else {"raw": info}


def resolve_machine_id(token: str, machine_id: int | None, name: str | None) -> int:
    if machine_id is not None:
        return int(machine_id)
    if not name:
        raise SystemExit("need --id or --name")
    rows = cmd_list(token, per_page=100, search=name)
    exact = [r for r in rows if str(r.get("name", "")).lower() == name.lower()]
    if exact:
        return int(exact[0]["id"])
    if rows:
        return int(rows[0]["id"])
    # try profile by name
    prof = cmd_profile(token, name)
    if "id" in prof:
        return int(prof["id"])
    raise SystemExit(f"machine not found: {name}")


def cmd_spawn(token: str, machine_id: int) -> dict:
    code, data = api("POST", "/vm/spawn", token, body={"machine_id": machine_id})
    # HTB returns 200 with success message
    return {"http": code, "body": data}


def cmd_reset(token: str, machine_id: int | None = None) -> dict:
    # Prefer active id
    active = cmd_active(token)
    mid = machine_id or (int(active["id"]) if active and active.get("id") else None)
    if mid is None:
        raise SystemExit("no active machine to reset")
    code, data = api("POST", "/vm/reset", token, body={"machine_id": mid})
    return {"http": code, "body": data, "machine_id": mid}


def cmd_stop(token: str, machine_id: int | None = None) -> dict:
    active = cmd_active(token)
    mid = machine_id or (int(active["id"]) if active and active.get("id") else None)
    if mid is None:
        raise SystemExit("no active machine to stop")
    # try known terminate endpoints
    for path, body in (
        ("/vm/terminate", {"machine_id": mid}),
        ("/machine/stop", {"machine_id": mid}),
    ):
        code, data = api("POST", path, token, body=body)
        if code in (200, 201):
            return {"http": code, "body": data, "machine_id": mid, "path": path}
        last = {"http": code, "body": data, "machine_id": mid, "path": path}
    return last


def wait_active(token: str, machine_id: int, timeout: float = 90) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = cmd_active(token)
        if info and int(info.get("id", -1)) == machine_id:
            if info.get("ip") and not info.get("isSpawning"):
                return info
            if info.get("ip"):
                return info
        time.sleep(2)
    return cmd_active(token)


def write_target(path: Path, active: dict | None, spawn: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = {
        "source": "hackthebox",
        "authorized": True,
        "subscription_note": "Dedicated/VIP lab API — subscription-authorized only",
        "spawn": spawn,
        "active": active,
        "ip": (active or {}).get("ip"),
        "name": (active or {}).get("name"),
        "id": (active or {}).get("id"),
        "vpn_server_id": (active or {}).get("vpn_server_id"),
        "lab_server": (active or {}).get("lab_server"),
        "reachability": "requires HTB VPN on runner host",
    }
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="HTB lab control (authorized)")
    ap.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("user")
    p_list = sub.add_parser("list")
    p_list.add_argument("--per-page", type=int, default=30)
    p_list.add_argument("--search", default="")
    sub.add_parser("active")
    p_spawn = sub.add_parser("spawn")
    p_spawn.add_argument("--id", type=int, default=None)
    p_spawn.add_argument("--name", default=None)
    p_spawn.add_argument("--wait", type=float, default=60)
    p_spawn.add_argument("--target-out", type=Path, default=None)
    p_reset = sub.add_parser("reset")
    p_reset.add_argument("--id", type=int, default=None)
    p_stop = sub.add_parser("stop")
    p_stop.add_argument("--id", type=int, default=None)
    sub.add_parser("status")
    p_wt = sub.add_parser("write-target")
    p_wt.add_argument("out", type=Path)

    args = ap.parse_args()
    token = load_token(args.token_file)

    if args.cmd == "user":
        info = cmd_user(token)
        print(json.dumps({"name": info.get("name"), "id": info.get("id"),
                          "isVip": info.get("isVip"), "isDedicatedVip": info.get("isDedicatedVip")}, indent=2))
        return 0

    if args.cmd == "list":
        rows = cmd_list(token, per_page=args.per_page, search=args.search)
        for r in rows:
            print(
                f"{r.get('id')}\t{r.get('name')}\t{r.get('os')}\t"
                f"{r.get('difficultyText')}\tfree={r.get('free')}"
            )
        print(f"# count={len(rows)}", file=sys.stderr)
        return 0

    if args.cmd == "active":
        info = cmd_active(token)
        print(json.dumps(info, indent=2))
        return 0 if info else 1

    if args.cmd == "spawn":
        mid = resolve_machine_id(token, args.id, args.name)
        spawn = cmd_spawn(token, mid)
        print(json.dumps({"machine_id": mid, "spawn": spawn}, indent=2))
        active = wait_active(token, mid, timeout=args.wait) if args.wait > 0 else cmd_active(token)
        if active:
            print(json.dumps({"active": {
                "id": active.get("id"),
                "name": active.get("name"),
                "ip": active.get("ip"),
                "isSpawning": active.get("isSpawning"),
                "lab_server": active.get("lab_server"),
                "vpn_server_id": active.get("vpn_server_id"),
            }}, indent=2))
        if args.target_out:
            write_target(args.target_out, active, spawn=spawn)
            print(f"wrote {args.target_out}", file=sys.stderr)
        body = spawn.get("body") if isinstance(spawn.get("body"), dict) else {}
        msg = str(body.get("message", "")).lower()
        ok = (
            spawn.get("http") in (200, 201)
            or body.get("success") is True
            or "already have an active" in msg
            or "deployed" in msg
        )
        # Prefer active machine presence as success for control plane
        if active and active.get("id"):
            ok = True
        return 0 if ok else 1

    if args.cmd == "reset":
        print(json.dumps(cmd_reset(token, args.id), indent=2))
        return 0

    if args.cmd == "stop":
        print(json.dumps(cmd_stop(token, args.id), indent=2))
        return 0

    if args.cmd == "status":
        u = cmd_user(token)
        a = cmd_active(token)
        print(json.dumps({
            "user": {"name": u.get("name"), "id": u.get("id"), "isDedicatedVip": u.get("isDedicatedVip")},
            "active": a,
        }, indent=2))
        return 0

    if args.cmd == "write-target":
        a = cmd_active(token)
        write_target(args.out, a)
        print(json.dumps({"wrote": str(args.out), "ip": (a or {}).get("ip"), "name": (a or {}).get("name")}, indent=2))
        return 0 if a else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
