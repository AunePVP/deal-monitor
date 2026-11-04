# Deal-Monitor – Hinweise für Claude

- Sprache: Deutsch, kurz. Nutzer liest ungern lange Texte und will keine Rückfragen.
- **„mach weiter“** heißt: `docs/HANDOFF.md` lesen (auf Branch `wip/quellen-ausbau`, falls `main` sie nicht enthält:
  `git fetch origin wip/quellen-ausbau && git checkout wip/quellen-ausbau`) und dort bei „Nächste Schritte“ fortfahren.
- Nur Python-Standardbibliothek. Kein E-Mail-Versand. Tests: `python3 -m unittest discover -s tests`, UI: `node tests/e2e/ui.mjs`.
- Push auf `main` baut automatisch das Unraid-Image `ghcr.io/aunepvp/deal-monitor:latest` → auf `main` nur getestete Stände.
- Git-Autor: `AunePVP <aunepvp4@gmail.com>`.
