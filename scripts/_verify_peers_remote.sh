#!/bin/bash
echo '=== REAL BINARY CHECK ==='
for t in massdns puredns shuffledns dnsx gobuster amass; do
  p=$(command -v "$t" || true)
  if [ -z "$p" ]; then
    echo "$t: MISSING"
    continue
  fi
  echo -n "$t: "
  file "$p" | head -1
done
echo "--- versions ---"
puredns -v 2>&1 | head -1
gobuster version 2>&1 | head -1
amass -version 2>&1 | head -1
dnsx -version 2>&1 | head -1
