import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, ist

size = load("trader-budget", "size")
chg = load("trader-budget", "charges")
PLAN = {"symbol": "SYNTHA", "side": "buy", "date": "2026-10-30", "setup": "orb", "entry": 1416.0,
        "entry_time": "09:50", "stop": 1408.0, "target": 1440.0, "tick": 0.1, "flat_by": "15:00"}
TODAY = "2026-10-30"


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


def sz(plan=PLAN, cfg=None, **kw):
    kw.setdefault("today", TODAY)
    return size.size_trade(plan, cfg or {}, **kw)


class TheSynthTrade(unittest.TestCase):
    def test_max_loss_decides_45_shares(self):
        s = sz()
        self.assertEqual((s["qty"], s["bound_by"]), (45, "max loss"))
        self.assertEqual([(l["name"], l["shares"]) for l in s["limits"]],
                         [("max loss", 45), ("position cap", 52), ("cash", 63)])
        self.assertLessEqual(s["max_loss"], 500)
        self.assertGreater(size.loss_at_stop(46, 1416.0, 1408.0, 0.05, chg.DEFAULT_RATES)[0], 500)
        self.assertIsNone(s["refusal"])

    def test_the_loss_includes_slippage_and_charges(self):
        s = sz()
        bf, sf = 1416 * 1.0005, 1408 * 0.9995
        c = chg.charges(45 * bf, 45 * sf)
        self.assertAlmostEqual(s["max_loss"], round(45 * (bf - sf) + c["total"], 2), places=2)
        self.assertEqual(s["charges_at_stop"], c["total"])

    def test_charges_only_when_slippage_is_switched_off(self):
        s = sz(cfg={"limits_include_slippage": False})
        self.assertEqual(s["qty"], 52)             # the position cap decides now
        self.assertEqual(s["bound_by"], "position cap")
        self.assertLessEqual(52 * 8 + chg.charges(52 * 1416, 52 * 1408)["total"], 500)

    def test_reward_risk_after_charges(self):
        s = sz()
        self.assertEqual(s["reward_risk_gross"], 3.0)
        self.assertGreater(s["reward_risk_net"], 1.5)
        s = sz(dict(PLAN, target=1432.0))          # 2R gross is about 1.2 after slippage and charges
        self.assertLess(s["reward_risk_net"], 1.5)
        self.assertIn("reward:risk after charges", s["refusal"])

    def test_charges_over_a_quarter_of_the_target_are_refused(self):
        plan = dict(PLAN, entry=1416.0, stop=1414.0, target=1420.0)
        s = sz(plan, {"min_reward_risk": 0})
        self.assertGreater(s["charges_pct_of_target"], 25)
        self.assertIn("charges of", s["refusal"])

    def test_position_cap_and_cash_decide_when_the_stop_is_close(self):
        s = sz(dict(PLAN, stop=1413.0, target=1428.0), {"min_reward_risk": 0, "max_charges_pct_of_target": 100})
        self.assertEqual(s["bound_by"], "position cap")
        s = sz(dict(PLAN, stop=1413.0, target=1428.0), {"min_reward_risk": 0, "max_charges_pct_of_target": 100},
               cash=50000)
        self.assertEqual((s["bound_by"], s["qty"]), ("cash", 28))   # (50,000 - 10,000) / 1416

    def test_a_fresher_higher_price_shrinks_the_trade(self):
        s = sz(price=1418.0)
        self.assertEqual((s["entry"], s["entry_from"]), (1418.0, "price"))
        self.assertLess(s["qty"], 45)
        self.assertEqual(sz(price=1410.0)["entry"], 1416.0)


class PlanRules(unittest.TestCase):
    def test_refusals(self):
        for plan, word in ((dict(PLAN, side="sell"), "shorts are off"), (dict(PLAN, date="2026-10-29"), "make a new"),
                           (dict(PLAN, stop=1420.0), "not below"), (dict(PLAN, target=1410.0), "not above"),
                           (dict(PLAN, symbol=""), "no symbol")):
            with self.assertRaises(size.SizeError) as e:
                sz(plan)
            self.assertIn(word, str(e.exception))
        with self.assertRaises(size.SizeError):
            sz(cfg={"blocked": ["SYNTHA"]})

    def test_a_stop_too_far_for_one_share(self):
        s = sz(dict(PLAN, stop=900.0, target=3000.0))
        self.assertEqual(s["qty"], 0)
        self.assertIn("one share loses", s["refusal"])
        self.assertIsNone(size.order_from(s, {}))


class TheOrder(unittest.TestCase):
    def test_broker_neutral_fields_and_the_kite_payload(self):
        s = sz()
        o = size.order_from(s, {})
        self.assertEqual({k: o[k] for k in size.NEUTRAL_FIELDS},
                         {"symbol": "SYNTHA", "exchange": "NSE", "side": "buy", "qty": 45, "product": "MIS",
                          "entry_type": "market", "limit_price": None, "stop": 1408.0, "target": 1440.0})
        k = o["kite"]
        self.assertEqual(k["entry"], {"variety": "regular", "exchange": "NSE", "tradingsymbol": "SYNTHA",
                                      "quantity": 45, "product": "MIS", "validity": "DAY",
                                      "transaction_type": "BUY", "order_type": "MARKET", "market_protection": -1})
        self.assertEqual((k["stop_after_fill"]["order_type"], k["stop_after_fill"]["trigger_price"],
                          k["stop_after_fill"]["transaction_type"]), ("SL-M", 1408.0, "SELL"))
        self.assertIn("not a resting order", k["target"])
        self.assertIn("decided by your max loss", o["why"])

    def test_limit_entry_and_sl_stop(self):
        s = sz(limit=1416.0)
        o = size.order_from(s, {"stop_order_type": "SL"})
        self.assertEqual((o["entry_type"], o["limit_price"]), ("limit", 1416.0))
        self.assertEqual((o["kite"]["entry"]["order_type"], o["kite"]["entry"]["price"]), ("LIMIT", 1416.0))
        st = o["kite"]["stop_after_fill"]
        self.assertEqual((st["order_type"], st["trigger_price"], st["price"]), ("SL", 1408.0, 1400.9))


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.plan = os.path.join(self.d, "plan.json")
        with open(self.plan, "w") as fh:
            json.dump(PLAN, fh)
        self.cfg = os.path.join(self.d, "config.json")
        self.orders = os.path.join(self.d, "orders.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_init_writes_your_limits_once(self):
        code, out, _ = run(["init", "--config", self.cfg])
        self.assertEqual(code, 0)
        self.assertIn("capital ₹1,00,000, max loss ₹500 a trade, ₹1,500 a day, ₹4,000 a week", out)
        with open(self.cfg) as fh:
            cfg = json.load(fh)
        self.assertEqual((cfg["mode"], cfg["kite_enabled"], cfg["flat_by"], cfg["max_open_positions"]),
                         ("paper", False, "15:00", 1))
        self.assertIn("exchange_pct", cfg["charges"])
        code, out, _ = run(["init", "--config", self.cfg])
        self.assertEqual(code, 1)
        self.assertIn("nothing changed", out)

    def test_size_and_write(self):
        code, out, _ = run(["--plan", self.plan, "--config", self.cfg, "--now", "2026-10-30 09:51",
                            "--paper", os.path.join(self.d, "none.json"), "--write", self.orders])
        self.assertEqual(code, 0)
        self.assertIn("max loss        45 shares", out)
        self.assertIn("<- decides it", out)
        self.assertIn("BUY 45 SYNTHA on NSE, market", out)
        with open(self.orders) as fh:
            doc = json.load(fh)
        self.assertEqual((doc["mode"], len(doc["orders"]), doc["orders"][0]["qty"]), ("paper", 1, 45))

    def test_the_paper_balance_sizes_the_cash_limit(self):
        paper = os.path.join(self.d, "paper.json")
        with open(paper, "w") as fh:
            json.dump({"balance": 30000}, fh)
        code, out, _ = run(["--plan", self.plan, "--config", self.cfg, "--now", "2026-10-30 09:51",
                            "--paper", paper, "--json"])
        s = json.loads(out)["sizing"]
        self.assertEqual((s["bound_by"], s["qty"]), ("cash", 14))     # (30,000 - 10,000) / 1416

    def test_refusal_writes_nothing(self):
        with open(self.plan, "w") as fh:
            json.dump(dict(PLAN, target=1432.0), fh)
        code, out, _ = run(["--plan", self.plan, "--config", self.cfg, "--now", "2026-10-30 09:51",
                            "--paper", os.path.join(self.d, "none.json"), "--write", self.orders])
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", out)
        self.assertFalse(os.path.exists(self.orders))

    def test_example_config_matches_the_defaults(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            ex = json.load(fh)
        for k, v in size.DEFAULTS.items():
            self.assertEqual(ex[k], v, k)


if __name__ == "__main__":
    unittest.main()
