import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, ROOT, load

res = load("trader-analyst", "results")
SAMPLE = os.path.join(FIX, "trader-analyst", "results-synthetic-alpha.txt")     # SYNTHETIC, not a real company


def sample():
    with open(SAMPLE, encoding="utf-8") as fh:
        return fh.read()


def table(rows, header="30-09-2026 30-06-2026 30-09-2025", unit="(Rs. in Crore)"):
    return "Results\n%s\nParticulars %s\n%s\n" % (unit, header, "\n".join(rows))


class Numbers(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(res.num("1,234.56"), 1234.56)
        self.assertEqual(res.num("(12.30)"), -12.3)
        self.assertEqual(res.num("(1)"), None)            # a note reference in a label, not a loss
        self.assertEqual(res.num("-"), 0.0)
        self.assertEqual(res.num("-45.5"), -45.5)
        self.assertIsNone(res.num("Profit"))

    def test_trailing_numbers_stop_at_the_label(self):
        label, nums = res.trailing_numbers("3 Total income (1+2)   1,296.00  1,220.00  1,135.00")
        self.assertEqual(nums, [1296.0, 1220.0, 1135.0])
        self.assertIn("Total income", label)

    def test_dates(self):
        import datetime
        D = datetime.date
        for text in ("30-09-2026", "30.09.2026", "30/09/2026", "30-Sep-2026", "September 30, 2026", "30th September 2026"):
            self.assertEqual(res.parse_date(text), D(2026, 9, 30), text)
        self.assertIsNone(res.parse_date("Unaudited"))


class TheSample(unittest.TestCase):
    def setUp(self):
        self.a = res.analyse(sample(), basis="consolidated")

    def test_columns_come_from_the_header_dates(self):
        self.assertEqual(self.a["columns_decided"], "from the dates in the header")
        self.assertEqual(self.a["unit"], "crore")
        cur = self.a["periods"]["cur"]
        self.assertEqual((cur["revenue"], cur["net_profit"], cur["eps"]), (1250.4, 184.5, 9.22))
        self.assertEqual(self.a["periods"]["yoy"]["net_profit"], 146.0)

    def test_changes_and_margins(self):
        ch = self.a["changes"]
        self.assertEqual(ch["yoy"]["revenue_pct"], 13.7)
        self.assertEqual(ch["yoy"]["net_profit_pct"], 26.4)
        self.assertEqual(ch["qoq"]["net_profit_pct"], 11.8)
        self.assertEqual(self.a["periods"]["cur"]["net_margin_pct"], 14.8)
        self.assertEqual(ch["yoy"]["net_margin_pp"], 1.5)

    def test_a_clean_quarter_has_no_flags_and_no_arithmetic_note(self):
        self.assertEqual(self.a["flags"], [])
        self.assertFalse([n for n in self.a["notes"] if "does not equal" in n])

    def test_extra_columns_are_ignored(self):
        self.assertEqual(self.a["periods"]["cur"]["revenue"], 1250.4)     # the half-year columns are not used

    def test_the_report_makes_no_forecast(self):
        text = res.render(self.a, "SYNTHA").lower()
        for bad in ("will rise", "will fall", "buy", "sell", "target price", "undervalued", "outlook"):
            self.assertNotIn(bad, text)
        self.assertIn("not financial advice", text)
        self.assertIn("nothing was fetched or checked", text)


class Flags(unittest.TestCase):
    def flags(self, rows, **kw):
        return res.analyse(table(rows), basis="standalone", **kw)

    def test_decline_and_margin(self):
        a = self.flags(["Revenue from operations 900.00 1,000.00 1,100.00", "Profit before tax 60.00 90.00 120.00",
                        "Tax expense 15.00 22.00 30.00", "Net profit for the period 45.00 68.00 90.00"])
        text = " ".join(a["flags"])
        self.assertIn("Revenue DOWN 18.2%", text)
        self.assertIn("Net profit DOWN 50.0%", text)
        self.assertIn("down 33.8% on the previous quarter", text)
        self.assertIn("Net margin down 3.2 points", text)

    def test_loss_and_swing(self):
        a = self.flags(["Revenue from operations 900.00 1,000.00 1,100.00", "Net profit for the period (45.00) 10.00 90.00"])
        text = " ".join(a["flags"])
        self.assertIn("LOSS this quarter", text)
        self.assertIn("Swing to loss", text)
        self.assertIsNone(a["changes"]["yoy"]["net_profit_pct"])        # no percentage on a loss

    def test_turnaround(self):
        a = self.flags(["Revenue from operations 900.00 800.00 700.00", "Net profit for the period 45.00 (10.00) (90.00)"])
        self.assertTrue(any("Turnaround" in f for f in a["flags"]))

    def test_one_offs_and_other_income(self):
        a = self.flags(["Revenue from operations 900.00 900.00 900.00", "Other income 60.00 20.00 20.00",
                        "Exceptional items 50.00 0.00 0.00", "Profit before tax 200.00 150.00 150.00",
                        "Tax expense 50.00 38.00 38.00", "Net profit for the period 150.00 112.00 112.00"])
        text = " ".join(a["flags"])
        self.assertIn("Exceptional items are 25%", text)
        self.assertIn("Other income is 30%", text)

    def test_arithmetic_mismatch_is_a_note_not_a_claim(self):
        a = self.flags(["Revenue from operations 900.00 900.00 900.00", "Profit before tax 200.00 150.00 150.00",
                        "Tax expense 50.00 38.00 38.00", "Net profit for the period 120.00 112.00 112.00"])
        self.assertTrue(any("does not equal profit before tax minus tax" in n for n in a["notes"]))
        self.assertIn("check the table", " ".join(a["notes"]))

    def test_odd_tax_rate(self):
        a = self.flags(["Revenue from operations 900.00 900.00 900.00", "Profit before tax 200.00 150.00 150.00",
                        "Tax expense 2.00 38.00 38.00", "Net profit for the period 198.00 112.00 112.00"])
        self.assertTrue(any("Tax is 1%" in f for f in a["flags"]))


class Columns(unittest.TestCase):
    ROWS = ["Revenue from operations 900.00 1,000.00 1,100.00", "Net profit for the period 45.00 68.00 90.00"]

    def test_no_dates_means_assumed_and_says_so(self):
        a = res.analyse("(Rs. in Lakhs)\n" + "\n".join(self.ROWS))
        self.assertTrue(a["columns_decided"].startswith("ASSUMED"))
        self.assertEqual(a["unit"], "lakh")
        self.assertEqual(a["periods"]["yoy"]["revenue"], 1100.0)

    def test_columns_flag(self):
        a = res.analyse("\n".join(["Revenue from operations 5.00 900.00 1,000.00 1,100.00",
                                   "Net profit for the period 1.00 45.00 68.00 90.00"]), forced="skip,cur,pq,yoy")
        self.assertEqual(a["columns_decided"], "as you said with --columns")
        self.assertEqual(a["periods"]["cur"]["revenue"], 900.0)
        with self.assertRaises(res.ResultsError):
            res.analyse("\n".join(self.ROWS), forced="now,then")

    def test_dates_in_another_order_are_sorted_out(self):
        text = table(["Revenue from operations 1,100.00 1,000.00 900.00", "Net profit for the period 90.00 68.00 45.00"],
                     header="30-09-2025 30-06-2026 30-09-2026")
        a = res.analyse(text)
        self.assertEqual(a["periods"]["cur"]["revenue"], 900.0)
        self.assertEqual(a["periods"]["yoy"]["revenue"], 1100.0)
        self.assertEqual(a["periods"]["pq"]["revenue"], 1000.0)

    def test_missing_unit_and_basis_are_noted(self):
        a = res.analyse(table(self.ROWS, unit=""))
        notes = " ".join(a["notes"])
        self.assertIn("No unit", notes)
        self.assertIn("Consolidated or standalone is not stated", notes)

    def test_net_sales_and_total_income_variants(self):
        a = res.analyse("\n".join(["Net Sales 900.00 800.00 700.00", "Profit after tax 45.00 40.00 35.00"]))
        self.assertEqual(a["top_line"], "revenue")
        a = res.analyse("\n".join(["Total income 900.00 800.00 700.00", "Net profit 45.00 40.00 35.00"]))
        self.assertEqual(a["top_line"], "total_income")

    def test_profit_before_tax_is_not_mistaken_for_net_profit(self):
        a = res.analyse("\n".join(["Revenue from operations 900.00 800.00 700.00",
                                   "Profit before tax 100.00 90.00 80.00", "Net profit 70.00 60.00 50.00"]))
        self.assertEqual(a["periods"]["cur"]["net_profit"], 70.0)
        self.assertEqual(a["periods"]["cur"]["pbt"], 100.0)


class Cli(unittest.TestCase):
    def run_cli(self, argv, stdin=None):
        import sys
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdin
        sys.stdin = io.StringIO(stdin or "")
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = res.main(argv)
        finally:
            sys.stdin = old
        return code, out.getvalue(), err.getvalue()

    def test_read_file_and_json_and_stdin(self):
        code, out, _ = self.run_cli(["read", SAMPLE, "--symbol", "SYNTHA", "--basis", "consolidated"])
        self.assertEqual(code, 0)
        self.assertIn("Quarterly results for SYNTHA (consolidated basis)", out)
        code, out, _ = self.run_cli(["read", SAMPLE, "--json"])
        self.assertEqual(json.loads(out)["changes"]["yoy"]["revenue_pct"], 13.7)
        code, out, _ = self.run_cli(["read", "-"], stdin=sample())
        self.assertEqual(code, 0)

    def test_text_that_is_not_a_results_table(self):
        code, _, err = self.run_cli(["read", "-"], stdin="The company had a good quarter.")
        self.assertEqual(code, 1)
        self.assertIn("could not find the revenue and net profit rows", err)
        code, _, err = self.run_cli(["read", "/nonexistent.txt"])
        self.assertEqual(code, 1)

    def test_no_us_leftovers_and_no_network(self):
        with open(os.path.join(ROOT, "trader-analyst", "results.py"), encoding="utf-8") as fh:
            text = fh.read()
        for bad in ("sec.gov", "XBRL", "10-K", "urllib", "socket", "http"):
            self.assertNotIn(bad, text.split('"""')[2], bad)


if __name__ == "__main__":
    unittest.main()
