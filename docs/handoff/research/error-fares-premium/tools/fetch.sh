#!/bin/bash
# usage: fetch.sh <name> <url>  -> saves dl/<name>.body, prints status/ctype/size
name="$1"; url="$2"
D=/tmp/claude-0/-home-user/8771aaf7-3b29-551f-a287-c88013dcf7a7/scratchpad/research/error-fares-premium/dl
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
out=$(curl -sS -L --max-time 25 -A "$UA" -H 'Accept: application/rss+xml, application/atom+xml, application/json, text/html;q=0.9, */*;q=0.8' -H 'Accept-Language: de-DE,de;q=0.9,en;q=0.8' -o "$D/$name.body" -w '%{http_code} %{content_type} %{size_download} %{url_effective}' "$url" 2>&1)
echo "$name | $out"
