/* Deal-Monitor – Weboberfläche (Vanilla JS, keine Abhängigkeiten).
 *
 * Sicherheit: Server-Daten werden ausschließlich per textContent / Attribut
 * gesetzt, nie per innerHTML. Links werden nur übernommen, wenn sie mit
 * http:// oder https:// beginnen.
 */
(() => {
  "use strict";

  // ------------------------------------------------------------------ Helfer
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  /** Element bauen: el("a", {class: "x", href}, "Text", child, …) – Text immer als textContent. */
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === "class") node.className = v;
        else if (k === "text") node.textContent = v;
        else if (k === "dataset") Object.assign(node.dataset, v);
        else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
        else if (v === true) node.setAttribute(k, "");
        else node.setAttribute(k, String(v));
      }
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  function safeUrl(u) {
    if (typeof u !== "string") return null;
    const s = u.trim();
    return /^https?:\/\//i.test(s) ? s : null;
  }

  function extLink(href, label, cls) {
    const url = safeUrl(href);
    if (!url) return null;
    return el("a", { class: cls || "btn", href: url, target: "_blank", rel: "noopener noreferrer" }, label);
  }

  const euroFmt = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
  const euro = (v) => (typeof v === "number" && isFinite(v) ? euroFmt.format(v) : "–");

  function parseIso(s) {
    if (!s || typeof s !== "string") return null;
    // "2026-11-04T16:20:00" (lokal) oder "2026-11-04"
    const m = s.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/);
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
  }
  const pad = (n) => String(n).padStart(2, "0");
  function fmtDate(s) {
    const d = parseIso(s);
    return d ? `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()}` : "";
  }
  function fmtDateTime(s) {
    const d = parseIso(s);
    return d ? `${fmtDate(s)}, ${pad(d.getHours())}:${pad(d.getMinutes())} Uhr` : "";
  }
  function fmtTime(s) {
    const d = parseIso(s);
    if (!d) return "";
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1).toDateString() === d.toDateString();
    const hm = `${pad(d.getHours())}:${pad(d.getMinutes())} Uhr`;
    if (sameDay) return `heute ${hm}`;
    if (tomorrow) return `morgen ${hm}`;
    return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}. ${hm}`;
  }
  // Zeitpunkt in der Zukunft: "in 12 Min." (unter einer Stunde), sonst wie fmtTime
  function untilTime(s) {
    const d = parseIso(s);
    if (!d) return "";
    const min = Math.round((d.getTime() - Date.now()) / 60000);
    if (min <= 0) return "gleich";
    if (min < 60) return `in ${min} Min.`;
    return fmtTime(s);
  }
  function relTime(s) {
    const d = parseIso(s);
    if (!d) return "";
    const min = Math.round((Date.now() - d.getTime()) / 60000);
    if (min < 1) return "gerade eben";
    if (min < 60) return `vor ${min} Min.`;
    const h = Math.round(min / 60);
    if (h < 24) return `vor ${h} Std.`;
    const days = Math.round(h / 24);
    return days === 1 ? "vor 1 Tag" : `vor ${days} Tagen`;
  }
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const numDe = (v) => String(v).replace(".", ",");

  /** Zahl aus einem Textfeld lesen – deutsch und tolerant: "99,5", "1.299", "1.299,50 €", "0.5".
   *  Leer → null. Ungültig → Error mit deutschem Text und Feldnamen (nie still verfälschen). */
  function parseNumber(raw, label, { int = false } = {}) {
    let s = String(raw ?? "").replace(/[\s\u00a0\u202f€%]/g, "").replace(/,-$/, "");
    if (s === "") return null;
    s = s.replace(/\.(?=\d{3}(?!\d))/g, "");   // Tausenderpunkte: 1.299 → 1299
    s = s.replace(",", ".");                      // Dezimalkomma: 99,5 → 99.5
    if (!/^\d+(\.\d+)?$/.test(s)) throw new Error(`„${label}“: bitte eine Zahl eingeben (z. B. ${int ? "3" : "99,5"}).`);
    const n = Number(s);
    if (int && !Number.isInteger(n)) throw new Error(`„${label}“: bitte eine ganze Zahl eingeben.`);
    return n;
  }
  function checkRange(n, label, min, max, unit = "") {
    if (n === null) return n;
    if (typeof min === "number" && n < min) throw new Error(`„${label}“: mindestens ${numDe(min)}${unit}.`);
    if (typeof max === "number" && n > max) throw new Error(`„${label}“: höchstens ${numDe(max)}${unit}.`);
    return n;
  }

  const CABIN = { economy: "Economy", premium: "Premium Economy", business: "Business Class", first: "First Class" };
  // Anzeigenamen der Quellen (der Server liefert bei Deals zusätzlich source_label)
  const SOURCE = {
    "travelpayouts": "Travelpayouts (Preis-API)", "travel-dealz-feed": "Travel-Dealz-Artikel",
    "feed:travel-dealz": "Travel-Dealz-Artikel", "feed:travel-dealz-suche": "Travel-Dealz-Suche",
    "feed:mydealz-fluege": "mydealz", "feed:fly4free": "fly4free", "feed:premium-flights": "premium-flights",
    "feed:urlaubspiraten": "Urlaubspiraten", "feed:reisetopia": "reisetopia", "feed:custom": "Eigener Feed",
    "google_flights": "Google Flights", "amadeus-dates": "Amadeus (Monatsscan)", "amadeus-offers": "Amadeus (Angebote)",
  };
  const sourceLabel = (d) => d.source_label || SOURCE[d.source] || d.source || "unbekannt";
  const RADAR_ID = "radar";
  const RADAR_NAME = "Radar: Hammer-Deals";
  const VERIFIED_RE = /^Google Flights bestätigt/;
  const REGION_LABEL = {
    DE: "Deutschland", DE_HUBS: "Deutschland (Hubs)", CH: "Schweiz", AT: "Österreich", DACH: "DACH",
    CN: "China", CN_MAJOR: "China (große Städte)", CN_HK: "China inkl. HK/Macau",
    CN_ALL: "China (alle Städte)", CN_TW: "China inkl. HK/Macau/Taiwan",
  };
  const NOTE_STATUS = { new: "Neu", cheaper: "Billiger", revived: "Wieder da", test: "Test" };
  const TRIGGER = { manual: "manuell", schedule: "Zeitplan", scheduler: "Zeitplan", startup: "Start", demo: "Demo", cli: "CLI",
    feeds: "Feed-Check", "manual-feeds": "manuell (nur Feeds)", fast: "Schnell-Check",
    "manual-fast": "manuell (schnell)", hot: "Sofort-Prüfung" };
  const CHANNEL = { inbox: "Meldungen (hier)", console: "Konsole", telegram: "Telegram", ntfy: "ntfy", webhook: "Webhook" };

  const stopsLabel = (n) => (n === 0 ? "direkt" : typeof n === "number" ? plural(n, "Umstieg", "Umstiege") : null);
  function ratingClass(r) {
    if (r === "Top-Deal") return "r-top";
    if (r === "Sehr gut") return "r-good";
    if (r === "Radar") return "r-radar";
    return "r-deal";
  }

  // ------------------------------------------------------------------ API
  class ApiError extends Error {
    constructor(msg, status) { super(msg); this.status = status; }
  }
  async function api(method, path, body) {
    const opts = { method, headers: { Accept: "application/json" }, cache: "no-store" };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(path, opts);
    } catch (e) {
      throw new ApiError("Keine Verbindung zum Server.", 0);
    }
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      const msg = (data && typeof data.error === "string" && data.error) || `Fehler ${res.status}`;
      throw new ApiError(msg, res.status);
    }
    return data || {};
  }

  // ------------------------------------------------------------------ Toasts
  function toast(msg, type = "ok") {
    const box = $("#toasts");
    const t = el("div", { class: `toast toast-${type}` },
      el("span", { class: "toast-msg" }, msg),
      el("button", { type: "button", class: "icon-btn", "aria-label": "Hinweis schließen", onclick: () => t.remove() }, "×"));
    box.append(t);
    while (box.childElementCount > 3) box.firstElementChild.remove();
    setTimeout(() => t.remove(), type === "error" ? 8000 : 4500);
  }

  // ------------------------------------------------------------------ Zustand
  const state = {
    route: "deals",
    status: null,
    alerts: [],
    regions: {},
    airports: {},
    dealFilter: null,
    showExpired: false,
    lastRunKey: undefined,
    lastUnread: null,
    maxNoteId: 0,        // höchste bekannte Meldungs-ID (Liste)
    notifiedNoteId: 0,   // bis hierhin wurden Desktop-Benachrichtigungen gezeigt
    settings: null,
    deleteSecrets: new Set(),
    pollTimer: null,
    connFails: 0,
  };
  try { state.showExpired = localStorage.getItem("dm.showExpired") === "1"; } catch (e) { /* egal */ }

  // ------------------------------------------------------------------ Routing
  const ROUTES = ["deals", "alarme", "meldungen", "einstellungen"];
  function currentRoute() {
    const h = (location.hash || "").replace(/^#/, "");
    return ROUTES.includes(h) ? h : "deals";
  }
  function showRoute() {
    const r = currentRoute();
    const changed = r !== state.route;
    state.route = r;
    for (const name of ROUTES) $(`#view-${name}`).hidden = name !== r;
    for (const a of $$(".main-nav a")) {
      if (a.dataset.route === r) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    document.title = { deals: "Deals", alarme: "Alarme", meldungen: "Meldungen", einstellungen: "Einstellungen" }[r] + " · Deal-Monitor";
    loadView(r);
    if (changed) { window.scrollTo(0, 0); $("#main").focus({ preventScroll: true }); }
  }
  function loadView(r) {
    if (r === "deals") loadDeals();
    else if (r === "alarme") loadAlerts();
    else if (r === "meldungen") loadNotes();
    else if (r === "einstellungen") { loadSettings(); loadRuns(); }
  }

  // ------------------------------------------------------------------ Status / Kopf
  function renderStatus() {
    const s = state.status;
    if (!s) return;
    const running = !!s.running;
    const btn = $("#run-btn");
    btn.disabled = running;
    btn.classList.toggle("is-running", running);
    btn.setAttribute("aria-busy", running ? "true" : "false");
    $(".run-label", btn).textContent = running ? "Prüfung läuft …" : "Jetzt prüfen";

    const parts = [];
    if (running) parts.push("Prüfung läuft gerade …");
    if (s.last_run) {
      const lr = s.last_run;
      const when = lr.finished_at || lr.started_at;
      let t = `Letzte Prüfung ${relTime(when)}`;
      const errs = Array.isArray(lr.errors) ? lr.errors.length : 0;
      if (errs) t += ` (${plural(errs, "Fehler", "Fehler")})`;
      parts.push(t);
    } else if (!running) {
      parts.push("Noch keine Prüfung");
    }
    // Nach einem reinen Feed-Check: Fehler der letzten vollen Prüfung (Preis-APIs, Google) nicht verstecken
    const lf = s.last_full_run;
    const fullErrs = lf && s.last_run && lf.id !== s.last_run.id && Array.isArray(lf.errors) ? lf.errors.length : 0;
    if (fullErrs) parts.push(`volle Prüfung ${relTime(lf.finished_at || lf.started_at)}: ${plural(fullErrs, "Fehler", "Fehler")}`);
    if (s.next_run) parts.push(`nächste ${fmtTime(s.next_run)}`);
    else if (!running) parts.push("kein automatischer Zeitplan aktiv");
    if (s.next_feed_run) parts.push(`nächster Feed-Check ${untilTime(s.next_feed_run)}`);
    const line = $("#status-line");
    line.textContent = parts.join(" · ");
    if ((s.last_run && s.last_run.errors && s.last_run.errors.length) || fullErrs) {
      line.append(" ", el("a", { href: "#einstellungen" }, "Details"));
    }

    $("#demo-banner").hidden = !s.demo;
    const srcs = Array.isArray(s.sources) ? s.sources : [];
    $("#onboarding-banner").hidden = !(s.configured && s.configured.travelpayouts === false && srcs.includes("travelpayouts"));

    const unread = (s.counts && s.counts.unread) || 0;
    const badge = $("#unread-badge");
    badge.hidden = unread === 0;
    badge.textContent = unread > 99 ? "99+" : String(unread);
    badge.setAttribute("aria-label", `${unread} ungelesen`);
    $("#mark-all-read-btn").disabled = unread === 0;
    // Radar-Chip und Zähler hängen am Status (counts.radar_deals, configured.radar)
    if (state.route === "deals" && state.alerts.length) renderDealFilters();
    renderSourceStates();
  }

  async function pollStatus() {
    clearTimeout(state.pollTimer);
    try {
      const s = await api("GET", "/api/status");
      state.connFails = 0;
      $("#conn-banner").hidden = true;
      const prev = state.status;
      state.status = s;
      renderStatus();

      const lr = s.last_run;
      const key = lr ? `${lr.id}|${lr.finished_at || ""}` : null;
      const runFinished = state.lastRunKey !== undefined && key !== state.lastRunKey && lr && lr.finished_at;
      const stoppedRunning = prev && prev.running && !s.running;
      state.lastRunKey = key;
      if (runFinished || stoppedRunning) onRunFinished(lr);

      const unread = (s.counts && s.counts.unread) || 0;
      if (state.lastUnread !== null && unread > state.lastUnread) onNewNotes();
      state.lastUnread = unread;
    } catch (e) {
      state.connFails += 1;
      if (state.connFails >= 2) $("#conn-banner").hidden = false;
    }
    const fast = state.status && state.status.running;
    state.pollTimer = setTimeout(pollStatus, fast ? 3000 : 15000);
  }

  function onRunFinished(lr) {
    if (lr) {
      const errs = Array.isArray(lr.errors) ? lr.errors.length : 0;
      const msg = `Prüfung fertig: ${plural(lr.offers || 0, "Angebot", "Angebote")}, ` +
        `${plural(lr.deals || 0, "Deal", "Deals")}` + (lr.radar ? `, ${lr.radar} Radar` : "") +
        `, ${lr.notified || 0} neu gemeldet` + (lr.rejected ? `, ${lr.rejected} bei Google verworfen` : "") +
        (errs ? ` – ${plural(errs, "Fehler", "Fehler")}, siehe Einstellungen → Letzte Läufe` : "");
      toast(msg, errs ? "error" : "ok");
    }
    loadAlertsData().then(() => {
      if (state.route === "deals") loadDeals();
      if (state.route === "alarme") renderAlerts();
    });
    if (state.route === "meldungen") loadNotes();
    if (state.route === "einstellungen") loadRuns();
  }

  async function startRun(alertId, mode) {
    try {
      const body = alertId ? { alert_id: alertId } : {};
      if (mode) body.mode = mode;
      await api("POST", "/api/run", body);
      toast(alertId ? "Prüfung für diesen Alarm gestartet."
        : mode === "feeds" ? "Deal-Seiten werden geprüft (nur Feeds + Radar)."
        : "Prüfung gestartet – das kann einen Moment dauern.");
      if (state.status) { state.status.running = true; renderStatus(); }
      clearTimeout(state.pollTimer);
      state.pollTimer = setTimeout(pollStatus, 1500);
    } catch (e) {
      toast(e.message, e.status === 409 ? "info" : "error");
    }
  }

  // ------------------------------------------------------------------ Desktop-Benachrichtigungen
  function notifySupported() { return "Notification" in window; }
  function renderNotifyBtn() {
    const btn = $("#desktop-notify-btn");
    if (!notifySupported()) { btn.hidden = true; return; }
    btn.hidden = false;
    const p = Notification.permission;
    btn.disabled = p !== "default";
    btn.textContent = p === "granted" ? "Desktop-Benachrichtigungen aktiv"
      : p === "denied" ? "Desktop-Benachrichtigungen im Browser blockiert"
      : "Desktop-Benachrichtigungen aktivieren";
  }
  async function onNewNotes() {
    if (state.route === "meldungen") loadNotes();
    if (!notifySupported() || Notification.permission !== "granted") return;
    try {
      const data = await api("GET", "/api/notifications?limit=20");
      const list = Array.isArray(data.notifications) ? data.notifications : [];
      // eigener Zähler: loadNotes() darf die Meldungen nicht vorher "verbrauchen"
      const fresh = list.filter((n) => typeof n.id === "number" && n.id > state.notifiedNoteId && !n.read);
      for (const n of list) if (typeof n.id === "number" && n.id > state.notifiedNoteId) state.notifiedNoteId = n.id;
      updateMaxNoteId(list);
      for (const n of fresh.slice(0, 5).reverse()) {
        const title = `${n.rating || "Deal"}: ${euro(n.price)}`;
        const notif = new Notification(title, { body: `${n.headline || ""}\n${n.alert_name || ""}`, tag: `dm-${n.id}` });
        notif.onclick = () => { window.focus(); location.hash = "#meldungen"; notif.close(); };
      }
    } catch (e) { /* still */ }
  }
  function updateMaxNoteId(list) {
    for (const n of list || []) if (typeof n.id === "number" && n.id > state.maxNoteId) state.maxNoteId = n.id;
  }

  // ------------------------------------------------------------------ Alarme laden (für Filter + Liste)
  async function loadAlertsData() {
    try {
      const data = await api("GET", "/api/alerts");
      state.alerts = Array.isArray(data.alerts) ? data.alerts : [];
    } catch (e) {
      toast(`Alarme konnten nicht geladen werden: ${e.message}`, "error");
    }
  }

  // ------------------------------------------------------------------ Deals
  let dealsReq = 0;
  async function loadDeals() {
    const req = ++dealsReq;
    if (!state.alerts.length) await loadAlertsData();
    if (state.dealFilter && state.dealFilter !== RADAR_ID && !state.alerts.some((a) => a.id === state.dealFilter)) state.dealFilter = null;
    renderDealFilters();
    renderBestPrices();
    $("#show-expired").checked = state.showExpired;
    const q = new URLSearchParams({ status: state.showExpired ? "all" : "active" });
    if (state.dealFilter) q.set("alert_id", state.dealFilter);
    const list = $("#deal-list");
    if (!list.childElementCount) list.replaceChildren(el("p", { class: "loading" }, "Lade Deals …"));
    try {
      const data = await api("GET", `/api/deals?${q}`);
      if (req !== dealsReq) return;
      renderDeals(Array.isArray(data.deals) ? data.deals : []);
    } catch (e) {
      if (req !== dealsReq) return;
      list.replaceChildren(el("div", { class: "empty" }, el("p", null, `Deals konnten nicht geladen werden: ${e.message}`)));
    }
  }

  // ------------------------------------------------------------------ Bestpreise (auch ohne Deal)
  /** Hin- und Rückflug aus zwei einzelnen One-way-Tickets (Google-Tagesrotation). */
  const isCombo = (x) => Array.isArray(x.legs) && x.legs.length === 2;
  const COMBO_LABEL = "2× One-way";
  const COMBO_TITLE = "Hin- und Rückflug als zwei einzelne One-way-Tickets";
  function shortDates(e) {
    if (!e.departure) return null;
    const d = fmtDate(e.departure);
    return e.return_date ? `${d.slice(0, 6)} – ${fmtDate(e.return_date)}` : d;
  }
  /** Angaben eines Bestpreises: Strecke, Termin, Nächte, Airline, Umstiege (bzw. Artikel-Titel). */
  function bestFacts(e) {
    if (e.kind === "article") return [e.title || "Artikel einer Deal-Seite"];
    return [e.origin && e.destination ? `${e.origin} → ${e.destination}` : null, shortDates(e),
      typeof e.nights === "number" ? plural(e.nights, "Nacht", "Nächte") : null,
      e.airline || null, stopsLabel(e.stops), isCombo(e) ? COMBO_LABEL : null].filter(Boolean);
  }
  function bestNote(e) {
    if (e.kind === "article") return "Artikel";
    if (e.kind === "live") return "Live-Preis";
    return e.confirmed ? "✓ bei Google Flights bestätigt" : "Cache-Preis, ungeprüft";
  }
  /** Alter genau dieses Eintrags (nicht des jüngsten der Liste) – Artikel: Veröffentlichung. */
  function bestAge(e) {
    if (e.kind === "article" && e.published) return `veröffentlicht ${relTime(e.published)}`;
    return e.seen_at ? `Stand ${relTime(e.seen_at)}` : "";
  }
  /** Abstand zur Preisgrenze: "73 € über deiner Grenze von 110 €" / "unter deiner Grenze". */
  function bestGap(a, price) {
    if (typeof a.max_price !== "number" || typeof price !== "number") return null;
    const diff = Math.round(price - a.max_price);
    return diff > 0
      ? el("span", { class: "best-gap over" }, `${euro(diff)} über deiner Grenze von ${euro(a.max_price)}`)
      : el("span", { class: "best-gap under" }, `unter deiner Grenze von ${euro(a.max_price)}`);
  }
  const joinDots = (items) => items.flatMap((f, i) => (i ? [" · ", f] : [f]));

  function bestRow(a) {
    const c = a.cheapest;
    const name = el("p", { class: "best-alert" }, a.name || a.id, a.enabled === false ? el("span", { class: "muted" }, " · pausiert") : null);
    if (!c || !c.best) {
      return el("div", { class: "best-row is-empty" },
        el("div", { class: "best-main" }, name,
          el("p", { class: "best-meta" }, a.enabled === false ? "Kein aktueller Preis – Alarm ist pausiert."
            : "Noch kein passender Preis – erscheint nach der nächsten Prüfung.")),
        el("div", { class: "best-price" }, el("div", { class: "price price-none" }, "–")));
    }
    const e = c.best;
    const live = c.best_live;
    const sub = e.roundtrip === false ? "p. P. one-way" : "p. P. hin & zurück";
    const main = el("div", { class: "best-main" }, name,
      el("p", { class: "best-facts" }, ...joinDots(bestFacts(e))),
      live ? el("p", { class: "best-live" }, `${live.kind === "live" ? "Live" : "✓ Bestätigt"} bei ${live.kind === "live" ? (live.source_label || "Google Flights") : "Google Flights"}: ab ${euro(live.price)}`,
        shortDates(live) ? ` (${shortDates(live)})` : "", el("span", { class: "muted" }, ` · ${bestAge(live)}`)) : null,
      el("p", { class: "best-meta" }, `Quelle: ${sourceLabel(e)} · ${bestNote(e)} · ${bestAge(e)}`,
        c.route_count > 1 ? ` · Preise für ${plural(c.route_count, "Strecke", "Strecken")}` : ""));
    const priceBox = el("div", { class: "best-price" },
      el("div", { class: "price" }, euro(e.price)), el("div", { class: "price-sub" }, sub), bestGap(a, e.price));
    const actions = el("div", { class: "best-actions" },
      extLink(e.link, e.kind === "article" ? "Artikel" : "Ansehen", "btn btn-small"),
      extLink(e.compare, "Google Flights", "btn btn-small"));
    return el("div", { class: "best-row" }, main, priceBox, actions.childElementCount ? actions : null);
  }

  function renderBestPrices() {
    const box = $("#best-prices");
    const f = state.dealFilter;
    const alerts = f === RADAR_ID ? [] : state.alerts.filter((a) => (f ? a.id === f : a.enabled !== false || a.cheapest));
    box.hidden = !alerts.length;
    if (!alerts.length) return;
    $("#best-list").replaceChildren(...alerts.map(bestRow));
  }

  function renderDealFilters() {
    const box = $("#deal-filters");
    const chip = (id, label, count) => el("button", {
      type: "button", class: "chip", "aria-pressed": String(state.dealFilter === id), dataset: { id: id ?? "" },
      onclick: () => { state.dealFilter = id; loadDeals(); },
    }, el("span", { class: "chip-text" }, label), typeof count === "number" ? el("span", { class: "chip-count" }, String(count)) : null);
    const radarCount = state.status && state.status.counts ? state.status.counts.radar_deals || 0 : 0;
    const radarOn = !!(state.status && state.status.configured && state.status.configured.radar);
    box.hidden = !state.alerts.length && !radarCount && !radarOn;
    const wanted = [[null, "Alle", null], ...state.alerts.map((a) => [a.id, a.name || a.id, a.active_deals || 0])];
    if (radarOn || radarCount || state.dealFilter === RADAR_ID) wanted.push([RADAR_ID, "Radar", radarCount]);
    const existing = $$("button.chip", box);
    const same = existing.length === wanted.length && existing.every((b, i) => b.dataset.id === (wanted[i][0] ?? ""));
    if (!same) { box.replaceChildren(...wanted.map(([id, label, count]) => chip(id, label, count))); return; }
    // gleiche Alarme: nur Zustand aktualisieren, damit der Tastatur-Fokus erhalten bleibt
    existing.forEach((b, i) => {
      const [id, label, count] = wanted[i];
      b.setAttribute("aria-pressed", String(state.dealFilter === id));
      $(".chip-text", b).textContent = label;
      const c = $(".chip-count", b);
      if (c && typeof count === "number") c.textContent = String(count);
    });
  }

  const RATING_RANK = { "Top-Deal": 0, "Sehr gut": 1, "Deal": 2 };
  function renderDeals(deals) {
    const list = $("#deal-list");
    deals.sort((a, b) => (a.status === "expired") - (b.status === "expired") ||
      (RATING_RANK[a.rating] ?? 3) - (RATING_RANK[b.rating] ?? 3) || (a.price || 0) - (b.price || 0));
    if (!deals.length) { list.replaceChildren(dealsEmpty()); return; }
    list.replaceChildren(...deals.map(dealCard));
  }

  function dealsEmpty() {
    const s = state.status || {};
    const box = el("div", { class: "empty" });
    if (!state.alerts.length) {
      box.append(el("p", { class: "empty-title" }, "Noch keine Deal-Alarme"),
        el("p", null, "Lege fest, welche Strecken dich interessieren und ab welchem Preis du informiert werden willst."),
        el("button", { type: "button", class: "btn btn-primary", onclick: () => { location.hash = "#alarme"; openAlertForm(null); } }, "Ersten Alarm anlegen"));
      return box;
    }
    box.append(el("p", { class: "empty-title" }, state.dealFilter === RADAR_ID ? "Das Radar hat gerade keinen Hammer-Deal gefunden"
      : state.dealFilter ? "Für diesen Alarm gibt es gerade keinen Deal" : "Gerade kein Deal, der deine Kriterien erfüllt"));
    const hint = s.next_run ? `Der Monitor prüft automatisch wieder ${fmtTime(s.next_run)}.` : "Starte eine Prüfung, um nach neuen Deals zu suchen.";
    box.append(el("p", null, "Das ist normal – gute Deals sind selten. " + hint));
    if (s.configured && s.configured.travelpayouts === false && (s.sources || []).includes("travelpayouts")) {
      box.append(el("p", null, "Tipp: Travelpayouts ist eingeschaltet, hat aber keinen Token – ",
        el("a", { href: "#einstellungen" }, "Token eintragen"), " oder die Quelle abschalten."));
    }
    if (!state.showExpired) box.append(el("button", { type: "button", class: "btn", onclick: () => setShowExpired(true) }, "Abgelaufene Deals anzeigen"));
    return box;
  }

  function dealCard(d) {
    const expired = d.status === "expired";
    const verified = d.verified === true || (Array.isArray(d.reasons) && d.reasons.some((r) => VERIFIED_RE.test(String(r))));
    const badges = el("div", { class: "badges" },
      expired ? el("span", { class: "badge r-exp" }, "Abgelaufen") : null,
      el("span", { class: `badge ${expired ? "r-exp-soft" : ratingClass(d.rating)}` }, d.rating || "Deal"),
      d.cabin && d.cabin !== "economy" ? el("span", { class: "badge badge-outline" }, CABIN[d.cabin] || d.cabin) : null,
      d.roundtrip === false ? el("span", { class: "badge badge-outline" }, "one-way") : null,
      isCombo(d) ? el("span", { class: "badge badge-outline", title: COMBO_TITLE }, COMBO_LABEL) : null,
      verified && !expired ? el("span", { class: "badge badge-verified", title: "Preis bei Google Flights gegengeprüft" }, "✓ Google Flights bestätigt") : null);

    const route = d.origin && d.destination ? `${d.origin} → ${d.destination}` : null;
    const priceBox = el("div", { class: "price-box" },
      // Radar: als Error Fare angekündigt, aber ohne €-Preis in der Überschrift -> Preis 0
      d.price > 0 ? el("div", { class: "price" }, euro(d.price)) : el("div", { class: "price price-none" }, "Preis im Artikel"),
      el("div", { class: "price-sub" }, d.roundtrip === false ? "p. P. one-way" : "p. P. hin & zurück"),
      typeof d.discount === "number" && d.discount > 0 && typeof d.reference_price === "number"
        ? el("div", { class: "price-ref" }, el("span", { class: "discount" }, `−${Math.round(d.discount * 100)} %`), " statt ", el("s", null, euro(d.reference_price)))
        : null);

    let when = fmtDate(d.departure);
    if (when && d.return_date) when += ` – ${fmtDate(d.return_date)}`;
    const facts = [when ? el("span", null, el("span", { class: "sr-only" }, "Reisedaten: "), when) : null,
      typeof d.nights === "number" ? plural(d.nights, "Nacht", "Nächte") : null,
      d.airline || null, stopsLabel(d.stops)].filter(Boolean);
    const factsEl = facts.length ? el("p", { class: "deal-facts" }, ...facts.flatMap((f, i) => (i ? [" · ", f] : [f]))) : null;

    const reasons = Array.isArray(d.reasons) && d.reasons.length
      ? el("ul", { class: "reasons", "aria-label": "Warum ein Deal?" }, ...d.reasons.map((r) => el("li", null, r))) : null;

    const meta = el("p", { class: "deal-meta" },
      d.alert_name || d.alert_id || "", " · Quelle: ", sourceLabel(d),
      d.first_seen ? ` · gefunden am ${fmtDateTime(d.first_seen)}` : "",
      expired && d.expired_at ? ` · abgelaufen am ${fmtDateTime(d.expired_at)}` : "");

    const actions = el("div", { class: "deal-actions" },
      extLink(d.link, "Zum Angebot", "btn btn-primary"),
      extLink(d.compare, "Bei Google Flights gegenprüfen", "btn"));

    const isRadar = d.alert_id === RADAR_ID || (Array.isArray(d.alert_ids) && d.alert_ids.includes(RADAR_ID));
    return el("article", { class: `deal-card${expired ? " expired" : ""}${isRadar ? " is-radar" : ""}${verified && !expired ? " is-verified" : ""}` },
      el("div", { class: "deal-top" }, el("div", { class: "deal-top-left" }, badges, route ? el("p", { class: "route" }, route) : null), priceBox),
      el("h2", { class: "deal-title" }, d.headline || "Deal"),
      factsEl, reasons, meta, actions.childElementCount ? actions : null);
  }

  function setShowExpired(v) {
    state.showExpired = v;
    try { localStorage.setItem("dm.showExpired", v ? "1" : "0"); } catch (e) { /* egal */ }
    loadDeals();
  }

  // ------------------------------------------------------------------ Alarme
  async function loadAlerts() {
    const list = $("#alert-list");
    if (!list.childElementCount) list.replaceChildren(el("p", { class: "loading" }, "Lade Alarme …"));
    await loadAlertsData();
    renderAlerts();
  }

  function codeLabel(code) {
    const c = String(code).toUpperCase();
    if (c.startsWith("@")) {
      const r = c.slice(1);
      const n = Array.isArray(state.regions[r]) ? state.regions[r].length : null;
      return `${REGION_LABEL[r] || r}${n ? ` (${n})` : ""}`;
    }
    return state.airports[c] ? `${state.airports[c]} (${c})` : c;
  }
  function codesLabel(codes) {
    const list = Array.isArray(codes) ? codes : [];
    const shown = list.slice(0, 4).map(codeLabel).join(", ");
    return list.length > 4 ? `${shown} +${list.length - 4}` : shown || "–";
  }

  function renderAlerts() {
    const list = $("#alert-list");
    if (!state.alerts.length) {
      list.replaceChildren(el("div", { class: "empty" },
        el("p", { class: "empty-title" }, "Noch keine Alarme"),
        el("p", null, "Ein Alarm ist zum Beispiel „Zürich → Hamburg hin und zurück unter 110 €“."),
        el("button", { type: "button", class: "btn btn-primary", onclick: () => openAlertForm(null) }, "+ Ersten Alarm anlegen")));
      return;
    }
    list.replaceChildren(...state.alerts.map(alertCard));
  }

  function alertCard(a) {
    const enabled = a.enabled !== false;
    const roundtrip = (a.trip || "roundtrip") === "roundtrip";
    const pairs = typeof a.pairs === "number" ? a.pairs : null;
    const limit = typeof a.max_price === "number" ? `bis ${euro(a.max_price)}`
      : `≥ ${Math.round((typeof a.min_discount === "number" ? a.min_discount : 0.3) * 100)} % unter Normalpreis`;
    const dealsText = a.active_deals ? `${plural(a.active_deals, "aktiver Deal", "aktive Deals")} · ab ${euro(a.best_price)}` : "keine aktiven Deals";

    const PAUSED_HINT = "Prüft diesen Alarm einmalig, obwohl er pausiert ist";
    const sw = el("input", { type: "checkbox", role: "switch", checked: enabled, "aria-label": `${a.name || a.id}: aktiv` });
    const swLabel = el("span", { class: "switch-text" }, enabled ? "aktiv" : "pausiert");
    const runBtn = el("button", { type: "button", class: "btn btn-small", title: enabled ? null : PAUSED_HINT, onclick: () => startRun(a.id) }, "Nur diesen prüfen");
    sw.addEventListener("change", async () => {
      const hadFocus = document.activeElement === sw;
      sw.disabled = true;
      try {
        const res = await api("PUT", `/api/alerts/${encodeURIComponent(a.id)}`, { enabled: sw.checked });
        Object.assign(a, res.alert || { enabled: sw.checked });
        toast(sw.checked ? `„${a.name}“ ist wieder aktiv.` : `„${a.name}“ ist pausiert.`);
        // Karte direkt anpassen statt neu zu rendern – der Fokus bleibt auf dem Schalter
        const on = a.enabled !== false;
        sw.checked = on;
        card.classList.toggle("paused", !on);
        swLabel.textContent = on ? "aktiv" : "pausiert";
        if (on) runBtn.removeAttribute("title"); else runBtn.title = PAUSED_HINT;
      } catch (e) {
        sw.checked = !sw.checked;
        toast(e.message, "error");
      } finally {
        sw.disabled = false;
        if (hadFocus) sw.focus();
      }
    });

    const card = el("article", { class: `alert-card${enabled ? "" : " paused"}` },
      el("div", { class: "alert-head" },
        el("div", { class: "alert-title-wrap" },
          el("h2", { class: "alert-title" }, a.name || a.id),
          el("p", { class: "alert-route" }, codesLabel(a.origins), " → ", codesLabel(a.destinations),
            pairs !== null ? el("span", { class: "muted" }, ` · ${plural(pairs, "Strecke", "Strecken")}`) : null)),
        el("label", { class: "switch" }, sw, el("span", { class: "switch-track", "aria-hidden": "true" }), swLabel)),
      el("ul", { class: "facts" },
        el("li", null, el("span", { class: "fact-k" }, "Preisgrenze"), el("span", { class: "fact-v" }, limit)),
        typeof a.baseline_price === "number" ? el("li", null, el("span", { class: "fact-k" }, "Normalpreis"), el("span", { class: "fact-v" }, euro(a.baseline_price))) : null,
        el("li", null, el("span", { class: "fact-k" }, "Kabine"), el("span", { class: "fact-v" }, CABIN[a.cabin || "economy"] || a.cabin)),
        el("li", null, el("span", { class: "fact-k" }, "Reiseart"), el("span", { class: "fact-v" }, roundtrip ? "Hin- und Rückflug" : "one-way")),
        el("li", null, el("span", { class: "fact-k" }, "Deals"), el("span", { class: `fact-v${a.active_deals ? " good" : ""}` }, dealsText))),
      alertBest(a),
      el("div", { class: "alert-actions" },
        runBtn,
        a.active_deals ? el("a", { class: "btn btn-small", href: "#deals", onclick: () => { state.dealFilter = a.id; } }, "Deals ansehen") : null,
        el("button", { type: "button", class: "btn btn-small", onclick: () => openAlertForm(a) }, "Bearbeiten"),
        el("button", { type: "button", class: "btn btn-small btn-danger-ghost", onclick: () => deleteAlert(a) }, "Löschen")));
    return card;
  }

  /** Bestpreis auf der Alarm-Karte; bei mehreren Strecken aufklappbar je Strecke. */
  function alertBest(a) {
    const c = a.cheapest;
    if (!c || !c.best) {
      return el("div", { class: "alert-best" }, el("span", { class: "fact-k" }, "Bestpreis jetzt"),
        el("span", { class: "muted" }, "noch keiner – erscheint nach der nächsten Prüfung"));
    }
    const e = c.best;
    const link = (x) => extLink(x.link || x.compare, x.kind === "article" ? "Artikel" : "ansehen", "inline-link");
    const routeLine = (x) => el("li", null, el("strong", null, euro(x.price)), " · ", ...joinDots(bestFacts(x)),
      el("span", { class: "muted" }, ` · ${sourceLabel(x)} · ${bestAge(x)}`), " ", link(x));
    const parts = [
      el("span", { class: "fact-k" }, "Bestpreis jetzt"),
      el("p", { class: "alert-best-price" }, el("strong", { class: "best-inline-price" }, euro(e.price)), " ", bestGap(a, e.price)),
      el("p", { class: "alert-best-facts" }, ...joinDots(bestFacts(e)), " ", link(e)),
      el("p", { class: "best-meta" }, `${sourceLabel(e)} · ${bestNote(e)} · ${bestAge(e)}`),
    ];
    if (c.routes && c.routes.length > 1) {
      const more = c.route_count > c.routes.length ? el("li", { class: "muted" }, `+ ${c.route_count - c.routes.length} weitere`) : null;
      parts.push(el("details", { class: "best-details" }, el("summary", null, `Je Strecke (${c.route_count})`),
        el("ul", { class: "best-routes" }, ...c.routes.map(routeLine), more)));
    }
    return el("div", { class: "alert-best" }, ...parts);
  }

  async function deleteAlert(a) {
    const ok = await confirmDialog("Alarm löschen?", `„${a.name || a.id}“ und alle zugehörigen Deals werden gelöscht. Das lässt sich nicht rückgängig machen.`, "Löschen");
    if (!ok) return;
    try {
      await api("DELETE", `/api/alerts/${encodeURIComponent(a.id)}`);
      toast(`„${a.name || a.id}“ wurde gelöscht.`);
      if (state.dealFilter === a.id) state.dealFilter = null;
      await loadAlerts();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  // ------------------------------------------------------------------ Bestätigungsdialog
  function confirmDialog(title, text, okLabel) {
    const dlg = $("#confirm-dialog");
    $("#confirm-title").textContent = title;
    $("#confirm-text").textContent = text;
    $("#confirm-ok").textContent = okLabel || "OK";
    dlg.returnValue = "";
    return new Promise((resolve) => {
      dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
      dlg.showModal();
      $("button[value=cancel]", dlg).focus();
    });
  }

  // ------------------------------------------------------------------ Chip-Eingabe (IATA / Regionen)
  function createChipInput(container, field) {
    const values = [];
    const inputId = `f-${field}-input`;
    const chipsEl = el("ul", { class: "chip-list", "aria-label": "Ausgewählt" });
    const input = el("input", {
      id: inputId, type: "text", list: "airport-list", autocomplete: "off", autocapitalize: "characters",
      spellcheck: "false", placeholder: "z. B. FRA", class: "chip-text-input", "aria-describedby": `${field}-chip-hint`,
    });
    const addBtn = el("button", { type: "button", class: "btn btn-small" }, "Hinzufügen");
    const hint = el("span", { id: `${field}-chip-hint`, class: "sr-only" }, "Enter oder Komma fügt den Code hinzu.");
    const regionBox = el("div", { class: "region-buttons", role: "group", "aria-label": "Regionen" });

    function render() {
      chipsEl.replaceChildren(...values.map((v, i) => el("li", { class: "tag" },
        el("span", null, v.startsWith("@") ? `${v} ${REGION_LABEL[v.slice(1)] || ""}`.trim() : (state.airports[v] ? `${v} ${state.airports[v]}` : v)),
        el("button", { type: "button", class: "tag-x", "aria-label": `${v} entfernen`, onclick: () => { values.splice(i, 1); render(); input.focus(); } }, "×"))));
      chipsEl.hidden = values.length === 0;
      for (const b of $$("button", regionBox)) b.setAttribute("aria-pressed", String(values.includes(b.dataset.code)));
    }
    function resolveCode(raw) {
      let s = raw.trim();
      if (!s) return null;
      const up = s.toUpperCase();
      if (/^@[A-Z_]+$/.test(up)) return up;
      if (/^[A-Z]{3}$/.test(up)) return up;
      // Stadtname eingetippt? -> Code suchen
      const hit = Object.entries(state.airports).find(([, name]) => name.toLowerCase() === s.toLowerCase());
      if (hit) return hit[0];
      const m = up.match(/^([A-Z]{3})\b/);
      return m ? m[1] : null;
    }
    function addFromInput() {
      const parts = input.value.split(/[,;]+/).map((p) => p.trim()).filter(Boolean);
      if (!parts.length) return true;
      const bad = [];
      for (const p of parts) {
        const code = resolveCode(p);
        if (!code) { bad.push(p); continue; }
        if (!values.includes(code)) values.push(code);
      }
      input.value = bad.join(", ");
      render();
      if (bad.length) { input.setCustomValidity(`Unbekannter Code: ${bad.join(", ")}`); input.reportValidity(); return false; }
      input.setCustomValidity("");
      return true;
    }
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addFromInput(); }
      else if (e.key === "Backspace" && !input.value && values.length) { values.pop(); render(); }
    });
    input.addEventListener("input", (e) => {
      input.setCustomValidity("");
      // Auswahl aus der Vorschlagsliste (datalist) sofort übernehmen
      const picked = !e.inputType || e.inputType === "insertReplacementText";
      const v = input.value.trim().toUpperCase();
      if (picked && state.airports[v]) addFromInput();
    });
    input.addEventListener("change", () => { if (input.value.trim()) addFromInput(); });
    addBtn.addEventListener("click", () => { addFromInput(); input.focus(); });

    function renderRegions() {
      regionBox.replaceChildren(...Object.keys(state.regions).map((r) => el("button", {
        type: "button", class: "chip chip-small", dataset: { code: `@${r}` }, "aria-pressed": "false",
        onclick: () => {
          const code = `@${r}`;
          const i = values.indexOf(code);
          if (i >= 0) values.splice(i, 1); else values.push(code);
          render();
        },
      }, el("span", { class: "chip-text" }, `@${r} ${REGION_LABEL[r] || ""}`.trim()), el("span", { class: "chip-count" }, String(state.regions[r].length)))));
      render();
    }

    container.replaceChildren(chipsEl, el("div", { class: "chip-entry" }, input, addBtn), hint, regionBox);
    renderRegions();
    return {
      get: () => values.slice(),
      set: (list) => { values.splice(0, values.length, ...(list || []).map((x) => String(x).toUpperCase())); input.value = ""; input.setCustomValidity(""); render(); },
      flush: addFromInput,
      renderRegions,
      focus: () => input.focus(),
      input,
    };
  }

  // ------------------------------------------------------------------ Alarm-Formular
  let chipFrom = null, chipTo = null, editing = null;

  async function ensureRegions() {
    if (Object.keys(state.airports).length) return;
    try {
      const data = await api("GET", "/api/regions");
      state.regions = data.regions || {};
      state.airports = data.airports || {};
      if (data.region_names && typeof data.region_names === "object") {
        for (const [k, v] of Object.entries(data.region_names)) if (typeof v === "string") REGION_LABEL[k] = v;
      }
      $("#airport-list").replaceChildren(...Object.entries(state.airports)
        .map(([code, name]) => el("option", { value: code, label: `${name} (${code})` })));
      if (chipFrom) { chipFrom.renderRegions(); chipTo.renderRegions(); }
    } catch (e) { /* Vorschläge sind optional */ }
  }

  // Zahlenfelder des Alarm-Formulars: Feld-ID → verständlicher Name für Fehlermeldungen
  const ALERT_NUM_FIELDS = {
    max_price: { id: "#f-max-price", label: "Preisgrenze" },
    baseline_price: { id: "#f-baseline", label: "Normalpreis" },
    min_discount: { id: "#f-discount", label: "Mindest-Rabatt", factor: 100, unit: " %" },
    max_stops: { id: "#f-stops", label: "Max. Umstiege", int: true },
    max_duration_hours: { id: "#f-duration", label: "Max. Flugdauer" },
    min_nights: { id: "#f-min-nights", label: "Nächte von", int: true },
    max_nights: { id: "#f-max-nights", label: "Nächte bis", int: true },
    months_ahead: { id: "#f-months", label: "Monate voraus", int: true },
  };
  function setNum(id, v, factor = 1) {
    const inp = $(id);
    inp.value = typeof v === "number" && isFinite(v) ? numDe(Math.round(v * factor * 100) / 100) : "";
  }
  /** Zahl aus dem Formular; prüft data-min/data-max (in Anzeige-Einheit). Wirft Error. */
  function getNum(key) {
    const f = ALERT_NUM_FIELDS[key];
    const inp = $(f.id);
    let n;
    try {
      n = parseNumber(inp.value, f.label, { int: !!f.int });
      checkRange(n, f.label, inp.dataset.min !== undefined ? Number(inp.dataset.min) : null,
        inp.dataset.max !== undefined ? Number(inp.dataset.max) : null, f.unit || "");
    } catch (e) { inp.focus(); throw e; }
    if (n === null) return null;
    return f.factor ? Math.round((n / f.factor) * 10000) / 10000 : n;
  }

  // Airline-Namen, die oft statt des Codes eingetippt werden
  const AIRLINE_CODES = {
    ryanair: "FR", wizzair: "W6", wizz: "W6", easyjet: "U2", eurowings: "EW", lufthansa: "LH",
    swiss: "LX", condor: "DE", austrian: "OS", austrianairlines: "OS", airchina: "CA",
    chinaeastern: "MU", chinasouthern: "CZ", hainan: "HU", hainanairlines: "HU",
    turkishairlines: "TK", pegasus: "PC", vueling: "VY", transavia: "HV", sunexpress: "XQ",
    klm: "KL", airfrance: "AF", britishairways: "BA", emirates: "EK", qatarairways: "QR",
  };
  function parseAirlines(raw) {
    const out = [], bad = [];
    const add = (c) => { if (!out.includes(c)) out.push(c); };
    for (const part of String(raw || "").split(/[,;\n]+/)) {
      const p = part.trim();
      if (!p) continue;
      const name = AIRLINE_CODES[p.toLowerCase().replace(/[\s.\-]+/g, "")];
      if (name) { add(name); continue; }
      for (const w of p.split(/\s+/)) {
        const up = w.toUpperCase();
        if (/^[A-Z0-9]{2}$/.test(up)) add(up); else bad.push(w);
      }
    }
    if (bad.length) {
      $("#f-airlines").focus();
      throw new Error(`„Ausgeschlossene Airlines“: bitte 2-stellige IATA-Codes wie FR, W6 (nicht „${bad.join(", ")}“).`);
    }
    return out;
  }

  async function openAlertForm(a) {
    editing = a;
    await ensureRegions();
    const form = $("#alert-form");
    form.reset();
    $("#alert-dialog-title").textContent = a ? "Alarm bearbeiten" : "Neuer Alarm";
    $("#alert-form-error").hidden = true;
    const info = $("#alert-id-info");
    info.hidden = !a;
    info.textContent = a ? `ID: ${a.id} (kann nicht geändert werden)` : "";
    $("#f-reverse-wrap").hidden = !!a;
    $("#f-reverse").checked = false;

    const v = a || {};
    $("#f-name").value = v.name || "";
    chipFrom.set(v.origins || []);
    chipTo.set(v.destinations || []);
    for (const r of $$("input[name=trip]", form)) r.checked = r.value === (v.trip || "roundtrip");
    $("#f-cabin").value = v.cabin || "economy";
    setNum("#f-max-price", v.max_price);
    setNum("#f-baseline", v.baseline_price);
    setNum("#f-discount", typeof v.min_discount === "number" ? v.min_discount : 0.3, 100);
    setNum("#f-stops", typeof v.max_stops === "number" ? v.max_stops : 1);
    setNum("#f-duration", v.max_duration_hours);
    setNum("#f-min-nights", v.min_nights);
    setNum("#f-max-nights", v.max_nights);
    setNum("#f-months", v.months_ahead);
    $("#f-airlines").value = (v.exclude_airlines || []).join(", ");
    $("#f-keywords").value = (v.feed_keywords || []).map((g) => (Array.isArray(g) ? g : [g]).join(", ")).join("\n");
    $("#f-enabled").checked = v.enabled !== false;
    $("#f-rotation").checked = v.rotation === true;

    $("#alert-dialog").showModal();
    $("#f-name").focus();
  }

  function formError(msg) {
    const box = $("#alert-form-error");
    box.textContent = msg;
    box.hidden = false;
    box.scrollIntoView({ block: "nearest" });
  }

  function collectAlert() {
    const name = $("#f-name").value.trim();
    if (!name) { $("#f-name").focus(); throw new Error("Bitte gib dem Alarm einen Namen."); }
    if (!chipFrom.flush() || !chipTo.flush()) throw new Error("Bitte nur gültige IATA-Codes (3 Buchstaben) oder Regionen verwenden.");
    const origins = chipFrom.get(), destinations = chipTo.get();
    if (!origins.length) { chipFrom.focus(); throw new Error("Bitte mindestens einen Abflugort (Von) angeben."); }
    if (!destinations.length) { chipTo.focus(); throw new Error("Bitte mindestens ein Ziel (Nach) angeben."); }

    const data = {
      name, origins, destinations,
      trip: ($("input[name=trip]:checked") || {}).value || "roundtrip",
      cabin: $("#f-cabin").value,
    };
    for (const key of Object.keys(ALERT_NUM_FIELDS)) data[key] = getNum(key);
    Object.assign(data, {
      exclude_airlines: parseAirlines($("#f-airlines").value),
      feed_keywords: $("#f-keywords").value.split(/\n+/)
        .map((line) => line.split(",").map((w) => w.trim()).filter(Boolean)).filter((g) => g.length),
      enabled: $("#f-enabled").checked,
      rotation: $("#f-rotation").checked,
    });
    if (data.max_price === null && data.baseline_price === null) throw new Error("Bitte eine Preisgrenze und/oder einen Normalpreis angeben.");
    if (data.max_price !== null && data.max_price <= 0) throw new Error("Die Preisgrenze muss größer als 0 sein.");
    if (data.min_nights !== null && data.max_nights !== null && data.min_nights > data.max_nights) throw new Error("„Nächte von“ ist größer als „Nächte bis“.");
    // Pflichtwerte ohne null-Variante
    if (data.min_discount === null) data.min_discount = 0.3;
    if (data.max_stops === null) data.max_stops = 1;
    return data;
  }

  const withoutNulls = (obj) => Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== null));

  async function submitAlert(ev) {
    ev.preventDefault();
    $("#alert-form-error").hidden = true;
    let data;
    try { data = collectAlert(); } catch (e) { formError(e.message); return; }
    const btn = $("#alert-save");
    btn.disabled = true;
    try {
      if (editing) {
        const res = await api("PUT", `/api/alerts/${encodeURIComponent(editing.id)}`, data);
        toast(`„${(res.alert && res.alert.name) || data.name}“ gespeichert.`);
      } else {
        const res = await api("POST", "/api/alerts", withoutNulls(data));
        const created = res.alert || {};
        let msg = `„${created.name || data.name}“ angelegt.`;
        if ($("#f-reverse").checked) {
          const back = withoutNulls({
            ...data, id: `${created.id}-retour`, name: `${data.name} (Gegenrichtung)`,
            origins: data.destinations, destinations: data.origins,
          });
          try {
            await api("POST", "/api/alerts", back);
            msg = `„${created.name || data.name}“ und Gegenrichtung angelegt.`;
          } catch (e) {
            toast(`Gegenrichtung konnte nicht angelegt werden: ${e.message}`, "error");
          }
        }
        toast(msg);
      }
      $("#alert-dialog").close();
      await loadAlerts();
    } catch (e) {
      formError(e.message);
    } finally {
      btn.disabled = false;
    }
  }

  // ------------------------------------------------------------------ Meldungen
  async function loadNotes() {
    renderNotifyBtn();
    const list = $("#note-list");
    if (!list.childElementCount) list.replaceChildren(el("li", { class: "loading" }, "Lade Meldungen …"));
    try {
      const data = await api("GET", "/api/notifications?limit=100");
      const notes = Array.isArray(data.notifications) ? data.notifications : [];
      updateMaxNoteId(notes);
      if (typeof data.unread === "number" && state.status && state.status.counts) {
        state.status.counts.unread = data.unread;
        state.lastUnread = data.unread;
        renderStatus();
      }
      renderNotes(notes);
    } catch (e) {
      list.replaceChildren(el("li", { class: "empty" }, `Meldungen konnten nicht geladen werden: ${e.message}`));
    }
  }

  function renderNotes(notes) {
    const list = $("#note-list");
    if (!notes.length) {
      list.replaceChildren(el("li", { class: "empty" },
        el("p", { class: "empty-title" }, "Noch keine Meldungen"),
        el("p", null, "Sobald ein Alarm einen Deal findet, erscheint er hier – egal ob du Telegram, ntfy oder einen Webhook eingerichtet hast.")));
      return;
    }
    // Fokus innerhalb der Liste merken (z. B. bei Aktualisierung nach einer Prüfung)
    const active = document.activeElement;
    const li = active && active.closest ? active.closest("#note-list li.note") : null;
    const keep = li ? { id: li.dataset.id, text: active.textContent } : null;
    list.replaceChildren(...notes.map(noteItem));
    if (keep) {
      const again = $(`#note-list li.note[data-id="${CSS.escape(keep.id)}"]`);
      const target = again && $$("a, button", again).find((x) => x.textContent === keep.text);
      if (target) target.focus();
    }
  }

  function noteItem(n) {
    const unread = !n.read;
    const markBtn = unread ? el("button", { type: "button", class: "btn btn-small btn-ghost", onclick: () => markRead([n.id]) }, "Als gelesen markieren") : null;
    const onOpen = unread ? () => markRead([n.id], true) : null;
    return el("li", { class: `note${unread ? " unread" : ""}`, dataset: { id: String(n.id) } },
      el("div", { class: "note-main" },
        el("div", { class: "badges" },
          unread ? el("span", { class: "dot", "aria-hidden": "true" }) : null,
          unread ? el("span", { class: "sr-only" }, "Ungelesen: ") : null,
          el("span", { class: "badge badge-outline" }, NOTE_STATUS[n.status] || n.status || "Meldung"),
          n.rating ? el("span", { class: `badge ${ratingClass(n.rating)}` }, n.rating) : null,
          el("span", { class: "note-time" }, fmtDateTime(n.created_at))),
        el("h2", { class: "note-title" }, n.headline || ""),
        el("p", { class: "deal-meta" }, n.alert_name || n.alert_id || ""),
        el("div", { class: "deal-actions" },
          withClick(extLink(n.link, "Zum Angebot", "btn btn-small btn-primary"), onOpen),
          withClick(extLink(n.compare, "Bei Google Flights gegenprüfen", "btn btn-small"), onOpen),
          markBtn)),
      typeof n.price === "number" && n.price > 0 ? el("div", { class: "note-price" }, euro(n.price)) : null);
  }
  function withClick(node, fn) { if (node && fn) node.addEventListener("click", fn); return node; }

  async function markRead(ids, quiet) {
    // Position merken, damit der Tastatur-Fokus danach sinnvoll weitergeht
    const items = $$("#note-list > li.note");
    const idx = ids && ids.length === 1 ? items.findIndex((x) => x.dataset.id === String(ids[0])) : -1;
    try {
      const res = await api("POST", "/api/notifications/read", ids ? { ids } : {});
      if (state.status && state.status.counts && typeof res.unread === "number") {
        state.status.counts.unread = res.unread;
        state.lastUnread = res.unread;
        renderStatus();
      }
      if (!quiet && !ids) toast("Alle Meldungen als gelesen markiert.");
      await loadNotes();
      if (!quiet) {
        const now = $$("#note-list > li.note");
        const next = now.slice(Math.max(0, idx)).concat(now.slice(0, Math.max(0, idx)))
          .map((x) => $("button.btn-ghost", x)).find(Boolean);
        if (next && idx >= 0) next.focus();
        else $("#h-meldungen").focus();
      }
    } catch (e) { toast(e.message, "error"); }
  }

  // ------------------------------------------------------------------ Einstellungen
  // type: text | url | secret | int | float | percent | lines | bool | select
  const SETTINGS = [
    { key: "travelpayouts_token", group: "travelpayouts", type: "secret", label: "Travelpayouts-Token", help: "Kostenlos – ohne Token gibt es keine Preise aus der API." },
    { key: "travelpayouts_marker", group: "travelpayouts", type: "text", label: "Travelpayouts-Marker (optional)", help: "Deine Partner-ID für Buchungslinks. Nicht nötig." },
    { key: "google_max_requests", group: "google", type: "int", label: "Max. Abfragen pro Lauf", min: 2, max: 500, help: "Gilt für alle Alarme zusammen, inkl. Gegenprüfungen (höchstens die Hälfte). Zwischen Abfragen wird 2–4 s gewartet." },
    { key: "google_verify", group: "google", type: "bool", label: "Deals aus Preis-APIs bei Google gegenprüfen", help: "Gecachte Preise (Travelpayouts, Amadeus-Monatsscan) werden mit genau diesen Daten bei Google geprüft: Ein Flug, der zu den Regeln des Alarms passt, höchstens 25 % teurer und unter der Grenze = bestätigt (gemeldet mit Google-Preis), sonst verworfen. Ist Google nicht erreichbar, wird der Deal mit Hinweis „nicht gegengeprüft“ gemeldet." },
    { key: "amadeus_api_key", group: "amadeus", type: "secret", label: "Amadeus API Key" },
    { key: "amadeus_api_secret", group: "amadeus", type: "secret", label: "Amadeus API Secret" },
    { key: "amadeus_env", group: "amadeus", type: "select", label: "Umgebung", options: [["test", "Test (wenig Daten)"], ["production", "Produktion"]] },
    { key: "amadeus_max_requests", group: "amadeus", type: "int", label: "Max. Abfragen pro Lauf", min: 1, max: 2000 },
    { key: "feed_interval_minutes", group: "feeds", type: "int", label: "Feed-Intervall (Minuten)", min: 10, max: 1440, help: "So oft werden nur die Deal-Seiten + Radar geprüft (zusätzlich zum vollen Lauf). Mindestens 10." },
    { key: "fast_interval_minutes", group: "feeds", type: "int", label: "Schnell-Intervall (Minuten)", min: 5, max: 1440, help: "So oft werden die schnellen Deal-Quellen (Secret Flying, Travel-Dealz schnell) + Radar geprüft. Mindestens 5." },
    { key: "feed_max_age_days", group: "feeds", type: "int", label: "Artikel max. Alter (Tage)", min: 1 },
    { key: "feeds", group: "feeds", type: "lines", label: "Eigene Feed-URLs (eine pro Zeile)", wide: true, help: "Weitere RSS-Feeds, z. B. die Kategorie-Feeds einer Deal-Seite." },
    { key: "hubs_enabled", group: "feeds", type: "bool", label: "Abflug ab Nachbar-Hub zulassen", help: "Business/First und Langstrecke: Deals ab Amsterdam, Budapest, Rom … zählen mit Aufschlag für die Anreise. Die Preisgrenze gilt für Ticket + Aufschlag." },
    { key: "hub_surcharge_a", group: "feeds", type: "float", label: "Aufschlag Zug/Auto (€)", min: 0, help: "Luxemburg, Brüssel, Amsterdam, Straßburg, Mailand, Prag, Kopenhagen. Schätzwert." },
    { key: "hub_surcharge_b", group: "feeds", type: "float", label: "Aufschlag Zubringerflug (€)", min: 0, help: "Budapest, Warschau, Oslo, Stockholm, Rom, Madrid, Barcelona, Athen, Istanbul, Helsinki. Schätzwert." },
    { key: "radar_enabled", group: "radar", type: "bool", label: "Radar aktiv" },
    { key: "radar_home_only", group: "radar", type: "bool", label: "Nur Abflug aus Deutschland, Österreich, Schweiz", help: "Aus = auch Abflüge ab Amsterdam, Mailand, Prag … melden." },
    { key: "radar_economy_max", group: "radar", type: "float", label: "Economy bis (€)", min: 0, help: "Bewusst niedrig – ob Kurz- oder Langstrecke, steht nicht im Artikel." },
    { key: "radar_economy_min", group: "radar", type: "float", label: "Economy ab (€)", min: 0, help: "Darunter sind es meist Kurzstrecken-Promos („Ryanair ab 14,99 €“)." },
    { key: "radar_premium_max", group: "radar", type: "float", label: "Premium Economy bis (€)", min: 0 },
    { key: "radar_business_max", group: "radar", type: "float", label: "Business Class bis (€)", min: 0 },
    { key: "radar_first_max", group: "radar", type: "float", label: "First Class bis (€)", min: 0 },
    { key: "interval_hours", group: "schedule", type: "float", label: "Prüfintervall (Stunden)", min: 0.5, step: 0.5, help: "Voller Lauf über alle Quellen. Mindestens 0,5. Zu häufiges Prüfen bringt kaum mehr Deals." },
    { key: "scan_months", group: "schedule", type: "int", label: "Monate voraus", min: 1, max: 12, help: "So viele Abflugmonate werden abgesucht." },
    { key: "webhook_url", group: "notify", type: "url", label: "Webhook-URL", placeholder: "https://…", help: "Für deinen späteren Benachrichtigungsserver (JSON per POST)." },
    { key: "webhook_token", group: "notify", type: "secret", label: "Webhook-Token", help: "Wird als „Authorization: Bearer …“ mitgeschickt." },
    { key: "ntfy_topic", group: "notify", type: "text", label: "ntfy-Topic", help: "Push aufs Handy mit der ntfy-App. Topic möglichst schwer zu erraten wählen." },
    { key: "ntfy_server", group: "notify", type: "url", label: "ntfy-Server", placeholder: "https://ntfy.sh" },
    { key: "telegram_bot_token", group: "notify", type: "secret", label: "Telegram Bot-Token" },
    { key: "telegram_chat_id", group: "notify", type: "text", label: "Telegram Chat-ID" },
    { key: "history_days", group: "advanced", type: "int", label: "Preis-Historie (Tage)", min: 1, help: "Zeitraum für den eigenen Referenzpreis (Median)." },
    { key: "min_history_samples", group: "advanced", type: "int", label: "Mindestanzahl Beobachtungen", min: 1, help: "Ab so vielen Preisen gilt der Median statt des Normalpreises." },
    { key: "renotify_drop", group: "advanced", type: "percent", label: "Erneut melden ab (% billiger)", min: 0, max: 90, step: 1, help: "Ein bekannter Deal wird nur erneut gemeldet, wenn er so viel billiger wird." },
    { key: "max_deals_per_alert", group: "advanced", type: "int", label: "Max. Meldungen pro Alarm und Lauf", min: 1 },
    { key: "request_delay", group: "advanced", type: "float", label: "Pause zwischen API-Anfragen (s)", min: 0, step: 0.05 },
  ];
  const SOURCE_STATE = {
    // Demo: "configured" ist dort immer true (Beispielpreise) - nicht "Token gesetzt" behaupten,
    // während das Token-Feld darunter "nicht gesetzt" zeigt
    travelpayouts: (c, demo) => (demo ? ["ok", "Demo: Beispielpreise, kein Token nötig"]
      : c ? ["ok", "Token gesetzt"] : ["warn", "Token fehlt – ohne Token keine Preise"]),
    google_flights: () => ["ok", "Kein Key nötig · inoffiziell, kann zeitweise blockiert sein"],
    amadeus: (c, demo) => (demo ? ["warn", "In der Demo nicht abgefragt"]
      : c ? ["warn", "Abgeschaltet seit 17.07.2026 · nur mit Enterprise-Key nutzbar"] : ["warn", "Abgeschaltet seit 17.07.2026 – nicht mehr verfügbar"]),
    feeds: (c) => (c ? ["ok", "Deal-Seiten ausgewählt"] : ["warn", "Keine Deal-Seite ausgewählt"]),
  };

  async function loadSettings() {
    try {
      const data = await api("GET", "/api/settings");
      state.settings = data;
      state.deleteSecrets.clear();
      renderSettings();
    } catch (e) {
      showSettingsError(`Einstellungen konnten nicht geladen werden: ${e.message}`);
    }
  }

  function envName(key) {
    const names = state.settings && state.settings.env_names;
    return names && names[key] ? ` (${names[key]})` : "";
  }

  function isLocked(key) {
    const s = state.settings;
    return !!(s && Array.isArray(s.env_locked) && s.env_locked.includes(key));
  }

  function renderSettings() {
    const s = state.settings || { values: {}, secrets: {}, env_locked: [] };
    const values = s.values || {}, secrets = s.secrets || {};
    $("#settings-error").hidden = true;
    for (const box of $$("#settings-form .field-grid[data-group]")) box.replaceChildren();
    for (const f of SETTINGS) {
      const known = f.type === "secret" ? f.key in secrets : f.key in values;
      if (!known) continue;
      const box = $(`#settings-form .field-grid[data-group="${f.group}"]`);
      box.append(settingField(f, values[f.key], secrets[f.key]));
    }
    // Quellen: Schalter je Karte + Zustand
    const srcLocked = isLocked("sources");
    const srcs = Array.isArray(values.sources) ? values.sources : [];
    for (const cb of $$("#sources-group input[name=src]")) {
      cb.checked = srcs.includes(cb.value);
      cb.disabled = srcLocked;
      setSourceCard(cb);
    }
    $("#sources-locked").hidden = !srcLocked;
    $("#sources-locked").textContent = `Quellen-Auswahl per Umgebungsvariable gesetzt${envName("sources")}`;
    renderSourceStates();
    renderPresets(values, s.presets);
  }

  // Zustand je Quelle (Token/Key gesetzt?) - kommt aus /api/status, das evtl. erst nach den
  // Einstellungen geladen ist -> auch aus renderStatus aufgerufen
  function renderSourceStates() {
    if (!state.status || !state.status.configured) return;
    const configured = state.status.configured;
    for (const p of $$("#sources-group .source-state")) {
      const id = p.dataset.state;
      const fn = SOURCE_STATE[id];
      const [cls, text] = fn ? fn(configured[id] === true, !!state.status.demo) : ["", ""];
      p.className = `source-state ${cls}`;
      p.textContent = text;
    }
  }

  function setSourceCard(cb) {
    const card = cb.closest(".source-card");
    if (!card) return;
    card.classList.toggle("is-off", !cb.checked);
    const txt = $(".switch-text", card);
    if (txt) txt.textContent = cb.checked ? "an" : "aus";
  }

  function renderPresets(values, presets) {
    const box = $("#feed-presets");
    const list = Array.isArray(presets) ? presets : [];
    const chosen = Array.isArray(values.feed_presets) ? values.feed_presets : [];
    const locked = isLocked("feed_presets");
    box.replaceChildren(...list.map((p) => {
      const cb = el("input", { type: "checkbox", name: "preset", value: p.id, checked: chosen.includes(p.id), disabled: locked });
      const link = safeUrl(p.homepage) ? el("a", { href: p.homepage, target: "_blank", rel: "noopener noreferrer" }, p.name) : el("strong", null, p.name);
      return el("label", { class: "check preset" }, cb, link,
        p.description ? el("span", { class: "help-inline" }, ` – ${p.description}`) : null);
    }));
    $("#presets-locked").hidden = !locked;
    $("#presets-locked").textContent = `per Umgebungsvariable gesetzt${envName("feed_presets")}`;
    box.closest(".field").hidden = !("feed_presets" in values);
  }

  function settingField(f, value, secret) {
    const id = `s-${f.key}`;
    const locked = isLocked(f.key);
    const wrap = el("div", { class: `field${f.wide ? " field-wide" : ""}${locked ? " is-locked" : ""}` });
    const helpId = `${id}-help`;
    let input;
    if (f.type === "lines") {
      input = el("textarea", { id, rows: "3", spellcheck: "false" });
      input.value = Array.isArray(value) ? value.join("\n") : (value || "");
    } else if (f.type === "bool") {
      input = el("input", { id, type: "checkbox", role: "switch" });
      input.checked = value === true;
    } else if (f.type === "select") {
      input = el("select", { id }, ...(f.options || []).map(([v, label]) => el("option", { value: v }, label)));
      input.value = value === null || value === undefined ? "" : String(value);
    } else if (f.type === "secret") {
      const isSet = !!(secret && secret.set);
      input = el("input", { id, type: "password", autocomplete: "new-password", spellcheck: "false",
        placeholder: isSet ? `gesetzt ${secret.hint || ""}`.trim() : "nicht gesetzt" });
    } else {
      const num = ["int", "float", "percent"].includes(f.type);
      // Zahlen bewusst als Textfeld: type=number verfälscht "99,5" je nach Browser still zu 995
      input = el("input", { id, type: f.type === "url" ? "url" : "text", spellcheck: "false",
        autocomplete: "off", inputmode: num ? (f.type === "int" ? "numeric" : "decimal") : null,
        placeholder: f.placeholder || null });
      if (f.type === "percent") input.value = typeof value === "number" ? numDe(Math.round(value * 1000) / 10) : "";
      else if (num) input.value = typeof value === "number" ? numDe(value) : "";
      else input.value = value === null || value === undefined ? "" : String(value);
    }
    input.dataset.key = f.key;
    input.dataset.type = f.type;
    if (locked) input.disabled = true;
    const helps = [];
    if (locked) helps.push(el("p", { class: "help locked-hint" }, `per Umgebungsvariable gesetzt${envName(f.key)}`));
    if (f.help) helps.push(el("p", { class: "help", id: helpId }, f.help));
    if (f.help) input.setAttribute("aria-describedby", helpId);
    if (f.type === "bool") {
      wrap.classList.add("field-bool");
      wrap.append(el("label", { class: "switch" }, input, el("span", { class: "switch-track", "aria-hidden": "true" }), el("span", null, f.label)));
    } else {
      wrap.append(el("label", { for: id }, f.label), input);
    }
    if (f.type === "secret") {
      const isSet = !!(secret && secret.set);
      wrap.append(el("p", { class: `secret-state${isSet ? " is-set" : ""}` }, isSet ? `✓ gesetzt ${secret.hint || ""} – leer lassen = unverändert` : "– nicht gesetzt"));
      if (isSet && !locked) {
        const cb = el("input", { type: "checkbox", "data-delete": f.key });
        cb.addEventListener("change", () => { if (cb.checked) state.deleteSecrets.add(f.key); else state.deleteSecrets.delete(f.key); input.disabled = cb.checked; });
        wrap.append(el("label", { class: "check check-small" }, cb, " Wert löschen"));
      }
    }
    wrap.append(...helps);
    return wrap;
  }

  function showSettingsError(msg) {
    const box = $("#settings-error");
    box.textContent = msg;
    box.hidden = false;
  }

  function collectSettings() {
    const s = state.settings || { values: {} };
    const values = {}, secrets = {};
    for (const inp of $$("#settings-form [data-key]")) {
      const key = inp.dataset.key, type = inp.dataset.type;
      const f = SETTINGS.find((x) => x.key === key);
      if (isLocked(key)) continue;
      if (type === "secret") {
        if (state.deleteSecrets.has(key)) secrets[key] = "";
        else if (inp.value.trim()) secrets[key] = inp.value.trim();
        continue;
      }
      if (type === "lines") { values[key] = inp.value.split(/\n+/).map((x) => x.trim()).filter(Boolean); continue; }
      if (type === "bool") { values[key] = !!inp.checked; continue; }
      if (type === "select") { values[key] = inp.value; continue; }
      if (type === "int" || type === "float" || type === "percent") {
        let n;
        try {
          n = parseNumber(inp.value, f.label, { int: type === "int" });
          if (n === null) throw new Error(`„${f.label}“: bitte eine Zahl eingeben.`);
          checkRange(n, f.label, f.min, f.max, type === "percent" ? " %" : "");
        } catch (e) { inp.focus(); throw e; }
        if (type === "percent") n = Math.round(n * 10) / 1000;
        values[key] = n;
        continue;
      }
      const v = inp.value.trim();
      if (type === "url" && v && !/^https?:\/\//i.test(v)) throw new Error(`„${f.label}“ muss mit http:// oder https:// beginnen.`);
      values[key] = v;
    }
    if ("sources" in (s.values || {}) && !isLocked("sources")) {
      values.sources = $$("#sources-group input[name=src]").filter((cb) => cb.checked).map((cb) => cb.value);
    }
    if ("feed_presets" in (s.values || {}) && !isLocked("feed_presets")) {
      values.feed_presets = $$("#feed-presets input[name=preset]").filter((cb) => cb.checked).map((cb) => cb.value);
    }
    return { values, secrets };
  }

  async function saveSettings(ev) {
    ev.preventDefault();
    $("#settings-error").hidden = true;
    let body;
    try { body = collectSettings(); } catch (e) { showSettingsError(e.message); toast(e.message, "error"); return; }
    const btn = $("#settings-save");
    btn.disabled = true;
    try {
      const data = await api("PUT", "/api/settings", body);
      state.settings = data;
      state.deleteSecrets.clear();
      renderSettings();
      toast("Einstellungen gespeichert.");
      pollStatus();
    } catch (e) {
      showSettingsError(e.message);
      toast(`Speichern fehlgeschlagen: ${e.message}`, "error");
    } finally {
      btn.disabled = false;
    }
  }

  async function testNotify() {
    const btn = $("#test-notify-btn");
    const out = $("#test-notify-result");
    btn.disabled = true;
    out.replaceChildren(el("li", { class: "loading" }, "Sende Testmeldung …"));
    try {
      const data = await api("POST", "/api/test-notify", {});
      const ch = Array.isArray(data.channels) ? data.channels : [];
      out.replaceChildren(...ch.map((c) => el("li", { class: c.ok ? "ok" : "fail" },
        el("span", { class: "ch-icon", "aria-hidden": "true" }, c.ok ? "✓" : "✗"),
        el("strong", null, CHANNEL[c.name] || c.name), c.ok ? " – gesendet" : ` – fehlgeschlagen${c.error ? `: ${c.error}` : ""}`)));
      if (!ch.length) out.replaceChildren(el("li", null, "Kein Kanal geantwortet."));
      const bad = ch.filter((c) => !c.ok).length;
      toast(bad ? `Testmeldung: ${bad} Kanal/Kanäle fehlgeschlagen.` : "Testmeldung gesendet.", bad ? "error" : "ok");
      pollStatus();
    } catch (e) {
      out.replaceChildren(el("li", { class: "fail" }, e.message));
      toast(e.message, "error");
    } finally { btn.disabled = false; }
  }

  async function loadRuns() {
    const box = $("#runs-table");
    try {
      const data = await api("GET", "/api/runs?limit=20");
      renderRuns(Array.isArray(data.runs) ? data.runs : []);
    } catch (e) {
      box.replaceChildren(el("p", { class: "help" }, `Läufe konnten nicht geladen werden: ${e.message}`));
    }
  }

  function renderRuns(runs) {
    const box = $("#runs-table");
    if (!runs.length) { box.replaceChildren(el("p", { class: "help" }, "Noch keine Prüfung gelaufen.")); return; }
    const alertName = (id) => { const a = state.alerts.find((x) => x.id === id); return a ? a.name : id; };
    const cols = ["Start", "Auslöser", "Alarm", "Dauer", "Angebote", "Deals", "Radar", "Gemeldet", "Abgelaufen", "Fehler"];
    const dur = (r) => {
      const a = parseIso(r.started_at), b = parseIso(r.finished_at);
      if (!a) return "";
      if (!b) return "läuft …";
      const s = Math.max(0, Math.round((b - a) / 1000));
      return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
    };
    const td = (label, ...content) => el("td", { "data-label": label }, ...content);
    const rows = runs.map((r) => {
      const errs = Array.isArray(r.errors) ? r.errors : [];
      return el("tr", { class: errs.length ? "has-errors" : null },
        td("Start", fmtDateTime(r.started_at)),
        td("Auslöser", TRIGGER[r.trigger] || r.trigger || ""),
        td("Alarm", r.alert_id ? alertName(r.alert_id) : "alle"),
        td("Dauer", dur(r)),
        td("Angebote", String(r.offers ?? "")),
        td("Deals", String(r.deals ?? ""), r.rejected ? el("span", { class: "muted" }, ` (${r.rejected} verworfen)`) : null),
        td("Radar", String(r.radar ?? 0)),
        td("Gemeldet", String(r.notified ?? "")),
        td("Abgelaufen", String(r.expired ?? "")),
        td("Fehler", errs.length ? el("ul", { class: "err-list" }, ...errs.map((x) => el("li", null, x))) : el("span", { class: "muted" }, "keine")));
    });
    box.replaceChildren(el("table", { class: "runs" },
      el("thead", null, el("tr", null, ...cols.map((c) => el("th", { scope: "col" }, c)))),
      el("tbody", null, ...rows)));
  }

  // ------------------------------------------------------------------ Start
  function init() {
    chipFrom = createChipInput($("#f-origins"), "origins");
    chipTo = createChipInput($("#f-destinations"), "destinations");

    $("#run-btn").addEventListener("click", () => startRun(null));
    $("#run-feeds-btn").addEventListener("click", () => startRun(null, "feeds"));
    for (const cb of $$("#sources-group input[name=src]")) cb.addEventListener("change", () => setSourceCard(cb));
    // Sprunglink: nur fokussieren – "#main" als Hash würde sonst auf die Deals-Seite wechseln
    $(".skip-link").addEventListener("click", (e) => { e.preventDefault(); $("#main").focus(); });
    $("#show-expired").addEventListener("change", (e) => setShowExpired(e.target.checked));
    // Bestpreise auf-/zugeklappt merken (pro Browser)
    const bestToggle = $("#best-toggle");
    try { if (localStorage.getItem("dm.bestOpen") === "0") bestToggle.open = false; } catch (e) { /* egal */ }
    bestToggle.addEventListener("toggle", () => {
      try { localStorage.setItem("dm.bestOpen", bestToggle.open ? "1" : "0"); } catch (e) { /* egal */ }
    });
    $("#new-alert-btn").addEventListener("click", () => openAlertForm(null));
    $("#alert-form").addEventListener("submit", submitAlert);
    for (const b of $$("#alert-dialog [data-close]")) b.addEventListener("click", () => $("#alert-dialog").close());
    $("#mark-all-read-btn").addEventListener("click", () => markRead(null));
    $("#desktop-notify-btn").addEventListener("click", async () => {
      try {
        const p = await Notification.requestPermission();
        if (p === "granted") {
          state.notifiedNoteId = Math.max(state.notifiedNoteId, state.maxNoteId);
          toast("Desktop-Benachrichtigungen aktiv, solange dieser Tab offen ist.");
          new Notification("Deal-Monitor", { body: "So sehen Benachrichtigungen aus." });
        } else toast("Benachrichtigungen wurden nicht erlaubt.", "info");
      } catch (e) { toast("Dein Browser unterstützt das leider nicht.", "error"); }
      renderNotifyBtn();
    });
    $("#settings-form").addEventListener("submit", saveSettings);
    $("#test-notify-btn").addEventListener("click", testNotify);
    window.addEventListener("hashchange", showRoute);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) pollStatus(); });

    state.route = currentRoute();
    pollStatus();
    ensureRegions().then(() => { if (state.route === "alarme") renderAlerts(); });
    api("GET", "/api/notifications?limit=1").then((d) => {
      updateMaxNoteId(d.notifications);
      state.notifiedNoteId = Math.max(state.notifiedNoteId, state.maxNoteId);
    }).catch(() => {});
    showRoute();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
