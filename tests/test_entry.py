import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, NSE, ist

entry = load("trader-entry", "entry")
F5 = os.path.join(NSE, "5m-1d")
F1 = os.path.join(NSE, "1m-1d")


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


def chart(sym="SYNTHA", folder=F5, interval="5m"):
    return entry.get_chart(sym, interval, offline=folder)


def ev(when, sym="SYNTHA", folder=F5, **kw):
    interval = kw.pop("interval", "5m")
    return entry.evaluate(chart(sym, folder, interval), ist("2026-10-30 %s" % when), interval=interval, **kw)


class TheSynthDay(unittest.TestCase):
    def test_triggers_at_0950_on_the_0945_bar(self):
        r = ev("09:50")
        self.assertEqual(r["status"], "triggered")
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("09:50", "09:45", 6, 1416.0))
        self.assertEqual(r["opening_range"]["high"], 1414.8)
        self.assertEqual(r["opening_range"]["low"], 1401.2)
        self.assertEqual(r["symbol"], "SYNTHA")

    def test_at_0948_the_0945_bar_is_still_forming(self):
        r = ev("09:48")
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["forming"]["bar_start"], "09:45")
        lines = entry.describe(r)
        self.assertIn("WAITING  last close ₹1,413.80 (09:40 bar), ₹1.00 under the range high ₹1,414.80", lines[2])
        self.assertIn("it does not count until it closes", lines[3])

    def test_before_the_open_and_while_the_range_forms(self):
        r = ev("09:00")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "pre-open"))
        self.assertIn("NSE opens at 09:15", r["reason"])
        r = ev("09:25")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "forming"))
        self.assertIn("2 of 3 range bars closed", r["reason"])

    def test_late_trigger_says_so(self):
        lines = entry.describe(ev("10:15"))
        self.assertIn("That was 25 minutes ago", " ".join(lines))
        self.assertIn("A late entry is a different trade", " ".join(lines))

    def test_no_breakout_expires_at_1445(self):
        r = ev("14:40", sym="SYNTHB")
        self.assertEqual(r["status"], "waiting")
        r = ev("14:50", sym="SYNTHB")
        self.assertEqual(r["status"], "expired")
        self.assertIn("by 14:45", r["reason"])

    def test_level_setup_and_volume(self):
        r = ev("10:15", setup="level", level=1416.1)
        self.assertEqual(r["status"], "triggered")
        self.assertGreater(r["trigger"]["price"], 1416.1)
        r = ev("09:50", min_volume_x=5.0)
        self.assertNotEqual(r["status"], "triggered")

    def test_one_minute_bars(self):
        r = ev("10:15", folder=F1, interval="1m")
        self.assertEqual(r["status"], "triggered")
        self.assertEqual(r["opening_range"]["of"], 15)
        self.assertEqual((r["opening_range"]["high"], r["opening_range"]["low"]), (1414.8, 1401.2))


class Rules(unittest.TestCase):
    def test_config_window(self):
        cfg = {"no_entries_before": "09:30", "no_entries_after": "09:45"}
        r = ev("10:15", cfg=cfg)
        self.assertEqual(r["status"], "expired")

    def test_bad_inputs(self):
        for kw in ({"interval": "15m"}, {"setup": "level"}, {"or_minutes": 7}, {"setup": "flag"}):
            with self.assertRaises(entry.EntryError):
                ev("10:15", **kw)

    def test_closed_days(self):
        c = chart()
        for when, word in (("2026-10-31 10:00", "weekend"), ("2026-11-10 10:00", "Diwali"),
                           ("2026-11-08 14:30", "Muhurat")):
            r = entry.evaluate(c, ist(when))
            self.assertEqual(r["status"], "no-data")
            self.assertIn(word, r["reason"])

    def test_the_file_must_hold_the_symbol(self):
        d = tempfile.mkdtemp()
        try:
            shutil.copy(os.path.join(F5, "SYNTHB.json"), os.path.join(d, "SYNTHA.json"))
            with self.assertRaises(entry.EntryError):
                entry.get_chart("SYNTHA", "5m", offline=d)
        finally:
            shutil.rmtree(d)

    def test_yahoo_symbols(self):
        self.assertEqual(entry.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(entry.chart_path("BAJAJ-AUTO", "1d", "5m"), "/v8/finance/chart/BAJAJ-AUTO.NS?range=1d&interval=5m")


class Network(unittest.TestCase):
    def setUp(self):
        self.saved = (entry._open_url, entry._sleep)
        self.calls, self.sleeps = [], []
        entry._sleep = self.sleeps.append

    def tearDown(self):
        entry._open_url, entry._sleep = self.saved

    def fake(self, codes):
        with open(os.path.join(F5, "SYNTHA.json")) as fh:
            body = fh.read()

        def op(url, timeout=20):
            self.calls.append(url)
            code = codes.pop(0)
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return body
        entry._open_url = op

    def test_429_retries_once_then_query1(self):
        self.fake([429, 429, 200])
        entry.yahoo_get(entry.chart_path("SYNTHA", "1d", "5m"))
        self.assertEqual([u.split("/")[2].split(".")[0] for u in self.calls], ["query2", "query2", "query1"])
        self.assertEqual(self.sleeps, [entry.BACKOFF])

    def test_still_busy_is_one_plain_error(self):
        self.fake([503, 503])
        with self.assertRaises(entry.EntryError) as e:
            entry.yahoo_get("/x")
        self.assertIn("wait a minute", str(e.exception))


class Watch(unittest.TestCase):
    def test_replay_triggers_on_the_first_closed_bar(self):
        lines = []
        clock = entry.SimClock(ist("2026-10-30 09:10"))
        opts = {"setup": "orb", "or_minutes": 15, "interval": "5m", "level": None, "min_volume_x": None,
                "cfg": dict(entry.DEFAULTS)}
        code, res = entry.watch_loop(lambda now: chart(), clock.now, clock.sleep, lines.append, opts)
        self.assertEqual(code, 0)
        self.assertEqual(res["trigger"]["time"], "09:50")
        self.assertTrue(any("TRIGGERED: closed above ₹1,414.80" in x for x in lines))
        self.assertTrue(lines[0].startswith("09:10 IST  not open yet"))

    def test_expires_without_a_trigger(self):
        lines = []
        clock = entry.SimClock(ist("2026-10-30 14:00"))
        opts = {"setup": "orb", "or_minutes": 15, "interval": "5m", "level": None, "min_volume_x": None,
                "cfg": dict(entry.DEFAULTS)}
        code, _ = entry.watch_loop(lambda now: chart("SYNTHB"), clock.now, clock.sleep, lines.append, opts)
        self.assertEqual(code, 1)
        self.assertTrue(lines[-1].startswith("EXPIRED"))


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_check_write_is_the_contract_shape(self):
        out_path = os.path.join(self.d, "entry.json")
        code, out, _ = run(["check", "SYNTHA", "--offline", F5, "--now", "2026-10-30 09:50", "--write", out_path,
                            "--config", os.path.join(self.d, "none.json")])
        self.assertEqual(code, 0)
        self.assertIn("TRIGGERED 09:50 at ₹1,416.00", out)
        with open(out_path) as fh:
            doc = json.load(fh)
        self.assertEqual({k: doc[k] for k in ("symbol", "status", "time", "price", "bar_index", "date")},
                         {"symbol": "SYNTHA", "status": "triggered", "time": "09:50", "price": 1416.0,
                          "bar_index": 6, "date": "2026-10-30"})
        self.assertEqual(doc["opening_range"], {"high": 1414.8, "low": 1401.2, "minutes": 15})

    def test_longs_only(self):
        code, _, err = run(["check", "SYNTHA", "--side", "short", "--offline", F5, "--now", "2026-10-30 09:50"])
        self.assertEqual(code, 1)
        self.assertIn("longs only", err)

    def test_watch_and_watchlist(self):
        out_path = os.path.join(self.d, "entry.json")
        code, out, _ = run(["watch", "SYNTHA", "--offline", F5, "--now", "2026-10-30 09:10", "--out", out_path,
                            "--config", os.path.join(self.d, "none.json")])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(out_path))
        wl = os.path.join(self.d, "w.json")
        with open(wl, "w") as fh:
            json.dump({"hits": [{"symbol": "SYNTHA"}, {"symbol": "SYNTHB"}]}, fh)
        code, out, _ = run(["watchlist", "--watchlist", wl, "--offline", F5, "--now", "2026-10-30 10:15",
                            "--config", os.path.join(self.d, "none.json")])
        self.assertEqual(code, 0)
        self.assertIn("1 of 2 triggered", out)
        self.assertIn("SYNTHA        triggered  09:50     ₹1,416.00", out)


def edited(**slots):
    """SYNTHA's day with some 5-minute bars changed: slot=(open, high, low, close) or None for a null bar."""
    with open(os.path.join(F5, "SYNTHA.json")) as fh:
        d = json.load(fh)
    q = d["chart"]["result"][0]["indicators"]["quote"][0]
    for k, v in slots.items():
        i = int(k[1:])
        for j, key in enumerate(("open", "high", "low", "close")):
            q[key][i] = None if v is None else v[j]
    return entry.parse_chart(json.dumps(d), "SYNTHA")


def live_provider(base, spike=1415.5, lag_until=None, calls=None):
    """Bars the way Yahoo serves them at `now`: closed bars final, the bar in progress spiking above
    the range high, plus a live tick off the grid. lag_until: before then Yahoo lags 30 seconds."""
    def provider(now):
        if calls is not None:
            calls.append(now)
        nts = entry.ts_of(now)
        data_ts = nts - 30 if (lag_until is not None and now < lag_until) else nts
        bars = []
        for b in base["bars"]:
            if b["t"] % 300 or b["t"] > data_ts:
                continue
            b = dict(b)
            if b["t"] + 300 > data_ts:
                b["c"], b["h"] = spike, max(b["h"], spike)
            bars.append(b)
        if data_ts % 300:
            bars.append({"t": data_ts, "o": spike, "h": spike, "l": spike, "c": spike, "v": 0})
        return {"symbol": "SYNTHA", "meta": {}, "bars": bars, "prev_close": 1400.0, "data_time": data_ts}
    return provider


class PortedLogic(unittest.TestCase):
    """Upstream tests of logic that did not change with the market, on NSE times."""
    OPTS = {"setup": "orb", "or_minutes": 15, "interval": "5m", "level": None, "min_volume_x": None, "cfg": None}

    def test_a_wick_and_a_close_at_the_high_do_not_trigger(self):
        c = edited(s6=(1413.8, 1416.2, 1412.9, 1414.8))       # 09:45: wick above, close AT the high
        r = entry.evaluate(c, ist("2026-10-30 10:15"))
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_start"]), ("09:55", "09:50"))

    def test_a_null_bar_is_dropped(self):
        r = entry.evaluate(edited(s4=None), ist("2026-10-30 10:15"))
        self.assertNotIn(4, [b["slot"] for b in r["_bars"]])
        self.assertEqual(r["trigger"]["time"], "09:50")

    def test_or_minutes_30_and_a_later_start(self):
        r = ev("10:15", or_minutes=30)
        self.assertEqual((r["opening_range"]["bars"], r["opening_range"]["high"]), (6, 1414.8))
        self.assertEqual(r["trigger"]["time"], "09:50")
        r = ev("10:30", cfg={"no_entries_before": "10:00", "no_entries_after": "14:45"})
        self.assertGreaterEqual(r["trigger"]["time"], "10:00")

    def test_the_bar_closing_at_the_clock_waits_for_yahoo(self):
        base = chart()
        p = live_provider(base, lag_until=ist("2026-10-30 09:50") + datetime.timedelta(seconds=40))
        r = entry.evaluate(p(ist("2026-10-30 09:50") + datetime.timedelta(seconds=10)), ist("2026-10-30 09:50")
                           + datetime.timedelta(seconds=10))
        self.assertEqual(r["status"], "waiting")
        self.assertIn("Yahoo's last trade is 09:49:40", r["lag_note"])

    def test_parse_and_ranges(self):
        with self.assertRaises(entry.EntryError):
            entry.parse_now("tomorrow")
        with self.assertRaises(entry.EntryError):
            entry.parse_hhmm("25:00", "x")
        today = datetime.date(2026, 10, 30)
        self.assertEqual(entry.chart_range(today, today, "5m"), "1d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 27), today, "5m"), "5d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 5), today, "5m"), "1mo")
        self.assertEqual(entry.chart_range(datetime.date(2026, 9, 10), today, "5m"), "60d")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 10, 5), today, "1m")

    def test_the_file_and_the_interval_must_agree(self):
        code, _, err = run(["check", "SYNTHA", "--offline", F1, "--now", "2026-10-30 10:15"])
        self.assertEqual(code, 1)
        self.assertIn("1m bars, not 5m", err)

    def loop(self, provider, start, **kw):
        clock = entry.SimClock(ist(start))
        lines, fired = [], []
        code, res = entry.watch_loop(provider, clock.now, clock.sleep, lines.append, dict(self.OPTS),
                                     on_trigger=lambda r, now: fired.append((r, now)), **kw)
        return code, res, lines, fired, clock

    def test_fires_on_the_first_closed_bar_not_the_spike_in_progress(self):
        calls = []
        code, res, lines, fired, _ = self.loop(live_provider(chart(), calls=calls), "2026-10-30 09:42", every=60)
        self.assertEqual((code, len(fired)), (0, 1))
        self.assertEqual(fired[0][0]["trigger"]["time"], "09:50")
        self.assertEqual([entry.to_ist(c).strftime("%H:%M:%S") for c in calls], ["09:42:00", "09:45:02", "09:50:02"])
        text = "\n".join(lines)
        self.assertIn("09:40 bar  closed ₹1,413.80  ₹1.00 under ₹1,414.80", text)
        self.assertEqual(text.count("TRIGGERED"), 1)

    def test_waits_for_yahoo_to_catch_up(self):
        calls = []
        lag = ist("2026-10-30 09:50") + datetime.timedelta(seconds=30)
        code, res, _, fired, _ = self.loop(live_provider(chart(), lag_until=lag, calls=calls), "2026-10-30 09:47",
                                           every=30)
        self.assertEqual(code, 0)
        self.assertEqual([entry.to_ist(c).strftime("%H:%M:%S") for c in calls], ["09:47:00", "09:50:02", "09:50:32"])

    def test_a_computer_that_dozes_off_catches_up_when_it_wakes(self):
        class Dozing(entry.SimClock):
            naps = 0

            def sleep(self, seconds):
                entry.SimClock.sleep(self, seconds)
                self.naps += 1
                if self.naps == 2:
                    self.t += datetime.timedelta(minutes=12)
        clock = Dozing(ist("2026-10-30 09:31"))
        calls, lines = [], []
        code, res = entry.watch_loop(live_provider(chart(), calls=calls), clock.now, clock.sleep, lines.append,
                                     dict(self.OPTS), every=30)
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        for start in ("09:30", "09:35", "09:40", "09:45"):
            self.assertIn(start + " bar  closed", text)
        self.assertEqual(res["trigger"]["time"], "09:50")

    def test_until_closed_day_and_repeated_errors(self):
        code, res, lines, fired, _ = self.loop(live_provider(chart("SYNTHB"), spike=200.0), "2026-10-30 10:00",
                                               every=300, until=datetime.datetime(2026, 10, 30, 11, 0))
        self.assertEqual(code, 1)
        self.assertIn("--until 11:00", lines[-1])
        code, _, lines, _, _ = self.loop(live_provider(chart()), "2026-10-31 10:00")
        self.assertEqual(code, 1)
        self.assertIn("weekend", lines[0])

        def broken(now):
            raise entry.EntryError("Yahoo did not answer")
        code, _, lines, _, _ = self.loop(broken, "2026-10-30 10:00", every=30)
        self.assertEqual(code, 1)
        self.assertIn("Stopping after 5 failed reads", lines[-1])


if __name__ == "__main__":
    unittest.main()
