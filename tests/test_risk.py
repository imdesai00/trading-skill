import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ist

check = load("trader-risk", "check")
size = load("trader-budget", "size")
PLAN = {"symbol": "SYNTHA", "side": "buy", "date": "2026-10-30", "setup": "orb", "entry": 1416.0,
        "entry_time": "09:50", "stop": 1408.0, "target": 1440.0, "tick": 0.1, "flat_by": "15:00"}
NOW = ist("2026-10-30 09:51")
TODAY = "2026-10-30"


def order(**over):
    o = size.order_from(size.size_trade(PLAN, {}, today=TODAY), {})
    o.update(over)
    return o


def trade(**kw):
    t = {"trade_id": "PT-20261030-01", "date": TODAY, "exit_date": TODAY, "symbol": "SYNTHB", "qty": 100,
         "status": "closed", "net_pnl": -400.0, "max_loss": 490.0, "entry_fill": 240.0}
    t.update(kw)
    return t


def ck(orders=None, cfg=None, ledger=None, now=NOW, kite=None, prices=None, times=None):
    return check.check(orders or [order()], cfg or {}, ledger, prices, kite, None, now, times)


def reasons(res, i=0):
    return " | ".join(res["results"][i]["reasons"])


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = check.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheSynthTrade(unittest.TestCase):
    def test_passes_with_the_numbers(self):
        res = ck()
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"], r["qty"]), ("PASS", "entry", 45))
        self.assertLessEqual(r["max_loss"], 500)
        self.assertGreaterEqual(r["reward_risk_net"], 1.5)
        self.assertEqual(r["fingerprint"], check.fingerprint(order()))
        self.assertEqual(res["mode"], "paper")

    def test_the_fingerprint_ignores_notes_but_not_the_order(self):
        self.assertEqual(check.fingerprint(order()), check.fingerprint(order(why="other words", kite={})))
        self.assertNotEqual(check.fingerprint(order()), check.fingerprint(order(qty=44)))


class PerTradeLimits(unittest.TestCase):
    def test_one_more_share_breaks_the_max_loss(self):
        res = ck([order(qty=46)])
        self.assertIn("loss: 46 shares lose", reasons(res))
        self.assertIn("over your ₹500 max loss", reasons(res))

    def test_reward_risk_after_charges(self):
        res = ck([order(target=1432.0)])
        self.assertIn("reward:risk after charges is", reasons(res))

    def test_charges_share_of_the_target(self):
        res = ck([order(stop=1414.0, target=1420.0, qty=40)], {"min_reward_risk": 0})
        self.assertIn("charges:", reasons(res))

    def test_position_cap_and_cash(self):
        res = ck([order(qty=53, stop=1413.0, target=1440.0)], {"max_loss_per_trade_inr": 5000})
        self.assertIn("position: ₹75,048.00 of SYNTHA is over your ₹75,000 position cap", reasons(res))
        res = ck([order(qty=45)], {}, {"balance": 60000, "trades": []})
        self.assertIn("cash: needs", reasons(res))
        self.assertIn("leverage is off", reasons(res))
        res = ck([order(qty=45)], {}, {"balance": 70000, "trades": []})
        self.assertIn("under your 10% cash buffer", reasons(res))

    def test_stop_rules(self):
        self.assertIn("stop: no stop", reasons(ck([order(stop=None)])))
        self.assertIn("must be below the entry", reasons(ck([order(stop=1420.0)])))

    def test_format_and_products(self):
        r = reasons(ck([order(product="CNC", exchange="BSE", qty=4.5, entry_type="stop")]))
        for word in ("product must be MIS", "NSE only", "whole number of shares", "entry_type must be"):
            self.assertIn(word, r)
        self.assertIn("not an NSE symbol", reasons(ck([order(symbol="BTC/INR")])))

    def test_shorts_are_off(self):
        res = ck([order(side="sell")])
        self.assertIn("short selling is off", reasons(res))

    def test_blocked_and_duplicate(self):
        self.assertIn("blocked list", reasons(ck(cfg={"blocked": ["SYNTHA"]})))
        res = ck([order(), order()], {"max_trades_per_day": 9, "max_open_positions": 9,
                                      "max_daily_loss_inr": 99999, "max_weekly_loss_inr": 99999})
        self.assertIn("duplicate", reasons(res, 1))


class DayWeekAndPause(unittest.TestCase):
    def test_daily_limit_counts_realised_loss_and_open_risk(self):
        led = {"balance": 99000, "trades": [trade(net_pnl=-1000.0)]}
        self.assertEqual(ck(ledger=led)["results"][0]["verdict"], "PASS")     # 1,000 + 490.88 fits in 1,500
        led = {"balance": 98900, "trades": [trade(net_pnl=-1100.0)]}
        res = ck(ledger=led)
        self.assertIn("daily: ₹1,100.00 used today", reasons(res))
        led = {"balance": 98500, "trades": [trade(net_pnl=-1500.0)]}
        self.assertIn("already reach your ₹1,500 daily limit. No new entries today", reasons(ck(ledger=led)))

    def test_a_winning_day_does_not_widen_the_limit(self):
        led = {"balance": 101000, "trades": [trade(net_pnl=1000.0)]}
        self.assertEqual(ck(ledger=led)["results"][0]["verdict"], "PASS")
        self.assertEqual(ck(ledger=led)["account"]["realised_today"], 1000.0)

    def test_weekly_limit_from_monday(self):
        week = [trade(trade_id="a", date="2026-10-26", exit_date="2026-10-26", net_pnl=-1400.0),
                trade(trade_id="b", date="2026-10-27", exit_date="2026-10-27", net_pnl=-1400.0),
                trade(trade_id="c", date="2026-10-28", exit_date="2026-10-28", net_pnl=-1000.0)]
        res = ck(ledger={"balance": 96200, "trades": week})
        self.assertIn("weekly:", reasons(res))
        self.assertIn("resets on Monday", reasons(res))
        last_week = [dict(t, date="2026-10-2" + str(i), exit_date="2026-10-2" + str(i)) for i, t in
                     zip((0, 1, 2), week)]
        self.assertNotIn("weekly", reasons(ck(ledger={"balance": 96200, "trades": last_week})))

    def test_drawdown_pause_stops_everything(self):
        res = ck(ledger={"balance": 92000, "trades": []})
        self.assertTrue(res["account"]["paused"])
        self.assertIn("pause: the account is ₹8,000.00 below capital", reasons(res))
        self.assertIn("drawdown pause is on", res["warnings"][0])
        self.assertFalse(ck(ledger={"balance": 92000.01, "trades": []})["account"]["paused"])

    def test_trades_per_day_and_one_open_position(self):
        led = {"balance": 100000, "trades": [trade(trade_id=str(i), net_pnl=0.0) for i in range(3)]}
        self.assertIn("trades: 3 entries already today", reasons(ck(ledger=led)))
        led = {"balance": 100000, "trades": [trade(status="open", net_pnl=None, max_loss=300.0)]}
        self.assertIn("open: 1 position already open (SYNTHB)", reasons(ck(ledger=led)))

    def test_two_entries_in_one_batch_hit_the_open_limit(self):
        o2 = order(symbol="SYNTHD")
        res = ck([order(), o2], {"max_trades_per_day": 9})
        self.assertEqual(res["results"][0]["verdict"], "PASS")
        self.assertIn("open:", reasons(res, 1))


class WindowAndMode(unittest.TestCase):
    def test_window(self):
        for when, word in (("2026-10-30 09:20", "before 09:30"), ("2026-10-30 14:50", "after 14:45"),
                           ("2026-10-31 10:00", "weekend"), ("2026-11-10 10:00", "Diwali"),
                           ("2026-11-08 14:30", "Muhurat"), ("2027-01-04 10:00", "2027")):
            self.assertIn(word, reasons(ck(now=ist(when))), when)

    def test_live_needs_kite_enabled_and_a_check_today(self):
        r = reasons(ck(cfg={"mode": "live"}))
        self.assertIn("kite_enabled", r)
        r = reasons(ck(cfg={"mode": "live", "kite_enabled": True}))
        self.assertIn("Kite is not verified today", r)
        r = reasons(ck(cfg={"mode": "live", "kite_enabled": True}, kite={"verified": True, "date": "2026-10-29"}))
        self.assertIn("not verified today", r)
        res = ck(cfg={"mode": "live", "kite_enabled": True}, kite={"verified": True, "date": TODAY})
        self.assertEqual(res["results"][0]["verdict"], "PASS")
        self.assertIn("LIVE mode", res["warnings"][0])

    def test_a_fresh_higher_price_raises_the_risk(self):
        res = ck(prices={"SYNTHA": 1419.0}, times={"SYNTHA": ist("2026-10-30 09:50")})
        self.assertEqual(res["results"][0]["entry_ref"], 1419.0)
        self.assertIn("loss:", reasons(res))
        res = ck(prices={"SYNTHA": 1419.0}, times={"SYNTHA": ist("2026-10-30 09:20")})
        self.assertEqual(res["results"][0]["entry_ref"], 1416.0)
        self.assertIn("older than 15 minutes", " ".join(res["results"][0]["warnings"]))


class Exits(unittest.TestCase):
    def test_an_exit_is_never_blocked_by_day_limits_or_the_window(self):
        led = {"balance": 91000, "trades": [trade(symbol="SYNTHA", qty=45, status="open", net_pnl=None,
                                                   entry_fill=1416.7)]}
        exit_order = {"symbol": "SYNTHA", "exchange": "NSE", "side": "sell", "qty": 45, "product": "MIS",
                      "entry_type": "market"}
        res = ck([exit_order], ledger=led, now=ist("2026-10-30 15:00"))
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"]), ("PASS", "exit"))
        self.assertTrue(any("after 14:45" in w for w in r["warnings"]))

    def test_selling_more_than_you_hold_is_a_short(self):
        led = {"balance": 100000, "trades": [trade(symbol="SYNTHA", qty=45, status="open", net_pnl=None)]}
        res = ck([{"symbol": "SYNTHA", "exchange": "NSE", "side": "sell", "qty": 50, "product": "MIS",
                   "entry_type": "market"}], ledger=led)
        self.assertIn("short selling is off", reasons(res))


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        with open(self.orders, "w") as fh:
            json.dump({"orders": [order()]}, fh)
        self.out = os.path.join(self.d, "state", "risk.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def argv(self, *extra):
        return ["--orders", self.orders, "--config", os.path.join(self.d, "none.json"),
                "--ledger", os.path.join(self.d, "none.json"), "--prices", os.path.join(self.d, "none.json"),
                "--kite-state", os.path.join(self.d, "none.json"), "--out", self.out] + list(extra)

    def test_pass_writes_the_verdicts(self):
        code, out, _ = run(self.argv("--now", "2026-10-30 09:51"))
        self.assertEqual(code, 0)
        self.assertIn("PASS  #1 buy SYNTHA 45 shares, entry", out)
        self.assertIn("PAPER mode", out)
        with open(self.out) as fh:
            rec = json.load(fh)
        self.assertEqual((rec["date"], rec["results"][0]["verdict"]), ("2026-10-30", "PASS"))

    def test_block_exit_code_and_no_write(self):
        code, out, _ = run(self.argv("--now", "2026-10-30 09:20", "--no-write"))
        self.assertEqual(code, 1)
        self.assertIn("BLOCK", out)
        self.assertFalse(os.path.exists(self.out))

    def test_missing_orders(self):
        code, _, err = run(["--orders", os.path.join(self.d, "nope.json")])
        self.assertEqual(code, 1)
        self.assertIn("run trader-budget first", err)


if __name__ == "__main__":
    unittest.main()
