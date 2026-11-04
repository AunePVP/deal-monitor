#!/usr/bin/env bash
# Deal-Monitor ohne Docker starten:  ./start.sh   (weitere Optionen: ./start.sh --help)
# Öffnet danach http://localhost:8080 im Browser. Beenden mit Strg+C.
set -euo pipefail
cd "$(dirname "$0")"

# --- .env laden (optional); bereits gesetzte Umgebungsvariablen gewinnen --------
if [[ -f .env ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    key="${BASH_REMATCH[2]}"
    val="${BASH_REMATCH[3]}"
    if [[ "$val" =~ ^\"(.*)\"$ || "$val" =~ ^\'(.*)\'$ ]]; then
      val="${BASH_REMATCH[1]}"
    fi
    if [[ -z "${!key+x}" ]]; then export "$key=$val"; fi
  done < .env
fi

# --- Python >= 3.11 suchen ---------------------------------------------------
PY=""
for c in python3 python3.13 python3.12 python3.11 python; do
  if command -v "$c" >/dev/null 2>&1 \
     && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$c"
    break
  fi
done

if [[ -z "$PY" ]]; then
  echo "Python 3.11 oder neuer wurde nicht gefunden." >&2
  if command -v docker >/dev/null 2>&1 && [[ "${1:-}" != "--demo" ]]; then
    echo "Docker ist vorhanden – starte stattdessen mit Docker ..." >&2
    docker compose up -d --build
    echo "Deal-Monitor läuft auf http://localhost:8080 (stoppen: docker compose down)"
    exit 0
  fi
  echo "Bitte Python installieren (https://www.python.org/downloads/) oder Docker nutzen:" >&2
  echo "  docker compose up -d" >&2
  exit 1
fi

# --- Port für den Browser-Link ermitteln ------------------------------------
PORT="${DEALMONITOR_PORT:-8080}"
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in
    --port) PORT="${args[$((i + 1))]:-$PORT}" ;;
    --port=*) PORT="${args[$i]#--port=}" ;;
  esac
done
URL="http://localhost:${PORT}"

# --- Browser öffnen, sobald der Server antwortet (im Hintergrund) -----------
open_browser() {
  "$PY" - "$URL" <<'PYEOF' || return 0
import sys, time, urllib.error, urllib.request
url = sys.argv[1] + "/api/status"
for _ in range(60):
    try:
        urllib.request.urlopen(url, timeout=2)
        sys.exit(0)
    except urllib.error.HTTPError:
        sys.exit(0)          # z. B. 401 bei Passwortschutz: Server läuft
    except Exception:
        time.sleep(0.5)
sys.exit(1)
PYEOF
  if command -v xdg-open >/dev/null 2>&1 && [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    xdg-open "$URL" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1 && [[ "$(uname -s)" == "Darwin" ]]; then
    open "$URL" >/dev/null 2>&1 || true
  elif command -v wslview >/dev/null 2>&1; then
    wslview "$URL" >/dev/null 2>&1 || true
  fi
}

if [[ "${NO_BROWSER:-}" != "1" ]]; then
  open_browser &
fi

echo "Starte Deal-Monitor mit $("$PY" --version) – Beenden mit Strg+C"
exec "$PY" -m dealmonitor serve "$@"
