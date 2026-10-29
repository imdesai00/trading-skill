import gzip
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, NSE, ist

tm = load("trader-timemachine", "timemachine")
F60 = os.path.join(NSE, "5m-60d")
PLAN = {"symbol": "SYNTHA", "side": "buy", "date": "2026-10-30", "setup": "orb", "entry": 1416.0,
        "entry_time": "09:50", "entry_bar": 6, "stop": 1408.0, "stop_rule": "or_mid", "target": 1440.0,
        "target_r": 3.0, "interval": "5m", "flat_by": "15:00", "tick": 0.1,
        "opening_range": {"high": 1414.8, "low": 1401.2, "minutes": 15}}
NOW = ist("2026-10-30 09:51")


def text():
    return tm.read_offline(F60, "SYNTHA")


def run_tm(plan=PLAN, **kw):
    kw.setdefault("replays", 2000)
    return tm.time_machine(text(), tm.normalize_plan(plan), NOW, **kw)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tm.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheSynthPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = run_tm()

    def test_pool_is_full_nse_sessions_before_the_day(self):
        m = self.doc["mc"]
        self.assertEqual((m["pool_sessions"], m["pool_to"]), (59, "2026-10-29"))
        self.assertEqual(m["until"], "15:00")          # the plan's flat_by
        self.assertEqual(m["bars_left"], 62)          # 09:50 to 15:00
        self.assertTrue(self.doc["data"]["synthetic"])

    def test_odds_add_up_and_the_seed_repeats(self):
        m = self.doc["mc"]
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=3)
        again = run_tm()
        self.assertEqual(again["mc"]["p_target"], m["p_target"])
        other = run_tm(seed=7)
        self.assertNotEqual(other["mc"]["sample_paths"][0], m["sample_paths"][0])

    def test_history_uses_the_rule_on_the_tick_grid(self):
        h = self.doc["history"]
        self.assertEqual(h["rule"]["stop_rule"], "or_mid")
        self.assertGreater(h["triggered"], 0)
        for t in h["trades"]:
            self.assertAlmostEqual(round(t["stop"] * 10), t["stop"] * 10, places=6)
            self.assertAlmostEqual(round(t["target"] * 10), t["target"] * 10, places=6)
        self.assertEqual(h["target"] + h["stop"] + h["close"], h["triggered"])

    def test_the_verdict_and_the_limits_note(self):
        self.assertTrue(self.doc["verdict"].startswith(("Close to", "Leans", "Strongly")))
        self.assertIn("drawn independently", self.doc["note"])
        self.assertIn("small sample", self.doc["note"])
        self.assertIn("not a prediction", self.doc["note"])
        lines = tm.report(self.doc)
        self.assertEqual(lines[0], "SYNTHETIC test data, not market prices.")
        self.assertTrue(any("past sessions fired" in x for x in lines))


class Bars(unittest.TestCase):
    def test_sessions_are_09_15_to_15_15(self):
        meta, bars, dropped = tm.parse_bars(tm.chart_result(text(), "SYNTHA"), NOW)
        starts = {b["start"] for b in bars}
        self.assertIn("09:15", starts)
        self.assertIn("15:10", starts)
        self.assertNotIn("15:15", starts)
        self.assertGreater(dropped["outside_hours"], 0)       # 15:15 to 15:25 bars are left out
        self.assertGreater(dropped["not_complete"], 0)        # today's bars after 09:51
        days = tm.split_sessions(bars)
        full, short = tm.full_sessions(days, "2026-10-30")
        self.assertEqual(len(full[0][1]), tm.SESSION_BARS)

    def test_tick_rounding_for_stops(self):
        self.assertEqual(tm.cents_away(1408.05, True, 0.1), 1408.0)
        self.assertEqual(tm.cents_away(1408.05, False, 0.1), 1408.1)


class Plans(unittest.TestCase):
    def test_entry_time_and_bar_must_agree(self):
        with self.assertRaises(tm.TMError):
            tm.normalize_plan(dict(PLAN, entry_bar=7))
        p = tm.normalize_plan(dict(PLAN, entry_bar=None))
        self.assertEqual(p["entry_bar"], 6)

    def test_bad_plans(self):
        for bad in (dict(PLAN, stop=1420.0), dict(PLAN, symbol="not a symbol"), dict(PLAN, interval="1m"),
                    dict(PLAN, entry_time="09:52"), dict(PLAN, entry_time="15:20")):
            with self.assertRaises(tm.TMError):
                tm.normalize_plan(bad)
        self.assertEqual(tm.normalize_plan(dict(PLAN, symbol="M&M"))["symbol"], "M&M")

    def test_until_limits(self):
        with self.assertRaises(tm.TMError):
            run_tm(until="15:20")
        doc = run_tm(until="15:15", replays=200)
        self.assertEqual(doc["mc"]["until"], "15:15")

    def test_not_enough_sessions_or_no_entry_bar(self):
        with self.assertRaises(tm.TMError) as e:
            tm.time_machine(text(), tm.normalize_plan(dict(PLAN, date="2026-08-10")), NOW, replays=10)
        self.assertIn("full session", str(e.exception))
        with self.assertRaises(tm.TMError) as e:
            tm.time_machine(text(), tm.normalize_plan(dict(PLAN, entry_time="11:00", entry_bar=None)), NOW,
                            replays=10)
        self.assertIn("not in the data", str(e.exception))


class Network(unittest.TestCase):
    def setUp(self):
        self.saved = (tm._get, tm._sleep)
        self.calls, self.sleeps = [], []
        tm._sleep = self.sleeps.append

    def tearDown(self):
        tm._get, tm._sleep = self.saved

    def test_url_and_fallback(self):
        codes = [429, 429, 200]

        def get(url, timeout=20):
            self.calls.append(url)
            c = codes.pop(0)
            if c != 200:
                raise urllib.error.HTTPError(url, c, "x", {}, io.BytesIO(b""))
            return "{}"
        tm._get = get
        tm.fetch_chart("SYNTHA")
        self.assertIn("query2.finance.yahoo.com/v8/finance/chart/SYNTHA.NS?range=60d&interval=5m", self.calls[0])
        self.assertIn("query1", self.calls[2])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.plan = os.path.join(self.d, "plan.json")
        with open(self.plan, "w") as fh:
            json.dump(PLAN, fh)

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_run_write_and_chart(self):
        out_path = os.path.join(self.d, "tm.json")
        code, out, _ = run(["run", "--plan", self.plan, "--offline", F60, "--now", "2026-10-30 09:51",
                            "--replays", "500", "--write", out_path])
        self.assertEqual(code, 0)
        self.assertIn("500 replays of the rest of the session, 09:50 to 15:00", out)
        self.assertIn("seed 19", out)
        code, out, _ = run(["chart", "--from", out_path])
        self.assertEqual(code, 0)
        self.assertIn("target", out)
        self.assertIn("09:50", out)
        self.assertIn("15:00", out)

    def test_flags_plan(self):
        code, out, _ = run(["run", "--symbol", "SYNTHA", "--date", "2026-10-30", "--entry", "1416", "--stop", "1408",
                            "--target", "1440", "--entry-time", "09:50", "--offline", F60, "--replays", "200",
                            "--now", "2026-10-30 09:51"])
        self.assertEqual(code, 0)
        self.assertIn("15:15", out)          # no flat_by from flags: the replays run to 15:15


class PortedLogic(unittest.TestCase):
    """Upstream tests of logic that did not change with the market, on the NSE data."""

    def test_same_seed_same_numbers_and_stable_draws(self):
        self.assertEqual(run_tm(replays=500, seed=5)["mc"], run_tm(replays=500, seed=5)["mc"])
        import random
        r = random.Random(19)
        self.assertEqual([r.randint(-2, 2) for _ in range(8)], [-2, 2, -2, 2, -1, 1, 0, 2])

    def test_window_and_replays_bounds(self):
        a = run_tm(replays=300, window=0)["mc"]
        self.assertEqual((a["replays"], a["window"]), (300, 0))
        for kw in ({"replays": 0}, {"window": 11}):
            with self.assertRaises(tm.TMError):
                run_tm(**kw)

    def test_the_verdict_thresholds_at_1r(self):
        self.assertTrue(tm.verdict({"p_target": 0.30, "p_stop": 0.26}).startswith("Close to a coin flip"))
        self.assertTrue(tm.verdict({"p_target": 0.40, "p_stop": 0.30}).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict({"p_target": 0.20, "p_stop": 0.30}).startswith("Leans toward the stop"))
        self.assertTrue(tm.verdict({"p_target": 0.60, "p_stop": 0.30}).startswith("Strongly toward the target"))
        self.assertEqual(tm.verdict({"p_target": 0.10, "p_stop": 0.60, "r_at_target": 1.0}),
                         "Strongly toward the stop: target first 10.0 % vs stop first 60.0 %.")
        self.assertEqual(tm.verdict({"p_target": 0.0, "p_stop": 0.0, "until": "15:00"}),
                         "No replay reached the target or the stop by 15:00: the price at 15:00 decides every one.")

    def test_a_2r_target_is_judged_against_its_break_even(self):
        mc = {"p_target": 0.241, "p_stop": 0.505, "p_neither": 0.254, "r_at_target": 2.0, "avg_R": 0.095}
        self.assertEqual(tm.verdict(mc), "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 32 % (target first 24.1 % vs stop "
                                         "first 50.5 %).")
        self.assertEqual(tm.verdict_notes(mc), [])
        lean = dict(mc, p_target=0.15, p_stop=0.55)
        self.assertEqual(tm.verdict_notes(lean), ["Average R is +0.095 all the same: the replays that hit neither "
                                                  "pull it the other way."])
        self.assertIn("at 1.5R the target has to come first in 40 %", tm.verdict(dict(mc, r_at_target=1.5)))
        many = dict(mc, p_neither=0.6, until="15:00")
        self.assertIn("60.0 % of the replays hit neither by 15:00", tm.verdict_notes(many)[0])

    def test_the_stop_rule_is_worked_out_when_not_given(self):
        rule = run_tm(dict(PLAN, stop_rule=None), replays=100)["history"]["rule"]
        self.assertEqual(rule["stop_rule"], "or_mid")
        rule = run_tm(dict(PLAN, stop_rule=None, stop=1401.2, target=1445.6), replays=100)["history"]["rule"]
        self.assertEqual(rule["stop_rule"], "or_low")
        rule = run_tm(dict(PLAN, stop_rule="atr", stop=1410.0, target=1434.0), replays=100)["history"]["rule"]
        self.assertEqual(rule["stop_rule"], "pct")

    def test_a_setup_other_than_orb_skips_the_history(self):
        doc = run_tm(dict(PLAN, setup="level"), replays=200)
        self.assertIsNone(doc["history"]["rule"])
        self.assertIn("History: skipped", "\n".join(tm.report(doc)))

    def test_a_sell_plan_mirrors_the_rules(self):
        doc = run_tm(dict(PLAN, side="sell", stop=1424.0, target=1392.0, stop_rule="or_high"), replays=1000)
        m = doc["mc"]
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        for t in doc["history"]["trades"]:
            self.assertTrue(t["target"] < t["entry"] < t["stop"], t)

    def test_an_entry_outside_its_bar_is_flagged(self):
        doc = run_tm(dict(PLAN, entry=1430.0, stop=1420.0, target=1460.0), replays=100)
        self.assertIn("outside that bar's range", doc["warnings"][0])

    def test_broken_data_and_the_wrong_symbol(self):
        for bad in ("not json", '{"x": 1}', '{"chart": {"result": null, "error": {"description": "No data"}}}'):
            with self.assertRaises(tm.TMError):
                tm.time_machine(bad, tm.normalize_plan(PLAN), NOW, replays=10)
        other = tm.read_offline(F60, "SYNTHB")
        with self.assertRaises(tm.TMError) as e:
            tm.time_machine(other, tm.normalize_plan(PLAN), NOW, replays=10)
        self.assertIn("the data is for SYNTHB", str(e.exception))

    def test_today_is_never_in_the_pool(self):
        doc = run_tm(replays=100)
        self.assertLess(doc["mc"]["pool_to"], "2026-10-30")
        self.assertNotIn("2026-10-30", [t["date"] for t in doc["history"]["trades"]])


class PortedNetwork(unittest.TestCase):
    def setUp(self):
        self.saved = (tm._get, tm._sleep)
        tm._sleep = lambda s: None

    def tearDown(self):
        tm._get, tm._sleep = self.saved

    def answer(self, codes):
        def get(url, timeout=20):
            c = codes.pop(0)
            raise urllib.error.HTTPError(url, c, "x", {}, io.BytesIO(b""))
        tm._get = get

    def test_rate_limited_everywhere_is_one_plain_line(self):
        self.answer([429, 429, 429, 429])
        with self.assertRaises(tm.TMError) as e:
            tm.fetch_chart("SYNTHA")
        self.assertIn("rate limiting", str(e.exception))

    def test_404(self):
        self.answer([404])
        with self.assertRaises(tm.TMError) as e:
            tm.fetch_chart("NOPE")
        self.assertIn("check the symbol", str(e.exception))


if __name__ == "__main__":
    unittest.main()
