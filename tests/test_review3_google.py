"""Review Runde 3 (Betrieb Google/Rotation): G1-G4, G6 - jeweils der reproduzierte Fall."""

import logging
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest import mock

from dealmonitor import http, rotation
from dealmonitor.appsettings import Context
from dealmonitor.scheduler import execute
from dealmonitor.sources.googleflights import GoogleFlightsSource

import test_google_insights as gi
import test_rotation as tr

ROUTE = [("ZRH", "HAM")]


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


class ErrorPageWithFlightsTest(unittest.TestCase):
    """G1: ErrorResponse-Kennung, aber Flüge auf der Seite - der zweite Abruf darf sie nicht
    verwerfen, wenn er nichts Besseres bringt."""

    PAGE = gi.OW.replace("data:[[null,", "data:[13,[null,", 1)

    def test_page_is_error_with_rows(self):
        from dealmonitor.sources.googleflights import empty_kind, parse_results
        rows = parse_results(self.PAGE)
        self.assertTrue(rows)
        self.assertEqual(empty_kind(self.PAGE, rows), "error")

    def test_second_error_page_keeps_first_rows(self):
        src = gi.source([self.PAGE, gi.ERROR_PAGE])
        rows = src.query("ZRH", "HAM", date(2027, 1, 4), None, "economy", False)
        self.assertTrue(rows)
        self.assertEqual((src.requests_made, src.retries, src.empty_fetches), (2, 1, 0))

    def test_second_network_error_keeps_first_rows(self):
        src = gi.source([self.PAGE, http.HttpError("weg", network=True)])
        rows = src.query("ZRH", "HAM", date(2027, 1, 4), None, "economy", False)
        self.assertTrue(rows)
        self.assertEqual(src.empty_fetches, 0)
        self.assertEqual(src._consecutive_failures, 0)

    def test_second_page_with_rows_wins(self):
        src = gi.source([self.PAGE, gi.OW])
        rows = src.query("ZRH", "HAM", date(2027, 1, 4), None, "economy", False)
        self.assertTrue(rows)
        url = src.calls[-1]
        self.assertIsNotNone(src.insight(next(iter(src._insights))), url)

    def test_error_without_rows_still_empty(self):
        src = gi.source([gi.ERROR_PAGE, http.HttpError("weg", network=True)])
        with self.assertRaises(http.HttpError):
            src.query("ZRH", "HAM", date(2027, 1, 4), None, "economy", False)


class MidnightCoverageTest(unittest.TestCase):
    """G2: die täglich schwankende Zahl der Zweitbesuche verschiebt den Grundkreis nicht."""

    def _cover(self, start: datetime, per_run: int, runs: int, n0: int = 1000):
        seen, t = set(), start
        for k in range(runs):
            seen |= set(rotation.slice_for_run(t, ROUTE, 90, per_run, today=t.date(),
                                               period_hours=3, index=n0 + k))
            t += timedelta(hours=3)
        return seen

    def test_every_day_checked_across_midnight(self):
        # Fall aus dem Review: 22.11. 12:10 bis 23.11. 09:10, 23 je Lauf, 8 Läufe
        start = datetime(2026, 11, 22, 12, 10)
        seen = self._cover(start, 23, 8, n0=1132)
        wanted = {("ZRH", "HAM", date(2026, 11, 23) + timedelta(days=k)) for k in range(90)}
        self.assertEqual(wanted - seen, set())

    def test_any_start_any_day(self):
        for day in range(30):
            for hour in (0, 12, 21):
                start = datetime(2026, 11, 1, hour, 10) + timedelta(days=day)
                seen = self._cover(start, 23, 8, n0=day * 7 + hour)
                last = start + timedelta(hours=21)
                wanted = {("ZRH", "HAM", last.date() + timedelta(days=k)) for k in range(1, 90)
                          if last.date() + timedelta(days=k) <= start.date() + timedelta(days=90)}
                self.assertEqual(wanted - seen, set(), start)

    def test_small_ring_once_each(self):
        part = rotation.slice_for_run(datetime(2026, 11, 22, 12), ROUTE, 10, 23,
                                      today=date(2026, 11, 22))
        self.assertEqual(len(part), 10)
        self.assertEqual(len(set(part)), 10)

    def test_base_share_and_extras(self):
        part = rotation.slice_for_run(datetime(2026, 11, 22, 12), tr.ROUTES, 90, 40,
                                      today=date(2026, 11, 22), index=5)
        self.assertEqual(len(part), 40)                         # 23 Grund + 17 Zweitbesuche
        self.assertEqual(rotation.base_share(180, 40), 23)
        self.assertEqual(rotation.cycle_hours(tr.ROUTES, 90, 40), 24)
        self.assertEqual(rotation.cycle_hours(tr.ROUTES, 90, 6), 90)


class RunNumberTest(unittest.TestCase):
    """G3: Sofort-Prüfung und "Nur diesen prüfen" zählen den Lauf-Index nicht weiter."""

    def test_peek_does_not_advance(self):
        m = rotation.Memory({})
        t0 = datetime(2026, 11, 6, 0, 10)
        seq = []
        for h, full in [(0, True), (1.6, False), (3, True), (3.2, False), (6, True), (9, True)]:
            seq.append(m.run_number("a", t0 + timedelta(hours=h), 3.0, advance=full))
        n = seq[0]
        self.assertEqual(seq, [n, n, n + 1, n + 1, n + 2, n + 3])

    def test_peek_without_state_stores_nothing(self):
        m = rotation.Memory({})
        m.run_number("a", datetime(2026, 11, 6, 1), advance=False)
        self.assertEqual((m.runs, m.changed), ({}, False))

    def test_hot_source_keeps_index_and_cycle(self):
        store = {}

        class St:
            def source_state(self, name):
                return store.get(name, {})

            def set_source_state(self, name, data):
                store[name] = data

        alert = tr.oneway()
        full = tr.source(state_store=St())
        full.begin_run([alert])
        full.fetch(alert)
        runs = dict(store[rotation.STATE_NAME]["runs"]["zrh-ham-oneway"])
        self.assertIn("cycle_h", runs)
        hot = tr.source(state_store=St(), max_requests=5, now=tr.NOW + timedelta(hours=2))
        hot.advance_rotation = False
        hot.begin_run([alert])
        hot.fetch(alert)
        self.assertEqual(store[rotation.STATE_NAME]["runs"]["zrh-ham-oneway"], runs)

    def test_scheduler_sets_flag_by_mode(self):
        class Fake:
            name = "google_flights"
            advance_rotation = True

        seen = []

        def fake_run(cfg, sources, *a, **kw):
            seen.append(sources[0].advance_rotation)
            raise RuntimeError("Ende")

        with tempfile.TemporaryDirectory() as tmp:
            ctx = Context(tmp, Path(tmp) / "fehlt.toml", env={},
                          source_factory=lambda c, n: [], today=tr.TODAY)
            cfg = tr.cfg()
            cfg.alerts.append(tr.oneway())
            with mock.patch.object(ctx, "sources", side_effect=lambda *a, **kw: [Fake()]), \
                    mock.patch.object(ctx, "build_config", return_value=cfg), \
                    mock.patch("dealmonitor.scheduler.run", side_effect=fake_run):
                execute(ctx, None, "schedule", notifiers=[], mode="full")
                execute(ctx, "zrh-ham-oneway", "manual", notifiers=[], mode="full")
                execute(ctx, "zrh-ham-oneway", "hot", notifiers=[], mode="hot")
        self.assertEqual(seen, [True, False, False])


class AgeLimitTest(unittest.TestCase):
    """G4: gemerkte Preise gelten höchstens einen Durchgang (+Takt), 36-48 h."""

    def test_report_hours(self):
        self.assertEqual(rotation.report_hours(24, 3), 36)
        self.assertEqual(rotation.report_hours(90, 3), 48)
        self.assertEqual(rotation.report_hours(float("inf"), 3), 48)

    def test_old_state_keep_is_capped(self):
        self.assertEqual(rotation.Memory({"keep_h": 135}).keep_hours, 48)

    def test_small_budget_drops_old_prices(self):
        """6 Abfragen je Lauf (Standard-Budget): 90-h-Durchgang, aber nichts über 48 h alt."""
        store = {}

        class St:
            def source_state(self, name):
                return store.get(name, {})

            def set_source_state(self, name, data):
                store[name] = data

        alert = tr.oneway()
        t = tr.NOW
        for _ in range(30):
            src = tr.source(state_store=St(), now=t)
            src._shares = {alert.id: 6}
            src.fetch(alert)
            mem = rotation.Memory(store[rotation.STATE_NAME])
            for days in mem.legs.values():
                for entry in days.values():
                    self.assertLessEqual(t - datetime.fromisoformat(entry["at"]),
                                         timedelta(hours=48))
            ages = [t - entry["at"] for entry in
                    mem.get("ZRH", "HAM", alert.cabin, t.date(), t).values()]
            self.assertTrue(ages and max(ages) <= timedelta(hours=48))
            t += timedelta(hours=3)
        mem = rotation.Memory(store[rotation.STATE_NAME])
        self.assertEqual(mem.keep_hours, 48)
        self.assertEqual(mem.cycle_hours(alert.id), 45)

    def test_cycle_info_full_budget(self):
        store = {}

        class St:
            def source_state(self, name):
                return store.get(name, {})

            def set_source_state(self, name, data):
                store[name] = data

        alert = tr.oneway()
        src = tr.source(state_store=St())
        src._shares = {alert.id: 23}
        src.fetch(alert)
        mem = rotation.Memory(store[rotation.STATE_NAME])
        self.assertEqual((mem.cycle_hours(alert.id), mem.keep_hours), (24, 36))


class SameDayComboTest(unittest.TestCase):
    """G6: min_nights=0 kombiniert nie Hin und Rück am selben Tag (Uhrzeit unbekannt)."""

    def test_zero_nights_means_one(self):
        day = date(2027, 1, 14)
        out = {day: {"price": 50}}
        back = {day: {"price": 40}, day + timedelta(days=1): {"price": 60}}
        combos = rotation.combine(out, back, 0, 5)
        self.assertEqual([(a, b) for a, b, _, _ in combos], [(day, day + timedelta(days=1))])


if __name__ == "__main__":
    unittest.main()
