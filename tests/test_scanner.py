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

scan = load("trader-scanner", "scan")
D5 = os.path.join(NSE, "5m-60d")
DAILY = os.path.join(NSE, "daily")
UNIV = os.path.join(NSE, "universe")
DAY = datetime.date(2026, 10, 30)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class Universe(unittest.TestCase):
    def test_eq_series_only(self):
        eq = scan.parse_equity_list(read(os.path.join(UNIV, "EQUITY_L.csv")))
        self.assertEqual(sorted(eq), ["SYNTHA", "SYNTHB", "SYNTHC", "SYNTHD", "SYNTHE", "SYNTHG"])
        self.assertEqual(eq["SYNTHA"], "Synthetic Alpha Limited")

    def test_index_intersection(self):
        kept, counts = scan.build_universe(read(os.path.join(UNIV, "EQUITY_L.csv")),
                                           read(os.path.join(UNIV, "ind_nifty100list.csv")))
        self.assertEqual([s for s, _ in kept], ["SYNTHA", "SYNTHB", "SYNTHC", "SYNTHD", "SYNTHE"])
        self.assertEqual((counts["index"], counts["index_not_in_eq_list"]), (6, 1))
        everything, _ = scan.build_universe(read(os.path.join(UNIV, "EQUITY_L.csv")))
        self.assertIn("SYNTHG", [s for s, _ in everything])

    def test_columns_are_found_by_name_and_bad_files_say_so(self):
        text = "ISIN NUMBER, SERIES,SYMBOL,NAME OF COMPANY\nX,EQ,ABC,Abc Ltd\nY,BE,DEF,Def Ltd\n"
        self.assertEqual(scan.parse_equity_list(text), {"ABC": "Abc Ltd"})
        with self.assertRaises(scan.ScanError):
            scan.parse_equity_list("a,b\n1,2\n")
        with self.assertRaises(scan.ScanError):
            scan.parse_index_list("Company,Industry\nx,y\n")

    def test_cli_offline_and_a_missing_file(self):
        d = tempfile.mkdtemp()
        try:
            out_path = os.path.join(d, "universe.txt")
            code, out, _ = run(["universe", "--offline", UNIV, "--out", out_path])
            self.assertEqual(code, 0)
            self.assertIn("NIFTY100: 6 constituents, 5 kept (1 not in the EQ list)", out)
            self.assertEqual(read(out_path).splitlines()[0], "SYNTHA|Synthetic Alpha Limited")
            code, _, err = run(["universe", "--equity-file", os.path.join(d, "nope.csv"), "--index", "all",
                                "--no-write"])
            self.assertEqual(code, 1)
            self.assertIn("no NSE equity list at", err)
        finally:
            shutil.rmtree(d)


class Symbols(unittest.TestCase):
    def test_yahoo_symbols(self):
        self.assertEqual(scan.to_yahoo("reliance"), "RELIANCE.NS")
        self.assertEqual(scan.from_yahoo("RELIANCE.NS"), "RELIANCE")
        self.assertEqual(scan.chart_path("M&M", "5d", "5m"), "/v8/finance/chart/M%26M.NS?range=5d&interval=5m")

    def test_offline_loads_symbols_without_ns(self):
        data, errors = scan.load_offline(D5)
        self.assertEqual(sorted(data), ["SYNTHA", "SYNTHB", "SYNTHC", "SYNTHD", "SYNTHE"])
        self.assertEqual(errors, {})
        self.assertTrue(scan.is_synthetic(data))


class Daily(unittest.TestCase):
    def setUp(self):
        self.data, _ = scan.load_offline(DAILY)

    def test_the_planted_setups_and_the_floors(self):
        hits, counts, last = scan.scan_daily(self.data, sorted(self.data), ist("2026-10-30 08:45"), 50, 500000)
        by = {h["symbol"]: h["setups"] for h in hits}
        self.assertIn("breakout_20d", by["SYNTHA"])
        self.assertIn("oversold", by["SYNTHB"])
        self.assertIn("bull_flag", by["SYNTHD"])
        self.assertNotIn("SYNTHC", by)     # under ₹50
        self.assertNotIn("SYNTHE", by)     # thin
        self.assertEqual((counts["fetched"], counts["illiquid"], counts["scanned"]), (5, 2, 3))
        self.assertEqual(last, datetime.date(2026, 10, 29))

    def test_lower_floors_let_them_in(self):
        hits, counts, _ = scan.scan_daily(self.data, sorted(self.data), ist("2026-10-30 08:45"), 0, 0)
        self.assertEqual(counts["illiquid"], 0)
        self.assertIn("SYNTHC", [h["symbol"] for h in hits])

    def test_rsi_and_setups_on_known_closes(self):
        self.assertIsNone(scan.rsi([1, 2, 3]))
        self.assertEqual(scan.rsi([float(x) for x in range(1, 40)]), 100.0)
        closes = [100.0] * 25 + [101.0]
        self.assertIn("breakout_20d", scan.find_setups(closes)[0])

    def test_cli_daily(self):
        d = tempfile.mkdtemp()
        try:
            out_path = os.path.join(d, "w.json")
            code, out, _ = run(["daily", "--offline", DAILY, "--now", "2026-10-30 08:45", "--out", out_path])
            self.assertEqual(code, 0)
            self.assertIn("SYNTHETIC test data", out)
            self.assertIn("₹50+ and 5,00,000+ shares a day on average", out)
            doc = json.loads(read(out_path))
            self.assertEqual((doc["mode"], doc["synthetic"], doc["as_of"]), ("daily", True, "2026-10-29 close"))
        finally:
            shutil.rmtree(d)


class Orb(unittest.TestCase):
    def setUp(self):
        self.data, _ = scan.load_offline(D5)

    def hits(self, at, **kw):
        day, cutoff = scan.orb_window(ist("2026-10-30 %s" % at), None, 15, datetime.time(14, 45))
        return scan.scan_orb(self.data, sorted(self.data), day, cutoff, 15, **kw)

    def test_syntha_breaks_out_at_0950(self):
        hits, counts = self.hits("10:15")
        self.assertEqual([h["symbol"] for h in hits], ["SYNTHA"])
        h = hits[0]
        self.assertEqual((h["at"], h["bar_start"], h["price"], h["or_high"], h["or_low"]),
                         ("09:50", "09:45", 1416.0, 1414.8, 1401.2))
        self.assertEqual((counts["scanned"], counts["illiquid"]), (3, 2))

    def test_later_cutoff_adds_synthd_and_floors_keep_the_rest_out(self):
        hits, _ = self.hits("11:00")
        self.assertEqual([(h["symbol"], h["at"]) for h in hits], [("SYNTHA", "09:50"), ("SYNTHD", "10:35")])
        hits, _ = self.hits("11:00", min_price=0, min_avg_volume=0)
        self.assertEqual(sorted(h["symbol"] for h in hits), ["SYNTHA", "SYNTHC", "SYNTHD", "SYNTHE"])

    def test_before_the_range_closes_and_after_the_cutoff(self):
        with self.assertRaises(scan.ScanError) as e:
            scan.orb_window(ist("2026-10-30 09:30"), None, 15, datetime.time(14, 45))
        self.assertIn("Run orb again after 09:35", str(e.exception))
        with self.assertRaises(scan.ScanError) as e:
            scan.orb_window(ist("2026-10-30 14:50"), None, 15, datetime.time(14, 45))
        self.assertIn("expired", str(e.exception))
        with self.assertRaises(scan.ScanError) as e:
            scan.orb_window(ist("2026-11-10 10:00"), None, 15, None)
        self.assertIn("Diwali Balipratipada", str(e.exception))

    def test_a_missing_bar_makes_a_chart_unusable(self):
        s = self.data["SYNTHA"]
        bars = scan.session_bars(s, DAY, datetime.datetime(2026, 10, 30, 10, 15))
        self.assertEqual(len(bars), 12)
        self.assertTrue(scan.usable(bars, DAY, datetime.datetime(2026, 10, 30, 10, 15), 3))
        self.assertFalse(scan.usable(bars[:5] + bars[6:], DAY, datetime.datetime(2026, 10, 30, 10, 15), 3))

    def test_average_volume_uses_earlier_days_only(self):
        avg = scan.avg_session_volume(self.data["SYNTHE"], DAY)
        self.assertLess(avg, 100000)
        self.assertGreater(scan.avg_session_volume(self.data["SYNTHA"], DAY), 500000)

    def test_cli_orb(self):
        d = tempfile.mkdtemp()
        try:
            out_path = os.path.join(d, "w.json")
            code, out, _ = run(["orb", "--offline", D5, "--now", "2026-10-30 10:15", "--out", out_path])
            self.assertEqual(code, 0)
            self.assertIn("09:50  SYNTHA          ₹1,416.00  range 1401.20 to 1414.80  +0.08% over", out)
            doc = json.loads(read(out_path))
            self.assertEqual((doc["as_of"], doc["hits"][0]["symbol"]), ("2026-10-30 10:15 IST", "SYNTHA"))
            code, _, err = run(["orb", "--offline", D5])
            self.assertIn("give --at HH:MM", err)
            code, out, _ = run(["orb", "--offline", D5, "--at", "10:15", "--no-write", "--json"])
            self.assertEqual(json.loads(out)["counts"]["scanned"], 3)
        finally:
            shutil.rmtree(d)


class Network(unittest.TestCase):
    def setUp(self):
        self.saved = (scan.http_get, scan.SLEEP)
        self.calls, self.sleeps = [], []
        scan.SLEEP = self.sleeps.append

    def tearDown(self):
        scan.http_get, scan.SLEEP = self.saved

    def fake(self, codes):
        body = read(os.path.join(NSE, "5m-1d", "SYNTHA.json"))

        def http_get(url, timeout=20):
            self.calls.append(url)
            code = codes.pop(0)
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return body
        scan.http_get = http_get

    def test_query2_then_query1_and_a_pause_after_429(self):
        self.fake([429, 429, 200])
        data, failed = scan.fetch_charts(["SYNTHA"], "5d", "5m", 0.3)
        self.assertIn("SYNTHA", data)
        self.assertEqual([u.split("/")[2] for u in self.calls],
                         ["query2.finance.yahoo.com", "query1.finance.yahoo.com", "query2.finance.yahoo.com"])
        self.assertEqual(self.sleeps, [scan.BACKOFF])
        self.assertIn("/v8/finance/chart/SYNTHA.NS?range=5d&interval=5m", self.calls[0])

    def test_a_failure_is_counted_not_fatal(self):
        self.fake([403, 200])
        data, failed = scan.fetch_charts(["NOPE", "SYNTHA"], "5d", "5m", 0.3)
        self.assertIn("HTTP 403", failed["NOPE"])
        self.assertIn("SYNTHA", data)
        self.assertEqual(self.sleeps, [0.3])


if __name__ == "__main__":
    unittest.main()
