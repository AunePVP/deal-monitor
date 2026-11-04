"""Kommandozeile: ``python -m dealmonitor <befehl>``."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import appsettings, notify, regions, report
from .appsettings import Context
from .config import normalize_alert
from .principles import CABIN_LABEL, euro
from .scheduler import execute

ROOT = appsettings.ROOT
DEFAULT_CONFIG = appsettings.DEFAULT_CONFIG


def context(args) -> Context:
    """Datenverzeichnis + Start-Konfiguration; beim ersten Mal aus der TOML seeden."""
    ctx = Context(getattr(args, "data", None), args.config)
    ctx.seed()
    return ctx


def cmd_run(args) -> int:
    ctx = context(args)
    names = [n.strip() for n in args.sources.split(",") if n.strip()] if args.sources else None
    notifiers = [notify.Console()] if args.dry_run else None
    total = {"offers": 0, "deals": 0, "notified": 0, "expired": 0, "errors": []}
    for alert_id in args.alert or [None]:
        row = execute(ctx, alert_id, "cli", source_names=names, notifiers=notifiers)
        for k in total:
            total[k] += row[k]
    with ctx.store() as store:
        path = report.write(ctx.build_config(store), store)
    print(f"\n{total['offers']} Angebote geprüft · {total['deals']} Deals · "
          f"{total['notified']} gemeldet · {total['expired']} abgelaufen · Bericht: {path}")
    for e in total["errors"]:
        print("WARNUNG:", e, file=sys.stderr)
    # Fehler nur dann als Exit-Code melden, wenn gar nichts geprüft werden konnte
    return 1 if total["errors"] and total["offers"] == 0 else 0


def cmd_alerts(args) -> int:
    ctx = context(args)
    with ctx.store() as store:
        cfg = ctx.build_config(store)
    if not cfg.alerts:
        print("Keine Alarme angelegt.")
    for a in cfg.alerts:
        limit = f"≤ {euro(a.max_price)}" if a.max_price else f"≥{a.min_discount:.0%} unter Normalpreis"
        print(f"{'●' if a.enabled else '○'} {a.id:<16} {a.name}")
        print(f"    {len(a.origins)} Abflughäfen × {len(a.destinations)} Ziele = {len(a.pairs)} Strecken · "
              f"{CABIN_LABEL[a.cabin]} · {'Hin+Rück' if a.roundtrip else 'one-way'} · {limit}")
        print(f"    von: {', '.join(a.origins)}\n    nach: {', '.join(a.destinations)}")
    return 0


def _insert(store, data: dict) -> None:
    raw = normalize_alert({k: v for k, v in data.items() if v is not None})
    if store.has_alert(raw["id"]):
        raise ValueError(f"Alarm-ID '{raw['id']}' existiert bereits")
    store.insert_alert(raw)


def cmd_add(args) -> int:
    data = {
        "id": args.id,
        "name": args.name or f"{regions.name(args.origin[0].lstrip('@'))} → "
                             f"{regions.name(args.destination[0].lstrip('@'))}",
        "origins": args.origin,
        "destinations": args.destination,
        "trip": "oneway" if args.oneway else "roundtrip",
        "cabin": args.cabin,
        "max_price": args.max_price,
        "baseline_price": args.baseline,
        "max_stops": args.max_stops,
        "min_nights": args.min_nights,
        "max_nights": args.max_nights,
        "feed_keywords": [args.keywords] if args.keywords else None,
    }
    ctx = context(args)
    with ctx.store() as store:
        if args.both_directions:
            back = dict(data, id=args.id + "-retour", origins=args.destination,
                        destinations=args.origin,
                        name=(args.name + " (Gegenrichtung)") if args.name else
                        f"{regions.name(args.destination[0].lstrip('@'))} → "
                        f"{regions.name(args.origin[0].lstrip('@'))}")
            normalize_alert({k: v for k, v in back.items() if v is not None})  # vorab prüfen
            if store.has_alert(back["id"]):
                raise ValueError(f"Alarm-ID '{back['id']}' existiert bereits")
            _insert(store, data)
            _insert(store, back)
            print(f"Alarme '{data['id']}' und '{back['id']}' hinzugefügt.")
        else:
            _insert(store, data)
            print(f"Alarm '{data['id']}' hinzugefügt.")
    return 0


def cmd_deals(args) -> int:
    ctx = context(args)
    with ctx.store() as store:
        rows = store.deals(status=None if args.all else "active", alert_id=args.alert)
    if not rows:
        print("Keine Deals gespeichert.")
    for d in rows:
        flag = "ABGELAUFEN " if d["status"] == "expired" else ""
        when = f" · {d['departure']}" + (f"–{d['return_date']}" if d.get("return_date") else "") \
            if d.get("departure") else ""
        print(f"{flag}[{d['rating']}] {euro(d['price']):>8}  {d['headline']}{when}")
        if d.get("link"):
            print(f"{'':>12}{d['link']}")
    return 0


def cmd_report(args) -> int:
    ctx = context(args)
    with ctx.store() as store:
        print(report.write(ctx.build_config(store), store, args.output))
    return 0


def cmd_test_notify(args) -> int:
    ctx = context(args)
    with ctx.store() as store:
        notifiers = ctx.notifiers(store)
    failed = 0
    for ch in notify.send_test(notifiers):
        if ch["ok"]:
            print(f"✓ {ch['name']}")
        else:
            failed += 1
            print(f"✗ {ch['name']}: {ch['error']}", file=sys.stderr)
    return 1 if failed else 0


def cmd_import_config(args) -> int:
    ctx = Context(args.data, args.config)
    with ctx.store() as store:
        added, updated = appsettings.import_config(store, args.config, replace=args.replace)
        store.kv_set("seeded", True)
    print(f"{added} Alarm(e) neu importiert, {updated} überschrieben (aus {args.config}).")
    return 0


def cmd_serve(args) -> int:
    from . import web
    logging.getLogger().setLevel(logging.DEBUG if args.verbose else logging.INFO)
    if args.demo:
        from . import demo
        ctx = demo.server_context(args.config)
        prepare = demo.prepare_server
    else:
        ctx = Context(args.data, args.config)
        prepare = None
    return web.serve(ctx, args.host, args.port, scheduler_enabled=not args.no_scheduler,
                     prepare=prepare)


def cmd_demo(args) -> int:
    from .demo import demo
    print(demo(args.config))
    return 0


def _env_port() -> int:
    try:
        return int(os.environ.get("DEALMONITOR_PORT", "8080"))
    except ValueError:
        return 8080


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dealmonitor", description="Flug-Deal-Monitor mit Deal-Alarmen")
    p.add_argument("-c", "--config", default=str(DEFAULT_CONFIG),
                   help="Start-Konfiguration (alerts.toml), wird beim ersten Start importiert")
    p.add_argument("--data", default=None,
                   help="Datenverzeichnis (Standard: $DEALMONITOR_DATA oder ./data)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Alle Alarme prüfen und Deals melden")
    r.add_argument("--sources", default=None,
                   help="Kommagetrennt: travelpayouts,feed (Standard: aus den Einstellungen)")
    r.add_argument("--alert", action="append", help="Nur diesen Alarm (mehrfach möglich)")
    r.add_argument("--dry-run", action="store_true", help="Nur Konsole, keine Benachrichtigungen")
    r.set_defaults(fn=cmd_run)

    sub.add_parser("alerts", help="Gespeicherte Alarme anzeigen").set_defaults(fn=cmd_alerts)

    a = sub.add_parser("add", help="Neuen Deal-Alarm anlegen")
    a.add_argument("--id", required=True)
    a.add_argument("--name")
    a.add_argument("--from", dest="origin", nargs="+", required=True,
                   help="IATA-Codes oder Regionen, z. B. ZRH oder @DE")
    a.add_argument("--to", dest="destination", nargs="+", required=True)
    a.add_argument("--max-price", type=float)
    a.add_argument("--baseline", type=float, help="Normalpreis (für Rabattberechnung)")
    a.add_argument("--cabin", default="economy",
                   choices=["economy", "premium", "business", "first"])
    a.add_argument("--oneway", action="store_true")
    a.add_argument("--max-stops", type=int, default=1)
    a.add_argument("--min-nights", type=int)
    a.add_argument("--max-nights", type=int)
    a.add_argument("--keywords", nargs="+", help="Stichwörter für den Travel-Dealz-Feed (UND)")
    a.add_argument("--both-directions", action="store_true",
                   help="Zusätzlich Alarm für die Gegenrichtung anlegen")
    a.set_defaults(fn=cmd_add)

    d = sub.add_parser("deals", help="Gespeicherte Deals anzeigen")
    d.add_argument("--all", action="store_true", help="Auch abgelaufene")
    d.add_argument("--alert")
    d.set_defaults(fn=cmd_deals)

    rp = sub.add_parser("report", help="HTML-Übersicht erzeugen")
    rp.add_argument("-o", "--output")
    rp.set_defaults(fn=cmd_report)

    sub.add_parser("test-notify", help="Testnachricht an alle Kanäle").set_defaults(fn=cmd_test_notify)
    sub.add_parser("demo", help="Offline-Demo mit Beispieldaten (HTML-Bericht)").set_defaults(fn=cmd_demo)

    ic = sub.add_parser("import-config", help="Alarme/Einstellungen erneut aus der TOML importieren")
    ic.add_argument("--replace", action="store_true",
                    help="Gleiche Alarm-IDs und Einstellungen überschreiben (sonst nur neue IDs)")
    ic.set_defaults(fn=cmd_import_config)

    sv = sub.add_parser("serve", help="Web-Oberfläche mit Zeitplan starten")
    sv.add_argument("--host", default=os.environ.get("DEALMONITOR_HOST", "127.0.0.1"))
    sv.add_argument("--port", type=int, default=_env_port())
    sv.add_argument("--data", default=argparse.SUPPRESS,
                    help="Datenverzeichnis (Standard: $DEALMONITOR_DATA oder ./data)")
    sv.add_argument("--demo", action="store_true",
                    help="Beispieldaten in temporärem Verzeichnis, keine echten Preise")
    sv.add_argument("--no-scheduler", action="store_true",
                    help="Kein automatischer Zeitplan (manuelle Prüfungen gehen trotzdem)")
    sv.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")
    try:
        return args.fn(args)
    except (ValueError, FileNotFoundError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
