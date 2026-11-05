// End-to-End-Test der Weboberfläche (Node + Playwright, Chromium).
//
//   node tests/e2e/ui.mjs
//
// Startet `python3 -m dealmonitor serve --demo --no-scheduler` auf einem freien Port
// (temporäres Datenverzeichnis, Beispieldaten), klickt alle Abläufe durch und
// beendet den Server am Ende wieder. Kein Teil von `python3 -m unittest`.
//
// Umgebungsvariablen:
//   E2E_SHOTS=<ordner>   Screenshots dorthin schreiben (Standard: tests/e2e/screenshots)
//   E2E_HEADED=1         Browser sichtbar starten
//   PYTHON=python3       Python-Interpreter

import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import http from "node:http";
import net from "node:net";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const SHOTS = path.resolve(process.env.E2E_SHOTS || path.join(ROOT, "tests", "e2e", "screenshots"));
const PYTHON = process.env.PYTHON || "python3";
mkdirSync(SHOTS, { recursive: true });

// ------------------------------------------------------------------ Hilfen
const results = [];
let failures = 0;
const consoleErrors = [];
let allowedHttpErrors = []; // erwartete 4xx-Antworten (Chrome loggt sie als Konsolenfehler)

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

async function step(name, fn) {
  const t0 = Date.now();
  try {
    await fn();
    results.push(["ok", name]);
    console.log(`  ✓ ${name} (${Date.now() - t0} ms)`);
  } catch (e) {
    failures += 1;
    results.push(["FEHLER", name, e.message]);
    console.log(`  ✗ ${name}\n      ${String(e.message).split("\n").join("\n      ")}`);
    if (currentPage) {
      const file = path.join(SHOTS, `e2e-fail-${name.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.png`);
      await currentPage.screenshot({ path: file, fullPage: true }).catch(() => {});
    }
  }
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(fn, { timeout = 15000, interval = 200, msg = "Zeitüberschreitung" } = {}) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    try {
      last = await fn();
      if (last) return last;
    } catch (e) { last = e; }
    await sleep(interval);
  }
  throw new Error(`${msg}${last instanceof Error ? `: ${last.message}` : ""}`);
}

const norm = (s) => String(s || "").replace(/\s+/g, " ").trim();

// ------------------------------------------------------------------ Server
let server = null;
let base = "";
let serverLog = "";

async function startServer() {
  const port = await freePort();
  base = `http://127.0.0.1:${port}`;
  const env = { ...process.env, PYTHONUNBUFFERED: "1" };
  delete env.DEALMONITOR_PASSWORD; // Test braucht freien Zugriff
  server = spawn(PYTHON, ["-m", "dealmonitor", "serve", "--demo", "--no-scheduler",
    "--host", "127.0.0.1", "--port", String(port)], { cwd: ROOT, env, stdio: ["ignore", "pipe", "pipe"] });
  server.stdout.on("data", (d) => { serverLog += d; });
  server.stderr.on("data", (d) => { serverLog += d; });
  let exited = null;
  server.on("exit", (code) => { exited = code; });
  await waitFor(async () => {
    if (exited !== null) throw new Error(`Server beendet (Code ${exited}):\n${serverLog}`);
    const s = await api("GET", "/api/status");
    return s && !s.running && s.last_run && s.last_run.finished_at;
  }, { timeout: 30000, msg: "Server startet nicht" });
}

async function stopServer() {
  if (!server || server.exitCode !== null) return;
  server.kill("SIGTERM");
  const done = await Promise.race([new Promise((r) => server.once("exit", () => r(true))), sleep(5000).then(() => false)]);
  if (!done) server.kill("SIGKILL");
}

async function api(method, p, body) {
  const res = await fetch(base + p, {
    method, headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) { const e = new Error(`${method} ${p} → ${res.status} ${data && data.error}`); e.status = res.status; throw e; }
  return data;
}

// Kleiner Webhook-Empfänger (lokal) für die Testmeldung
const hooks = [];
let hookUrl = "";
const hookServer = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    let json = null;
    try { json = JSON.parse(body); } catch (e) { /* bleibt null */ }
    hooks.push({ method: req.method, url: req.url, auth: req.headers.authorization || null, json });
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end('{"ok":true}');
  });
});

// ------------------------------------------------------------------ Browser-Hilfen
let currentPage = null;

function watchConsole(page, label) {
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const text = m.text();
    const http4xx = text.match(/status of (\d{3})/);
    if (http4xx) {
      const i = allowedHttpErrors.indexOf(Number(http4xx[1]));
      if (i >= 0) { allowedHttpErrors.splice(i, 1); return; }
    }
    consoleErrors.push(`[${label}] ${text}`);
  });
  page.on("pageerror", (e) => consoleErrors.push(`[${label}] pageerror: ${e.message}`));
  page.on("dialog", (d) => { consoleErrors.push(`[${label}] unerwarteter Browser-Dialog: ${d.message()}`); d.dismiss(); });
}

async function noHScroll(page, where) {
  const w = await page.evaluate(() => ({
    sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
    bsw: document.body.scrollWidth,
  }));
  assert(w.sw <= w.cw && w.bsw <= w.cw, `Horizontales Scrollen in ${where}: scrollWidth ${w.sw}/${w.bsw} > clientWidth ${w.cw}`);
}

async function shot(page, name, fullPage = true) {
  if (fullPage) await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(150);
  await page.screenshot({ path: path.join(SHOTS, `e2e-${name}.png`), fullPage });
}

async function go(page, route) {
  await page.click(`.main-nav a[data-route="${route}"]`);
  await page.waitForSelector(`#view-${route}:not([hidden])`);
}

async function waitToast(page, re, timeout = 15000) {
  await page.waitForFunction((src) => {
    const r = new RegExp(src);
    return [...document.querySelectorAll("#toasts .toast-msg")].some((t) => r.test(t.textContent));
  }, re.source, { timeout });
}

async function alertCard(page, name) {
  const card = page.locator(".alert-card").filter({ has: page.locator(".alert-title", { hasText: new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`) }) });
  await card.first().waitFor({ timeout: 5000 });
  return card.first();
}

async function addChip(page, field, text) {
  await page.fill(`#f-${field}-input`, text);
  await page.press(`#f-${field}-input`, "Enter");
}

// ------------------------------------------------------------------ Ablauf
async function main() {
  await new Promise((r) => hookServer.listen(0, "127.0.0.1", r));
  hookUrl = `http://127.0.0.1:${hookServer.address().port}/hook`;

  console.log("Starte Demo-Server …");
  await startServer();
  console.log(`Server: ${base}`);

  const browser = await chromium.launch({ headless: process.env.E2E_HEADED !== "1" });
  try {
    await desktopFlows(browser);
    await mobileFlows(browser);
    await darkFlows(browser);
  } finally {
    await browser.close();
  }
}

async function desktopFlows(browser) {
  console.log("\nDesktop (1280 px):");
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1, locale: "de-DE", colorScheme: "light" });
  const page = await ctx.newPage();
  currentPage = page;
  watchConsole(page, "desktop");
  const alertsBefore = (await api("GET", "/api/alerts")).alerts;

  await step("Deals-Ansicht zeigt Demo-Deals", async () => {
    await page.goto(base + "/");
    await page.waitForSelector(".deal-card");
    const n = await page.locator(".deal-card").count();
    const apiN = (await api("GET", "/api/deals")).deals.length;
    assert(n > 0 && n === apiN, `Karten ${n}, API ${apiN}`);
    assert(await page.isVisible("#demo-banner"), "Demo-Banner fehlt");
    assert(/DEMO/.test(await page.textContent("#demo-banner")), "Demo-Text fehlt");
    assert(!(await page.isVisible("#onboarding-banner")), "Onboarding-Banner sollte im Demo-Modus fehlen");
    assert(/Letzte Prüfung/.test(await page.textContent("#status-line")), "Statuszeile ohne letzte Prüfung");
    const first = page.locator(".deal-card").first();
    assert(await first.locator(".price").isVisible(), "Preis fehlt");
    assert(/\d{2}\.\d{2}\.\d{4}/.test(await page.locator("#deal-list").textContent()), "Datum TT.MM.JJJJ fehlt");
    assert(await page.locator(".deal-card a:has-text('Bei Google Flights gegenprüfen')").count() > 0, "Google-Flights-Link fehlt");
    assert(await page.locator(".deal-card a:has-text('Zum Angebot')").count() > 0, "Angebotslink fehlt");
    const hrefs = await page.$$eval(".deal-card a", (as) => as.map((a) => a.getAttribute("href")));
    assert(hrefs.every((h) => /^https?:\/\//.test(h)), `Unsichere Links: ${hrefs.filter((h) => !/^https?:/.test(h))}`);
    await noHScroll(page, "Deals");
    await shot(page, "desktop-deals");
  });

  await step("Deals: Radar-Badge, Google-bestätigt, Quelle im Klartext", async () => {
    const radarCards = page.locator(".deal-card.is-radar");
    const apiRadar = (await api("GET", "/api/deals?alert_id=radar")).deals;
    assert(apiRadar.length >= 2, `Demo ohne Radar-Beispiele (${apiRadar.length})`);
    assert(await radarCards.count() === apiRadar.length, `Radar-Karten ${await radarCards.count()} ≠ ${apiRadar.length}`);
    const badge = radarCards.first().locator(".badge.r-radar");
    assert(norm(await badge.textContent()) === "Radar", "Radar-Badge fehlt");
    const bg = await badge.evaluate((e) => getComputedStyle(e).backgroundColor);
    assert(bg === "rgb(180, 83, 9)", `Radar-Badge nicht amber: ${bg}`);
    assert(/Error Fare/.test(await page.locator("#deal-list").textContent()), "Error-Fare-Beispiel fehlt");
    const verified = page.locator(".deal-card.is-verified");
    assert(await verified.count() >= 1, "Keine Google-bestätigte Karte in der Demo");
    assert(norm(await verified.first().locator(".badge-verified").textContent()) === "✓ Google Flights bestätigt", "Bestätigt-Badge falsch");
    assert(/Google Flights bestätigt: \d+\s€/.test(await verified.first().locator(".reasons").textContent()), "Bestätigt-Grund fehlt");
    assert(/nicht gegengeprüft/.test(await page.locator("#deal-list").textContent()), "Hinweis „nicht gegengeprüft“ fehlt");
    const metas = (await page.$$eval(".deal-card .deal-meta", (els) => els.map((e) => e.textContent))).join("\n");
    assert(/Quelle: Travel-Dealz-Artikel/.test(metas) && /Quelle: Travelpayouts \(Preis-API\)/.test(metas), `Quellen-Klartext fehlt: ${metas}`);
    assert(!/feed:|travel-dealz-feed|google_flights/.test(metas), `Interne Quellen-Namen sichtbar: ${metas}`);
  });

  await step("Filter-Chip pro Alarm", async () => {
    const chips = page.locator("#deal-filters .chip");
    assert(await chips.count() === alertsBefore.length + 2, `Chips ${await chips.count()} ≠ ${alertsBefore.length + 2} (Alle + Alarme + Radar)`);
    assert(norm(await chips.first().textContent()) === "Alle", "Erster Chip ist nicht „Alle“");
    assert(/^Radar/.test(norm(await chips.last().textContent())), "Letzter Chip ist nicht „Radar“");
    let checked = 0;
    for (const a of alertsBefore) {
      const chip = chips.filter({ hasText: a.name });
      await chip.click();
      await page.waitForFunction((name) => {
        const c = [...document.querySelectorAll("#deal-filters .chip")].find((x) => x.textContent.includes(name));
        return c && c.getAttribute("aria-pressed") === "true";
      }, a.name);
      const expected = (await api("GET", `/api/deals?alert_id=${encodeURIComponent(a.id)}`)).deals.length;
      // gleiche Kartenzahl wie beim vorigen Filter: auch auf die neuen Karten warten
      await waitFor(async () => ((await page.locator(".deal-card").count()) === expected
        && (await page.$$eval(".deal-card .deal-meta", (els, n) => els.every((e) => e.textContent.includes(n)), a.name)))
        || (expected === 0 && await page.locator("#deal-list .empty").count() === 1),
      { timeout: 5000, msg: `Filter ${a.id}: erwartet ${expected} Karten` });
      if (expected) {
        const metas = await page.$$eval(".deal-card .deal-meta", (els) => els.map((e) => e.textContent));
        assert(metas.every((m) => m.includes(a.name)), `Filter ${a.id} zeigt fremde Deals`);
        const chipCount = norm(await chip.locator(".chip-count").textContent());
        assert(Number(chipCount) === a.active_deals, `Chip-Zähler ${chipCount} ≠ ${a.active_deals}`);
        checked += 1;
      }
    }
    assert(checked >= 2, "Weniger als zwei Alarme mit Deals");
    await chips.first().click();
    const all = (await api("GET", "/api/deals")).deals.length;
    await waitFor(async () => (await page.locator(".deal-card").count()) === all, { msg: "„Alle“ zeigt nicht alle Deals" });
  });

  await step("Radar-Chip filtert und zählt", async () => {
    const st = await api("GET", "/api/status");
    const chip = page.locator('#deal-filters .chip[data-id="radar"]');
    assert(Number(norm(await chip.locator(".chip-count").textContent())) === st.counts.radar_deals, "Radar-Zähler ≠ status.counts.radar_deals");
    await chip.click();
    await page.waitForFunction(() => document.querySelector('#deal-filters .chip[data-id="radar"]').getAttribute("aria-pressed") === "true");
    const expected = (await api("GET", "/api/deals?alert_id=radar")).deals.length;
    await waitFor(async () => (await page.locator(".deal-card").count()) === expected && (await page.locator(".deal-card.is-radar").count()) === expected,
      { timeout: 5000, msg: `Radar-Filter: erwartet ${expected} Radar-Karten` });
    const metas = await page.$$eval(".deal-card .deal-meta", (els) => els.map((e) => e.textContent));
    assert(metas.every((m) => m.startsWith("Radar: Hammer-Deals")), `Radar-Filter zeigt fremde Deals: ${metas}`);
    await shot(page, "desktop-deals-radar");
    await page.locator('#deal-filters .chip[data-id=""]').click();
    await waitFor(async () => (await page.locator(".deal-card").count()) === (await api("GET", "/api/deals")).deals.length, { msg: "„Alle“ nach Radar" });
  });

  await step("Bestpreise je Alarm – auch ohne Deal, Filter, Einklappen", async () => {
    const alerts = (await api("GET", "/api/alerts")).alerts;
    const withBest = alerts.filter((a) => a.cheapest && a.cheapest.best);
    assert(withBest.length >= 3, `Demo mit zu wenigen Bestpreisen: ${withBest.length}`);
    assert(await page.isVisible("#best-prices"), "Bestpreis-Panel fehlt");
    const rows = page.locator("#best-list .best-row");
    const shown = alerts.filter((a) => a.enabled !== false || a.cheapest);
    assert(await rows.count() === shown.length, `Bestpreis-Zeilen ${await rows.count()} ≠ ${shown.length}`);
    const de = (v) => new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(v).replace(/\s/g, " ");
    for (const a of withBest) {
      const row = rows.filter({ has: page.locator(".best-alert", { hasText: a.name }) });
      const txt = norm(await row.textContent());
      assert(txt.includes(de(a.cheapest.best.price)), `${a.id}: Preis ${de(a.cheapest.best.price)} fehlt: ${txt}`);
      assert(/Quelle: /.test(txt) && /(Stand|veröffentlicht) (gerade|vor)/.test(txt), `${a.id}: Quelle/Stand fehlt: ${txt}`);
      assert(/(über|unter) deiner Grenze/.test(txt), `${a.id}: Abstand zur Grenze fehlt: ${txt}`);
      assert(!/feed:|google_flights|travelpayouts\b/.test(txt), `${a.id}: interner Quellenname: ${txt}`);
    }
    const empty = alerts.find((a) => !a.cheapest && a.enabled !== false);
    if (empty) {
      const row = rows.filter({ has: page.locator(".best-alert", { hasText: empty.name }) });
      assert(/Noch kein passender Preis/.test(await row.textContent()), "Leerer Bestpreis ohne Hinweis");
    }
    const hrefs = await page.$$eval("#best-list a", (as) => as.map((a) => a.getAttribute("href")));
    assert(hrefs.length && hrefs.every((h) => /^https?:\/\//.test(h)), `Unsichere Bestpreis-Links: ${hrefs}`);
    // Filter: ein Alarm -> eine Zeile; Radar -> Panel weg
    const one = withBest[0];
    await page.locator("#deal-filters .chip").filter({ hasText: one.name }).click();
    await waitFor(async () => (await rows.count()) === 1, { msg: "Alarm-Filter zeigt nicht genau eine Bestpreis-Zeile" });
    await page.locator('#deal-filters .chip[data-id="radar"]').click();
    await waitFor(async () => !(await page.isVisible("#best-prices")), { msg: "Radar-Filter zeigt Bestpreise" });
    await page.locator('#deal-filters .chip[data-id=""]').click();
    await waitFor(async () => (await rows.count()) === shown.length, { msg: "„Alle“ zeigt nicht alle Bestpreise" });
    // Einklappen bleibt nach Reload
    await page.click("#best-toggle > summary");
    assert(!(await page.isVisible("#best-list")), "Einklappen wirkt nicht");
    await page.reload();
    await page.waitForSelector(".deal-card");
    assert(!(await page.isVisible("#best-list")), "Eingeklappt-Zustand nicht gemerkt");
    await page.click("#best-toggle > summary");
    assert(await page.isVisible("#best-list"), "Aufklappen wirkt nicht");
    await noHScroll(page, "Deals mit Bestpreisen");
    await shot(page, "desktop-bestpreise");
  });

  await step("Deal-Karte: Google-Preisniveau, 62-Tage-Verlauf, Quellen, Nachbar-Hub, Ablauf", async () => {
    await page.goto(base + "/#deals");
    await page.waitForSelector(".deal-card");
    const deals = (await api("GET", "/api/deals")).deals;
    const withHistory = deals.filter((d) => d.insight && Array.isArray(d.insight.history) && d.insight.history.length >= 2);
    assert(withHistory.length >= 2, `Demo mit zu wenig Preisverläufen: ${withHistory.length}`);
    assert(await page.locator("#deal-list .deal-card .spark").count() === withHistory.length, "Verlauf fehlt auf Karten");
    // Zürich → Hamburg 92 €: Google bestätigt, Preise niedrig, Band 160–230 €
    const card = page.locator(".deal-card.is-verified").first();
    const spark = card.locator(".spark");
    assert(/Preisverlauf bei Google · 62 Tage/.test(norm(await spark.textContent())), "Titel des Verlaufs fehlt");
    assert(norm(await spark.locator(".badge.lvl-niedrig").textContent()) === "Google: Preise niedrig · 97 € unter Normalpreis", "Preisniveau-Badge falsch");
    assert(norm(await spark.locator(".key-band").textContent()) === "typisch 160–230 €", "Band-Legende falsch");
    assert(norm(await spark.locator(".key-dot").textContent()) === "dieses Angebot 92 €", "Angebots-Marke fehlt");
    const d = await spark.locator("path.spark-line").getAttribute("d");
    assert((d.match(/[ML]/g) || []).length === 62, `Linie hat nicht 62 Punkte: ${d.slice(0, 40)}`);
    const band = await spark.locator("rect.spark-band").evaluate((r) => [Number(r.getAttribute("y")), Number(r.getAttribute("height"))]);
    assert(band[1] > 0 && band[0] >= 0 && band[0] + band[1] <= 40, `Band außerhalb: ${band}`);
    // Marke liegt unter dem Band (92 € < 160 €) und im Bild
    const geo = await spark.locator(".spark-plot").evaluate((p) => {
      const r = p.getBoundingClientRect(), dot = p.querySelector(".spark-dot").getBoundingClientRect(),
        b = p.querySelector(".spark-band").getBoundingClientRect();
      return { top: r.top, bottom: r.bottom, dot: dot.top + dot.height / 2, bandBottom: b.bottom };
    });
    assert(geo.dot > geo.bandBottom && geo.dot <= geo.bottom + 1, `Marke falsch: ${JSON.stringify(geo)}`);
    assert(/Google-Preisverlauf der letzten 62 Tage/.test(await spark.locator(".spark-plot").getAttribute("aria-label")), "Verlauf ohne Textalternative");
    await spark.locator(".spark-plot").hover({ position: { x: 20, y: 20 } });
    assert(/^\d{2}\.\d{2}\.: \d+ €$/.test(norm(await spark.locator(".spark-tip").textContent())), "Hover-Wert fehlt");
    assert(!/Google: Preise derzeit niedrig/.test(await card.locator(".reasons").textContent()), "Preisniveau doppelt in den Gründen");
    // derselbe China-Deal bei drei Seiten
    const multi = page.locator(".deal-card", { hasText: "3 Quellen" });
    assert(await multi.count() === 1, "Karte mit 3 Quellen fehlt");
    assert(norm(await multi.locator(".badge.sig-accent").textContent()) === "Erstmeldung Secret Flying, 7 h früher", "Erstmeldung-Badge falsch");
    const links = await multi.locator(".deal-meta a").evaluateAll((as) => as.map((a) => [a.textContent, a.getAttribute("href")]));
    assert(links.length === 3 && links.every(([, h]) => /^https:\/\//.test(h)), `Quellen-Links: ${JSON.stringify(links)}`);
    assert(/Quellen: Secret Flying, Travel-Dealz-Artikel, ErrorFareAlerts/.test(norm(await multi.locator(".deal-meta").textContent())), "Quellenliste fehlt");
    assert(await page.locator(".deal-card", { hasText: "2 Quellen" }).count() === 1, "Karte mit 2 Quellen fehlt");
    // Abflug ab Budapest, Zubringer eingerechnet, läuft ab
    const hub = page.locator(".deal-card", { hasText: "Abflug Nachbar-Hub Budapest" });
    assert(norm(await hub.locator(".badge.sig-info").textContent()) === "Abflug Nachbar-Hub Budapest (+180 € Zubringer)", "Hub-Badge falsch");
    assert(/^läuft ab \d{2}\.\d{2}\.$/.test(norm(await hub.locator(".signals .badge", { hasText: "läuft ab" }).textContent())), "Ablauf-Badge fehlt");
    assert(norm(await hub.locator(".price-box").textContent()).includes("inkl. 180 € Zubringer"), "Zubringer am Preis fehlt");
    assert(!/Abflug Nachbar-Hub/.test(await hub.locator(".reasons").textContent()), "Hub doppelt in den Gründen");
    await noHScroll(page, "Deals mit Signalen");
    await card.scrollIntoViewIfNeeded();
    await shot(page, "desktop-deal-signale", false);
  });

  await step("Beinahe-Deals: je Alarm sichtbar, nie gemeldet", async () => {
    const alerts = (await api("GET", "/api/alerts")).alerts;
    const near = alerts.flatMap((a) => a.near_deals.map((e) => [a, e]));
    assert(near.length >= 1, "Demo ohne Beinahe-Deal");
    assert(await page.isVisible("#near-deals"), "Beinahe-Deals fehlen");
    const rows = page.locator("#near-list .near-row");
    assert(await rows.count() === near.length, `Beinahe-Zeilen ${await rows.count()} ≠ ${near.length}`);
    const txt = norm(await rows.first().textContent());
    assert(/559 €/.test(txt) && /60 € über deiner Grenze/.test(txt) && /7% unter typischer Spanne/.test(txt), `Beinahe-Deal: ${txt}`);
    assert(await rows.first().locator(".spark-compact path.spark-line").count() === 1, "Mini-Verlauf fehlt");
    assert(/nie gemeldet/.test(await page.textContent("#near-deals")), "Hinweis „nie gemeldet“ fehlt");
    const notes = (await api("GET", "/api/notifications?limit=500")).notifications;
    assert(!notes.some((n) => n.price === 559), "Beinahe-Deal wurde gemeldet");
    // Filter: anderer Alarm -> Liste weg, Radar -> weg, Alle -> wieder da
    await page.locator("#deal-filters .chip").filter({ hasText: "Zürich → Hamburg (und zurück)" }).click();
    await waitFor(async () => !(await page.isVisible("#near-deals")), { msg: "Filter zeigt fremde Beinahe-Deals" });
    await page.locator('#deal-filters .chip[data-id=""]').click();
    await waitFor(async () => (await rows.count()) === near.length, { msg: "„Alle“ ohne Beinahe-Deals" });
    await page.locator("#near-deals").scrollIntoViewIfNeeded();
    await shot(page, "desktop-beinahe-deals", false);
    // Alarm-Karte: Beinahe-Deals und Hinweis zur Google-Rotation
    await go(page, "alarme");
    const [a] = near[0];
    const card = await alertCard(page, a.name);
    assert(await card.locator(".alert-near .near-row").count() === a.near_deals.length, "Beinahe-Deals fehlen auf der Alarm-Karte");
    const rot = alerts.find((x) => x.rotation);
    const rotCard = await alertCard(page, rot.name);
    const hint = norm(await rotCard.locator(".rotation-hint").textContent());
    assert(/^Keine Deal-Seite meldet diese Strecke\. Abdeckung über die Google-Rotation: Jeder Abflugtag der nächsten 90 Tage wird einzeln geprüft\./.test(hint), `Rotations-Hinweis: ${hint}`);
    assert(/Aktuell \d+ von 90 Tagen mit Preis/.test(hint), `Rotations-Stand fehlt: ${hint}`);
    assert(await card.locator(".rotation-hint").count() === 0, "Rotations-Hinweis bei China");
    await noHScroll(page, "Alarme mit Beinahe-Deals");
  });

  await step("Quellen-Gesundheit: Ampel, Alter, Artikel, Google leer, bewusst nicht genutzt", async () => {
    const rep = await api("GET", "/api/sources/health");
    await go(page, "quellen");
    await page.waitForSelector(".health-row");
    const rows = page.locator(".health-row");
    assert(await rows.count() === rep.sources.length, `Zeilen ${await rows.count()} ≠ ${rep.sources.length}`);
    const ok = rep.sources.filter((s) => s.state === "ok").length;
    assert(ok < rep.sources.length, "Demo ohne Quelle mit Problem");
    assert(norm(await page.textContent(".health-headline")) === `${ok} von ${rep.sources.length} Quellen OK`, "Zusammenfassung falsch");
    const first = rows.first();
    assert((await first.getAttribute("class")).includes("h-warn") && norm(await first.locator(".h-label").textContent()) === "Veraltet", "Veraltete Quelle nicht oben/gelb");
    assert(/premium-flights/.test(await first.textContent()) && /neuester Artikel vor 30 Std\. \(erwartet: höchstens 24 Std\.\)/.test(norm(await first.textContent())), `Alter fehlt: ${norm(await first.textContent())}`);
    const light = await first.locator(".h-light").evaluate((e) => getComputedStyle(e).backgroundColor);
    assert(light === "rgb(180, 83, 9)", `Ampel nicht gelb: ${light}`);
    const google = norm(await page.locator('.health-row[data-id="google_flights"]').textContent());
    assert(/[\d.]+ Abrufe im letzten Lauf · \d+ % leer/.test(google), `Google-Zeile: ${google}`);
    assert(/\d+ Artikel im letzten Lauf/.test(norm(await page.locator('.health-row[data-id="feed:secretflying"]').textContent())), "Artikelzahl fehlt");
    const unused = page.locator("#unused-list li");
    assert(await unused.count() === rep.not_used.length && rep.not_used.length >= 5, "Liste „bewusst nicht genutzt“ unvollständig");
    const utxt = norm(await page.textContent("#unused-list"));
    assert(/secretflying\.com.*Cloudflare/.test(utxt) && /Amadeus.*abgeschaltet/.test(utxt) && /Lufthansa.*403/.test(utxt), `Gründe fehlen: ${utxt}`);
    await noHScroll(page, "Quellen");
    await shot(page, "desktop-quellen");
    await go(page, "deals");               // weitere Schritte beginnen auf der Deals-Seite
    await page.waitForSelector(".deal-card");
  });

  await step("Status: „nächster Feed-Check in …“", async () => {
    // Die Demo läuft ohne Zeitplan - Status-Antwort einmal mit einem Feed-Termin anreichern
    const local = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}T${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
    const p2 = await page.context().newPage();
    watchConsole(p2, "desktop-status");
    await p2.route("**/api/status", async (route) => {
      const res = await route.fetch();
      const json = await res.json();
      json.next_run = local(new Date(Date.now() + 150 * 60000));
      json.next_feed_run = local(new Date(Date.now() + 12.5 * 60000));
      await route.fulfill({ response: res, json });
    });
    await p2.goto(base + "/#deals");
    await p2.waitForFunction(() => /nächster Feed-Check/.test(document.querySelector("#status-line").textContent));
    const line = norm(await p2.textContent("#status-line"));
    assert(/nächster Feed-Check in 1[23] Min\./.test(line), `Statuszeile: ${line}`);
    await p2.close();
  });

  await step("Abgelaufene-Umschalter (ohne abgelaufene Deals)", async () => {
    const before = await page.locator(".deal-card").count();
    const wait = page.waitForResponse((r) => r.url().includes("/api/deals?status=all"));
    await page.click("#view-deals .view-head .switch");
    await wait;
    assert(await page.isChecked("#show-expired"), "Umschalter nicht aktiv");
    await page.waitForTimeout(200);
    assert(await page.locator(".deal-card").count() === before, "Anzahl ändert sich ohne abgelaufene Deals");
    const back = page.waitForResponse((r) => r.url().includes("/api/deals?status=active"));
    await page.click("#view-deals .view-head .switch");
    await back;
  });

  await step("Tastatur: Sprunglink und Filter-Chips behalten Ansicht und Fokus", async () => {
    await page.goto(base + "/#alarme");
    await page.waitForSelector("#view-alarme:not([hidden])");
    await page.focus(".skip-link");
    await page.keyboard.press("Enter");
    await page.waitForTimeout(200);
    assert(await page.evaluate(() => location.hash) === "#alarme", "Sprunglink wechselt die Seite");
    assert(await page.isVisible("#view-alarme"), "Alarme-Ansicht verschwunden");
    assert(await page.evaluate(() => document.activeElement.id) === "main", "Fokus nicht auf dem Inhalt");
    await go(page, "deals");
    await page.waitForSelector("#deal-filters .chip");
    const id = alertsBefore[0].id;
    await page.focus(`#deal-filters .chip[data-id="${id}"]`);
    await page.keyboard.press("Enter");
    await page.waitForFunction((i) => document.querySelector(`#deal-filters .chip[data-id="${i}"]`).getAttribute("aria-pressed") === "true", id);
    await page.waitForTimeout(300);
    const focused = await page.evaluate(() => document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.id : null);
    assert(focused === id, `Fokus nach Filter-Chip verloren (${focused})`);
    await page.focus('#deal-filters .chip[data-id=""]');
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.querySelector('#deal-filters .chip[data-id=""]').getAttribute("aria-pressed") === "true");
  });

  await step("Alarme-Ansicht", async () => {
    await go(page, "alarme");
    await page.waitForSelector(".alert-card");
    assert(await page.locator(".alert-card").count() === alertsBefore.length, "Anzahl Alarme falsch");
    const zrh = await alertCard(page, "Zürich → Hamburg (und zurück)");
    const txt = norm(await zrh.textContent());
    assert(/Zürich \(ZRH\) → Hamburg \(HAM\)/.test(txt), `Route fehlt: ${txt}`);
    assert(/1 Strecke/.test(txt) && /bis 110\s€/.test(txt) && /Economy/.test(txt) && /Hin- und Rückflug/.test(txt), `Fakten fehlen: ${txt}`);
    assert(/aktive[r]? Deal/.test(txt) && /ab \d+\s€/.test(txt), `Deal-Zähler/Bestpreis fehlt: ${txt}`);
    const cn = await alertCard(page, alertsBefore.find((a) => a.id === "de-cn-economy").name);
    assert(/Strecken/.test(await cn.textContent()), "Regionen-Alarm ohne Streckenzahl");
    // Bestpreis auf der Karte (auch über der Grenze sichtbar), je Strecke aufklappbar
    const best = norm(await zrh.locator(".alert-best").textContent());
    assert(/Bestpreis jetzt/.test(best) && /\d+\s€/.test(best) && /(Stand|veröffentlicht) (gerade|vor)/.test(best), `Bestpreis auf Karte fehlt: ${best}`);
    const multi = alertsBefore.find((a) => a.cheapest && a.cheapest.route_count > 1);
    if (multi) {
      const card = await alertCard(page, multi.name);
      await card.locator(".best-details summary").click();
      assert(await card.locator(".best-routes li").count() >= 2, `${multi.id}: Strecken-Liste fehlt`);
    }
    await noHScroll(page, "Alarme");
    await shot(page, "desktop-alarme");
  });

  await step("Ungültige Eingabe → Fehlermeldung", async () => {
    await page.click("#new-alert-btn");
    await page.waitForSelector("#alert-dialog[open]");
    await page.fill("#f-name", "Kaputt");
    await page.click("#alert-save");
    await page.waitForSelector("#alert-form-error:not([hidden])");
    assert(/Abflugort/.test(await page.textContent("#alert-form-error")), "Fehlermeldung „Von“ fehlt");
    await addChip(page, "origins", "VIE");
    await addChip(page, "destinations", "PEK");
    await page.click("#alert-save");
    await page.waitForFunction(() => /Preisgrenze/.test(document.querySelector("#alert-form-error").textContent));
    // Fehler vom Server (unbekannte Region) muss im Formular landen
    await addChip(page, "destinations", "@XX");
    await page.fill("#f-max-price", "300");
    allowedHttpErrors.push(400);
    const resp = page.waitForResponse((r) => r.url().endsWith("/api/alerts") && r.request().method() === "POST");
    await page.click("#alert-save");
    assert((await resp).status() === 400, "Server hätte 400 liefern sollen");
    await page.waitForFunction(() => /Unbekannte Region/.test(document.querySelector("#alert-form-error").textContent));
    assert(await page.isVisible("#alert-dialog[open]"), "Dialog wurde trotz Fehler geschlossen");
    await shot(page, "desktop-form-error", false);
    await page.click("#alert-dialog .dialog-foot [data-close]");
    await page.waitForSelector("#alert-dialog[open]", { state: "detached" });
    assert(!(await api("GET", "/api/alerts")).alerts.some((a) => a.name === "Kaputt"), "Ungültiger Alarm wurde gespeichert");
  });

  await step("Neuer Alarm Wien → Peking mit Gegenrichtung", async () => {
    await page.click("#new-alert-btn");
    await page.waitForSelector("#alert-dialog[open]");
    assert(await page.locator("#f-origins .tag").count() === 0, "Formular nicht zurückgesetzt");
    assert(await page.isHidden("#alert-form-error"), "Alte Fehlermeldung noch sichtbar");
    await page.fill("#f-name", "Wien → Peking");
    await addChip(page, "origins", "Wien");        // Stadtname → VIE
    await addChip(page, "destinations", "Peking"); // Stadtname → BJS
    assert(/VIE/.test(await page.textContent("#f-origins .chip-list")), "Wien wurde nicht zu VIE");
    assert(/BJS/.test(await page.textContent("#f-destinations .chip-list")), "Peking wurde nicht zu BJS");
    await page.fill("#f-max-price", "450");
    await page.fill("#f-baseline", "700");
    await page.fill("#f-min-nights", "5");
    await page.fill("#f-max-nights", "21");
    await page.fill("#f-keywords", "Wien, Peking\nWien, China");
    await page.check("#f-reverse");
    await shot(page, "desktop-form-new", false);
    await page.click("#alert-save");
    await page.waitForSelector("#alert-dialog[open]", { state: "detached" });
    await waitToast(page, /Gegenrichtung angelegt/);
    await page.waitForFunction((n) => document.querySelectorAll(".alert-card").length === n, alertsBefore.length + 2);
    const all = (await api("GET", "/api/alerts")).alerts;
    const hin = all.find((a) => a.name === "Wien → Peking");
    const back = all.find((a) => a.name === "Wien → Peking (Gegenrichtung)");
    assert(hin && back, "Alarme fehlen in der API");
    assert(back.id === `${hin.id}-retour`, `ID Gegenrichtung: ${back.id}`);
    assert(JSON.stringify(hin.origins) === '["VIE"]' && JSON.stringify(hin.destinations) === '["BJS"]', "Hinrichtung falsch");
    assert(JSON.stringify(back.origins) === '["BJS"]' && JSON.stringify(back.destinations) === '["VIE"]', "Rückrichtung falsch");
    assert(hin.max_price === 450 && hin.baseline_price === 700 && hin.min_nights === 5 && hin.max_nights === 21, "Zahlen falsch gespeichert");
    assert(JSON.stringify(hin.feed_keywords) === '[["Wien","Peking"],["Wien","China"]]', `Stichwörter: ${JSON.stringify(hin.feed_keywords)}`);
    const titles = await page.$$eval(".alert-title", (els) => els.map((e) => e.textContent));
    assert(titles.filter((t) => t.startsWith("Wien → Peking")).length === 2, "Neue Alarme nicht 2× in der Liste");
    const card = await alertCard(page, "Wien → Peking");
    assert(/Wien \(VIE\) → Peking \(BJS\)/.test(norm(await card.textContent())), "Route auf Karte falsch");
  });

  await step("Alarm bearbeiten (Preis ändern)", async () => {
    const card = await alertCard(page, "Wien → Peking");
    await card.locator("button:has-text('Bearbeiten')").click();
    await page.waitForSelector("#alert-dialog[open]");
    assert(/Alarm bearbeiten/.test(await page.textContent("#alert-dialog-title")), "Titel nicht „bearbeiten“");
    assert(await page.inputValue("#f-max-price") === "450", "Preis nicht vorbelegt");
    assert(await page.isHidden("#f-reverse-wrap"), "Gegenrichtung beim Bearbeiten sichtbar");
    assert(/VIE/.test(await page.textContent("#f-origins .chip-list")), "Von nicht vorbelegt");
    assert(!(await page.isChecked("#f-rotation")), "Tagesrotation ungefragt an");
    await page.fill("#f-max-price", "399");
    await page.fill("#f-baseline", ""); // optionales Feld leeren → null
    await page.check("#f-rotation");
    await page.click("#alert-save");
    await page.waitForSelector("#alert-dialog[open]", { state: "detached" });
    await waitToast(page, /gespeichert/);
    const c2 = await alertCard(page, "Wien → Peking");
    await waitFor(async () => /bis 399\s€/.test(norm(await c2.textContent())), { msg: "Neuer Preis nicht auf der Karte" });
    const a = (await api("GET", "/api/alerts")).alerts.find((x) => x.name === "Wien → Peking");
    assert(a.max_price === 399, `API max_price ${a.max_price}`);
    assert(a.baseline_price === null || a.baseline_price === undefined, `Normalpreis nicht geleert: ${a.baseline_price}`);
    assert(a.min_nights === 5, "Andere Felder verloren");
    assert(a.rotation === true, "Tagesrotation nicht gespeichert");
  });

  await step("Deutsche Zahlen und Airline-Codes im Formular", async () => {
    const card = await alertCard(page, "Wien → Peking");
    await card.locator("button:has-text('Bearbeiten')").click();
    await page.waitForSelector("#alert-dialog[open]");
    // ungültige Eingaben → verständliche deutsche Fehler, nichts gespeichert
    for (const [sel, value, re] of [["#f-max-price", "1e", /„Preisgrenze“: bitte eine Zahl/],
      ["#f-stops", "5", /„Max\. Umstiege“: höchstens 3/], ["#f-discount", "0", /„Mindest-Rabatt“: mindestens 1 %/],
      ["#f-airlines", "Ryanairx", /2-stellige IATA-Codes/]]) {
      const old = await page.inputValue(sel);
      await page.fill(sel, value);
      await page.click("#alert-save");
      await page.waitForFunction((src) => new RegExp(src).test(document.querySelector("#alert-form-error").textContent), re.source);
      assert(await page.isVisible("#alert-dialog[open]"), `Dialog bei ${sel}=${value} geschlossen`);
      await page.fill(sel, old);
    }
    await page.fill("#f-max-price", "1.299");
    await page.fill("#f-baseline", "1.499,50");
    await page.fill("#f-duration", "11,5");
    await page.fill("#f-airlines", "Ryanair, w6");
    await page.click("#alert-save");
    await page.waitForSelector("#alert-dialog[open]", { state: "detached" });
    await waitToast(page, /gespeichert/);
    let a = (await api("GET", "/api/alerts")).alerts.find((x) => x.name === "Wien → Peking");
    assert(a.max_price === 1299 && a.baseline_price === 1499.5 && a.max_duration_hours === 11.5,
      `Zahlen falsch: ${a.max_price} / ${a.baseline_price} / ${a.max_duration_hours}`);
    assert(JSON.stringify(a.exclude_airlines) === '["FR","W6"]', `Airlines: ${a.exclude_airlines}`);
    const c2 = await alertCard(page, "Wien → Peking");
    await c2.locator("button:has-text('Bearbeiten')").click();
    await page.waitForSelector("#alert-dialog[open]");
    assert(await page.inputValue("#f-duration") === "11,5", `Anzeige Flugdauer: ${await page.inputValue("#f-duration")}`);
    await page.fill("#f-max-price", "399");
    await page.fill("#f-baseline", "");
    await page.click("#alert-save");
    await page.waitForSelector("#alert-dialog[open]", { state: "detached" });
    a = (await api("GET", "/api/alerts")).alerts.find((x) => x.name === "Wien → Peking");
    assert(a.max_price === 399, `Preis nicht zurückgesetzt: ${a.max_price}`);
  });

  await step("Alarm pausieren", async () => {
    const card = await alertCard(page, "Wien → Peking");
    await card.locator(".switch").click();
    await waitToast(page, /pausiert/);
    const c2 = await alertCard(page, "Wien → Peking");
    await waitFor(async () => (await c2.getAttribute("class")).includes("paused"), { msg: "Karte nicht als pausiert markiert" });
    assert(/pausiert/.test(await c2.locator(".switch-text").textContent()), "Text „pausiert“ fehlt");
    const a = (await api("GET", "/api/alerts")).alerts.find((x) => x.name === "Wien → Peking");
    assert(a.enabled === false, "API: Alarm nicht pausiert");
    const st = await api("GET", "/api/status");
    assert(st.counts.alerts_enabled === st.counts.alerts - 1, "Status zählt pausierten Alarm mit");
    await shot(page, "desktop-alarme-neu");
  });

  await step("Tastatur: Alarm-Schalter behält den Fokus", async () => {
    const card = await alertCard(page, "Wien → Peking");
    const sw = card.locator(".switch input");
    await sw.focus();
    await page.keyboard.press("Space");
    await waitToast(page, /wieder aktiv/);
    await waitFor(async () => !(await card.getAttribute("class")).includes("paused"), { msg: "Karte nicht aktiv" });
    assert(await sw.evaluate((x) => x === document.activeElement), "Fokus nach Umschalten verloren");
    await page.keyboard.press("Space");
    await waitFor(async () => (await card.getAttribute("class")).includes("paused"), { msg: "Karte nicht wieder pausiert" });
    await waitFor(async () => !(await sw.isDisabled()), { msg: "Schalter bleibt gesperrt" });
    assert(await sw.evaluate((x) => x === document.activeElement), "Fokus nach zweitem Umschalten verloren");
    assert((await api("GET", "/api/alerts")).alerts.find((x) => x.name === "Wien → Peking").enabled === false, "Nicht pausiert");
  });

  await step("Alarm löschen (mit Bestätigung)", async () => {
    const card = await alertCard(page, "Wien → Peking (Gegenrichtung)");
    await card.locator("button:has-text('Löschen')").click();
    await page.waitForSelector("#confirm-dialog[open]");
    assert(/Gegenrichtung/.test(await page.textContent("#confirm-text")), "Bestätigung nennt Alarm nicht");
    await shot(page, "desktop-confirm", false);
    // erst abbrechen → bleibt
    await page.click("#confirm-dialog button[value=cancel]");
    await page.waitForSelector("#confirm-dialog[open]", { state: "detached" });
    assert(await page.locator(".alert-title", { hasText: "(Gegenrichtung)" }).count() === 1, "Abbrechen hat gelöscht");
    await card.locator("button:has-text('Löschen')").click();
    await page.waitForSelector("#confirm-dialog[open]");
    await page.click("#confirm-ok");
    await waitToast(page, /gelöscht/);
    await page.waitForFunction((n) => document.querySelectorAll(".alert-card").length === n, alertsBefore.length + 1);
    assert(!(await api("GET", "/api/alerts")).alerts.some((a) => a.name.includes("Gegenrichtung")), "API: noch vorhanden");
  });

  let runsBefore = 0;
  await step("„Jetzt prüfen“ → Lauf endet, Laufhistorie zeigt Eintrag", async () => {
    runsBefore = (await api("GET", "/api/runs")).runs.length;
    await go(page, "deals");
    await page.click("#run-btn");
    await waitToast(page, /Prüfung gestartet/);
    await waitToast(page, /Prüfung fertig/, 30000);
    await page.waitForFunction(() => !document.querySelector("#run-btn").disabled);
    assert(/Jetzt prüfen/.test(await page.textContent("#run-btn")), "Button-Text nicht zurückgesetzt");
    const runs = (await api("GET", "/api/runs")).runs;
    assert(runs.length === runsBefore + 1, `Läufe ${runs.length}, vorher ${runsBefore}`);
    assert(runs[0].trigger === "manual" && runs[0].finished_at, "Lauf nicht manuell/fertig");
    assert(runs[0].errors.length === 0, `Lauf mit Fehlern: ${runs[0].errors}`);
    await go(page, "einstellungen");
    await page.waitForSelector("#runs-table table");
    const rows = page.locator("#runs-table tbody tr");
    assert(await rows.count() === runsBefore + 1, `Tabelle hat ${await rows.count()} Zeilen`);
    const first = norm(await rows.first().textContent());
    assert(/manuell/.test(first) && /alle/.test(first) && /keine/.test(first), `Erste Zeile: ${first}`);
  });

  await step("„Jetzt prüfen“ gegen Doppelklick (409 → Hinweis)", async () => {
    // Zweiter Start, während einer läuft: API muss 409 liefern, UI darf nicht abstürzen
    const json = { "Content-Type": "application/json" };
    const r1 = await fetch(base + "/api/run", { method: "POST", headers: json, body: "{}" });
    const r2 = await fetch(base + "/api/run", { method: "POST", headers: json, body: "{}" });
    assert(r1.status === 202, `Erster Start ${r1.status}`);
    assert(r2.status === 409 || r2.status === 202, `Zweiter Start ${r2.status}`);
    if (r2.status === 409) assert(/läuft bereits/.test((await r2.json()).error), "409 ohne deutsche Meldung");
    await waitFor(async () => !(await api("GET", "/api/status")).running, { timeout: 30000, msg: "Lauf endet nicht" });
    await waitToast(page, /Prüfung fertig/, 30000);
  });

  await step("Preis senken + „Nur diesen prüfen“ → abgelaufene Deals durchgestrichen", async () => {
    await go(page, "alarme");
    const card = await alertCard(page, "Zürich → Hamburg (und zurück)");
    await card.locator("button:has-text('Bearbeiten')").click();
    await page.waitForSelector("#alert-dialog[open]");
    await page.fill("#f-max-price", "20");
    await page.click("#alert-save");
    await page.waitForSelector("#alert-dialog[open]", { state: "detached" });
    const c2 = await alertCard(page, "Zürich → Hamburg (und zurück)");
    await c2.locator("button:has-text('Nur diesen prüfen')").click();
    await waitToast(page, /für diesen Alarm gestartet/);
    // nicht auf den Toast verlassen: der des vorigen Laufs kann noch sichtbar sein
    await waitFor(async () => { const r = (await api("GET", "/api/runs")).runs[0]; return r.alert_id === "zrh-ham" && r.finished_at; },
      { timeout: 30000, msg: "Einzelprüfung endet nicht" });
    await waitToast(page, /Prüfung fertig/, 30000);
    const runs = (await api("GET", "/api/runs")).runs;
    assert(runs[0].alert_id === "zrh-ham" && runs[0].expired > 0, `Lauf: ${JSON.stringify(runs[0])}`);
    await waitFor(async () => /keine aktiven Deals/.test(await (await alertCard(page, "Zürich → Hamburg (und zurück)")).textContent()),
      { msg: "Karte zeigt noch aktive Deals" });
    await go(page, "deals");
    await page.waitForSelector(".deal-card");
    assert(await page.locator(".deal-card.expired").count() === 0, "Abgelaufene ohne Umschalter sichtbar");
    await page.click("#view-deals .view-head .switch");
    await page.waitForSelector(".deal-card.expired");
    const exp = page.locator(".deal-card.expired").first();
    assert(/Abgelaufen/.test(await exp.textContent()), "Badge „Abgelaufen“ fehlt");
    const deco = await exp.locator(".price").evaluate((e) => getComputedStyle(e).textDecorationLine);
    assert(/line-through/.test(deco), `Preis nicht durchgestrichen (${deco})`);
    const apiExp = (await api("GET", "/api/deals?status=expired")).deals.length;
    assert(await page.locator(".deal-card.expired").count() === apiExp, "Anzahl abgelaufener Karten ≠ API");
    await shot(page, "desktop-deals-abgelaufen");
    const back = page.waitForResponse((r) => r.url().includes("/api/deals?status=active"));
    await page.click("#view-deals .view-head .switch");
    await back;
  });

  await step("Meldungen mit Badge, „Alle als gelesen“ → Badge weg", async () => {
    const st = await api("GET", "/api/status");
    assert(st.counts.unread > 0, "Keine ungelesenen Meldungen in der Demo");
    await page.waitForSelector("#unread-badge:not([hidden])");
    const badge = norm(await page.textContent("#unread-badge"));
    assert(badge === String(st.counts.unread) || (st.counts.unread > 99 && badge === "99+"), `Badge ${badge} ≠ ${st.counts.unread}`);
    await go(page, "meldungen");
    await page.waitForSelector(".note");
    const unreadCards = await page.locator(".note.unread").count();
    assert(unreadCards === Math.min(st.counts.unread, 100), `ungelesen markiert: ${unreadCards}`);
    await page.waitForTimeout(500);
    assert((await api("GET", "/api/status")).counts.unread === st.counts.unread, "Öffnen der Seite hat Meldungen als gelesen markiert");
    assert(await page.isVisible("#unread-badge"), "Badge verschwindet schon beim Öffnen");
    await shot(page, "desktop-meldungen");
    await page.click("#mark-all-read-btn");
    await waitToast(page, /als gelesen markiert/);
    await page.waitForSelector("#unread-badge", { state: "hidden" });
    await waitFor(async () => (await page.locator(".note.unread").count()) === 0, { msg: "Noch ungelesene Karten" });
    assert((await api("GET", "/api/status")).counts.unread === 0, "API: noch ungelesen");
    assert(await page.isDisabled("#mark-all-read-btn"), "Button nicht deaktiviert");
  });

  const TOKEN = "test-dummy-token";
  await step("Einstellungen: ungültiges Intervall → Fehlermeldung", async () => {
    await go(page, "einstellungen");
    await page.waitForSelector("#s-interval_hours");
    await page.fill("#s-interval_hours", "0.2");
    await page.click("#settings-save");
    await page.waitForSelector("#settings-error:not([hidden])");
    assert(/mindestens 0,5/.test(await page.textContent("#settings-error")), "Fehlertext Intervall fehlt");
    assert((await api("GET", "/api/settings")).values.interval_hours === 3, "Ungültiger Wert gespeichert");
    await page.fill("#s-interval_hours", "6");
  });

  await step("Einstellungen: Token setzen → maskierter Hinweis, Webhook-URL speichern", async () => {
    await page.fill("#s-travelpayouts_token", TOKEN);
    await page.fill("#s-webhook_url", hookUrl);
    await page.fill("#s-webhook_token", "test-dummy-hook-1234");
    await page.click("#advanced-panel summary");
    await page.fill("#s-renotify_drop", "10");
    await page.fill("#s-request_delay", "0,35");
    await page.click("#settings-save");
    await waitToast(page, /Einstellungen gespeichert/);
    const s = await api("GET", "/api/settings");
    assert(s.secrets.travelpayouts_token.set && s.secrets.travelpayouts_token.hint === "…oken", `Hinweis: ${JSON.stringify(s.secrets.travelpayouts_token)}`);
    assert(!JSON.stringify(s).includes(TOKEN), "Token im Klartext ausgeliefert");
    assert(s.values.webhook_url === hookUrl, "Webhook-URL nicht gespeichert");
    assert(s.values.interval_hours === 6, `Intervall ${s.values.interval_hours}`);
    assert(Math.abs(s.values.renotify_drop - 0.1) < 1e-9, `renotify_drop ${s.values.renotify_drop}`);
    assert(s.values.request_delay === 0.35, `request_delay ${s.values.request_delay} (Dezimalkomma)`);
    assert(await page.inputValue("#s-request_delay") === "0,35", "Dezimalkomma nicht angezeigt");
    assert(await page.inputValue("#s-travelpayouts_token") === "", "Passwortfeld nach Speichern nicht leer");
    const tokenField = page.locator(".field", { has: page.locator("#s-travelpayouts_token") });
    assert(/gesetzt …oken/.test(await tokenField.textContent()), "Maskierter Hinweis nicht sichtbar");
    assert(!(await page.content()).includes(TOKEN), "Token steht im HTML");
    assert(await page.inputValue("#s-webhook_url") === hookUrl, "Webhook-Feld leer");
    assert(await page.inputValue("#s-renotify_drop") === "10", "Prozentwert falsch angezeigt");
    assert(/Letzte Prüfung/.test(await page.textContent("#status-line")), "Statuszeile kaputt");
    await shot(page, "desktop-einstellungen");
  });

  await step("Testmeldung → Ergebnis pro Kanal", async () => {
    hooks.length = 0;
    await page.click("#test-notify-btn");
    await page.waitForSelector("#test-notify-result li.ok");
    await waitToast(page, /Testmeldung/);
    const items = await page.$$eval("#test-notify-result li", (els) => els.map((e) => ({ cls: e.className, text: e.textContent })));
    const names = items.map((i) => i.text);
    assert(items.length >= 3, `Nur ${items.length} Kanäle: ${names}`);
    assert(names.some((t) => /Meldungen/.test(t)) && names.some((t) => /Webhook/.test(t)), `Kanäle: ${names}`);
    assert(items.every((i) => i.cls === "ok"), `Fehlgeschlagen: ${names.join(" | ")}`);
    await waitFor(async () => hooks.length > 0, { timeout: 5000, msg: "Webhook hat nichts empfangen" });
    assert(hooks[0].method === "POST" && hooks[0].auth === "Bearer test-dummy-hook-1234", `Webhook: ${JSON.stringify(hooks[0])}`);
    assert(hooks[0].json && typeof hooks[0].json.text === "string", "Webhook-JSON ohne text");
    await page.waitForSelector("#unread-badge:not([hidden])", { timeout: 20000 });
    await shot(page, "desktop-testmeldung", false);
  });

  await step("Reload behält Daten", async () => {
    await page.reload();
    await page.waitForSelector("#s-webhook_url");
    await page.waitForFunction((u) => document.querySelector("#s-webhook_url").value === u, hookUrl);
    const tokenField = page.locator(".field", { has: page.locator("#s-travelpayouts_token") });
    assert(/gesetzt …oken/.test(await tokenField.textContent()), "Token-Hinweis nach Reload weg");
    assert(await page.inputValue("#s-interval_hours") === "6", "Intervall nach Reload weg");
    await go(page, "alarme");
    await page.waitForSelector(".alert-card");
    assert(await page.locator(".alert-card").count() === alertsBefore.length + 1, "Alarmanzahl nach Reload falsch");
    const card = await alertCard(page, "Wien → Peking");
    const t = norm(await card.textContent());
    assert(/bis 399\s€/.test(t) && /pausiert/.test(t), `Wien → Peking nach Reload: ${t}`);
    await go(page, "meldungen");
    await page.waitForSelector(".note");
    assert(await page.locator(".note").count() >= 1, "Meldungen nach Reload weg");
    assert(await page.locator(".note.unread").count() === 1, "Nur die Testmeldung sollte ungelesen sein");
    // einzelne Meldung als gelesen markieren
    await page.locator(".note.unread button:has-text('Als gelesen markieren')").click();
    await page.waitForSelector("#unread-badge", { state: "hidden" });
    await page.waitForFunction(() => document.activeElement && document.activeElement.id === "h-meldungen", null, { timeout: 5000 });
  });

  await step("Token löschen → Onboarding-Hinweis bleibt im Demo aus, Secret weg", async () => {
    await go(page, "einstellungen");
    await page.waitForSelector("[data-delete=travelpayouts_token]");
    await page.check("[data-delete=travelpayouts_token]");
    await page.click("#settings-save");
    await waitToast(page, /Einstellungen gespeichert/);
    const s = await api("GET", "/api/settings");
    assert(!s.secrets.travelpayouts_token.set, "Token nicht gelöscht");
    assert(/nicht gesetzt/.test(await page.locator(".field", { has: page.locator("#s-travelpayouts_token") }).textContent()), "UI zeigt Token noch");
  });

  const srcSwitch = (id) => page.locator(`.source-card[data-source="${id}"] .source-head .switch`);
  const srcInput = (id) => `#sources-group input[name=src][value="${id}"]`;
  const presetBox = (id) => `#feed-presets input[name=preset][value="${id}"]`;

  await step("Quellen-Karten: Zustand, Google-Schalter aus/an", async () => {
    await go(page, "einstellungen");
    await page.waitForSelector("#s-google_max_requests");
    const ids = await page.$$eval(".source-card", (els) => els.map((e) => e.dataset.source));
    assert(JSON.stringify(ids) === '["travelpayouts","google_flights","amadeus","feeds"]', `Karten: ${ids}`);
    for (const id of ["travelpayouts", "google_flights", "feeds"]) assert(await page.isChecked(srcInput(id)), `${id} nicht an`);
    assert(!(await page.isChecked(srcInput("amadeus"))), "Amadeus sollte aus sein");
    assert((await page.getAttribute('.source-card[data-source="amadeus"]', "class")).includes("is-off"), "Amadeus-Karte nicht als aus markiert");
    assert(/inoffiziell/.test(await page.textContent('.source-card[data-source="google_flights"]')), "Google-Hinweis „inoffiziell“ fehlt");
    assert(/nicht live getestet/.test(await page.textContent('.source-card[data-source="amadeus"]')), "Amadeus-Hinweis „ungetestet“ fehlt");
    await page.waitForFunction(() => document.querySelector('[data-state="google_flights"]').textContent.length > 0);
    assert(/Kein Key nötig/.test(await page.textContent('[data-state="google_flights"]')), "Google-Zustand fehlt");
    // Google aus -> speichern -> Reload -> bleibt aus
    await srcSwitch("google_flights").click();
    assert(!(await page.isChecked(srcInput("google_flights"))), "Google-Schalter reagiert nicht");
    assert(norm(await page.textContent('.source-card[data-source="google_flights"] .switch-text')) === "aus", "Schaltertext nicht „aus“");
    await page.fill("#s-google_max_requests", "12");
    await page.click('label.switch:has(#s-google_verify)');
    await page.click("#settings-save");
    await waitToast(page, /Einstellungen gespeichert/);
    // der Hinweis kann noch vom vorigen Speichern stehen: auf den gespeicherten Stand warten
    let s = await waitFor(async () => {
      const x = await api("GET", "/api/settings");
      return JSON.stringify(x.values.sources) === '["travelpayouts","feeds"]' && x;
    }, { timeout: 5000, msg: "Google-Schalter nicht gespeichert" });
    assert(s.values.google_max_requests === 12 && s.values.google_verify === false, `Google-Werte: ${s.values.google_max_requests}/${s.values.google_verify}`);
    await page.reload();
    await page.waitForSelector("#s-google_max_requests");
    assert(!(await page.isChecked(srcInput("google_flights"))), "Google nach Reload wieder an");
    assert(!(await page.isChecked("#s-google_verify")) && await page.inputValue("#s-google_max_requests") === "12", "Google-Felder nach Reload falsch");
    // wieder an (Standard für die weiteren Schritte)
    await srcSwitch("google_flights").click();
    await page.click('label.switch:has(#s-google_verify)');
    await page.fill("#s-google_max_requests", "40");
    await page.click("#settings-save");
    await waitToast(page, /Einstellungen gespeichert/);
    s = await api("GET", "/api/settings");
    assert(s.values.sources.includes("google_flights") && s.values.google_verify === true, "Google nicht wieder an");
  });

  await step("Deal-Seiten: Preset abwählen, eigener Feed, Intervall → Reload", async () => {
    await page.fill("#s-feed_interval_minutes", "5");
    await page.click("#settings-save");
    await page.waitForSelector("#settings-error:not([hidden])");
    assert(/mindestens 10/.test(await page.textContent("#settings-error")), `Fehler Feed-Intervall: ${await page.textContent("#settings-error")}`);
    await page.fill("#s-feed_interval_minutes", "20");
    await page.fill("#s-feeds", "ftp://example.org/feed");
    allowedHttpErrors.push(400);
    const resp = page.waitForResponse((r) => r.url().endsWith("/api/settings") && r.request().method() === "PUT");
    await page.click("#settings-save");
    assert((await resp).status() === 400, "Server hätte die ftp-Adresse ablehnen sollen");
    await page.waitForFunction(() => /http\(s\)-Adresse/.test(document.querySelector("#settings-error").textContent));
    await page.fill("#s-feeds", "https://example.org/feed/");
    assert(await page.isChecked(presetBox("mydealz-fluege")), "mydealz sollte vorausgewählt sein");
    assert(!(await page.isChecked(presetBox("urlaubspiraten"))), "Urlaubspiraten sollte aus sein");
    await page.uncheck(presetBox("mydealz-fluege"));
    await page.check(presetBox("urlaubspiraten"));
    await page.click("#settings-save");
    await waitToast(page, /Einstellungen gespeichert/);
    const s = await api("GET", "/api/settings");
    assert(!s.values.feed_presets.includes("mydealz-fluege") && s.values.feed_presets.includes("urlaubspiraten"), `Presets: ${s.values.feed_presets}`);
    assert(s.values.feed_interval_minutes === 20 && JSON.stringify(s.values.feeds) === '["https://example.org/feed/"]', `Feeds: ${s.values.feed_interval_minutes} / ${s.values.feeds}`);
    await page.reload();
    await page.waitForSelector(presetBox("mydealz-fluege"));
    assert(!(await page.isChecked(presetBox("mydealz-fluege"))) && await page.isChecked(presetBox("urlaubspiraten")), "Presets nach Reload falsch");
    assert(await page.inputValue("#s-feed_interval_minutes") === "20" && await page.inputValue("#s-feeds") === "https://example.org/feed/", "Feed-Felder nach Reload falsch");
    const links = await page.$$eval("#feed-presets a", (as) => as.map((a) => [a.getAttribute("href"), a.getAttribute("rel")]));
    assert(links.length === s.presets.length && links.every(([h, r]) => /^https:\/\//.test(h) && /noopener/.test(r)), `Preset-Links: ${JSON.stringify(links)}`);
    await shot(page, "desktop-quellen");
  });

  const AMADEUS_KEY = "test-dummy-key-abcd";
  const AMADEUS_SECRET = "test-dummy-secret-wxyz";
  await step("Amadeus: Key/Secret maskiert, Umgebung, Schalter", async () => {
    await srcSwitch("amadeus").click();
    await page.fill("#s-amadeus_api_key", AMADEUS_KEY);
    await page.fill("#s-amadeus_api_secret", AMADEUS_SECRET);
    await page.selectOption("#s-amadeus_env", "production");
    await page.click("#settings-save");
    await waitToast(page, /Einstellungen gespeichert/);
    const s = await api("GET", "/api/settings");
    assert(s.secrets.amadeus_api_key.set && s.secrets.amadeus_api_key.hint === "…abcd", `Key: ${JSON.stringify(s.secrets.amadeus_api_key)}`);
    assert(s.secrets.amadeus_api_secret.set && s.secrets.amadeus_api_secret.hint === "…wxyz", `Secret: ${JSON.stringify(s.secrets.amadeus_api_secret)}`);
    assert(s.values.amadeus_env === "production" && s.values.sources.includes("amadeus"), "Amadeus-Werte nicht gespeichert");
    const raw = JSON.stringify(s) + JSON.stringify(await api("GET", "/api/status"));
    assert(!raw.includes(AMADEUS_KEY) && !raw.includes(AMADEUS_SECRET), "Amadeus-Secret im Klartext ausgeliefert");
    assert(await page.inputValue("#s-amadeus_api_key") === "" && await page.inputValue("#s-amadeus_api_secret") === "", "Passwortfelder nach Speichern nicht leer");
    assert(await page.getAttribute("#s-amadeus_api_secret", "type") === "password", "Secret-Feld ist kein Passwortfeld");
    assert(/gesetzt …wxyz/.test(await page.locator(".field", { has: page.locator("#s-amadeus_api_secret") }).textContent()), "Maskierter Hinweis fehlt");
    const html = await page.content();
    assert(!html.includes(AMADEUS_KEY) && !html.includes(AMADEUS_SECRET), "Amadeus-Secret steht im HTML");
    // aufräumen: Secrets löschen, Quelle aus
    await page.check("[data-delete=amadeus_api_key]");
    await page.check("[data-delete=amadeus_api_secret]");
    await srcSwitch("amadeus").click();
    await page.click("#settings-save");
    // nicht auf den Toast warten - der vom ersten Speichern kann noch sichtbar sein
    await waitFor(async () => {
      const s2 = await api("GET", "/api/settings");
      return !s2.secrets.amadeus_api_key.set && !s2.secrets.amadeus_api_secret.set && !s2.values.sources.includes("amadeus");
    }, { timeout: 5000, msg: "Amadeus nicht aufgeräumt" });
  });

  await step("Radar-Einstellungen speichern → Reload → Feed-Check wirkt", async () => {
    assert(await page.isChecked("#s-radar_enabled") && await page.isChecked("#s-radar_home_only"), "Radar-Schalter nicht an");
    assert(await page.inputValue("#s-radar_business_max") === "1300", `Business-Grenze: ${await page.inputValue("#s-radar_business_max")}`);
    await page.fill("#s-radar_business_max", "1.100");
    await page.fill("#s-radar_economy_max", "250,5");
    await page.click('label.switch:has(#s-radar_home_only)');
    await page.click("#settings-save");
    await waitToast(page, /Einstellungen gespeichert/);
    let s = await api("GET", "/api/settings");
    assert(s.values.radar_business_max === 1100 && s.values.radar_economy_max === 250.5 && s.values.radar_home_only === false,
      `Radar-Werte: ${s.values.radar_business_max} / ${s.values.radar_economy_max} / ${s.values.radar_home_only}`);
    await page.reload();
    await page.waitForSelector("#s-radar_business_max");
    assert(await page.inputValue("#s-radar_business_max") === "1100" && await page.inputValue("#s-radar_economy_max") === "250,5", "Radar-Grenzen nach Reload falsch");
    assert(!(await page.isChecked("#s-radar_home_only")), "„Nur DACH“ nach Reload wieder an");
    await shot(page, "desktop-radar-einstellungen");
    // Nur Deal-Seiten prüfen: Bangkok Business 1.199 € liegt jetzt über der Grenze -> läuft ab
    const before = (await api("GET", "/api/status")).counts.radar_deals;
    await page.click("#run-feeds-btn");
    await waitToast(page, /Deal-Seiten werden geprüft/);
    await waitFor(async () => { const r = (await api("GET", "/api/runs")).runs[0]; return r.trigger === "manual-feeds" && r.finished_at; },
      { timeout: 30000, msg: "Feed-Lauf endet nicht" });
    await waitToast(page, /Prüfung fertig/, 30000);
    const run = (await api("GET", "/api/runs")).runs[0];
    assert(run.trigger === "manual-feeds" && run.errors.length === 0, `Feed-Lauf: ${JSON.stringify(run)}`);
    await waitFor(async () => /manuell \(nur Feeds\)/.test(norm(await page.locator("#runs-table tbody tr").first().textContent())),
      { timeout: 5000, msg: "Laufhistorie zeigt Feed-Lauf nicht" });
    const after = (await api("GET", "/api/status")).counts.radar_deals;
    assert(after === before - 1, `Radar-Deals ${before} → ${after}`);
    await go(page, "deals");
    await waitFor(async () => Number(norm(await page.locator('#deal-filters .chip[data-id="radar"] .chip-count').textContent())) === after,
      { msg: "Radar-Zähler nicht aktualisiert" });
    // zurück auf Standard
    await go(page, "einstellungen");
    await page.waitForSelector("#s-radar_business_max");
    await page.fill("#s-radar_business_max", "1300");
    await page.fill("#s-radar_economy_max", "300");
    await page.click('label.switch:has(#s-radar_home_only)');
    await page.click("#settings-save");
    await waitToast(page, /Einstellungen gespeichert/);
    s = await api("GET", "/api/settings");
    assert(s.values.radar_business_max === 1300 && s.values.radar_home_only === true, "Radar nicht zurückgesetzt");
  });

  await ctx.close();
}

async function mobileFlows(browser) {
  console.log("\nMobil (390 px):");
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, locale: "de-DE",
    isMobile: true, hasTouch: true, colorScheme: "light" });
  const page = await ctx.newPage();
  currentPage = page;
  watchConsole(page, "mobil");

  await step("Mobil: alle Ansichten ohne horizontales Scrollen", async () => {
    await page.goto(base + "/#deals");
    await page.waitForSelector(".deal-card");
    await noHScroll(page, "Deals (mobil)");
    await shot(page, "mobile-deals");
    const nav = await page.locator(".main-nav").boundingBox();
    assert(nav && nav.y + nav.height >= 844 - 2, `Navigation nicht unten: ${JSON.stringify(nav)}`);
    for (const r of ["alarme", "quellen", "meldungen", "einstellungen"]) {
      await go(page, r);
      await page.waitForTimeout(400);
      await noHScroll(page, `${r} (mobil)`);
      await shot(page, `mobile-${r}`);
    }
    await page.click("#advanced-panel summary");
    await noHScroll(page, "Einstellungen erweitert (mobil)");
  });

  await step("Mobil: Quellen-Karten, Radar-Einstellungen und Radar-Filter passen (390 px)", async () => {
    await go(page, "einstellungen");
    await page.waitForSelector("#feed-presets input[name=preset]");
    for (const sel of [".source-card", "#feed-presets .preset", "#radar-panel .field"]) {
      const boxes = await page.$$eval(sel, (els) => els.map((e) => { const r = e.getBoundingClientRect(); return [r.left, r.right]; }));
      assert(boxes.length > 0, `${sel} fehlt`);
      assert(boxes.every(([l, r]) => l >= 0 && r <= 390), `${sel} ragt aus dem Bild: ${JSON.stringify(boxes)}`);
    }
    // Schalter jeder Quellen-Karte steht neben dem Titel (nicht abgeschnitten)
    const sw = await page.$$eval(".source-card .source-head > .switch", (els) => els.map((e) => e.getBoundingClientRect().right));
    assert(sw.length === 4 && sw.every((r) => r <= 390), `Quellen-Schalter: ${sw}`);
    await noHScroll(page, "Einstellungen Quellen (mobil)");
    await page.locator("#radar-panel").scrollIntoViewIfNeeded();
    await shot(page, "mobile-radar-einstellungen", false);
    await go(page, "deals");
    await page.waitForSelector('#deal-filters .chip[data-id="radar"]');
    await page.locator('#deal-filters .chip[data-id="radar"]').click();
    await waitFor(async () => (await page.locator(".deal-card").count()) > 0
      && (await page.locator(".deal-card").count()) === (await page.locator(".deal-card.is-radar").count()), { msg: "Radar-Filter mobil" });
    await noHScroll(page, "Deals Radar (mobil)");
    await shot(page, "mobile-deals-radar", false);
    await page.locator('#deal-filters .chip[data-id=""]').click();
  });

  await step("Mobil: Verlauf, Signale und Beinahe-Deals passen (390 px)", async () => {
    await go(page, "deals");
    await page.waitForSelector(".deal-card .spark");
    for (const sel of [".deal-card .spark", ".deal-card .signals .badge", "#near-list .near-row", "#near-list .spark-compact"]) {
      const boxes = await page.$$eval(sel, (els) => els.map((e) => { const r = e.getBoundingClientRect(); return [r.left, r.right, r.width]; }));
      assert(boxes.length > 0, `${sel} fehlt`);
      assert(boxes.every(([l, r, w]) => l >= 0 && r <= 390 && w > 0), `${sel} ragt aus dem Bild: ${JSON.stringify(boxes)}`);
    }
    await noHScroll(page, "Deals mit Verlauf (mobil)");
    await page.locator(".deal-card .spark").first().scrollIntoViewIfNeeded();
    await shot(page, "mobile-deal-signale", false);
    await go(page, "quellen");
    await page.waitForSelector(".health-row");
    await noHScroll(page, "Quellen (mobil)");
    const nav = await page.$$eval(".main-nav a", (as) => as.map((a) => a.getBoundingClientRect().right));
    assert(nav.length === 5 && nav.every((r) => r <= 390), `Tab-Leiste: ${nav}`);
  });

  await step("Mobil: Speichern-Button nicht von der Tab-Leiste verdeckt", async () => {
    await go(page, "einstellungen");
    await page.waitForSelector("#s-webhook_url");
    await page.evaluate(() => window.scrollTo(0, 400));
    await page.waitForTimeout(150);
    const hit = await page.evaluate(() => {
      const b = document.querySelector("#settings-save").getBoundingClientRect();
      if (b.bottom > innerHeight || b.top < 0) return "nicht im Bild";
      const e = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
      return e && e.closest("#settings-save") ? "ok" : (e ? e.outerHTML.slice(0, 80) : "nichts");
    });
    assert(hit === "ok", `Speichern-Button verdeckt: ${hit}`);
    await shot(page, "mobile-einstellungen-scroll", false);
  });

  await step("Mobil: pausierter Alarm lässt sich einzeln prüfen", async () => {
    await go(page, "alarme");
    const card = await alertCard(page, "Wien → Peking");
    assert(await card.locator("button:has-text('Nur diesen prüfen')").isEnabled(), "Button bei pausiertem Alarm gesperrt");
    await card.locator("button:has-text('Nur diesen prüfen')").click();
    await waitToast(page, /für diesen Alarm gestartet/);
    await waitFor(async () => {
      const r = (await api("GET", "/api/runs")).runs[0];
      return r.alert_id && r.finished_at && r.alert_id.startsWith("wien");
    }, { timeout: 30000, msg: "Einzelprüfung des pausierten Alarms nicht protokolliert" });
  });

  await step("Mobil: Alarm-Formular passt", async () => {
    await go(page, "alarme");
    await page.click("#new-alert-btn");
    await page.waitForSelector("#alert-dialog[open]");
    await addChip(page, "origins", "MUC");
    await page.click('#f-destinations .region-buttons button[data-code="@CN"]');
    await noHScroll(page, "Formular (mobil)");
    const box = await page.locator("#alert-dialog").boundingBox();
    assert(box.x >= 0 && box.x + box.width <= 390, `Dialog breiter als Bildschirm: ${JSON.stringify(box)}`);
    const sw = await page.$eval("#alert-dialog", (d) => [d.scrollWidth, d.clientWidth]);
    assert(sw[0] <= sw[1], `Dialog scrollt horizontal: ${sw}`);
    await shot(page, "mobile-form", false);
    await page.click("#alert-dialog .dialog-head [data-close]");
  });

  await step("Mobil: Deals mit Abgelaufenen", async () => {
    await go(page, "deals");
    await page.click("#view-deals .view-head .switch");
    await page.waitForSelector(".deal-card.expired");
    await noHScroll(page, "Deals abgelaufen (mobil)");
    await page.click("#view-deals .view-head .switch");
  });
  await ctx.close();
}

async function darkFlows(browser) {
  console.log("\nDunkelmodus:");
  for (const [label, vp] of [["desktop", { width: 1280, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
    const ctx = await browser.newContext({ viewport: vp, deviceScaleFactor: 1, locale: "de-DE", colorScheme: "dark" });
    const page = await ctx.newPage();
    currentPage = page;
    watchConsole(page, `dunkel-${label}`);
    await step(`Dunkel ${label}: Deals + Alarme`, async () => {
      await page.goto(base + "/#deals");
      await page.waitForSelector(".deal-card");
      const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
      const [r, g, b] = bg.match(/\d+/g).map(Number);
      assert(r + g + b < 200, `Hintergrund im Dunkelmodus zu hell: ${bg}`);
      await noHScroll(page, "Deals dunkel");
      await shot(page, `dark-${label}-deals`);
      await go(page, "alarme");
      await page.waitForSelector(".alert-card");
      await shot(page, `dark-${label}-alarme`);
      await go(page, "quellen");
      await page.waitForSelector(".health-row");
      await noHScroll(page, "Quellen dunkel");
      await shot(page, `dark-${label}-quellen`);
    });
    await ctx.close();
  }
}

// ------------------------------------------------------------------ Start
let code = 0;
try {
  await main();
  await step("Keine Konsolenfehler", async () => {
    assert(consoleErrors.length === 0, consoleErrors.join("\n"));
    assert(allowedHttpErrors.length === 0, `Erwarteter HTTP-Fehler kam nicht: ${allowedHttpErrors}`);
  });
} catch (e) {
  failures += 1;
  console.error(`\nAbbruch: ${e.stack || e.message}`);
} finally {
  await stopServer();
  hookServer.close();
}
if (failures) {
  console.log(`\n${failures} Fehler. Server-Log:\n${serverLog.split("\n").slice(-30).join("\n")}`);
  code = 1;
} else {
  console.log(`\nAlle ${results.length} Schritte grün. Screenshots: ${SHOTS}`);
}
process.exit(code);
