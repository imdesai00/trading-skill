import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, ROOT, load, write

ins = load("trader-insider", "insider")
DIR = os.path.join(FIX, "trader-insider")           # SYNTHETIC: no real filing was readable when this was built
CSV = os.path.join(DIR, "nse_export_synthetic.csv")
NOW = "2026-10-30 08:30"


def csv_text():
    with open(CSV, encoding="utf-8") as fh:
        return fh.read()


class Reading(unittest.TestCase):
    def setUp(self):
        self.rows, self.cols = ins.read_csv(csv_text())

    def test_columns_are_found_by_name(self):
        self.assertEqual(len(self.rows), 5)
        for k in ("symbol", "person", "txn", "qty", "value", "mode", "txn_date", "broadcast"):
            self.assertIn(k, self.cols)

    def test_the_same_columns_in_another_order_and_case(self):
        text = "transaction type,name of the person,SYMBOL\nBuy,Someone,ABC\n"
        rows, cols = ins.read_csv(text)
        self.assertEqual((rows[0]["symbol"], rows[0]["kind"], rows[0]["person"]), ("ABC", "buy", "Someone"))
        self.assertIsNone(rows[0]["qty"])
        self.assertIsNone(rows[0]["txn_date"])

    def test_a_missing_required_column_is_named_and_stops(self):
        with self.assertRaises(ins.InsiderError) as cm:
            ins.read_csv("SYMBOL,VALUE\nABC,10\n")
        self.assertIn("person, txn", str(cm.exception))
        self.assertIn("VALUE", str(cm.exception))          # it shows what the columns ARE
        with self.assertRaises(ins.InsiderError):
            ins.read_csv("SYMBOL\n")

    def test_kinds(self):
        kinds = [(r["person"], r["kind"], r["open_market"]) for r in self.rows]
        self.assertEqual(kinds, [("Person One", "buy", True), ("Person Two", "sell", True),
                                 ("Person Three", "buy", False), ("Person Four", "buy", True),
                                 ("Person One", "pledge", False)])

    def test_direction_edges(self):
        self.assertEqual(ins.direction("Acquisition", "Market Purchase"), ("buy", True))
        self.assertEqual(ins.direction("Disposal", "Off Market"), ("sell", False))
        self.assertEqual(ins.direction("Sell", "Market Sale"), ("sell", True))
        self.assertEqual(ins.direction("Buy", "Revokation of Pledge")[0], "pledge")
        self.assertEqual(ins.direction("", "")[0], "other")

    def test_numbers_and_dates(self):
        self.assertEqual(ins.to_number("₹ 25,00,000"), 2500000.0)
        self.assertIsNone(ins.to_number("-"))
        self.assertEqual(ins.parse_when("28-Oct-2026 17:45").isoformat(), "2026-10-28T17:45:00")
        self.assertEqual(ins.parse_when("28/10/2026").date().isoformat(), "2026-10-28")
        self.assertIsNone(ins.parse_when("tomorrow"))

    def test_a_byte_order_mark_is_ignored(self):
        rows, _ = ins.read_csv("﻿" + csv_text())
        self.assertEqual(len(rows), 5)

    def test_keys_are_stable_and_distinct(self):
        again, _ = ins.read_csv(csv_text())
        self.assertEqual([r["key"] for r in self.rows], [r["key"] for r in again])
        self.assertEqual(len({r["key"] for r in self.rows}), 5)


class TheGap(unittest.TestCase):
    def setUp(self):
        self.rows = {r["person"] + r["txn"]: r for r in ins.read_csv(csv_text())[0]}
        self.now = ins.parse_now(NOW)

    def test_trading_days_skip_weekends_and_holidays(self):
        D = __import__("datetime").date
        self.assertEqual(ins.trading_days_between(D(2026, 10, 30), D(2026, 11, 2)), 1)      # Fri -> Mon
        self.assertEqual(ins.trading_days_between(D(2026, 10, 1), D(2026, 10, 5)), 1)       # 2 Oct holiday, weekend
        self.assertEqual(ins.trading_days_between(D(2026, 10, 5), D(2026, 10, 5)), 0)

    def test_gap_and_age(self):
        g = ins.gap_of(self.rows["Person OneBuy"], self.now)
        self.assertEqual((g["to_publish_days"], g["age_days"]), (2, 4))
        g = ins.gap_of(self.rows["Person TwoSell"], self.now)
        self.assertEqual(g["to_publish_days"], 7)               # 20 Oct was a holiday; 21 to 29 Oct is 7 trading days

    def test_other_years_fall_back_to_calendar_days_and_say_so(self):
        r = dict(self.rows["Person OneBuy"], txn_date="2027-01-04", published="2027-01-07 10:00")
        g = ins.gap_of(r, self.now)
        self.assertEqual(g["to_publish_days"], 3)
        self.assertIn("calendar days", g["published_gap_note"])


class Commands(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.state = os.path.join(self.d, "state", "insider.json")
        self.wl = os.path.join(self.d, "wl.json")
        write(self.wl, {"hits": [{"symbol": "SYNTHA"}, {"symbol": "SYNTHB"}]})
        self.base = ["--state", self.state, "--watchlist", self.wl, "--plan", os.path.join(self.d, "p"),
                     "--portfolio", os.path.join(self.d, "q"), "--now", NOW]

    def tearDown(self):
        shutil.rmtree(self.d)

    def run_cmd(self, cmd, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ins.main([cmd] + self.base + list(extra))
        return code, out.getvalue(), err.getvalue()

    def test_import_then_check_alerts_once(self):
        code, out, _ = self.run_cmd("import", CSV)
        self.assertEqual(code, 0)
        self.assertIn("5 new", out)
        code, out, _ = self.run_cmd("import", CSV)
        self.assertIn("0 new, 5 already known", out)
        code, out, _ = self.run_cmd("check", "--days", "60")
        self.assertEqual(code, 0)
        self.assertIn("Person One (Promoter) BOUGHT: 50,000 shares, ₹25,00,000", out)
        self.assertIn("Person Two (Director) SOLD", out)
        self.assertNotIn("Person Four", out)                       # SYNTHC is not on the watchlist
        self.assertIn("(NOT an open-market trade)", out)           # the ESOP allotment
        self.assertIn("PLEDGE/RELEASE", out)
        self.assertIn("7 days to publish (late", out)
        self.assertIn("you are reading it 4 days after the trade", out)
        self.assertIn("bought ₹19,80,000", out)                    # 25,00,000 bought less 5,20,000 sold
        self.assertIn("not a signal", out)
        code, out, _ = self.run_cmd("check", "--days", "60")
        self.assertEqual(code, 1)
        self.assertIn("Nothing new", out)
        code, out, _ = self.run_cmd("check", "--days", "60", "--all")
        self.assertEqual(code, 0)

    def test_window_and_symbols(self):
        self.run_cmd("import", CSV)
        _, out, _ = self.run_cmd("check", "--days", "5", "--symbols", "SYNTHA")
        self.assertIn("Person One", out)
        self.assertNotIn("Person Two", out)                        # traded 10 days ago
        self.assertNotIn("PLEDGE", out)
        _, out, _ = self.run_cmd("check", "--days", "5", "--symbols", "SYNTHC", "--all")
        self.assertIn("Person Four", out)

    def test_dry_run_alerts_again(self):
        self.run_cmd("import", CSV)
        self.assertEqual(self.run_cmd("check", "--dry-run")[0], 0)
        self.assertEqual(self.run_cmd("check")[0], 0)

    def test_check_before_import_and_with_no_symbols(self):
        code, _, err = self.run_cmd("check")
        self.assertEqual(code, 2)
        self.assertIn("insider.py import", err)
        self.run_cmd("import", CSV)
        os.remove(self.wl)
        code, _, err = self.run_cmd("check")
        self.assertEqual(code, 2)
        self.assertIn("no symbols", err)

    def test_bad_csv_does_not_touch_the_state(self):
        bad = os.path.join(self.d, "bad.csv")
        with open(bad, "w") as fh:
            fh.write("A,B\n1,2\n")
        code, _, err = self.run_cmd("import", bad)
        self.assertEqual(code, 1)
        self.assertIn("missing a column", err)
        self.assertFalse(os.path.exists(self.state))

    def test_old_rows_are_pruned(self):
        self.run_cmd("import", CSV)
        later = ["--state", self.state, "--now", "2027-06-30 10:00"]
        out = io.StringIO()
        with redirect_stdout(out):
            ins.main(["import", CSV] + later)
        with open(self.state, encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["rows"]), 0)

    def test_feed_notices_are_matched_and_alert_once(self):
        code, out, _ = self.run_cmd("feed", "--offline", DIR)
        self.assertEqual(code, 0)
        self.assertIn("SYNTHA - Synthetic Alpha Limited", out)
        self.assertIn("[your: SYNTHA]", out)
        self.assertNotIn("SYNTHC", out)
        self.assertEqual(self.run_cmd("feed", "--offline", DIR)[0], 1)
        _, out, _ = self.run_cmd("feed", "--offline", DIR, "--market", "--all")
        self.assertIn("SYNTHC", out)

    def test_feed_that_cannot_be_read_points_to_the_csv_route(self):
        code, _, err = self.run_cmd("feed", "--offline", self.d)
        self.assertEqual(code, 2)
        self.assertIn("insider.py import", err)

    def test_status(self):
        self.run_cmd("import", CSV)
        _, out, _ = self.run_cmd("status")
        self.assertIn("5 insider rows kept", out)


class Source(unittest.TestCase):
    def test_no_congress_leftovers_and_it_never_places_anything(self):
        with open(os.path.join(ROOT, "trader-insider", "insider.py"), encoding="utf-8") as fh:
            text = fh.read()
        for bad in ("house.gov", "Pelosi", "STOCK Act", "pdftotext", "kite", "place_order"):
            self.assertNotIn(bad, text, bad)


if __name__ == "__main__":
    unittest.main()
