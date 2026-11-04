# Deal-Monitor – Web-Oberfläche mit Zeitplan (nur Python-Standardbibliothek)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEALMONITOR_DATA=/data \
    DEALMONITOR_HOST=0.0.0.0 \
    DEALMONITOR_PORT=8080 \
    TZ=Europe/Berlin \
    HOME=/app

WORKDIR /app

# Die App läuft nie als root: docker-entrypoint.sh wechselt beim Start auf den
# Besitzer von /data (bzw. auf den Benutzer "app", UID 1000).
# tzdata: Zeitstempel ("Letzte Prüfung", "gefunden am") in deutscher Ortszeit statt UTC
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 app \
 && useradd --uid 1000 --gid app --no-create-home --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /data \
 && chown app:app /data

COPY dealmonitor ./dealmonitor
COPY config ./config
# Beispieldaten für die Demo (serve --demo)
COPY tests/fixtures ./tests/fixtures
COPY pyproject.toml README.md ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# Windows-Zeilenenden (CRLF) entfernen, falls das Repo unter Windows ausgecheckt wurde
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
 && chmod 755 /usr/local/bin/docker-entrypoint.sh

VOLUME /data
EXPOSE 8080

# Gesund = /api/status antwortet (mit Passwort, falls DEALMONITOR_PASSWORD gesetzt ist)
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import base64,os,urllib.request as u; p=os.environ.get('DEALMONITOR_PASSWORD'); r=u.Request('http://127.0.0.1:%s/api/status' % os.environ.get('DEALMONITOR_PORT','8080')); p and r.add_header('Authorization','Basic '+base64.b64encode(('monitor:'+p).encode()).decode()); u.urlopen(r, timeout=5).read()"]

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "dealmonitor", "serve"]
