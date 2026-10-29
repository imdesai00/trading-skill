import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, ROOT, load, write

news = load("trader-news", "news")
DIR = os.path.join(FIX, "trader-news")      # SYNTHETIC feeds: the real ones could not be fetched when this was built
NOW = "2026-10-30 08:30"                    # India time, a trading Friday, before the open
WATCH = {"SYNTHA": "Synthetic Alpha Limited", "SYNTHB": "Synthetic Beta Limited"}


def raw(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


class Parsing(unittest.TestCase):
    def test_rss_items_carry_source_symbol_and_utc_time(self):
        items = news.parse_rss(raw("yahoo-SYNTHA.xml"), "Yahoo Finance", "SYNTHA")
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["symbol"], "SYNTHA")
        self.assertEqual(items[0]["time"].isoformat(), "2026-10-30T02:15:00")      # naive UTC
        self.assertTrue(items[0]["id"].startswith("Yahoo Finance:"))

    def test_a_time_without_a_zone_is_india_time(self):
        self.assertEqual(news.parse_time("2026-10-30T09:00:00").isoformat(), "2026-10-30T03:30:00")
        self.assertEqual(news.parse_time("Fri, 30 Oct 2026 09:00:00 +0530").isoformat(), "2026-10-30T03:30:00")
        self.assertIsNone(news.parse_time("soon"))

    def test_bad_xml_is_a_news_error(self):
        with self.assertRaises(news.NewsError):
            news.parse_rss(b"<html>not a feed", "NSE announcements")

    def test_chart(self):
        m = news.parse_chart(raw("chart-SYNTHA.json"), "SYNTHA")
        self.assertEqual((m["price"], m["prev_close"], m["pct"]), (105.0, 100.0, 5.0))
        with self.assertRaises(news.NewsError):
            news.parse_chart(b"{}", "X")

    def test_names_file(self):
        names = news.read_names(raw("EQUITY_L.csv").decode())
        self.assertEqual(names["SYNTHA"], "Synthetic Alpha Limited")


class Matching(unittest.TestCase):
    def item(self, title, symbol=None, source="Moneycontrol"):
        return {"id": "x:" + title, "source": source, "symbol": symbol, "title": title, "summary": "",
                "link": "", "time": None}

    def tagged(self, title, watch=WATCH, **kw):
        return news.tag([self.item(title, **kw)], watch)

    def test_company_name_is_an_exact_match(self):
        r = self.tagged("Synthetic Beta shares jump on block deal")
        self.assertEqual((r[0]["touches"], r[0]["possible"]), (["SYNTHB"], []))
        self.assertEqual(r[0]["tags"], ["deal"])

    def test_symbol_forms(self):
        for title in ("SYNTHA - outcome of board meeting", "Alpha story (SYNTHA) rises", "Shares of NSE: SYNTHA fall"):
            self.assertEqual(self.tagged(title)[0]["touches"], ["SYNTHA"], title)

    def test_an_unwatched_company_is_dropped(self):
        self.assertEqual(self.tagged("Synthetic Gamma plans buyback"), [])

    def test_a_short_symbol_as_a_bare_word_does_not_match(self):
        watch = {"ITC": "ITC Limited", "TCS": None}
        self.assertEqual(self.tagged("Rising demand for TCS style outsourcing", watch=watch), [])
        self.assertEqual(self.tagged("ITC Limited posts profit", watch=watch)[0]["touches"], ["ITC"])
        self.assertEqual(self.tagged("Shares of (TCS) rise", watch=watch)[0]["touches"], ["TCS"])

    def test_symbols_with_punctuation_are_escaped(self):
        watch = {"M&M": "Mahindra & Mahindra Limited", "BAJAJ-AUTO": None}
        self.assertEqual(self.tagged("(M&M) tractor sales up", watch=watch)[0]["touches"], ["M&M"])
        self.assertEqual(self.tagged("(BAJAJ-AUTO) exports", watch=watch)[0]["touches"], ["BAJAJ-AUTO"])

    def test_a_distinctive_first_word_is_only_a_possible_match(self):
        watch = {"INFYX": "Infyx Technologies Limited"}
        r = self.tagged("Infyx wins a large order", watch=watch)
        self.assertEqual((r[0]["touches"], r[0]["possible"]), ([], ["INFYX"]))
        r = self.tagged("Infyx Technologies wins a large order", watch=watch)
        self.assertEqual((r[0]["touches"], r[0]["possible"]), (["INFYX"], []))

    def test_a_common_first_word_never_matches_alone(self):
        watch = {"TATAPWR": "Tata Power Company Limited", "ADANIENT": "Adani Enterprises Limited"}
        self.assertEqual(self.tagged("Tata Motors and Adani group in the news", watch=watch), [])

    def test_a_first_word_two_of_your_companies_share_is_dropped(self):
        self.assertEqual(self.tagged("Synthetic energy stocks rally"), [])

    def test_a_yahoo_item_belongs_to_its_symbol(self):
        r = self.tagged("Some headline with no names", symbol="SYNTHA", source="Yahoo Finance")
        self.assertEqual(r[0]["touches"], ["SYNTHA"])

    def test_tags(self):
        cases = {"Board meeting on 5 Nov to consider results": {"results", "board meeting"},
                 "SEBI order against promoter": {"legal"}, "Broker raises target price": {"analyst"},
                 "ASM stage 2 for the stock": {"surveillance"}, "Bonus and stock split proposed": {"capital"},
                 "RBI policy keeps repo rate": {"macro", "regulatory"}}
        for title, want in cases.items():
            got = set(self.tagged(title, symbol="SYNTHA")[0]["tags"])
            self.assertTrue(want <= got, (title, got))

    def test_dedupe_merges_the_same_story_from_two_sources(self):
        a = self.item("Same story", symbol="SYNTHA")
        a["time"] = news.parse_time("2026-10-30T09:00:00")
        b = self.item("Same story")
        b["time"] = a["time"]
        b["id"] = "y"
        out = news.dedupe(news.tag([a, b], WATCH) if False else [dict(a, touches=["SYNTHA"], possible=[]),
                                                                 dict(b, touches=["SYNTHB"], possible=[])])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["touches"], ["SYNTHA", "SYNTHB"])


class Warnings(unittest.TestCase):
    def test_results_today_warns_but_yesterdays_does_not(self):
        now = news.parse_now(NOW)
        today = {"tags": ["results"], "touches": ["SYNTHA"], "title": "Q2 results", "time": news.parse_time("2026-10-30T02:00:00")}
        yday = {"tags": ["results"], "touches": ["SYNTHB"], "title": "Q2 results", "time": news.parse_time("2026-10-29T02:00:00")}
        other = {"tags": ["analyst"], "touches": ["SYNTHC"], "title": "upgrade", "time": news.parse_time("2026-10-30T02:00:00")}
        self.assertEqual(list(news.results_warnings([today, yday, other], now)), ["SYNTHA"])


class Selection(unittest.TestCase):
    def test_window_and_seen(self):
        now = news.parse_now(NOW)
        items = [{"id": "a", "time": news.parse_time("2026-10-30T02:00:00")},
                 {"id": "b", "time": news.parse_time("2026-10-20T02:00:00")},
                 {"id": "c", "time": news.parse_time("2026-10-31T02:00:00")},     # from after --now
                 {"id": "d", "time": None}]
        state = {}
        shown = news.select(items, state, now, hours=48)
        self.assertEqual(sorted(i["id"] for i in shown), ["a", "d"])
        news.remember(items, state, now)
        self.assertEqual(news.select(items, state, now, hours=48), [])
        self.assertEqual(len(news.select(items, state, now, hours=48, show_all=True)), 2)

    def test_old_state_is_pruned(self):
        now = news.parse_now(NOW)
        state = {"seen": {"old": "2026-01-01T00:00:00", "new": "2026-10-29T00:00:00"}}
        news.remember([], state, now)
        self.assertEqual(list(state["seen"]), ["new"])


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.wl = os.path.join(self.d, "watchlist.json")
        write(self.wl, {"hits": [{"symbol": "SYNTHA", "name": "Synthetic Alpha Limited"}, {"symbol": "SYNTHB"}]})
        self.state = os.path.join(self.d, "state", "news.json")
        self.cfg = os.path.join(self.d, "config.json")
        write(self.cfg, {"news_sources": {}, "moneycontrol_sections": ["latestnews"]})

    def tearDown(self):
        shutil.rmtree(self.d)

    def run_news(self, *extra):
        argv = ["--watchlist", self.wl, "--plan", os.path.join(self.d, "p"), "--portfolio", os.path.join(self.d, "q"),
                "--state", self.state, "--config", self.cfg, "--offline", DIR, "--now", NOW] + list(extra)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = news.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_report_then_nothing_new(self):
        code, out, _ = self.run_news()
        self.assertEqual(code, 0)
        self.assertIn("RESULTS TODAY: SYNTHA", out)
        self.assertIn("MOVING", out)
        self.assertIn("IST", out)
        self.assertNotIn(" ET", out)
        for source in ("Yahoo Finance", "NSE announcements", "Moneycontrol"):
            self.assertIn(source, out)
        self.assertNotIn("Old story from last week", out)         # outside the 48-hour window
        self.assertNotIn("Gamma", out)                             # not on the watchlist
        story = [ln for ln in out.splitlines() if ln.startswith("  2026") and "Q2 results: net profit up 26%" in ln]
        self.assertEqual(len(story), 1)                                       # two sources, one story
        code, out, _ = self.run_news()
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)
        self.assertIn("RESULTS TODAY", out)                        # the warning does not depend on what is new
        code, out, _ = self.run_news("--all")
        self.assertEqual(code, 1)                                  # nothing NEW, though --all lists the old ones
        self.assertIn("Synthetic Beta shares jump on block deal", out)

    def test_dry_run_leaves_no_state(self):
        self.run_news("--dry-run")
        self.assertFalse(os.path.exists(self.state))

    def test_sources_can_be_switched_off(self):
        write(self.cfg, {"news_sources": {"moneycontrol": False, "nse": False}})
        _, out, _ = self.run_news()
        self.assertNotIn("Moneycontrol", out)
        self.assertNotIn("NSE announcements", out)
        self.assertIn("Yahoo Finance", out)

    def test_a_failing_source_is_reported_and_the_rest_still_run(self):
        write(self.cfg, {"news_sources": {}, "moneycontrol_sections": ["latestnews", "results"]})
        _, out, _ = self.run_news()
        self.assertIn("note: Moneycontrol results", out)
        self.assertIn("Yahoo Finance", out)

    def test_json(self):
        _, out, _ = self.run_news("--json")
        doc = json.loads(out)
        self.assertEqual(list(doc["results_today"]), ["SYNTHA"])
        self.assertTrue(any(m["moving"] for m in doc["moves"]))
        self.assertTrue(all(i["time"] for i in doc["items"]))

    def test_no_symbols_is_an_error(self):
        os.remove(self.wl)
        code, _, err = self.run_news()
        self.assertEqual(code, 2)
        self.assertIn("no symbols", err)

    def test_symbols_flag_overrides_the_watchlist(self):
        _, out, _ = self.run_news("--symbols", "SYNTHB", "--no-moves")
        self.assertNotIn("RESULTS TODAY", out)
        self.assertIn("SYNTHB", out)

    def test_names_come_from_the_universe_file_when_the_watchlist_has_none(self):
        write(self.wl, {"hits": [{"symbol": "SYNTHB"}]})
        _, out, _ = self.run_news("--no-moves")
        self.assertIn("Synthetic Beta shares jump on block deal", out)     # matched by name, via EQUITY_L.csv


class Source(unittest.TestCase):
    def test_no_us_leftovers(self):
        with open(os.path.join(ROOT, "trader-news", "news.py"), encoding="utf-8") as fh:
            text = fh.read()
        for bad in ("sec.gov", "EDGAR", "8-K", "Eastern", "NYSE", "NASDAQ", "region=US"):
            self.assertNotIn(bad, text, bad)


if __name__ == "__main__":
    unittest.main()
