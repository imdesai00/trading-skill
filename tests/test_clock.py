"""The NSE clock block that every script carries, and the charges calculator."""

import datetime
import os
import re
import unittest

from _load import load, ROOT, ist

SCRIPTS = [("trader-alerts", "alerts"), ("trader-scanner", "scan"), ("trader-entry", "entry"),
           ("trader-exit", "exit"), ("trader-timemachine", "timemachine"), ("trader-budget", "size"),
           ("trader-risk", "check"), ("trader-broker", "ticket"), ("trader-broker", "paper"),
           ("trader-report", "report"), ("trader-news", "news"), ("trader-insider", "insider"),
           ("trader-control", "control")]
START = "# ---------------------------------------------------------------- NSE clock (same block in every script)"
END = "# ---------------------------------------------------------------- end NSE clock"
alerts = load("trader-alerts", "alerts")
chg = load("trader-budget", "charges")
D = datetime.date


def block(folder, name):
    with open(os.path.join(ROOT, folder, name + ".py"), encoding="utf-8") as fh:
        text = fh.read()
    i, j = text.find(START), text.find(END)
    return text[i:j] if i >= 0 and j > i else None


class SameBlockEverywhere(unittest.TestCase):
    def test_every_script_has_the_identical_block(self):
        first = block(*SCRIPTS[0])
        self.assertIsNotNone(first)
        for folder, name in SCRIPTS:
            self.assertEqual(block(folder, name), first, "%s/%s.py has a different NSE clock" % (folder, name))

    def test_every_script_imports_what_the_block_needs(self):
        for folder, name in SCRIPTS:
            with open(os.path.join(ROOT, folder, name + ".py"), encoding="utf-8") as fh:
                text = fh.read()
            for mod in ("datetime", "math"):
                self.assertRegex(text, r"(?m)^import %s$" % mod, "%s/%s.py" % (folder, name))
            self.assertRegex(text, r"(?m)^ClockError = \w+Error$", "%s/%s.py" % (folder, name))


class Calendar(unittest.TestCase):
    def test_weekdays_are_trading_days(self):
        self.assertEqual(alerts.trading_day(D(2026, 10, 30)), (True, ""))
        self.assertEqual(alerts.session(D(2026, 10, 30)), (datetime.time(9, 15), datetime.time(15, 30)))

    def test_weekend_holidays_and_muhurat(self):
        self.assertEqual(alerts.trading_day(D(2026, 10, 31)), (False, "weekend"))
        for d, name in ((D(2026, 11, 10), "Diwali Balipratipada"), (D(2026, 11, 24), "Guru Nanak"),
                        (D(2026, 12, 25), "Christmas"), (D(2026, 10, 20), "Dussehra")):
            ok, why = alerts.trading_day(d)
            self.assertFalse(ok)
            self.assertIn(name, why)
        ok, why = alerts.trading_day(D(2026, 11, 8))
        self.assertFalse(ok)
        self.assertIn("Muhurat", why)
        self.assertIsNone(alerts.session(D(2026, 11, 8)))

    def test_an_unknown_year_is_closed_and_says_why(self):
        ok, why = alerts.trading_day(D(2027, 1, 4))
        self.assertFalse(ok)
        self.assertIn("2027 is not in this tool yet", why)

    def test_india_time_has_no_daylight_saving(self):
        for d in ("2026-01-15 09:15", "2026-07-15 09:15", "2026-10-30 15:00"):
            utc = ist(d)
            self.assertEqual(alerts.to_ist(utc).strftime("%Y-%m-%d %H:%M"), d)
            self.assertEqual(alerts.parse_now(d), utc)
        self.assertEqual(alerts.parse_now("2026-10-30T04:20:00Z"), ist("2026-10-30 09:50"))
        self.assertEqual(alerts.parse_now("2026-10-30T09:50:00+05:30"), ist("2026-10-30 09:50"))
        t = int((ist("2026-10-30 09:20") - datetime.datetime(1970, 1, 1)).total_seconds())
        self.assertEqual(alerts.ts_to_ist(t), datetime.datetime(2026, 10, 30, 9, 20))
        with self.assertRaises(alerts.AlertError):
            alerts.parse_now("half past nine")


class Money(unittest.TestCase):
    def test_indian_grouping(self):
        cases = {100000: "₹1,00,000", 2913.4: "₹2,913.40", 73.63: "₹73.63", 1234567.891: "₹12,34,567.89",
                 0: "₹0", 999: "₹999", 1000: "₹1,000", 10000000: "₹1,00,00,000", -500: "-₹500"}
        for x, want in cases.items():
            self.assertEqual(alerts.inr(x), want, x)
        self.assertEqual(alerts.inr(2913.4, False), "₹2,913")
        self.assertEqual(alerts.inr(5, True), "₹5.00")
        self.assertEqual(alerts.inr(-0.001), "₹0")


class Ticks(unittest.TestCase):
    def test_bands(self):
        want = [(10, 0.01), (249.99, 0.01), (250, 0.05), (1000, 0.05), (1000.05, 0.10), (5000, 0.10),
                (5000.5, 0.50), (10000, 0.50), (15000, 1.00), (20000, 1.00), (25000, 5.00)]
        for price, tick in want:
            self.assertEqual(alerts.tick_size(price), tick, price)

    def test_rounding_on_the_grid(self):
        self.assertEqual(alerts.to_tick(1408.37, 0.10, "down"), 1408.30)
        self.assertEqual(alerts.to_tick(1408.31, 0.10, "up"), 1408.40)
        self.assertEqual(alerts.to_tick(1408.30, 0.10, "down"), 1408.30)
        self.assertEqual(alerts.to_tick(1408.30, 0.10, "up"), 1408.30)
        self.assertEqual(alerts.to_tick(245.555, 0.01), 245.56)
        self.assertEqual(alerts.to_tick(612.47, 0.05, "down"), 612.45)
        self.assertEqual(alerts.to_tick(612.47, 0.05, "up"), 612.50)


class Charges(unittest.TestCase):
    def test_the_synth_trade_at_its_stop(self):
        c = chg.charges(45 * 1416.0, 45 * 1408.0)
        self.assertEqual(c["brokerage"], 38.13)                # 0.03% of each order, under the ₹20 cap
        self.assertEqual(c["stt"], round(45 * 1408 * 0.00025, 2))
        self.assertEqual(c["stamp"], round(45 * 1416 * 0.00003, 2))
        self.assertAlmostEqual(c["total"], sum(v for k, v in c.items() if k != "total"), places=2)

    def test_brokerage_is_the_lower_of_20_or_003_percent(self):
        self.assertEqual(chg.brokerage(10000, chg.DEFAULT_RATES), 3.0)
        self.assertEqual(chg.brokerage(66666.67, chg.DEFAULT_RATES), 20.0)
        self.assertEqual(chg.brokerage(0, chg.DEFAULT_RATES), 0.0)

    def test_the_full_round_trip_at_75000(self):
        c = chg.charges(75000, 75000)
        self.assertEqual((c["brokerage"], c["stt"], c["exchange"], c["sebi"], c["stamp"]),
                         (40.0, 18.75, 4.46, 0.15, 2.25))
        self.assertEqual(c["gst"], round(0.18 * (40 + 4.46 + 0.15), 2))
        self.assertAlmostEqual(c["total"], 73.64, places=2)

    def test_stt_is_on_the_sell_and_stamp_on_the_buy(self):
        buy_only, sell_only = chg.charges(50000, 0), chg.charges(0, 50000)
        self.assertEqual((buy_only["stt"], sell_only["stamp"]), (0.0, 0.0))
        self.assertGreater(sell_only["stt"], 0)
        self.assertGreater(buy_only["stamp"], 0)

    def test_rates_come_from_the_config(self):
        r = chg.rates_from({"charges": {"exchange_pct": 0.00322}})
        self.assertEqual(r["exchange_pct"], 0.00322)
        self.assertEqual(r["stt_sell_pct"], 0.025)
        self.assertGreater(chg.charges(75000, 75000, r)["total"], chg.charges(75000, 75000)["total"])
        with self.assertRaises(ValueError):
            chg.charges(-1, 0)

    def test_cli(self):
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(chg.main(["--qty", "45", "--entry", "1416", "--exit", "1408"]), 0)
        self.assertIn("total", out.getvalue())
        self.assertIn("estimate", out.getvalue())
        self.assertRegex(out.getvalue(), re.compile(r"checked 2026-10-29"))


class StandaloneSkills(unittest.TestCase):
    """Claude uploads each skill as its own ZIP, so no script may reach into a sibling folder."""

    def test_the_charges_copies_are_identical(self):
        def body(folder):
            with open(os.path.join(ROOT, folder, "charges.py"), encoding="utf-8") as fh:
                return fh.read()
        for folder in ("trader-risk", "trader-broker"):
            self.assertEqual(body(folder), body("trader-budget"), folder)

    def test_the_db_copies_are_identical(self):
        def body(folder):
            with open(os.path.join(ROOT, folder, "db.py"), encoding="utf-8") as fh:
                return fh.read()
        for folder in ("trader-broker", "trader-risk", "trader-report", "trader-golive", "trader-budget",
                       "trader-control"):
            self.assertEqual(body(folder), body("trader-db"), folder)

    def test_the_guards_copies_are_identical(self):
        def body(folder):
            with open(os.path.join(ROOT, folder, "guards.py"), encoding="utf-8") as fh:
                return fh.read()
        for folder in ("trader-budget", "trader-scanner", "trader-control"):
            self.assertEqual(body(folder), body("trader-risk"), folder)

    def test_no_script_imports_a_sibling_skill(self):
        for folder, name in SCRIPTS + [("trader-budget", "charges"), ("trader-db", "db"), ("trader-risk", "guards")]:
            with open(os.path.join(ROOT, folder, name + ".py"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotRegex(text, r'os\.path\.dirname\(HERE\)|"trader-[a-z]+"\)\)', "%s/%s.py" % (folder, name))


if __name__ == "__main__":
    unittest.main()
