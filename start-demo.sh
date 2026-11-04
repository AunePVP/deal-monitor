#!/usr/bin/env bash
# Demo mit Beispieldaten ansehen (keine echten Preise, kein Token nötig):  ./start-demo.sh
# Läuft auf http://localhost:8081, damit es nicht mit dem echten Monitor (8080) kollidiert.
# Alles liegt in einem temporären Ordner – nach dem Beenden (Strg+C) ist die Demo weg.
set -euo pipefail
cd "$(dirname "$0")"
export DEALMONITOR_PORT="${DEMO_PORT:-8081}"
# Ohne Python, aber mit Docker: Demo im Container starten
if ! command -v python3 >/dev/null 2>&1 \
   || ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  if command -v docker >/dev/null 2>&1; then
    echo "Starte Demo per Docker auf http://localhost:${DEALMONITOR_PORT} – Beenden mit Strg+C"
    exec docker compose run --rm --build -p "${DEALMONITOR_PORT}:8080" deal-monitor \
      python -m dealmonitor serve --demo
  fi
fi
exec ./start.sh --demo --port "$DEALMONITOR_PORT" "$@"
