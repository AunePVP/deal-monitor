#!/usr/bin/env bash
# Usage: fetch.sh <url> [name]
# Fetches once with browser UA, saves body to raw/<name>.body, prints status + content-type + size
set -u
URL="$1"
NAME="${2:-$(echo "$URL" | sed 's#https\?://##; s#[^A-Za-z0-9._-]#_#g' | cut -c1-120)}"
RAW=/tmp/claude-0/-home-user/8771aaf7-3b29-551f-a287-c88013dcf7a7/scratchpad/research/dach-deal-seiten/raw
mkdir -p "$RAW"
OUT="$RAW/$NAME.body"
HDR="$RAW/$NAME.hdr"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
code=$(curl -sS -L --max-time 25 --max-redirs 5 -A "$UA" -H "Accept: application/rss+xml, application/atom+xml, application/xml, text/xml, application/json, text/html;q=0.8, */*;q=0.5" -H "Accept-Language: de-DE,de;q=0.9,en;q=0.7" -D "$HDR" -o "$OUT" -w "%{http_code} %{url_effective} %{content_type} %{size_download}" "$URL" 2>&1)
echo "$NAME | $code"
