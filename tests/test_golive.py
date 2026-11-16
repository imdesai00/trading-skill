import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT

golive = load("trader-golive", "golive")


def trade(i, net, day=None, charges=65.0, **kw):
    day = day or (datetime.date(2026, 10, 1) + datetime.timedelta(days=i)).isoformat()
    t = {"trade_id": "PT-%s-01" % day.replace("-", ""), "date": day, "exit_date": day, "status": "closed",
         "net_pnl": net, "gross_pnl": round(net + charges, 2), "charges": charges, "r_multiple": round(net / 490.0, 2)}
    t.update(kw)
    return t


def ledger(nets, **kw):
    trades = [trade(i, n) for i, n in enumerate(nets)]
    led = {"capital": 100000, "balance": 100000 + sum(nets), "trades": trades, "skipped": []}
    led.update(kw)
    return led


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = golive.main(argv)
    return code, out.getvalue(), err.getvalue()


class Numbers(unittest.TestCase):
    def test_a_known_record(self):
        a = golive.analyse(ledger([-400, 900, -500, -300, 700, -450]), {})
        self.assertEqual((a["n"], a["wins"], a["losses"], a["net"]), (6, 2, 4, -50.0))
        self.assertEqual((a["win_rate"], a["expectancy"]), (100 * 2 / 6.0, round(-50 / 6.0, 2)))
        self.assertEqual((a["avg_win"], a["avg_loss"]), (800.0, -412.5))
        self.assertEqual(a["breakeven_win_rate"], round(100 * 412.5 / (800 + 412.5), 1))
        self.assertEqual(a["charges"], 390.0)
        self.assertEqual(a["longest_losing_streak"], 2)
        self.assertEqual((a["max_drawdown"], a["below_start_max"]), (800.0, 400.0))   # +500 peak down to -300; first dip -400
        self.assertEqual(a["balance"], 99950.0)

    def test_drawdown_is_peak_to_trough_and_below_start_is_separate(self):
        a = golive.analyse(ledger([1000, -300, -500, -400, 200]), {})
        self.assertEqual(a["max_drawdown"], 1200.0)          # from the +1000 peak down to -200
        self.assertEqual(a["below_start_max"], 200.0)
        self.assertEqual(a["longest_losing_streak"], 3)

    def test_limits_and_rules(self):
        led = ledger([-1600, -300, -300])
        led["trades"][1]["date"] = led["trades"][1]["exit_date"] = "2026-10-01"       # two trades on day 1
        led["trades"].append(trade(3, -700, "2026-10-01"))
        a = golive.analyse(led, {})
        self.assertEqual(a["daily_limit_days"], 1)
        self.assertEqual(a["rules_broken"], 2)                  # -1600 and -700 are over the ₹500 limit
        self.assertEqual(a["trade_cap_days"], 1)
        led = ledger([-1900, -1900, -400])
        for t, d in zip(led["trades"], ("2026-10-26", "2026-10-27", "2026-10-28")):
            t["date"] = t["exit_date"] = d
        self.assertEqual(golive.analyse(led, {})["weekly_limit_weeks"], 1)

    def test_pause_reached(self):
        led = ledger([-2100] * 4)
        self.assertTrue(golive.analyse(led, {})["pause_reached"])

    def test_skipped_signals(self):
        led = ledger([100, -50])
        led["skipped"] = [{"hypothetical_net_pnl": 400.0}, {"hypothetical_net_pnl": -100.0}, {"hypothetical_net_pnl": None}]
        a = golive.analyse(led, {})
        self.assertEqual((a["skipped"], a["skipped_replayed"], a["skipped_net"]), (3, 2, 300.0))

    def test_no_trades(self):
        a = golive.analyse({"capital": 100000, "trades": [], "skipped": []}, {})
        self.assertEqual(a["n"], 0)
        self.assertIn("no closed paper trades", "\n".join(golive.report_lines(a)))


class TheLuckTest(unittest.TestCase):
    def test_wilson(self):
        lo, hi = golive.wilson(20, 40)
        self.assertAlmostEqual(lo, 0.352, places=2)
        self.assertAlmostEqual(hi, 0.648, places=2)
        self.assertIsNone(golive.wilson(0, 0))

    def test_same_record_same_answer(self):
        vals = [-400.0, 900.0, -500.0, -300.0, 700.0, -450.0, 120.0, -80.0]
        self.assertEqual(golive.bootstrap(vals, resamples=2000), golive.bootstrap(vals, resamples=2000))
        self.assertNotEqual(golive.bootstrap(vals, seed=3, resamples=2000), golive.bootstrap(vals, resamples=2000))

    def test_a_small_mixed_record_cannot_be_told_from_luck(self):
        a = golive.analyse(ledger([-400, 900, -500, -300, 700, -450, 650, -480, 720, -430]), {})
        self.assertEqual(a["luck"], "inside")
        self.assertLess(a["ci_low"], 0)
        self.assertGreater(a["ci_high"], 0)
        text = "\n".join(golive.report_lines(a))
        self.assertIn("CANNOT be told apart from luck", text)
        self.assertIn("WARNING: 10 trades is under the 40", text)

    def test_a_clear_edge_and_a_clear_loser(self):
        win = golive.analyse(ledger([300 + (i % 3) * 10 for i in range(45)]), {})
        self.assertEqual(win["luck"], "above")
        self.assertNotIn("WARNING", "\n".join(golive.report_lines(win)).split("Net result")[0])
        self.assertIn("Pure luck is unlikely", "\n".join(golive.report_lines(win)))
        lose = golive.analyse(ledger([-200 - (i % 3) * 10 for i in range(45)]), {})
        self.assertEqual(lose["luck"], "below")
        self.assertIn("lost money", "\n".join(golive.report_lines(lose)))

    def test_one_trade_has_nothing_to_measure(self):
        a = golive.analyse(ledger([500]), {})
        self.assertNotIn("luck", a)
        self.assertIn("Not with 1 trade", "\n".join(golive.report_lines(a)))


class TheReportIsHonest(unittest.TestCase):
    def setUp(self):
        self.text = "\n".join(golive.report_lines(golive.analyse(
            ledger([-400, 900, -500, -300, 700, -450, 650, -480, 720, -430]), {})))

    def test_it_never_decides(self):
        low = self.text.lower()
        for bad in ("you should go live", "ready to go live", "not ready", "go live now", "recommend", "buy", "sell "):
            self.assertNotIn(bad, low)
        self.assertIn("does not decide anything for you", self.text)
        self.assertIn("your decision", self.text)

    def test_it_states_what_it_cannot_see(self):
        for want in ("Paper fills are kinder", "one market mood", "7 in 10", "Not financial advice",
                     "sebi.gov.in", "Expectancy", "Longest losing streak", "Deepest drawdown", "Charges"):
            self.assertIn(want, self.text)

    def test_rupees_use_indian_grouping(self):
        self.assertIn("₹1,00,000", self.text)
        self.assertEqual(golive.inr(100000), "₹1,00,000")


class Steps(unittest.TestCase):
    def test_the_steps_you_asked_for(self):
        text = "\n".join(golive.steps_lines(broker="kite"))
        for want in ("Connect Kite", "Needs approval", "place_order", "cancel_order", "mode\": \"live\"",
                     "kite_enabled\": true", "max_loss_per_trade_inr\": 250", "first 2 weeks", "Mode = live",
                     "Never paste them into a chat", "Re-verify", "mcp.kite.trade"):
            self.assertIn(want, text)
        self.assertEqual(len(golive.RECHECK), 12)

    def test_the_dhan_steps_are_the_default_and_never_switch_anything(self):
        text = "\n".join(golive.steps_lines())
        for want in ("Connect Dhan", "Needs approval", "mcp.dhan.co", "dhan.py securities", "--static-ip-confirmed",
                     "\"broker\": \"dhan\"", "\"dhan_enabled\": true", "ONE super order", "max_loss_per_trade_inr\": 250",
                     "Never paste a token", "dhan_tools", "Not financial advice"):
            self.assertIn(want, text, want)
        self.assertNotIn("kite_enabled", text)
        self.assertEqual(len(golive.RECHECK_DHAN), 11)
        with open(os.path.join(ROOT, "VERIFY.md"), encoding="utf-8") as fh:
            v = fh.read().lower()
        self.assertIn("recheck-at-dhan", v)
        self.assertIn("## 15. dhan", v)

    def test_recheck_list_matches_verify_md(self):
        with open(os.path.join(ROOT, "VERIFY.md"), encoding="utf-8") as fh:
            v = fh.read()
        section = v.split("## Items to re-check when you connect Kite")[1]
        for needle in ("place_order", "variety=co", "GTT", "market_protection", "SL-M", "static IP", "07:30",
                       "15:12", "zerodha.com/charges", "tick"):
            self.assertIn(needle.lower(), section.lower() + " " + " ".join(golive.RECHECK).lower())

    def test_half_risk_can_be_changed_and_json(self):
        code, out, _ = run(["steps", "--half-risk", "200", "--json"])
        self.assertEqual(code, 0)
        items = [i for step in json.loads(out) for i in step["items"]]
        self.assertTrue(any('"max_loss_per_trade_inr": 200' in i for i in items))


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_check_reads_the_ledger_and_changes_nothing(self):
        p, c = os.path.join(self.d, "paper.json"), os.path.join(self.d, "config.json")
        with open(p, "w") as fh:
            json.dump(ledger([-400, 900, -500]), fh)
        cfg = {"mode": "paper", "kite_enabled": False, "min_trades_for_review": 40}
        with open(c, "w") as fh:
            json.dump(cfg, fh)
        with open(c) as fh:
            before = fh.read()
        code, out, _ = run(["check", "--ledger", p, "--config", c])
        self.assertEqual(code, 0)
        self.assertIn("Trades: 3 closed", out)
        with open(c) as fh:
            self.assertEqual(fh.read(), before)                 # the config is untouched
        code, out, _ = run(["check", "--ledger", p, "--config", c, "--json"])
        self.assertEqual(json.loads(out)["n"], 3)
        code, _, err = run(["check", "--ledger", os.path.join(self.d, "nope.json")])
        self.assertEqual(code, 1)
        self.assertIn("paper broker writes it", err)

    def test_it_cannot_write_the_config_or_reach_kite(self):
        with open(os.path.join(ROOT, "trader-golive", "golive.py"), encoding="utf-8") as fh:
            text = fh.read()
        for bad in ("urllib", "socket", "http.client", "requests", "os.environ", "'w'", '"w"', "write_json",
                    "os.replace", ".write("):
            self.assertNotIn(bad, text, bad)


if __name__ == "__main__":
    unittest.main()
