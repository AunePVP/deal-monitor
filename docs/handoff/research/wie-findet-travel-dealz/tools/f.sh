#!/bin/bash
# usage: f.sh <name> <url> [extra curl args...]  -> dl2/<name>.body/.hdr
D=/home/user/deal-monitor/docs/handoff/research/wie-findet-travel-dealz/dl2
NAME="$1"; URL="$2"; shift 2
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
curl -sS -L --max-time 40 --compressed -A "$UA" \
  -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7" \
  -H "Accept-Language: de-DE,de;q=0.9,en;q=0.5" \
  -o "$D/$NAME.body" -D "$D/$NAME.hdr" \
  -w "name=$NAME status=%{http_code} final=%{url_effective} size=%{size_download} ct=%{content_type} t=%{time_total}\n" \
  "$@" "$URL" 2>&1 || echo "name=$NAME CURL_ERROR"
sleep 3
