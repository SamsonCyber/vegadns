#!/usr/bin/env bash
# Install REAL peer tools for vegadns unbiased bench (Linux/Kali/WSL).
# massdns is C (blechschmidt); puredns/shuffledns wrap it; dnsx/gobuster/amass from Go.
set -euo pipefail

echo "[install] massdns (real C binary from package or build)"
if ! command -v massdns >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y massdns || true
  fi
fi
if ! command -v massdns >/dev/null 2>&1; then
  tmp=$(mktemp -d)
  git clone --depth 1 https://github.com/blechschmidt/massdns.git "$tmp/massdns"
  make -C "$tmp/massdns" nolinux || make -C "$tmp/massdns"
  sudo install -m 0755 "$tmp/massdns/bin/massdns" /usr/local/bin/massdns
fi
file "$(command -v massdns)"
massdns 2>&1 | head -2 || true

echo "[install] Go-based peers"
export PATH="${PATH}:${HOME}/go/bin:/usr/local/go/bin"
if ! command -v go >/dev/null 2>&1; then
  echo "go missing — install go to get dnsx/puredns/shuffledns/gobuster/amass" >&2
else
  go install github.com/projectdiscovery/dnsx/cmd/dnsx@latest
  go install github.com/d3mondev/puredns/v2@latest
  go install github.com/projectdiscovery/shuffledns/cmd/shuffledns@latest
  go install github.com/OJ/gobuster/v3@latest
  go install github.com/owasp-amass/amass/v4/...@master || true
fi

echo "[verify]"
for t in massdns dnsx puredns shuffledns gobuster amass; do
  if command -v "$t" >/dev/null 2>&1; then
    echo "OK  $t -> $(command -v $t)"
  else
    echo "MISS $t"
  fi
done
