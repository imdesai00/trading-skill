import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, NSE, ist

paper = load("trader-broker", "paper")
ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")
size = load("trader-budget", "size")
chg = load("trader-budget", "charges")
F1 = os.path.join(NSE, "1m-1d")
DAY = datetime.date(2026, 10, 30)
PLAN = {"symbol": "SYNTHA", "side": "buy", "date": "2026-10-30", "setup": "orb", "entry": 1416.0,
        "entry_time": "09:50", "stop": 1408.0, "target": 1440.0, "tick": 0.1, "flat_by": "15:00"}
ORDER = size.order_from(size.size_trade(PLAN, {}, today="2026-10-30"), {})
FLAT = datetime.datetime(2026, 10, 30, 15, 0)


def bar(hhmm, o, h, l, c):
    hh, mm = map(int, hhmm.split(":"))
    return {"start": datetime.datetime(2026, 10, 30, hh, mm), "o": o, "h": h, "l": l, "c": c}


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


class Replay(unittest.TestCase):
    def test_stop_first_when_both_in_one_bar(self):
        bars = [bar("10:00", 1416, 1417, 1415, 1416), bar("10:01", 1416, 1441, 1407, 1420)]
        ex = paper.replay(bars, 0, 1416.7, 1408, 1440, FLAT, 0.05, 60)
        self.assertEqual((ex["reason"], ex["price"], ex["time"], ex["both"]), ("stop", 1408.0, "10:01", True))
        self.assertEqual(ex["fill"], round(1408 * 0.9995, 2))

    def test_a_gap_through_the_stop_fills_at_the_open(self):
        bars = [bar("10:00", 1416, 1417, 1415, 1416), bar("10:01", 1404, 1405, 1402, 1403)]
        ex = paper.replay(bars, 0, 1416.7, 1408, 1440, FLAT, 0.05, 60)
        self.assertEqual((ex["price"], ex["detail"]), (1404.0, "stop (gap through it)"))

    def test_target_fills_at_the_target_never_better(self):
        bars = [bar("10:00", 1416, 1417, 1415, 1416), bar("10:01", 1445, 1450, 1444, 1449)]
        ex = paper.replay(bars, 0, 1416.7, 1408, 1440, FLAT, 0.05, 60)
        self.assertEqual((ex["reason"], ex["price"]), ("target", 1440.0))

    def test_the_entry_bar_itself_can_stop_out(self):
        bars = [bar("10:00", 1416, 1417, 1407, 1410)]
        ex = paper.replay(bars, 0, 1416.7, 1408, 1440, FLAT, 0.05, 60)
        self.assertEqual((ex["reason"], ex["price"]), ("stop", 1408.0))

    def test_flat_by_exits_at_the_close_of_the_last_bar_before_it(self):
        bars = [bar("14:58", 1416, 1417, 1415, 1416), bar("14:59", 1416, 1418, 1415, 1417.5),
                bar("15:00", 1417, 1450, 1417, 1449)]
        ex = paper.replay(bars, 0, 1416.7, 1408, 1440, FLAT, 0.05, 60)
        self.assertEqual((ex["reason"], ex["price"], ex["time"]), ("flat-by", 1417.5, "15:00"))

    def test_still_open_before_the_cutoff(self):
        bars = [bar("10:00", 1416, 1417, 1415, 1416), bar("10:01", 1416, 1417, 1409, 1410)]
        self.assertIsNone(paper.replay(bars, 0, 1416.7, 1408, 1440, FLAT, 0.05, 60,
                                       cutoff=datetime.datetime(2026, 10, 30, 10, 5)))
        bars.append(bar("10:02", 1410, 1411, 1400, 1401))
        self.assertIsNone(paper.replay(bars, 0, 1416.7, 1408, 1440, FLAT, 0.05, 60,
                                       cutoff=datetime.datetime(2026, 10, 30, 10, 2, 30)))

    def test_outcome_has_full_charges(self):
        o = paper.outcome(45, 1416.71, 1407.3, 490.88, chg.DEFAULT_RATES)
        c = chg.charges(45 * 1416.71, 45 * 1407.3)
        self.assertEqual(o["charges"], c["total"])
        self.assertEqual(o["gross_pnl"], round(45 * (1407.3 - 1416.71), 2))
        self.assertEqual(o["net_pnl"], round(o["gross_pnl"] - c["total"], 2))
        self.assertEqual(o["r_multiple"], round(o["net_pnl"] / 490.88, 2))


class Ledger(unittest.TestCase):
    def appr(self, at="2026-10-30T09:51:20+05:30"):
        return {"date": "2026-10-30", "at": at, "status": "approved", "reply": "yes 45 SYNTHA", "mode": "paper",
                "fingerprint": paper.fingerprint(ORDER)}

    def test_ids_fills_and_balance(self):
        led = paper.new_ledger({})
        t = paper.open_trade(led, ORDER, self.appr(), {})
        self.assertEqual((t["trade_id"], t["status"], t["approval"]), ("PT-20261030-01", "open", "yes 45 SYNTHA"))
        with self.assertRaises(paper.PaperError):
            paper.open_trade(led, ORDER, self.appr(), {})
        bars, syn = paper.get_bars("SYNTHA", DAY, "1m", F1)
        self.assertTrue(syn)
        k = paper.fill_entry(t, bars, 0.05, 60)
        self.assertEqual((bars[k]["start"].strftime("%H:%M"), t["entry_time"]), ("09:52", "09:52"))
        self.assertEqual(t["entry_fill"], round(bars[k]["o"] * 1.0005, 2))

    def test_reconcile_closes_and_books_the_balance(self):
        led = paper.new_ledger({})
        paper.open_trade(led, ORDER, self.appr(), {})
        bars, _ = paper.get_bars("SYNTHA", DAY, "1m", F1)
        rep = paper.reconcile(led, {}, "2026-10-30", lambda s: bars, ist("2026-10-30 15:45") + paper.IST)
        t = led["trades"][0]
        self.assertEqual((rep[0][1], t["status"], t["exit_reason"]), ("closed", "closed", "stop"))
        self.assertEqual(led["balance"], round(100000 + t["net_pnl"], 2))
        self.assertEqual(t["balance_after"], led["balance"])
        self.assertLess(t["net_pnl"], -500)                       # the fill was worse than the plan
        self.assertIn("lost", t["notes"])
        self.assertIn("above the planned entry", t["notes"])

    def test_risk_reads_the_ledger(self):
        led = paper.new_ledger({})
        paper.open_trade(led, ORDER, self.appr(), {})
        res = check.check([ORDER], {}, led, None, None, None, ist("2026-10-30 10:00"))
        self.assertIn("open: 1 position already open", " ".join(res["results"][0]["reasons"]))
        bars, _ = paper.get_bars("SYNTHA", DAY, "1m", F1)
        paper.reconcile(led, {}, "2026-10-30", lambda s: bars, None)
        res = check.check([dict(ORDER, symbol="SYNTHD")], {}, led, None, None, None, ist("2026-10-30 11:00"))
        acct = res["account"]
        self.assertEqual((acct["entries_today"], acct["realised_today"]), (1, led["trades"][0]["net_pnl"]))

    def test_skipped_signal_is_replayed(self):
        led = paper.new_ledger({})
        s = paper.skip_signal(led, ORDER, "you said no", datetime.datetime(2026, 10, 30, 9, 51, 20), {})
        self.assertEqual((s["signal_id"], s["qty"], s["hypothetical_net_pnl"]), ("SK-20261030-01", 45, None))
        bars, _ = paper.get_bars("SYNTHA", DAY, "1m", F1)
        paper.reconcile(led, {}, "2026-10-30", lambda sym: bars, None)
        self.assertEqual(s["hypothetical_exit_reason"], "stop")
        self.assertLess(s["hypothetical_net_pnl"], 0)
        self.assertEqual(led["balance"], 100000)                 # a skipped signal never touches the balance


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = lambda *x: os.path.join(self.d, *x)
        with open(self.p("orders.json"), "w") as fh:
            json.dump({"orders": [ORDER]}, fh)
        with open(self.p("config.json"), "w") as fh:
            json.dump({"mode": "paper"}, fh)

    def tearDown(self):
        shutil.rmtree(self.d)

    def common(self, now):
        return ["--config", self.p("config.json"), "--ledger", self.p("state", "paper.json"), "--now", now]

    def risk(self, now="2026-10-30 09:51"):
        run(check, ["--orders", self.p("orders.json"), "--config", self.p("config.json"), "--ledger",
                    self.p("state", "paper.json"), "--prices", self.p("x"), "--kite-state", self.p("x"),
                    "--out", self.p("state", "risk.json"), "--now", now])

    def tk(self, *argv):
        return run(ticket, list(argv) + ["--orders", self.p("orders.json"), "--config", self.p("config.json"),
                                         "--risk", self.p("state", "risk.json"), "--state",
                                         self.p("state", "broker.json")])

    def pp(self, cmd, now, *extra):
        argv = [cmd] + self.common(now) + list(extra)
        if cmd in ("open", "skip"):
            argv += ["--orders", self.p("orders.json"), "--state", self.p("state", "broker.json"),
                     "--risk", self.p("state", "risk.json")]
        if cmd == "open":
            argv += ["--timemachine", self.p("x")]
        return run(paper, argv)

    def test_no_yes_no_trade(self):
        self.risk()
        code, _, err = self.pp("open", "2026-10-30 09:52", "--index", "1")
        self.assertEqual(code, 1)
        self.assertIn("No yes, no trade", err)

    def test_the_whole_day(self):
        self.risk()
        self.assertEqual(self.tk("approve", "--index", "1", "--reply", "yes", "--now", "2026-10-30 09:51:20")[0], 0)
        code, out, _ = self.pp("open", "2026-10-30 09:51:30", "--index", "1")
        self.assertEqual(code, 0)
        self.assertIn("Booked paper trade PT-20261030-01", out)
        self.assertIn("No broker was contacted", out)
        code, out, _ = self.pp("reconcile", "2026-10-30 09:58", "--offline", F1)
        self.assertIn("still open", out)
        code, out, _ = self.pp("reconcile", "2026-10-30 15:45", "--offline", F1)
        self.assertIn("SYNTHETIC test data", out)
        self.assertIn("(stop)", out)
        self.assertIn("note: lost", out)
        code, out, _ = self.pp("status", "2026-10-30 15:45")
        self.assertIn("1 trade, 0 skipped signals", out)
        with open(self.p("state", "paper.json")) as fh:
            led = json.load(fh)
        t = led["trades"][0]
        for k in ("trade_id", "date", "symbol", "setup", "side", "qty", "planned_entry", "entry_fill", "stop",
                  "target", "exit_price", "exit_time", "exit_reason", "gross_pnl", "charges", "net_pnl",
                  "r_multiple", "risk_at_stop", "tm_odds", "risk_verdict", "news_tags", "approval", "mode",
                  "status", "balance_after", "notes"):
            self.assertIn(k, t)
        self.assertEqual((t["mode"], t["risk_verdict"], t["approval"]), ("paper", "PASS", "yes"))

    def test_a_declined_and_a_blocked_signal(self):
        self.risk()
        self.tk("decline", "--index", "1", "--reply", "no, results today", "--now", "2026-10-30 09:51:20")
        code, out, _ = self.pp("skip", "2026-10-30 09:51:30", "--index", "1")
        self.assertEqual(code, 0)
        self.assertIn('you said no: "no, results today"', out)
        code, _, err = self.pp("skip", "2026-10-30 09:52", "--index", "1")
        self.assertIn("already logged as a skipped signal", err)
        with open(self.p("orders.json"), "w") as fh:
            json.dump({"orders": [dict(ORDER, qty=44)]}, fh)
        self.risk("2026-10-30 09:20")
        code, out, _ = self.pp("skip", "2026-10-30 09:21", "--index", "1")
        self.assertIn("trader-risk blocked it: window", out)
        self.assertIn("SK-20261030-02", out)
        code, out, _ = self.pp("reconcile", "2026-10-30 15:45", "--offline", F1)
        self.assertIn("Skipped SK-20261030-01 SYNTHA", out)

    def test_a_refused_plan_is_a_skipped_signal_too(self):
        with open(self.p("plan.json"), "w") as fh:
            json.dump(dict(PLAN, target=1432.0), fh)
        code, _, err = self.pp("skip", "2026-10-30 09:51", "--from-plan", "--sizing", self.p("nope.json"))
        self.assertIn("run trader-budget size.py first", err)
        code, _, _ = run(size, ["--plan", self.p("plan.json"), "--config", self.p("config.json"),
                                "--now", "2026-10-30 09:51", "--paper", self.p("x"), "--sizing", self.p("sizing.json")])
        self.assertEqual(code, 1)                                  # refused, but the sizing is saved
        code, out, _ = self.pp("skip", "2026-10-30 09:51", "--from-plan", "--sizing", self.p("sizing.json"))
        self.assertEqual(code, 0)
        self.assertIn("budget refused: reward:risk after charges", out)

    def test_a_yes_to_the_exit_closes_at_the_next_bar(self):
        self.risk()
        self.tk("approve", "--index", "1", "--reply", "yes", "--now", "2026-10-30 09:51:20")
        self.pp("open", "2026-10-30 09:51:30", "--index", "1")
        exit_order = {"symbol": "SYNTHA", "exchange": "NSE", "side": "sell", "qty": 45, "product": "MIS",
                      "entry_type": "market"}
        with open(self.p("orders.json"), "w") as fh:
            json.dump({"orders": [exit_order]}, fh)
        self.risk("2026-10-30 09:56")
        self.assertEqual(self.tk("approve", "--index", "1", "--reply", "yes", "--now", "2026-10-30 09:56:10")[0], 0)
        code, out, _ = self.pp("open", "2026-10-30 09:56:15", "--index", "1")
        self.assertIn("Recorded your yes to close PT-20261030-01", out)
        code, out, _ = self.pp("reconcile", "2026-10-30 15:45", "--offline", F1)
        with open(self.p("state", "paper.json")) as fh:
            t = json.load(fh)["trades"][0]
        self.assertEqual((t["exit_reason"], t["exit_time"]), ("manual", "09:57"))
        self.assertIn("closed by your yes", t["notes"])

    def test_paper_broker_refuses_live_mode(self):
        with open(self.p("config.json"), "w") as fh:
            json.dump({"mode": "live"}, fh)
        code, _, err = self.pp("status", "2026-10-30 10:00")
        self.assertEqual(code, 1)
        self.assertIn("only runs in paper mode", err)


if __name__ == "__main__":
    unittest.main()
