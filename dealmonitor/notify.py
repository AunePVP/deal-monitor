"""Benachrichtigungen.

* Inbox          - immer (Meldungen in der Web-Oberfläche, Tabelle ``notifications``)
* Konsole        - immer
* Telegram       - TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
* ntfy (Push)    - NTFY_TOPIC (optional NTFY_SERVER, Standard https://ntfy.sh)
* Webhook        - WEBHOOK_URL (eigener Benachrichtigungsserver, Slack, Discord, n8n ...)
                   optional WEBHOOK_TOKEN -> Header "Authorization: Bearer <token>"

Neben Deals melden Telegram, ntfy und Webhook auch kurze Status-Texte (``send_text``, z. B.
"Quelle gestört" aus :mod:`dealmonitor.health`); Inbox und Konsole bleiben den Deals vorbehalten.
"""

from __future__ import annotations

import html
import json
import logging
import os

from . import http
from .principles import euro, suspected_error_fare, why
from .models import Deal
from .sources.feed import SAFE_LINK_RE, source_label

log = logging.getLogger(__name__)

STATUS_LABEL = {"new": "NEU", "cheaper": "BILLIGER", "revived": "WIEDER DA", "test": "TEST"}


def safe_link(url: str | None) -> str | None:
    """Nur http(s)-Links weitergeben (ein "javascript:"-Link aus einem Feed wäre klickbarer Code)."""
    return url if url and SAFE_LINK_RE.match(url.strip()) else None


def compare_link(deal: Deal) -> str | None:
    """Google-Flights-Suche zum Gegenprüfen (Prinzip: Preise selbst vergleichen)."""
    o = deal.offer
    if not (o.origin and o.destination and o.departure):
        return None
    q = f"Flights from {o.origin} to {o.destination} on {o.departure.isoformat()}"
    if o.roundtrip and o.return_date:
        q += f" through {o.return_date.isoformat()}"
    else:
        q += " one way"
    if o.cabin != "economy":
        q += f" {o.cabin} class"
    from urllib.parse import quote
    return "https://www.google.com/travel/flights?hl=de&curr=EUR&q=" + quote(q)


def price_text(price: float) -> str:
    """Preis für Meldungen - Radar-Treffer ohne €-Preis in der Überschrift haben 0."""
    return euro(price) if price and price > 0 else "Preis im Artikel"


def article_price(deal: Deal) -> str | None:
    """Preiszeile für Artikel-Deals - die Überschrift nennt den Preis nicht immer (mydealz)."""
    o = deal.offer
    if not o.title:
        return None
    return f"Preis: ab {euro(o.price)}" if o.price and o.price > 0 else "Preis: siehe Artikel"


def priority(deal: Deal) -> int:
    """ntfy-Priorität: Fehlerpreis-Verdacht dringend (5), Top-Deal hoch (4), sonst normal."""
    if suspected_error_fare(deal.reasons):
        return 5
    return 4 if deal.rating == "Top-Deal" else 3


def format_text(deal: Deal, status: str) -> str:
    o = deal.offer
    lines = [f"[{STATUS_LABEL.get(status, status)}] {deal.rating}: {deal.headline}"]
    if (line := article_price(deal)):
        lines.append(line)
    if (short := why(deal.reasons)):
        lines.append(f"Warum: {short}")
    if o.departure:
        when = o.departure.strftime("%d.%m.%Y")
        if o.return_date:
            when += " – " + o.return_date.strftime("%d.%m.%Y")
        lines.append(f"Reisedaten: {when}")
    lines += [f"• {r}" for r in deal.reasons]
    lines.append(f"Quelle: {source_label(o.source)}")
    if (link := safe_link(o.link)):
        lines.append(f"Buchen/Artikel: {link}")
    cmp = compare_link(deal)
    if cmp:
        lines.append(f"Gegenprüfen: {cmp}")
    return "\n".join(lines)


class Console:
    name = "console"

    def send(self, alert_name: str, items: list[tuple[Deal, str]]) -> None:
        print(f"\n=== Deal-Alarm: {alert_name} ({len(items)}) ===")
        for deal, status in items:
            print(format_text(deal, status))
            print("-" * 60)


class Inbox:
    """Schreibt jede Meldung in die Datenbank - sichtbar unter "Meldungen" in der Web-UI."""

    name = "inbox"

    def __init__(self, store_path) -> None:
        self.store_path = store_path

    def send(self, alert_name: str, items: list[tuple[Deal, str]]) -> None:
        from .store import Store
        store = self.store_path if isinstance(self.store_path, Store) else Store(self.store_path)
        try:
            for deal, status in items:
                store.add_notification(
                    alert_id=deal.alert.id, alert_name=alert_name, status=status,
                    rating=deal.rating, headline=deal.headline, price=deal.offer.price,
                    link=deal.offer.link, compare=compare_link(deal),
                    fingerprint=deal.fingerprint)
        finally:
            if store is not self.store_path:
                store.close()


class Telegram:
    name = "telegram"

    def __init__(self, token: str, chat_id: str) -> None:
        self.token, self.chat_id = token, chat_id

    def send(self, alert_name: str, items: list[tuple[Deal, str]]) -> None:
        for deal, status in items:
            o = deal.offer
            parts = [f"<b>✈️ {html.escape(alert_name)} – {STATUS_LABEL.get(status, status)}</b>",
                     f"<b>{html.escape(deal.rating)}:</b> {html.escape(deal.headline)}"]
            if (line := article_price(deal)):
                parts.append(html.escape(line))
            if (short := why(deal.reasons)):
                parts.append(f"<i>Warum: {html.escape(short)}</i>")
            if o.departure:
                when = o.departure.strftime("%d.%m.%Y")
                if o.return_date:
                    when += " – " + o.return_date.strftime("%d.%m.%Y")
                parts.append(f"📅 {when}")
            parts += [f"• {html.escape(r)}" for r in deal.reasons]
            links = []
            if (link := safe_link(o.link)):
                links.append(f'<a href="{html.escape(link)}">Zum Angebot</a>')
            if (cmp := compare_link(deal)):
                links.append(f'<a href="{html.escape(cmp)}">Gegenprüfen</a>')
            if links:
                parts.append(" · ".join(links))
            parts.append(f"<i>Quelle: {html.escape(source_label(o.source))}</i>")
            try:
                http.request(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    data=json.dumps({"chat_id": self.chat_id, "text": "\n".join(parts),
                                     "parse_mode": "HTML",
                                     "disable_web_page_preview": True}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
            except Exception as e:  # noqa: BLE001 - Token darf nie in Fehlertexten landen
                msg = str(e)
                if self.token:
                    msg = msg.replace(self.token, "***")
                raise http.HttpError(msg, getattr(e, "status", None)) from None

    def send_text(self, title: str, text: str, *, priority: int = 3, tags=()) -> None:
        body = f"<b>{html.escape(title)}</b>\n{html.escape(text)}"
        try:
            http.request(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                data=json.dumps({"chat_id": self.chat_id, "text": body, "parse_mode": "HTML",
                                 "disable_web_page_preview": True,
                                 "disable_notification": priority < 3}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        except Exception as e:  # noqa: BLE001 - Token darf nie in Fehlertexten landen
            msg = str(e).replace(self.token, "***") if self.token else str(e)
            raise http.HttpError(msg, getattr(e, "status", None)) from None


class Ntfy:
    name = "ntfy"

    def __init__(self, topic: str, server: str = "https://ntfy.sh") -> None:
        self.topic, self.server = topic, server.rstrip("/")

    def send(self, alert_name: str, items: list[tuple[Deal, str]]) -> None:
        for deal, status in items:
            # JSON-Publish: UTF-8 (Umlaute, €) ohne Header-Kodierungsprobleme
            msg = {
                "topic": self.topic,
                "title": f"{alert_name}: {price_text(deal.offer.price)} ({deal.rating})",
                "message": format_text(deal, status),
                "tags": ["airplane"],
                "priority": priority(deal),
            }
            if (link := safe_link(deal.offer.link)):
                msg["click"] = link
            http.request(self.server, data=json.dumps(msg).encode(),
                         headers={"Content-Type": "application/json"}, method="POST")

    def send_text(self, title: str, text: str, *, priority: int = 3, tags=()) -> None:
        msg = {"topic": self.topic, "title": title, "message": text,
               "tags": list(tags) or ["warning"], "priority": priority}
        http.request(self.server, data=json.dumps(msg).encode(),
                     headers={"Content-Type": "application/json"}, method="POST")


class Webhook:
    name = "webhook"

    def __init__(self, url: str, token: str | None = None) -> None:
        self.url, self.token = url, token

    def send(self, alert_name: str, items: list[tuple[Deal, str]]) -> None:
        text = f"✈️ Deal-Alarm {alert_name}\n\n" + "\n\n".join(format_text(d, s) for d, s in items)
        payload = {
            "text": text,      # Slack, Mattermost, Teams (einfach)
            "content": text[:1990],  # Discord
            "alert": alert_name,
            "deals": [{
                "status": s, "rating": d.rating, "headline": d.headline,
                "price": d.offer.price, "currency": d.offer.currency,
                "origin": d.offer.origin, "destination": d.offer.destination,
                "departure": d.offer.departure and d.offer.departure.isoformat(),
                "return": d.offer.return_date and d.offer.return_date.isoformat(),
                "link": safe_link(d.offer.link), "compare": compare_link(d), "source": d.offer.source,
                "source_label": source_label(d.offer.source), "reasons": list(d.reasons),
                "why": why(d.reasons), "error_fare": suspected_error_fare(d.reasons),
            } for d, s in items],
        }
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        http.request(self.url, data=json.dumps(payload).encode(), headers=headers, method="POST")

    def send_text(self, title: str, text: str, *, priority: int = 3, tags=()) -> None:
        body = f"{title}\n\n{text}"
        payload = {"text": body, "content": body[:1990], "kind": "status", "title": title,
                   "message": text, "priority": priority}
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        http.request(self.url, data=json.dumps(payload).encode(), headers=headers, method="POST")


def send_status(notifiers: list, title: str, text: str, *, priority: int = 3,
                heartbeat: bool = False) -> list[str]:
    """Status-Text über alle Kanäle mit ``send_text`` (ntfy, Webhook, Telegram).
    Fehler werden nicht geworfen, sondern als Liste zurückgegeben."""
    tags = ["heartbeat"] if heartbeat else ["warning"] if priority >= 3 else ["white_check_mark"]
    log.info("%s: %s", title, text.replace("\n", "; "))
    errors = []
    for n in notifiers:
        if not hasattr(n, "send_text"):
            continue
        try:
            n.send_text(title, text, priority=priority, tags=tags)
        except Exception as e:  # noqa: BLE001 - jeder Kanal einzeln, nie den Lauf stoppen
            msg = f"Statusmeldung über {getattr(n, 'name', '?')} fehlgeschlagen: {e}"
            log.warning(msg)
            errors.append(msg)
    return errors


def from_env() -> list:
    env = os.environ
    out: list = [Console()]
    if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        out.append(Telegram(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"]))
    if env.get("NTFY_TOPIC"):
        out.append(Ntfy(env["NTFY_TOPIC"], env.get("NTFY_SERVER", "https://ntfy.sh")))
    if env.get("WEBHOOK_URL"):
        out.append(Webhook(env["WEBHOOK_URL"], env.get("WEBHOOK_TOKEN")))
    return out


def test_deal() -> Deal:
    """Erfundener Beispiel-Deal für "Testmeldung senden"."""
    from datetime import date, timedelta
    from .models import Alert, Offer
    from .principles import evaluate
    alert = Alert(id="test", name="Testalarm", origins=["ZRH"], destinations=["HAM"],
                  max_price=150, max_stops=1)
    dep = date.today() + timedelta(days=30)
    offer = Offer(source="test", origin="ZRH", destination="HAM", price=79, airline="LX",
                  stops=0, departure=dep, return_date=dep + timedelta(days=4))
    deal = evaluate(alert, offer, [])
    deal.headline = "[TEST] " + deal.headline
    return deal


def send_test(notifiers: list) -> list[dict]:
    """Testmeldung über alle Kanäle; Ergebnis pro Kanal (Fehler werden nicht geworfen)."""
    deal = test_deal()
    out = []
    for n in notifiers:
        try:
            n.send("Testalarm (keine echte Meldung)", [(deal, "test")])
            out.append({"name": n.name, "ok": True})
        except Exception as e:  # noqa: BLE001 - jeder Kanal einzeln
            log.warning("Testmeldung über %s fehlgeschlagen: %s", n.name, e)
            out.append({"name": n.name, "ok": False, "error": str(e)})
    return out
