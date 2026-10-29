import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, NSE, ROOT

ex = load("trader-exit", "exit")
F5 = os.path.join(NSE, "5m-1d")
ENTRY = {"created_by": "trader-entry", "symbol": "SYNTHA", "setup": "orb", "side": "buy", "date": "2026-10-30",
         "status": "triggered", "time": "09:50", "bar_start": "09:45", "bar_index": 6, "price": 1416.0,
         "level": 1414.8, "interval": "5m", "opening_range": {"high": 1414.8, "low": 1401.2, "minutes": 15}}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ex.main(argv)
    return code, out.getvalue(), err.getvalue()


def compute(entry, stop, target="2R", tick=None, **kw):
    s = ex.parse_stop(stop)
    t = ex.parse_target(target)
    return ex.compute(entry, s[0], s[1], t[0], t[1], tick=tick, **kw)


class Math(unittest.TestCase):
    def test_or_mid_and_2r_on_the_tick(self):
        c = compute(1416, "or-mid", or_high=1414.8, or_low=1401.2)
        self.assertEqual((c["stop"], c["target"], c["risk"], c["tick"]),
                         (Decimal("1408.00"), Decimal("1432.00"), Decimal("8.00"), Decimal("0.1")))
        self.assertEqual(c["reward_risk"], Decimal("2.00"))

    def test_the_stop_rounds_down_and_the_target_too(self):
        c = compute(1416, "or-mid", or_high=1414.9, or_low=1401.2)          # midpoint 1408.05
        self.assertEqual(c["stop"], Decimal("1408.00"))
        self.assertIn("rounded down to the ₹0.10 tick", c["stop_detail"])
        c = compute(1416, "pct:0.37", "1.5R")                              # stop 1410.7608 -> 1410.70
        self.assertEqual(c["stop"], Decimal("1410.70"))
        self.assertEqual(c["target"], Decimal("1423.90"))                  # 1423.95 -> down to the tick
        c = compute(245.56, "or-mid", or_high=245, or_low=240)
        self.assertEqual((c["stop"], c["tick"]), (Decimal("242.50"), Decimal("0.01")))

    def test_refuses_nonsense_stops(self):
        for args, word in (((1416, "price:1416"), "at or above the entry"),
                           ((1416, "price:1415.9"), "under 2 ticks"),
                           ((245.56, "price:245.50"), "0.05%"),
                           ((1416, "price:1408", "price:1410"), "at or below the entry")):
            with self.assertRaises(ex.PlanError) as e:
                compute(*args)
            self.assertIn(word, str(e.exception))

    def test_an_entry_off_the_tick_grid_is_refused(self):
        with self.assertRaises(ex.PlanError) as e:
            compute(1416.05, "price:1400")
        self.assertIn("tick grid", str(e.exception))
        self.assertEqual(compute(1416.05, "price:1400", tick=0.05)["stop"], Decimal("1400.00"))

    def test_parse_rules(self):
        self.assertEqual(ex.parse_stop("price:₹1,408"), ("price", Decimal("1408")))
        self.assertEqual(ex.parse_target("3R"), ("r", Decimal("3")))
        for bad in ("pct:0", "pct:120", "x"):
            with self.assertRaises(ex.PlanError):
                ex.parse_stop(bad)


class FlatBy(unittest.TestCase):
    def test_flat_by_rules(self):
        import datetime
        day = datetime.date(2026, 10, 30)
        self.assertEqual(ex.resolve_flat_by(day, None, {"flat_by": "15:00"}, None), ("15:00", ""))
        t, note = ex.resolve_flat_by(day, "15:11", {}, None)
        self.assertIn("only 1 minute before Zerodha's 15:12", note)
        with self.assertRaises(ex.PlanError):
            ex.resolve_flat_by(day, "15:15", {}, None)
        with self.assertRaises(ex.PlanError):
            ex.resolve_flat_by(day, None, {"flat_by": "15:00"}, datetime.time(15, 0))


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.entry = os.path.join(self.d, "entry.json")
        with open(self.entry, "w") as fh:
            json.dump(ENTRY, fh)
        self.cfg = os.path.join(self.d, "none.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_plan_from_the_entry(self):
        out_path = os.path.join(self.d, "plan.json")
        code, out, _ = run(["plan", "--from", self.entry, "--target", "3R", "--write", out_path,
                            "--now", "2026-10-30 09:51", "--config", self.cfg])
        self.assertEqual(code, 0)
        self.assertIn("Stop          ₹1,408.00  opening-range midpoint", out)
        self.assertIn("Target        ₹1,440.00  3R (three times the risk)", out)
        self.assertIn("Flat by       15:00 IST", out)
        with open(out_path) as fh:
            plan = json.load(fh)
        self.assertEqual({k: plan[k] for k in ("symbol", "entry", "stop", "target", "risk_per_share", "flat_by",
                                               "tick", "product", "exchange", "entry_time", "entry_bar")},
                         {"symbol": "SYNTHA", "entry": 1416.0, "stop": 1408.0, "target": 1440.0,
                          "risk_per_share": 8.0, "flat_by": "15:00", "tick": 0.1, "product": "MIS",
                          "exchange": "NSE", "entry_time": "09:50", "entry_bar": 6})

    def test_a_2r_plan_warns_about_the_minimum(self):
        code, out, _ = run(["plan", "--from", self.entry, "--target", "1R", "--now", "2026-10-30 09:51",
                            "--config", self.cfg])
        self.assertEqual(code, 0)
        self.assertIn("under your min_reward_risk 1.5", out)

    def test_atr_stop_reads_the_bars(self):
        code, out, _ = run(["plan", "--from", self.entry, "--stop", "atr:1.5", "--offline", F5,
                            "--now", "2026-10-30 09:51", "--config", self.cfg])
        self.assertEqual(code, 0)
        self.assertIn("ATR(7)", out)
        self.assertIn("fewer than 14 so far", out)

    def test_longs_only_and_not_triggered(self):
        code, _, err = run(["plan", "--from", self.entry, "--side", "short", "--config", self.cfg])
        self.assertIn("longs only", err)
        with open(self.entry, "w") as fh:
            json.dump(dict(ENTRY, status="waiting"), fh)
        code, _, err = run(["plan", "--from", self.entry, "--config", self.cfg])
        self.assertEqual(code, 1)
        self.assertIn("not triggered", err)

    def test_show(self):
        p = os.path.join(self.d, "plan.json")
        run(["plan", "--from", self.entry, "--write", p, "--now", "2026-10-30 09:51", "--config", self.cfg])
        code, out, _ = run(["show", "--plan", p, "--now", "2026-11-02 09:51"])
        self.assertEqual(code, 0)
        self.assertIn("this plan is for 2026-10-30, not today", out)


class SkillDoc(unittest.TestCase):
    def test_skill_md_matches_the_tool(self):
        with open(os.path.join(ROOT, "trader-exit", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        for word in ("tick", "15:00", "or-mid", "IST"):
            self.assertIn(word, text)


if __name__ == "__main__":
    unittest.main()
