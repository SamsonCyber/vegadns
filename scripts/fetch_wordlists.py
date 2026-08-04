#!/usr/bin/env python3
"""Fetch optional large DNS wordlists and rebuild vegadns preset tiers.

Built-in presets (tiny/small/medium/alter) live under wordlists/ and are
embedded at compile time. This script refreshes snapshots and optional cache
lists from public sources (SecLists, altdns, trickest when available).

Usage:
  python scripts/fetch_wordlists.py --rebuild-tiers
  python scripts/fetch_wordlists.py --cache-extra
"""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WL = ROOT / "wordlists"
CACHE = WL / "cache"

URLS = {
    "seclists_top5k.txt": (
        "https://raw.githubusercontent.com/danielmiessler/SecLists/master/"
        "Discovery/DNS/subdomains-top1million-5000.txt"
    ),
    "seclists_top20k.txt": (
        "https://raw.githubusercontent.com/danielmiessler/SecLists/master/"
        "Discovery/DNS/subdomains-top1million-20000.txt"
    ),
    "n0kovo_tiny.txt": (
        "https://raw.githubusercontent.com/n0kovo/n0kovo_subdomains/main/"
        "n0kovo_subdomains_tiny.txt"
    ),
    "alter_words.txt": (
        "https://raw.githubusercontent.com/infosec-au/altdns/master/words.txt"
    ),
}

# Optional extra lists (not embedded; for -w merge in the field).
CACHE_URLS = {
    "trickest_dns.txt": (
        "https://raw.githubusercontent.com/trickest/wordlists/main/inventory/"
        "domains/domain-wordlist.txt"
    ),
}

BOOST = """
api,dev,staging,prod,production,uat,qa,stage,test,www,mail,cdn,static,assets,
admin,portal,app,apps,beta,alpha,edge,gateway,auth,sso,oauth,login,vpn,git,
gitlab,ci,jenkins,grafana,prometheus,kibana,elastic,k8s,kubernetes,docker,
registry,harbor,minio,s3,storage,db,mysql,postgres,redis,mongo,cache,internal,
intranet,corp,remote,status,health,metrics,logs,trace,sentry,datadog,newrelic,
billing,pay,payments,shop,store,checkout,v1,v2,v3,graphql,ws,websocket,mqtt,
iot,ml,ai,model,inference,gpu,jupyter,notebook,airflow,spark,kafka,mq,queue,
worker,jobs,cron,scheduler,webhook,hooks,callback,preview,preprod,sandbox,demo,
lab,research,partner,partners,vendor,vendors,customer,customers,support,help,
docs,wiki,confluence,jira,slack,teams,zoom,meet,calendar,mailgun,sendgrid,ses,
smtp,mx,ns,dns,ns1,ns2,ftp,sftp,ssh,bastion,jump,mgmt,management,ops,devops,
sre,infra,platform,cloud,aws,azure,gcp,cf,cloudflare,akamai,fastly,origin,
backend,frontend,web,m,mobile,ios,android,amp,blog,news,media,img,images,video,
stream,cdn1,cdn2,lb,loadbalancer,proxy,waf,firewall,ids,siem,splunk,elk,
logstash,graylog,zabbix,nagios,monitor,monitoring,uptime,statuspage,backoffice,
cms,wp,wordpress,drupal,magento,shopify,erp,crm,hr,finance,legal,hris,okta,
auth0,cognito,keycloak,ldap,ad,adfs,radius,ntp,time,sync,backup,backups,archive,
old,legacy,new,next,www2,www3,staging2,dev1,dev2,test1,test2,api1,api2,secure,
private,public,external,int,ext,na,eu,us,uk,ap,apac,emea,latam
""".replace("\n", "").replace(" ", "")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "vegadns-fetch/0.1"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf-8", errors="replace")


def clean_lines(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for ln in text.splitlines():
        s = ln.strip().lower()
        if not s or s.startswith("#"):
            continue
        if not all(c.isalnum() or c in "-_." for c in s):
            continue
        if len(s) > 63:
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {path} ({len(lines)} lines)")


def merge_unique(*lists: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for x in lst:
            if x not in seen:
                seen.add(x)
                out.append(x)
    return out


def rebuild_tiers() -> None:
    WL.mkdir(parents=True, exist_ok=True)
    for name, url in URLS.items():
        print(f"fetch {name}")
        write_lines(WL / name, clean_lines(fetch(url)))

    top5 = clean_lines((WL / "seclists_top5k.txt").read_text(encoding="utf-8"))
    top20 = clean_lines((WL / "seclists_top20k.txt").read_text(encoding="utf-8"))
    n0 = clean_lines((WL / "n0kovo_tiny.txt").read_text(encoding="utf-8"))
    boost = [x for x in BOOST.split(",") if x]
    write_lines(WL / "dns_tiny.txt", merge_unique(boost, top5[:80]))
    write_lines(WL / "dns_small.txt", merge_unique(boost, top5[:500]))
    write_lines(WL / "dns_medium.txt", merge_unique(boost, top5))
    write_lines(WL / "dns_large.txt", merge_unique(boost, top20))
    write_lines(WL / "dns_final.txt", merge_unique(boost, top20, n0))
    print("tiers rebuilt (tiny→final); recompile vegadns to re-embed")


def cache_extra() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    for name, url in CACHE_URLS.items():
        print(f"fetch cache {name}")
        try:
            write_lines(CACHE / name, clean_lines(fetch(url)))
        except Exception as e:
            print(f"skip {name}: {e}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rebuild-tiers", action="store_true")
    ap.add_argument("--cache-extra", action="store_true")
    args = ap.parse_args()
    if not args.rebuild_tiers and not args.cache_extra:
        ap.error("pass --rebuild-tiers and/or --cache-extra")
    if args.rebuild_tiers:
        rebuild_tiers()
    if args.cache_extra:
        cache_extra()


if __name__ == "__main__":
    main()
