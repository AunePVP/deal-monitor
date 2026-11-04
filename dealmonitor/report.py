"""Statische HTML-Übersicht aller aktiven und abgelaufenen Deals."""

from __future__ import annotations

import html
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from . import regions
from .config import Config
from .principles import CABIN_LABEL, euro
from .sources.feed import source_label
from .store import Store

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1b1f24;--muted:#5f6b7a;--line:#e3e6ea;
--accent:#0b6bcb;--top:#0a7d4f;--good:#2b7bb9;--deal:#6b5bd2;--exp:#9aa3ad}
@media (prefers-color-scheme:dark){:root{--bg:#121417;--card:#1b1f24;--text:#e8eaed;
--muted:#9aa3ad;--line:#2c323a;--accent:#5aa8f0;--top:#3ccf91;--good:#6fb3e8;--deal:#a99cf0;--exp:#6b7480}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 12px}
.sub{color:var(--muted);margin:0 0 20px}
.banner{background:#fff3cd;color:#664d03;border:1px solid #ffe69c;padding:10px 14px;border-radius:8px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:10px}
.row{display:flex;gap:12px;align-items:flex-start;justify-content:space-between;flex-wrap:wrap}
.title{font-weight:600;font-size:16px}.price{font-size:22px;font-weight:700;white-space:nowrap}
.meta{color:var(--muted);font-size:13px;margin-top:4px}
.badge{display:inline-block;font-size:12px;font-weight:600;padding:2px 8px;border-radius:999px;color:#fff;margin-right:6px}
.b-Top-Deal{background:var(--top)}.b-Sehr{background:var(--good)}.b-Deal{background:var(--deal)}
.b-Radar{background:#b45309}
.b-exp{background:var(--exp)}
ul{margin:6px 0 0 18px;padding:0}li{font-size:13px}
a{color:var(--accent)}.links a{margin-right:14px;font-size:14px}
.expired .title,.expired .price{text-decoration:line-through;color:var(--muted)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:top}
th{color:var(--muted);font-weight:600}
.empty{color:var(--muted);font-style:italic}
"""


def is_article(d: dict) -> bool:
    """Deal aus einem Feed-Artikel (Deal-Seiten) - erkennbar an der Quelle."""
    src = d.get("source") or ""
    return src.startswith("feed:") or src.endswith("-feed")


def _gf_link(d: dict) -> str | None:
    if not (d.get("origin") and d.get("destination") and d.get("departure")):
        return None
    q = f"Flights from {d['origin']} to {d['destination']} on {d['departure']}"
    q += f" through {d['return_date']}" if d.get("return_date") else " one way"
    if d.get("cabin") and d["cabin"] != "economy":
        q += f" {d['cabin']} class"
    return "https://www.google.com/travel/flights?hl=de&curr=EUR&q=" + quote(q)


def _fmt_date(s: str | None) -> str:
    return date.fromisoformat(s).strftime("%d.%m.%Y") if s else ""


def _card(d: dict) -> str:
    e = html.escape
    expired = d["status"] == "expired"
    badge = ('<span class="badge b-exp">Abgelaufen</span>' if expired else "") + \
        f'<span class="badge b-{e(d["rating"].split()[0])}">{e(d["rating"])}</span>'
    when = _fmt_date(d.get("departure"))
    if d.get("return_date"):
        when += " – " + _fmt_date(d["return_date"])
    meta = [e(d.get("alert_name") or d["alert_id"])]
    if when:
        meta.append(e(when))
    meta.append("Quelle: " + e(source_label(d["source"])))
    meta.append("gefunden " + e(d["first_seen"][:16].replace("T", " ")))
    if expired and d.get("expired_at"):
        meta.append("abgelaufen " + e(d["expired_at"][:16].replace("T", " ")))
    links = []
    if d.get("link"):
        links.append(f'<a href="{e(d["link"])}" rel="noopener" target="_blank">Zum Angebot</a>')
    if (gf := _gf_link(d)):
        links.append(f'<a href="{e(gf)}" rel="noopener" target="_blank">Bei Google Flights gegenprüfen</a>')
    reasons = "".join(f"<li>{e(r)}</li>" for r in d.get("reasons") or [])
    return f"""<div class="card{' expired' if expired else ''}">
<div class="row"><div><div>{badge}</div><div class="title">{e(d['headline'])}</div>
<div class="meta">{' · '.join(meta)}</div></div><div class="price">{euro(d['price'])}</div></div>
{f'<ul>{reasons}</ul>' if reasons else ''}
{f'<div class="links">{"".join(links)}</div>' if links else ''}</div>"""


def _alert_rows(cfg: Config) -> str:
    rows = []
    for a in cfg.alerts:
        def names(codes):
            shown = ", ".join(f"{regions.name(c)} ({c})" for c in codes[:6])
            return shown + (f" +{len(codes) - 6} weitere" if len(codes) > 6 else "")
        limit = f"≤ {euro(a.max_price)}" if a.max_price else f"≥ {a.min_discount:.0%} unter Normalpreis"
        rule = [CABIN_LABEL[a.cabin], "Hin- und Rückflug" if a.roundtrip else "one-way",
                f"max. {a.max_stops} Umstieg(e)"]
        if a.min_nights or a.max_nights:
            rule.append(f"{a.min_nights or 0}–{a.max_nights or '∞'} Nächte")
        rows.append(
            f"<tr><td><b>{html.escape(a.name)}</b>{'' if a.enabled else ' (pausiert)'}</td>"
            f"<td>{html.escape(names(a.origins))}</td><td>{html.escape(names(a.destinations))}</td>"
            f"<td>{html.escape(limit)}</td><td>{html.escape(', '.join(rule))}</td></tr>")
    return "".join(rows)


def _merge_articles(rows: list[dict]) -> list[dict]:
    """Derselbe Feed-Artikel unter mehreren Alarmen -> eine Karte."""
    out, by_link = [], {}
    for d in rows:
        key = d.get("link") if is_article(d) else None
        if key and key in by_link:
            first = by_link[key]
            first["alert_name"] = f"{first.get('alert_name') or first['alert_id']} · " \
                                  f"{d.get('alert_name') or d['alert_id']}"
            # Artikel passt auf Hin- und Gegenrichtung: keine (falsche) Richtung anzeigen
            if (first.get("origin"), first.get("destination")) != \
                    (d.get("origin"), d.get("destination")):
                first["origin"] = first["destination"] = ""
            continue
        if key:
            by_link[key] = d
        out.append(d)
    return out


def render(cfg: Config, store: Store, *, demo: bool = False) -> str:
    active = _merge_articles(store.deals(status="active"))
    expired = _merge_articles(store.deals(status="expired", limit=50))
    rank = {"Top-Deal": 0, "Sehr gut": 1, "Deal": 2, "Radar": 3}
    active.sort(key=lambda d: (rank.get(d["rating"], 3), d["price"]))
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    banner = ('<div class="banner"><b>DEMO:</b> Diese Seite zeigt erfundene Beispieldaten '
              'aus <code>tests/fixtures</code>, keine echten Preise.</div>') if demo else ""
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Deal-Monitor</title><style>{CSS}</style></head><body><main>
{banner}<h1>✈️ Deal-Monitor</h1>
<p class="sub">Stand {now} · Preise pro Person, Hin- und Rückflug (one-way wird gekennzeichnet) ·
Rabatt nur gegen eigene Preisbeobachtung, nie gegen Werbeversprechen.</p>
<h2>Aktive Deals ({len(active)})</h2>
{''.join(_card(d) for d in active) or '<p class="empty">Gerade kein Deal, der die Kriterien erfüllt.</p>'}
<h2>Deal-Alarme ({len(cfg.alerts)})</h2>
<table><tr><th>Alarm</th><th>Von</th><th>Nach</th><th>Preisgrenze</th><th>Regeln</th></tr>
{_alert_rows(cfg)}</table>
<h2>Abgelaufen ({len(expired)})</h2>
{''.join(_card(d) for d in expired) or '<p class="empty">Noch nichts abgelaufen.</p>'}
</main></body></html>"""


def write(cfg: Config, store: Store, path: str | None = None, *, demo: bool = False) -> Path:
    out = Path(path or cfg.settings.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(cfg, store, demo=demo), encoding="utf-8")
    return out
