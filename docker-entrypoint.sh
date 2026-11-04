#!/bin/sh
# Startet den Deal-Monitor als normalen Benutzer (nicht root).
# Läuft der Container als root, wird der Datenordner (/data = ./data auf dem Host)
# so vorbereitet, dass er beschreibbar ist:
#   - gehört er einem normalen Benutzer, läuft die App mit dessen UID/GID
#     (dann bleiben die Dateien auf dem Host "deine"),
#   - gehört er root (z. B. von Docker neu angelegt), bekommt ihn der App-Benutzer 1000,
#   - PUID/PGID gesetzt (Unraid: 99/100), dann genau diese IDs.
set -e
DATA="${DEALMONITOR_DATA:-/data}"

if [ "$(id -u)" != "0" ]; then
  exec "$@"
fi

mkdir -p "$DATA"
uid="$(stat -c %u "$DATA")"
gid="$(stat -c %g "$DATA")"
# Unraid & Co.: PUID/PGID gewinnen (Unraid-Standard: 99/100 = nobody/users)
if [ -n "$PUID" ]; then
  uid="$PUID"
  gid="${PGID:-$PUID}"
elif [ "$uid" = "0" ]; then
  uid=1000
  gid=1000
fi
# Alles in /data muss dem App-Benutzer gehören (z. B. nach "sudo ./start.sh" oder
# "docker compose exec" als root angelegte Dateien) – sonst ist die Datenbank schreibgeschützt.
find "$DATA" \( ! -user "$uid" -o ! -group "$gid" \) -exec chown "$uid:$gid" {} + 2>/dev/null || \
  echo "Warnung: Dateirechte in $DATA konnten nicht angepasst werden" >&2
# Datenbank enthält Tokens: nur für den App-Benutzer lesbar
chmod 700 "$DATA" 2>/dev/null || true

exec python -c 'import os, sys
uid, gid = int(sys.argv[1]), int(sys.argv[2])
os.setgroups([])
os.setgid(gid)
os.setuid(uid)
os.execvp(sys.argv[3], sys.argv[3:])' "$uid" "$gid" "$@"
