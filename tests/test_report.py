import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import ROOT, load, ist, write

report = load("trader-report", "report")
CFG = dict(report.LIMIT_DEFAULTS)


def trade(n, day, net, sym="SYNTHA", reason="stop", setup="orb", odds=0.18, **kw):
    t = {"trade_id": "PT-%s-%02d" % (day.replace("-", ""), n), "date": day, "symbol": sym, "setup": setup,
         "side": "buy", "qty": 45, "planned_entry": 1416.0, "entry_fill": 1416.7, "entry_time": "09:52",
         "stop": 1408.0, "target": 1440.0, "flat_by": "15:00", "exit_price": 1407.3, "exit_fill": 1407.3,
         "exit_time": "10:09", "exit_reason": reason, "exit_date": day, "gross_pnl": round(net + 67.37, 2),
         "charges": 67.37, "net_pnl": net, "r_multiple": round(net / 490.88, 2), "max_loss": 490.88,
         "risk_at_stop": 490.88, "tm_odds": {"target": odds, "stop": 0.695, "neither": 0.128, "verdict": "x"},
         "risk_verdict": "PASS", "news_tags": ["results"], "approval": "yes 45 SYNTHA", "approved_at": "x",
         "mode": "paper", "status": "closed", "balance_after": None, "notes": "n", "fingerprint": "ab"}
    t.update(kw)
    return t


def ledger(trades, skipped=()):
    bal = 100000.0
    for t in sorted(trades, key=lambda t: t["trade_id"]):
        if t["status"] == "closed":
            bal = round(bal + t["net_pnl"], 2)
            t["balance_after"] = bal
    return {"mode": "paper", "capital": 100000.0, "balance": bal, "trades": list(trades), "skipped": list(skipped)}


def skipped(n, day, net=None):
    return {"signal_id": "SK-%s-%02d" % (day.replace("-", ""), n), "date": day, "symbol": "SYNTHD", "setup": "orb",
            "side": "buy", "qty": 20, "planned_entry": 3200.0, "stop": 3190.0, "target": 3230.0,
            "risk_at_stop": 300.0, "why_skipped": "you said no", "skipped_at": day + "T09:51:20+05:30",
            "hypothetical_net_pnl": net, "hypothetical_exit_reason": "target" if net else None,
            "hypothetical_r": 1.5 if net else None, "mode": "paper", "notes": ""}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = report.main(argv)
    return code, out.getvalue(), err.getvalue()

class Numbers(unittest.TestCase):
    def setUp(self):
        self.led = ledger([trade(1, "2026-10-27", -400.0), trade(1, "2026-10-28", 900.0, reason="target", odds=0.4),
                           trade(1, "2026-10-29", -1200.0, setup="level", odds=None)],
                          [skipped(1, "2026-10-28", 650.0), skipped(1, "2026-10-29", None)])

    def test_day_stats(self):
        st = report.day_stats(self.led, CFG, "2026-10-28")
        self.assertEqual((st["trades"], st["wins"], st["losses"], st["net"], st["charges"]), (1, 1, 0, 900.0, 67.37))
        self.assertEqual((st["balance"], st["drawdown"], st["win_rate"]), (100500.0, 0.0, 100.0))
        st = report.day_stats(self.led, CFG, "2026-10-29")
        self.assertEqual((st["balance"], st["drawdown"]), (99300.0, 700.0))
        self.assertEqual(st["limits"], [])

    def test_limits_hit_and_rules_broken(self):
        led = ledger([trade(1, "2026-10-26", -1600.0), trade(2, "2026-10-26", 0.0)])
        st = report.day_stats(led, CFG, "2026-10-26")
        self.assertTrue(any("daily loss limit ₹1,500 reached" in x for x in st["limits"]))
        self.assertTrue(any("over the ₹500 per-trade limit" in x for x in st["rules"]))
        led = ledger([trade(1, "2026-10-26", -1900.0), trade(1, "2026-10-27", -1900.0), trade(1, "2026-10-28", -300.0)])
        self.assertTrue(any("weekly loss limit" in x for x in report.day_stats(led, CFG, "2026-10-28")["limits"]))
        led = ledger([trade(i, "2026-10-26", -450.0) for i in range(1, 4)])
        self.assertTrue(any("3 trades today" in x for x in report.day_stats(led, CFG, "2026-10-26")["limits"]))
        led = ledger([trade(1, d, -1700.0) for d in ("2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29",
                                                      "2026-10-30")])
        self.assertTrue(any("drawdown pause" in x for x in report.day_stats(led, CFG, "2026-10-30")["limits"]))

    def test_a_no_trade_day(self):
        st = report.day_stats(self.led, CFG, "2026-10-30")
        self.assertEqual((st["trades"], st["win_rate"], st["net"]), (0, None, 0.0))
        self.assertEqual(report.daily_text(st)[0], "2026-10-30: no trades.")

    def test_weekly(self):
        w = report.week_stats(self.led, CFG, "2026-10-30")
        self.assertEqual((w["iso"], w["monday"], w["friday"]), ("2026-W44", "2026-10-26", "2026-10-30"))
        s = w["summary"]
        self.assertEqual((s["n"], s["wins"], s["net"], s["expectancy"]), (3, 1, -700.0, -233.33))
        self.assertEqual((s["best"]["net_pnl"], s["worst"]["net_pnl"]), (900.0, -1200.0))
        self.assertEqual(sorted(w["by_setup"]), ["level", "orb"])
        self.assertEqual(sorted(w["by_odds"]), ["no time-machine odds recorded", "target-first 20 to 35 %"
                                                if False else "target-first over 35 %",
                                                "target-first under 20 %"])
        self.assertEqual((w["skipped"], w["skipped_replayed"], w["skipped_net"]), (2, 1, 650.0))
        self.assertEqual((w["balance"], w["vs_start"]), (99300.0, -700.0))

    def test_the_weekly_text_covers_everything_you_asked_for(self):
        md = report.weekly_markdown(report.week_stats(self.led, CFG, "2026-10-30"))
        for want in ("Trades: 3", "Win rate 33 %", "Net expectancy per trade after charges", "Charges paid",
                     "Best trade", "Worst trade", "## By setup type", "## Results against the time machine's odds",
                     "## Signals you skipped", "would have netted ₹650.00", "Limits hit:", "Rules broken:",
                     "₹1,00,000 you started with", "cannot tell skill from luck", "Not financial advice"):
            self.assertIn(want, md)
        self.assertNotRegex(md.lower(), r"amazing|great|crush|moon|guarantee")

    def test_an_empty_week(self):
        md = report.weekly_markdown(report.week_stats(self.led, CFG, "2026-11-04"))
        self.assertIn("No trades closed this week", md)




class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = lambda *x: os.path.join(self.d, *x)
        db_ = load("trader-db", "db")
        self.db = db_
        write(self.p("config.json"), dict(CFG))
        db_.init(self.p("trader.db"))
        led = ledger([trade(1, "2026-10-30", -571.82, why="clean ORB", order_id=None)], [skipped(1, "2026-10-30", 400.0)])
        db_.save_ledger(self.p("trader.db"), "paper", led)
        db_.set_account(self.p("trader.db"), "paper", capital=100000, balance=led["balance"], baseline=100000)
        db_.add_note(self.p("trader.db"), "PT-20261030-01", "feel", "calm, the stop did its job", "paper")

    def tearDown(self):
        shutil.rmtree(self.d)

    def run_cmd(self, *argv):
        return run(list(argv) + ["--config", self.p("config.json"), "--now", "2026-10-30 15:55"])

    def test_daily_prints_saves_and_keeps_the_report(self):
        code, out, _ = self.run_cmd("daily")
        self.assertEqual(code, 0, out)
        for want in ("Daily report, PAPER mode, 2026-10-30", "1 trade (0 wins, 1 loss), net -₹571.82", "why I took it: clean ORB",
                     "how I felt: calm, the stop did its job", "(estimated)", "Skipped signals: 1", "would have netted ₹400.00",
                     "Limits hit: none"):
            self.assertIn(want, out)
        with open(self.p("reports", "daily-paper-2026-10-30.md"), encoding="utf-8") as fh:
            self.assertIn("Daily report, PAPER mode", fh.read())
        import sqlite3
        conn = sqlite3.connect(self.p("trader.db"))
        try:
            self.assertEqual(conn.execute("SELECT date, mode FROM daily_summary").fetchall(), [("2026-10-30", "paper")])
        finally:
            conn.close()
        self.run_cmd("daily")                                            # running it again replaces, never duplicates
        conn = sqlite3.connect(self.p("trader.db"))
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM daily_summary").fetchone()[0], 1)
        finally:
            conn.close()

    def test_weekly_is_per_mode(self):
        live = ledger([trade(1, "2026-10-30", 300.0, mode="live", reason="target")])
        live["mode"] = "live"
        live["trades"][0]["trade_id"] = "LT-20261030-01"
        self.db.save_ledger(self.p("trader.db"), "live", live, source="kite")
        code, out, _ = self.run_cmd("weekly", "--week-of", "2026-10-30")
        self.assertIn("PAPER only", out)
        self.assertIn("-₹571.82", out)
        self.assertNotIn("LT-20261030-01", out)
        code, out, _ = self.run_cmd("weekly", "--week-of", "2026-10-30", "--mode", "live")
        self.assertIn("LIVE only", out)
        self.assertIn("₹300.00", out)
        self.assertTrue(os.path.exists(self.p("reports", "weekly-live-2026-W44.md")))

    def test_no_record_is_a_plain_error_and_a_json_ledger_still_works(self):
        os.remove(self.p("trader.db"))
        code, _, err = self.run_cmd("daily")
        self.assertEqual(code, 1)
        self.assertIn("no record yet", err)
        write(self.p("paper.json"), ledger([trade(1, "2026-10-30", -100.0)]))
        code, out, _ = self.run_cmd("daily", "--ledger", self.p("paper.json"))
        self.assertEqual(code, 0, out)
        self.assertIn("1 trade", out)

    def test_nothing_in_the_report_tool_reaches_the_network_or_holds_a_secret(self):
        with open(os.path.join(ROOT, "trader-report", "report.py"), encoding="utf-8") as fh:
            text = fh.read()
        for bad in ("urllib", "socket", "requests", "os.environ", "token", "notion", "Notion"):
            self.assertNotIn(bad, text, bad)


if __name__ == "__main__":
    unittest.main()
