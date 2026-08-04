#!/usr/bin/env python3
"""Generate Subdomain Scanner Gym fixtures (realistic + obscure + traps).

Private lab only. Base: gym.test

Outputs under fixtures/gym/:
  zone_gym.json, known_true_gym.txt, wordlist_gym.txt,
  obscure_true.txt, realistic_true.txt, fixture_meta.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "gym"
BASE = "gym.test"

# Common / realistic recon labels (high frequency in wordlists).
REALISTIC = [
    "www", "mail", "api", "vpn", "cdn", "admin", "git", "ci", "staging", "dev",
    "portal", "auth", "db", "ns1", "ns2", "ftp", "ssh", "grafana", "prometheus",
    "vault", "app", "web", "mx", "smtp", "imap", "pop", "ldap", "sso", "oauth",
    "login", "dashboard", "status", "health", "monitor", "jenkins", "gitlab",
    "jira", "confluence", "wiki", "docs", "blog", "shop", "store", "cdn1",
    "static", "assets", "img", "media", "video", "stream", "ws", "socket",
    "gateway", "edge", "proxy", "cache", "redis", "mongo", "mysql", "postgres",
    "elastic", "kibana", "log", "logs", "siem", "splunk", "zabbix", "nagios",
    "backup", "bak", "old", "test", "qa", "uat", "preprod", "prod", "internal",
    "intranet", "extranet", "corp", "office", "remote", "rdp", "citrix",
    "owa", "autodiscover", "lyncdiscover", "sip", "pbx", "voip", "meet",
    "zoom", "teams", "slack", "chat", "support", "help", "helpdesk", "ticket",
    "billing", "pay", "payments", "checkout", "cart", "crm", "erp", "hr",
    "people", "careers", "jobs", "partners", "vendor", "suppliers", "api-v1",
    "api-v2", "api2", "v1", "v2", "beta", "alpha", "demo", "sandbox", "lab",
    "research", "ml", "ai", "data", "analytics", "bi", "warehouse", "etl",
    "kafka", "rabbit", "queue", "worker", "batch", "cron", "scheduler",
    "k8s", "kube", "kubernetes", "docker", "registry", "harbor", "nexus",
    "artifactory", "npm", "pypi", "mirrors", "repo", "source", "code",
    "build", "deploy", "cd", "argo", "spinnaker", "terraform", "ansible",
    "consul", "etcd", "nomad", "traefik", "nginx", "haproxy", "envoy",
    "istio", "linkerd", "service", "svc", "backend", "frontend", "mobile",
    "ios", "android", "m", "amp", "graphql", "grpc", "rest", "soap", "wsdl",
    "swagger", "openapi", "developer", "developers", "devportal", "id",
    "identity", "iam", "keycloak", "okta", "auth0", "cognito", "saml",
    "ldap-prod", "ad", "dc1", "dc2", "fs", "files", "nas", "san", "storage",
    "s3", "blob", "minio", "ceph", "gluster", "nfs", "smb", "cifs",
]

# Obscure / low-frequency patterns tools often miss without deep wordlists.
# These still resolve in the mock zone (not "invisible DNS").
OBSCURE_PATTERNS = [
    # multi-label / env-service
    "s3-us-west-2-internal",
    "ecs-cluster-prod-01",
    "eks-nodegroup-a1b2",
    "gke-us-central1-c",
    "aks-agentpool-0",
    "cf-pages-preview-7f3a",
    "vercel-alias-edge-02",
    "netlify-deploy-preview",
    "cloudfront-d111111abcdef8",
    "elb-internal-a1b2c3d4",
    "nlb-priv-0a1b2c3d",
    "alb-pub-e5f6a7b8",
    "rds-ro-prod-snapshot",
    "aurora-cluster-writer",
    "elasticache-redis-001",
    "opensearch-domain-logs",
    "msk-broker-1",
    "kinesis-firehose-stream",
    "lambda-edge-us-east-1",
    "stepfn-state-machine",
    # hex / uuid-ish
    "a1b2c3d4e5f6",
    "f00dbabe-cafe",
    "deadbeef01",
    "c0ffee42",
    "node-7f3a9c1e",
    "pod-0a1b2c3d4e5f",
    # k8s / service mesh style
    "svc-auth-prod.namespace",
    "ing-default-nginx",
    "cm-app-config-v3",
    "sts-redis-0",
    "ds-node-exporter",
    "cj-backup-nightly",
    # reverse-proxy / internal mesh
    "int-gw-01.corp",
    "dmz-proxy-west",
    "bastion-jump-03",
    "ztna-connector-2",
    "tailscale-exit-node",
    "wireguard-hub",
    # legacy / weird
    "old-www2",
    "www-old-2019",
    "intranet-legacy",
    "lotus-notes-mx",
    "as400-prod",
    "mainframe-gateway",
    "pbx-asterisk-01",
    "scada-hmi-lab",
    "iot-broker-mqtt",
    "coap-gateway",
    # deep multi-label under gym
    "api.internal.v2",
    "auth.sso.prod",
    "cdn.assets.static",
    "db.replica.eu-west",
    "mq.cluster.node1",
    "git.internal.mirror",
    "ci.runner.gpu",
    "ml.training.gpu01",
    "data.lake.raw",
    "sec.vault.unseal",
]


def obscure_labels(n: int) -> list[str]:
    """Build n obscure labels (patterns + generated low-frequency forms)."""
    out: list[str] = []
    for p in OBSCURE_PATTERNS:
        if p not in out:
            out.append(p)
    i = 0
    while len(out) < n:
        # low-frequency synthetic: hex + env + region crumbs
        lab = f"x{i:04x}-svc-r{(i % 7) + 1}-{(i * 17) % 997:03d}"
        if lab not in out:
            out.append(lab)
        i += 1
        if len(out) < n:
            lab2 = f"nsvc{(i % 50):02d}.az{(i % 3) + 1}.internal"
            if lab2 not in out:
                out.append(lab2)
            i += 1
    return out[:n]


def realistic_labels(n: int) -> list[str]:
    out: list[str] = []
    for p in REALISTIC:
        if p not in out:
            out.append(p)
    i = 0
    while len(out) < n:
        lab = f"host{i:04d}"
        if lab not in out:
            out.append(lab)
        i += 1
    return out[:n]


def is_obscure_style(label: str) -> bool:
    """Heuristic used by tests: multi-label, hex crumbs, or known obscure patterns."""
    if label in OBSCURE_PATTERNS:
        return True
    if "." in label:
        return True
    if re.search(r"[0-9a-f]{6,}", label):
        return True
    if re.match(r"^x[0-9a-f]{4}-svc-", label):
        return True
    if "internal" in label or "namespace" in label or "unseal" in label:
        return True
    return False


def generate(
    known: int = 800,
    wordlist: int = 30000,
    obscure_fraction: float = 0.35,
    wild_fillers: int = 300,
    out_dir: Path | None = None,
) -> dict:
    """Generate gym fixtures. Returns meta summary (also written to disk)."""
    out = out_dir or OUT
    out.mkdir(parents=True, exist_ok=True)

    n_obscure = max(50, int(known * obscure_fraction))
    n_realistic = known - n_obscure
    if n_realistic < 40:
        n_realistic = 40
        n_obscure = known - n_realistic

    real = realistic_labels(n_realistic)
    obsc = obscure_labels(n_obscure)

    # Merge known-true: realistic first, then obscure (dedupe)
    known_labels: list[str] = []
    seen: set[str] = set()
    for lab in real + obsc:
        if lab not in seen:
            seen.add(lab)
            known_labels.append(lab)
    known_labels = known_labels[:known]

    records: dict[str, list[str]] = {}
    known_fqdns: list[str] = []
    realistic_fqdns: list[str] = []
    obscure_fqdns: list[str] = []

    for n, lab in enumerate(known_labels):
        fqdn = f"{lab}.{BASE}"
        a = 10 + (n % 200)
        b = 30 + (n // 200) % 200
        records[fqdn] = [f"10.210.{b}.{a}"]
        known_fqdns.append(fqdn)
        if is_obscure_style(lab):
            obscure_fqdns.append(fqdn)
        else:
            realistic_fqdns.append(fqdn)

    wildcards = {
        f"wild.{BASE}": ["9.9.9.9"],
        f"cdn-edge.{BASE}": ["8.8.8.8"],
        f"catch-all.{BASE}": ["7.7.7.7"],
    }

    zone = {
        "base": BASE,
        "records": records,
        "wildcards": wildcards,
        "meta": {
            "scope": "private lab mock only — Subdomain Scanner Gym",
            "known_true_count": len(known_fqdns),
            "obscure_count": len(obscure_fqdns),
            "realistic_count": len(realistic_fqdns),
            "not_public_internet": True,
        },
    }
    (out / "zone_gym.json").write_text(json.dumps(zone, indent=2) + "\n", encoding="utf-8")
    (out / "known_true_gym.txt").write_text("\n".join(known_fqdns) + "\n", encoding="utf-8")
    (out / "obscure_true.txt").write_text("\n".join(obscure_fqdns) + "\n", encoding="utf-8")
    (out / "realistic_true.txt").write_text("\n".join(realistic_fqdns) + "\n", encoding="utf-8")

    # Wordlist: all known + wildcard bait + junk
    wl: list[str] = list(known_labels)
    for j in range(wild_fillers):
        wl.append(f"w{j:04d}.wild")
        wl.append(f"e{j:04d}.cdn-edge")
        wl.append(f"c{j:04d}.catch-all")
    g = 0
    while len(wl) < wordlist:
        wl.append(f"junk{g:05d}")
        g += 1
        if len(wl) < wordlist:
            wl.append(f"noise-{g:05d}")
            g += 1
    # de-dupe preserve order
    seen_w: set[str] = set()
    uniq: list[str] = []
    for x in wl[:wordlist]:
        if x not in seen_w:
            seen_w.add(x)
            uniq.append(x)
    # ensure every known label is present even if cap was tight
    for lab in known_labels:
        if lab not in seen_w:
            uniq.insert(0, lab)
            seen_w.add(lab)
    uniq = uniq[: max(wordlist, len(known_labels))]
    (out / "wordlist_gym.txt").write_text("\n".join(uniq) + "\n", encoding="utf-8")

    summary = {
        "base": BASE,
        "known_true": len(known_fqdns),
        "obscure_true": len(obscure_fqdns),
        "realistic_true": len(realistic_fqdns),
        "wordlist": len(uniq),
        "records": len(records),
        "wild_parents": list(wildcards.keys()),
        "scope": "127.0.0.1 private lab only",
        "paths": {
            "zone": str(out / "zone_gym.json"),
            "known_true": str(out / "known_true_gym.txt"),
            "obscure_true": str(out / "obscure_true.txt"),
            "realistic_true": str(out / "realistic_true.txt"),
            "wordlist": str(out / "wordlist_gym.txt"),
        },
    }
    (out / "fixture_meta.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate Subdomain Scanner Gym fixtures")
    ap.add_argument("--known", type=int, default=800)
    ap.add_argument("--wordlist", type=int, default=30000)
    ap.add_argument("--obscure-fraction", type=float, default=0.35)
    ap.add_argument("--wild-fillers", type=int, default=300)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    summary = generate(
        known=args.known,
        wordlist=args.wordlist,
        obscure_fraction=args.obscure_fraction,
        wild_fillers=args.wild_fillers,
        out_dir=args.out,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
