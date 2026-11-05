#!/bin/bash
# usage: f.sh <name> <url> [extra curl args]  -> live/<name>.body ; log in live/_log.tsv ; sleeps 2.5s before each fetch
B=/home/user/deal-monitor/docs/handoff/research/error-fares-premium
name="$1"; url="$2"; shift 2
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
sleep 2.5
out=$(curl -sS -L --max-time 25 -A "$UA" -H 'Accept: application/rss+xml, application/atom+xml, application/json, text/html;q=0.9, */*;q=0.8' -H 'Accept-Language: de-DE,de;q=0.9,en;q=0.8' "$@" -o "$B/live/$name.body" -w '%{http_code}\t%{content_type}\t%{size_download}\t%{url_effective}' "$url" 2>&1)
printf '%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$name" "$url" "$out" >> "$B/live/_log.tsv"
echo "$name | $out"
