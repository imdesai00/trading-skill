import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ist, quote_doc, write

alerts = load("trader-alerts", "alerts")
UA = "Mozilla/5.0 (compatible; india-day-trader-skill/1.0)"
PLAN = {"symbol": "SYNTHA", "date": "2026-10-30", "entry": 1416.0, "stop": 1408.0, "target": 1440.0,
        "stop_label": "opening-range midpoint", "target_label": "3R", "flat_by": "15:00"}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


def status(text):
    return alerts.market_status(ist(text))


class Clock(unittest.TestCase):
    def test_open_and_the_closing_auction_note(self):
        st = status("2026-10-30 10:15")
        self.assertTrue(st["open"])
        self.assertEqual((st["opens"], st["closes"], st["reason"]), ("09:15", "15:30", "normal market"))
        st = status("2026-10-30 15:20")
        self.assertTrue(st["open"])
        self.assertIn("closing-auction stocks stopped continuous trading at 15:15", st["reason"])

    def test_before_after_and_the_next_open(self):
        st = status("2026-10-30 09:00")
        self.assertEqual((st["open"], st["reason"], st["next_open"]), (False, "before the open", "2026-10-30 09:15"))
        st = status("2026-10-30 15:30")
        self.assertEqual((st["open"], st["reason"], st["next_open"]), (False, "after the close", "2026-11-02 09:15"))

    def test_holiday_weekend_and_muhurat_skip_to_the_next_session(self):
        self.assertEqual(status("2026-11-09 16:00")["next_open"], "2026-11-11 09:15")   # Nov 10 holiday
        st = status("2026-11-08 14:30")
        self.assertFalse(st["open"])
        self.assertIn("Muhurat", st["reason"])
        self.assertEqual(st["next_open"], "2026-11-09 09:15")

    def test_cli_clock(self):
        code, out, _ = run(["clock", "--now", "2026-10-30 10:15"])
        self.assertEqual(code, 0)
        self.assertIn("NSE OPEN", out)
        code, out, _ = run(["clock", "--now", "2027-01-04 10:00"])
        self.assertEqual(code, 1)
        self.assertIn("check nseindia.com", out)


class Quotes(unittest.TestCase):
    def test_yahoo_symbol_and_url(self):
        self.assertEqual(alerts.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(alerts.yahoo_symbol("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(alerts.yahoo_url("M&M"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/M%26M.NS?range=1d&interval=1d")

    def test_parse(self):
        q = alerts.parse_yahoo(json.dumps(quote_doc("SYNTHA", 1412.3, 1400.0)), "SYNTHA")
        self.assertEqual((q["price"], q["prev_close"], q["currency"], q["source"]), (1412.3, 1400.0, "INR", "yahoo"))
        with self.assertRaises(alerts.AlertError):
            alerts.parse_yahoo('{"chart":{"result":null,"error":{"description":"No data found"}}}', "NOPE")
        with self.assertRaises(alerts.AlertError):
            alerts.parse_yahoo("<html>", "X")

    def test_offline_files_and_prices_json(self):
        d = tempfile.mkdtemp()
        try:
            write(os.path.join(d, "yahoo-SYNTHA.json"), quote_doc("SYNTHA", 1412.3, 1400.0))
            q, errors = alerts.get_quotes(["SYNTHA", "NOPE"], d)
            self.assertEqual(q["SYNTHA"]["price"], 1412.3)
            self.assertIn("no offline quote file", errors[0])
            p = os.path.join(d, "prices.json")
            doc = alerts.write_prices(p, q, ist("2026-10-30 10:00"))
            self.assertEqual(doc["prices"], {"SYNTHA": 1412.3})
            self.assertEqual(doc["detail"]["SYNTHA"]["fetched"], "2026-10-30T04:30:00Z")
        finally:
            shutil.rmtree(d)


class FakeNet(object):
    """Stands in for urllib: answers each request with the next status code."""

    def __init__(self, codes):
        self.codes, self.calls, self.sleeps = list(codes), [], []
        self.body = json.dumps(quote_doc("SYNTHA", 1412.3, 1400.0))

    def urlopen(self, req, timeout=None):
        self.calls.append((req.full_url.split("/")[2], req.get_header("User-agent"), req.full_url))
        code = self.codes.pop(0)
        if code != 200:
            fp = io.BytesIO(b'{"chart":{"result":null,"error":{"code":"Not Found",'
                            b'"description":"No data found, symbol may be delisted"}}}' if code == 404 else b"")
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, fp)
        return io.BytesIO(self.body.encode("utf-8"))


class Network(unittest.TestCase):
    def setUp(self):
        self.saved = (alerts._urlopen, alerts._sleep)

    def tearDown(self):
        alerts._urlopen, alerts._sleep = self.saved

    def net(self, codes):
        f = FakeNet(codes)
        alerts._urlopen = f.urlopen
        alerts._sleep = f.sleeps.append
        return f

    def hosts(self, f):
        return [h.split(".")[0] for h, _, _ in f.calls]

    def test_query2_first_with_ns_and_our_user_agent(self):
        f = self.net([200])
        q, errors = alerts.get_quotes(["SYNTHA"])
        self.assertEqual((q["SYNTHA"]["price"], errors), (1412.3, []))
        self.assertEqual(f.calls[0][:2], ("query2.finance.yahoo.com", UA))
        self.assertIn("/chart/SYNTHA.NS?", f.calls[0][2])

    def test_one_retry_with_a_pause_on_429(self):
        f = self.net([429, 200])
        self.assertEqual(alerts.get_quotes(["SYNTHA"])[0]["SYNTHA"]["price"], 1412.3)
        self.assertEqual(self.hosts(f), ["query2", "query2"])
        self.assertEqual(f.sleeps, [alerts.BACKOFF])

    def test_falls_back_to_query1(self):
        f = self.net([429, 429, 200])
        self.assertIn("SYNTHA", alerts.get_quotes(["SYNTHA"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query2", "query1"])
        f = self.net([500, 502])
        q, errors = alerts.get_quotes(["SYNTHA"])
        self.assertEqual(q, {})
        self.assertIn("HTTP 502", errors[0])

    def test_404_and_403(self):
        f = self.net([404])
        self.assertIn("No data found", alerts.get_quotes(["NOPE"])[1][0])
        f = self.net([403])
        self.assertIn("HTTP 403", alerts.get_quotes(["SYNTHA"])[1][0])
        self.assertEqual(self.hosts(f), ["query2"])

    def test_a_pause_between_symbols(self):
        f = self.net([200, 200])
        alerts.get_quotes(["SYNTHA", "SYNTHB"])
        self.assertEqual(f.sleeps, [alerts.PAUSE])


class Rules(unittest.TestCase):
    def rules(self):
        return alerts.rules_from_plan(PLAN)

    def ev(self, price, when, state=None, rules=None):
        q = {"SYNTHA": {"price": price, "prev_close": 1400.0}}
        return alerts.evaluate(rules or self.rules(), q, status(when), state or {}, ist(when))

    def test_from_plan(self):
        r = self.rules()
        self.assertEqual([x["id"] for x in r], ["syntha-plan-stop", "syntha-plan-target", "syntha-flat-by"])
        self.assertEqual(r[2], {"id": "syntha-flat-by", "symbol": "SYNTHA", "type": "time", "at": "15:00",
                                "label": "flat by 15:00", "date": "2026-10-30"})
        self.assertEqual(alerts.rules_from_plan(dict(PLAN, flat_by=None))[2]["at"], "15:00")

    def test_nothing_between_the_stop_and_the_target(self):
        rings, _ = self.ev(1420.0, "2026-10-30 10:30")
        self.assertEqual(rings, [])

    def test_the_stop_rings_once(self):
        rings, st = self.ev(1407.5, "2026-10-30 10:05")
        self.assertEqual([r["id"] for r in rings], ["syntha-plan-stop"])
        self.assertIn("₹1,407.50: at or below your stop ₹1,408.00", rings[0]["message"])
        rings, st = self.ev(1406.0, "2026-10-30 10:10", st)
        self.assertEqual(rings, [])

    def test_the_target_and_flat_by(self):
        rings, _ = self.ev(1441.0, "2026-10-30 11:00")
        self.assertEqual([r["id"] for r in rings], ["syntha-plan-target"])
        rings, st = self.ev(1420.0, "2026-10-30 15:00")
        self.assertEqual([r["id"] for r in rings], ["syntha-flat-by"])
        self.assertIn("If SYNTHA is still open, close it now", rings[0]["message"])
        self.assertEqual(self.ev(1420.0, "2026-10-30 15:05", st)[0], [])

    def test_plan_rules_are_for_the_plans_day_only(self):
        self.assertEqual(self.ev(1300.0, "2026-11-02 10:00")[0], [])

    def test_pct_move_and_opening_bell(self):
        rules = [{"id": "m", "symbol": "SYNTHA", "type": "pct_move", "pct": 1}, {"id": "bell", "type": "opening_bell"}]
        rings, st = self.ev(1415.0, "2026-10-30 09:20", rules=rules)
        self.assertEqual(sorted(r["id"] for r in rings), ["bell", "m"])
        self.assertEqual(self.ev(1416.0, "2026-10-30 09:30", st, rules)[0], [])

    def test_rearm(self):
        rules = [{"id": "a", "symbol": "SYNTHA", "type": "above", "price": 1415, "rearm": True}]
        rings, st = self.ev(1416, "2026-10-30 10:00", rules=rules)
        self.assertEqual(len(rings), 1)
        rings, st = self.ev(1410, "2026-10-30 10:05", st, rules)
        self.assertEqual(rings, [])
        self.assertEqual(len(self.ev(1416, "2026-10-30 10:10", st, rules)[0]), 1)

    def test_bad_rules(self):
        for bad in ({"type": "nope"}, {"type": "stop", "symbol": "X"}, {"type": "time", "at": "25:00"},
                    {"type": "above", "symbol": "X", "price": 1, "date": "30-10-2026"}):
            with self.assertRaises(alerts.AlertError):
                alerts.validate(bad)
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan({"symbol": "X"})


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rules = os.path.join(self.d, "alerts.json")
        self.state = os.path.join(self.d, "state.json")
        self.prices = os.path.join(self.d, "prices.json")
        self.plan = os.path.join(self.d, "plan.json")
        write(self.plan, PLAN)
        self.q = os.path.join(self.d, "q")
        write(os.path.join(self.q, "yahoo-SYNTHA.json"), quote_doc("SYNTHA", 1407.9, 1400.0))

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_add_from_plan_check_list_reset(self):
        code, out, _ = run(["add", "--from-plan", "--plan", self.plan, "--rules", self.rules, "--state", self.state,
                            "--config", os.path.join(self.d, "none.json")])
        self.assertEqual(code, 0)
        self.assertEqual(out.count("Added"), 3)
        argv = ["check", "--rules", self.rules, "--state", self.state, "--prices-out", self.prices,
                "--offline", self.q, "--now", "2026-10-30 10:10"]
        code, out, _ = run(argv)
        self.assertEqual(code, 0)
        self.assertIn("ALERT SYNTHA is ₹1,407.90: at or below your stop ₹1,408.00", out)
        self.assertIn("An alert is information, not an order", out)
        with open(self.prices) as fh:
            self.assertEqual(json.load(fh)["prices"]["SYNTHA"], 1407.9)
        code, out, _ = run(argv)
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit", out)
        code, out, _ = run(["list", "--rules", self.rules, "--state", self.state])
        self.assertIn("(rang ", out)
        code, out, _ = run(["reset", "syntha-plan-stop", "--state", self.state])
        self.assertEqual((code, out.strip()), (0, "Re-armed syntha-plan-stop."))

    def test_add_single_rules(self):
        base = ["add", "--rules", self.rules]
        self.assertEqual(run(base + ["--symbol", "SYNTHA", "--stop", "1408"])[0], 0)
        self.assertEqual(run(base + ["--at", "15:00", "--label", "flat by", "--symbol", "SYNTHA"])[0], 0)
        self.assertEqual(run(base + ["--opening-bell"])[0], 0)
        code, _, err = run(base + ["--symbol", "SYNTHA", "--stop", "1408"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        code, _, err = run(base + ["--at", "3pm"])
        self.assertIn("India time", err)

    def test_quote_cli(self):
        code, out, _ = run(["quote", "SYNTHA", "--offline", self.q, "--write", self.prices, "--now", "2026-10-30 10:10"])
        self.assertEqual(code, 0)
        self.assertIn("SYNTHA       ₹1,407.90 (+0.56% vs prev close ₹1,400.00)", out)
        self.assertIn("for alerts only", out)
        code, _, err = run(["quote"])
        self.assertIn("which symbols?", err)

    def test_no_rules_is_one_plain_line(self):
        code, _, err = run(["check", "--rules", self.rules, "--state", self.state])
        self.assertEqual(code, 1)
        self.assertIn("no rules in", err)


if __name__ == "__main__":
    unittest.main()
