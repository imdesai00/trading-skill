import datetime
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import NSE, ROOT, load, write

control = load("trader-control", "control")
db = load("trader-db", "db")
paper = load("trader-broker", "paper")
ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")
size = load("trader-budget", "size")
F1 = os.path.join(NSE, "1m-1d")
PLAN = {"symbol": "SYNTHA", "side": "buy", "date": "2026-10-30", "setup": "orb", "entry": 1416.0,
        "entry_time": "09:50", "stop": 1408.0, "target": 1440.0, "tick": 0.1, "flat_by": "15:00"}
ORDER = size.order_from(size.size_trade(PLAN, {}, today="2026-10-30"), {})


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.t = os.path.join(self.d, "trader")
        self.cfgp = os.path.join(self.t, "config.json")
        code, _, _ = run(size, ["init", "--config", self.cfgp])
        self.assertEqual(code, 0)
        self.dbp = os.path.join(self.t, "trader.db")

    def tearDown(self):
        shutil.rmtree(self.d)

    def ctl(self, *argv):
        return run(control, list(argv) + ["--config", self.cfgp, "--now", "2026-10-30 08:45"])

    def cfg(self):
        with open(self.cfgp, encoding="utf-8") as fh:
            return json.load(fh)

    def rows(self, sql):
        conn = sqlite3.connect(self.dbp)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()


class Capital(Base):
    def test_it_shows_and_changes_nothing_without_a_yes(self):
        code, out, _ = self.ctl("capital", "--set", "150000", "--reason", "added funds")
        self.assertEqual(code, 2)
        self.assertIn("₹1,00,000 -> ₹1,50,000", out)
        self.assertIn("₹500.00 (0.5%)", out)
        self.assertIn("₹750.00 (0.5%)", out)
        self.assertIn("Nothing was changed", out)
        self.assertEqual(self.cfg()["capital_inr"], 100000)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM capital_changes")[0][0], 0)

    def test_a_word_that_is_not_yes_changes_nothing(self):
        for reply in ("ok", "sure", "go ahead", "yes to all"):
            self.assertEqual(self.ctl("capital", "--set", "150000", "--reason", "r", "--reply", reply)[0], 2, reply)
        self.assertEqual(self.cfg()["capital_inr"], 100000)

    def test_with_a_yes_all_limits_follow_and_everything_is_recorded(self):
        code, out, _ = self.ctl("capital", "--set", "150000", "--reason", "added funds", "--reply", "yes")
        self.assertEqual(code, 0, out)
        cfg = self.cfg()
        self.assertEqual((cfg["capital_inr"], cfg["max_loss_per_trade_inr"], cfg["max_daily_loss_inr"],
                          cfg["max_position_value_inr"]), (150000, 750.0, 2250.0, 112500.0))
        self.assertEqual(self.rows("SELECT change_id, old_paise, new_paise, reason FROM capital_changes"),
                         [("CAP-0001", 10000000, 15000000, "added funds")])
        self.assertEqual(db.get_account(self.dbp, "paper"), {"capital": 150000.0, "balance": 150000.0, "baseline": 150000.0})
        self.assertEqual(self.rows("SELECT source FROM config_history ORDER BY seq")[-1][0], "tool")
        self.assertTrue(db.check(self.dbp)[0])

    def test_a_deposit_is_not_profit_and_a_withdrawal_keeps_the_pnl(self):
        led = {"mode": "paper", "capital": 100000.0, "balance": 100500.0, "trades": [], "skipped": []}
        write(os.path.join(self.t, "state", "paper.json"), led)
        db.set_account(self.dbp, "paper", balance=100500.0)
        self.ctl("capital", "--set", "90000", "--reason", "took money out", "--reply", "yes")
        acct = db.get_account(self.dbp, "paper")
        self.assertEqual((acct["capital"], acct["balance"], acct["baseline"]), (90000.0, 90500.0, 90000.0))

    def test_a_reason_is_required(self):
        code, _, err = self.ctl("capital", "--set", "150000", "--reply", "yes")
        self.assertEqual(code, 1)
        self.assertIn("--reason", err)

    def test_a_warning_during_the_paper_test(self):
        led = {"mode": "paper", "capital": 100000.0, "balance": 100000.0, "skipped": [],
               "trades": [{"trade_id": "PT-1", "date": "2026-10-29", "symbol": "SYNTHA", "qty": 5, "status": "open"}]}
        db.save_ledger(self.dbp, "paper", led)
        code, out, _ = self.ctl("capital", "--set", "150000", "--reason", "r")
        self.assertIn("DURING the paper-test period (1 paper trades so far)", out)

    def test_live_capital_cannot_exceed_what_kite_shows(self):
        cfg = self.cfg()
        cfg.update(mode="live", kite_enabled=True)
        write(self.cfgp, cfg)
        code, _, err = self.ctl("capital", "--set", "150000", "--reason", "r", "--reply", "yes")
        self.assertIn("--kite-available", err)
        code, _, err = self.ctl("capital", "--set", "150000", "--reason", "r", "--reply", "yes", "--kite-available", "120000")
        self.assertEqual(code, 1)
        self.assertIn("more than the ₹1,20,000.00 Kite shows", err)
        self.assertEqual(self.cfg()["capital_inr"], 100000)
        code, _, _ = self.ctl("capital", "--set", "110000", "--reason", "r", "--reply", "yes", "--kite-available", "120000")
        self.assertEqual(code, 0)


class OtherLimits(Base):
    def test_set_a_percent_in_two_steps_and_log_it(self):
        code, out, _ = self.ctl("set", "max_loss_per_trade_pct", "0.4")
        self.assertEqual(code, 2)
        self.assertIn("max_loss_per_trade_inr", out)
        self.assertEqual(self.cfg()["limits_pct"]["max_loss_per_trade_pct"], 0.5)
        code, out, _ = self.ctl("set", "max_loss_per_trade_pct", "0.4", "--reply", "yes")
        self.assertEqual(code, 0)
        cfg = self.cfg()
        self.assertEqual((cfg["limits_pct"]["max_loss_per_trade_pct"], cfg["max_loss_per_trade_inr"]), (0.4, 400.0))
        self.assertIn("max_loss_per_trade_pct: 0.5 -> 0.4", self.rows("SELECT note FROM config_history ORDER BY seq")[-1][0])

    def test_raising_risk_carries_a_warning(self):
        _, out, _ = self.ctl("set", "max_loss_per_trade_pct", "1.0")
        self.assertIn("RAISES your risk", out)

    def test_plain_keys_and_refusals(self):
        self.assertEqual(self.ctl("set", "max_trades_per_day", "2", "--reply", "yes")[0], 0)
        self.assertEqual(self.cfg()["max_trades_per_day"], 2)
        for key in ("mode", "kite_enabled"):
            code, _, err = self.ctl("set", key, '"live"', "--reply", "yes")
            self.assertEqual(code, 1)
            self.assertIn("yours alone", err)
        code, _, err = self.ctl("set", "kite_api_secret", "x", "--reply", "yes")
        self.assertIn("no secrets", err)
        code, _, err = self.ctl("set", "charges", "{}", "--reply", "yes")
        self.assertIn("not a setting this tool changes", err)
        self.assertEqual(self.cfg()["mode"], "paper")

    def test_hand_edits_are_detected_once_and_logged(self):
        self.assertIn("No hand edits", self.ctl("edits")[1])
        cfg = self.cfg()
        cfg["max_trades_per_day"] = 5
        write(self.cfgp, cfg)
        code, out, _ = self.ctl("edits")
        self.assertIn("max_trades_per_day", out)
        self.assertEqual(self.rows("SELECT source FROM config_history ORDER BY seq")[-1][0], "manual-edit")
        self.assertIn("No hand edits", self.ctl("edits")[1])

    def test_a_hand_edited_rupee_limit_is_called_out(self):
        cfg = self.cfg()
        cfg["max_loss_per_trade_inr"] = 900
        write(self.cfgp, cfg)
        _, out, _ = self.ctl("edits")
        self.assertIn("using 500.0", out)


class PauseAndKill(Base):
    def test_pause_resume(self):
        code, out, _ = self.ctl("pause", "--until", "2026-11-06", "--reason", "review week")
        self.assertIn("PAUSED until 2026-11-06", out)
        self.assertTrue(db.control_blocks(self.cfgp, "paper", "2026-10-30"))
        self.assertTrue(db.control_blocks(self.cfgp, "live", "2026-10-30"))              # both modes
        self.assertEqual(db.control_blocks(self.cfgp, "paper", "2026-11-07"), [])         # it ended by itself
        self.ctl("resume")
        self.assertEqual(db.control_blocks(self.cfgp, "paper", "2026-10-30"), [])
        self.assertEqual(self.ctl("pause", "--until", "soon")[0], 1)

    def test_kill_switch_file_and_unkill_needs_a_yes(self):
        code, out, _ = self.ctl("kill")
        self.assertIn("KILL SWITCH ON", out)
        self.assertTrue(os.path.exists(os.path.join(self.t, "STOP")))
        self.assertIn("KILL SWITCH", db.control_blocks(self.cfgp, "paper", "2026-10-30")[0])
        self.assertEqual(self.ctl("unkill", "--reply", "ok")[0], 2)
        self.assertTrue(os.path.exists(os.path.join(self.t, "STOP")))
        self.assertEqual(self.ctl("unkill", "--reply", "yes")[0], 0)
        self.assertFalse(os.path.exists(os.path.join(self.t, "STOP")))

    def test_kill_in_live_mode_lists_positions_and_offers_to_flatten(self):
        cfg = self.cfg()
        cfg.update(mode="live", kite_enabled=True)
        write(self.cfgp, cfg)
        write(os.path.join(self.t, "state", "live.json"), {"mode": "live", "capital": 100000.0, "balance": 100000.0, "skipped": [],
              "trades": [{"trade_id": "LT-1", "date": "2026-10-30", "symbol": "SYNTHA", "qty": 45, "status": "open"}]})
        _, out, _ = self.ctl("kill")
        self.assertIn("open LIVE position: LT-1 45 SYNTHA", out)
        self.assertIn("ticket.py flatten --symbol SYNTHA --qty 45", out)
        self.assertIn("your own yes", out)

    def test_status(self):
        _, out, _ = self.ctl("status")
        self.assertIn("Mode: PAPER", out)
        self.assertIn("max loss per trade", out)
        self.ctl("kill")
        self.assertIn("BLOCKED: KILL SWITCH", self.ctl("status")[1])


class Morning(Base):
    def test_the_whole_picture(self):
        write(os.path.join(self.t, "state", "market.json"), {"date": "2026-10-30", "vix": 21.0, "nifty_gap_pct": 0.2})
        write(os.path.join(self.t, "state", "results_today.json"), {"date": "2026-10-30", "symbols": {"SYNTHA": "Q2"}})
        write(os.path.join(self.t, "watchlist.json"), {"mode": "daily", "as_of": "2026-10-29 close", "hits": [{"symbol": "SYNTHA"}]})
        db.save_outbox(self.dbp, [{"key": "paper_trades:PT-1", "db": "paper_trades", "id": "PT-1", "hash": "h", "props": {},
                                   "queued_at": "t"}])
        self.ctl("pause", "--reason", "testing")
        code, out, _ = self.ctl("morning")
        self.assertEqual(code, 0)
        for want in ("MORNING CHECK, Fri 2026-10-30", "open day (09:15 to 15:30)", "Mode: PAPER", "PAPER account: balance ₹1,00,000.00",
                     "Limits used: today ₹0.00 of ₹1,500,", "BLOCKED: trading is PAUSED", "Database: intact",
                     "Last backup: none yet", "Notion sync: 1 row waiting", "index expiry day" if False else "Events today",
                     "RESULTS TODAY (half size): SYNTHA", "India VIX 21.0", "Scan: daily, 1 hit", "PROTECTION DATA MISSING (paper warns only)",
                     "TOTP"):
            self.assertIn(want, out)

    def test_a_holiday_and_no_database(self):
        os.remove(self.dbp)
        code, out, _ = run(control, ["morning", "--config", self.cfgp, "--now", "2026-10-20 08:45"])
        self.assertIn("CLOSED today: NSE holiday: Dussehra", out)
        self.assertIn("No database yet", out)


class WholeDayInTheDatabase(unittest.TestCase):
    """approve -> paper open -> reconcile with trader.db present: the database is the record."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = lambda *x: os.path.join(self.d, *x)
        write(self.p("orders.json"), {"orders": [ORDER]})
        write(self.p("config.json"), {"mode": "paper"})
        db.init(self.p("trader.db"))
        self.led = self.p("state", "paper.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def risk(self, now="2026-10-30 09:51"):
        return run(check, ["--orders", self.p("orders.json"), "--config", self.p("config.json"), "--ledger", self.led,
                           "--prices", self.p("x"), "--kite-state", self.p("x"), "--out", self.p("state", "risk.json"),
                           "--now", now])

    def tk(self, *argv):
        return run(ticket, list(argv) + ["--orders", self.p("orders.json"), "--config", self.p("config.json"),
                                         "--risk", self.p("state", "risk.json"), "--state", self.p("state", "broker.json")])

    def pp(self, cmd, now, *extra):
        argv = [cmd, "--config", self.p("config.json"), "--ledger", self.led, "--now", now] + list(extra)
        if cmd == "open":
            argv += ["--orders", self.p("orders.json"), "--state", self.p("state", "broker.json"),
                     "--risk", self.p("state", "risk.json"), "--timemachine", self.p("x")]
        return run(paper, argv)

    def rows(self, sql):
        conn = sqlite3.connect(self.p("trader.db"))
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def test_an_entry_needs_a_why(self):
        self.risk()
        code, _, err = self.tk("approve", "--index", "1", "--reply", "yes", "--now", "2026-10-30 09:51:20")
        self.assertEqual(code, 1)
        self.assertIn("Why are you taking this trade?", err)
        code, _, err = self.tk("approve", "--index", "1", "--reply", "yes", "--why", "  ", "--now", "2026-10-30 09:51:20")
        self.assertEqual(code, 1)

    def test_the_whole_day_lands_in_the_tables(self):
        self.risk()
        self.assertEqual(self.rows("SELECT verdict FROM risk_verdicts"), [("PASS",)])
        self.assertEqual(self.tk("approve", "--index", "1", "--reply", "yes", "--why", "clean ORB, news clear",
                                 "--now", "2026-10-30 09:51:20")[0], 0)
        self.assertEqual(self.rows("SELECT decision, reply, why, order_id FROM approvals"),
                         [("yes", "yes", "clean ORB, news clear", "PAPER-20261030-01")])
        code, out, _ = self.pp("open", "2026-10-30 09:51:30", "--index", "1")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.rows("SELECT trade_id, status, order_id FROM trades"), [("PT-20261030-01", "open", "PAPER-20261030-01")])
        code, out, _ = self.pp("reconcile", "2026-10-30 15:45", "--offline", F1)
        self.assertEqual(code, 0, out)
        self.assertIn("Database backup:", out)
        self.assertEqual(self.rows("SELECT version, status FROM trades ORDER BY seq"), [(1, "open"), (2, "closed")])
        self.assertEqual([r[0] for r in self.rows("SELECT leg FROM fills ORDER BY seq")], ["entry", "exit"])
        self.assertEqual([r[0] for r in self.rows("SELECT status FROM order_events ORDER BY seq")], ["approved", "filled"])
        self.assertEqual(len(os.listdir(self.p("backups"))), 1)
        self.assertTrue(db.check(self.p("trader.db"))[0])
        acct = db.get_account(self.p("trader.db"), "paper")
        with open(self.led, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["balance"], acct["balance"])
        code, out, _ = self.pp("feel", "2026-10-30 15:50", "--trade", "PT-20261030-01", "--text", "calm; the stop did its job")
        self.assertEqual(code, 0)
        self.assertEqual([(n["kind"], n["text"]) for n in db.notes_for(self.p("trader.db"))], [("feel", "calm; the stop did its job")])
        self.assertEqual(self.pp("feel", "2026-10-30 15:50", "--trade", "NOPE", "--text", "x")[0], 1)

    def test_the_kill_switch_stops_a_yes_and_a_paper_booking(self):
        self.risk()
        open(self.p("STOP"), "w").close()
        code, _, err = self.tk("approve", "--index", "1", "--reply", "yes", "--why", "x", "--now", "2026-10-30 09:51:20")
        self.assertEqual(code, 1)
        self.assertIn("KILL SWITCH", err)
        os.remove(self.p("STOP"))
        self.assertEqual(self.tk("approve", "--index", "1", "--reply", "yes", "--why", "x", "--now", "2026-10-30 09:51:20")[0], 0)
        open(self.p("STOP"), "w").close()                        # set AFTER the yes, before the booking
        code, _, err = self.pp("open", "2026-10-30 09:51:30", "--index", "1")
        self.assertEqual(code, 1)
        self.assertIn("KILL SWITCH", err)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM trades")[0][0], 0)

    def test_a_pause_stops_the_check_too(self):
        db.add_pause(self.p("trader.db"), "all", "pause", None, "testing")
        code, out, _ = self.risk()
        self.assertEqual(code, 1)
        self.assertIn("trading is PAUSED", out)

    def test_the_json_copy_cannot_be_used_to_cheat(self):
        self.risk()
        self.tk("approve", "--index", "1", "--reply", "yes", "--why", "x", "--now", "2026-10-30 09:51:20")
        self.pp("open", "2026-10-30 09:51:30", "--index", "1")
        self.pp("reconcile", "2026-10-30 15:45", "--offline", F1)
        with open(self.led, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["trades"][0]["net_pnl"] = 99999.0
        doc["balance"] = 200000.0
        write(self.led, doc)
        code, out, err = self.pp("status", "2026-10-30 16:00")
        self.assertIn("changed outside the tools", err)
        self.assertNotIn("99999", out)
        self.assertNotIn("2,00,000", out)


class MoreWiring(WholeDayInTheDatabase):
    def test_a_hand_edit_is_logged_when_trader_risk_runs(self):
        write(self.p("config.json"), {"mode": "paper"})
        self.risk()
        write(self.p("config.json"), {"mode": "paper", "max_trades_per_day": 2})
        self.risk()
        rows = self.rows("SELECT source FROM config_history ORDER BY seq")
        self.assertEqual([r[0] for r in rows], ["initial", "manual-edit"])
        self.risk()
        self.assertEqual(len(self.rows("SELECT source FROM config_history")), 2)           # unchanged: no new row

    def test_golive_reads_the_database(self):
        golive = load("trader-golive", "golive")
        led = {"mode": "paper", "capital": 100000.0, "balance": 100300.0, "skipped": [], "trades": [
            {"trade_id": "PT-20261030-01", "date": "2026-10-30", "exit_date": "2026-10-30", "symbol": "SYNTHA", "qty": 5,
             "status": "closed", "net_pnl": 300.0, "gross_pnl": 360.0, "charges": 60.0, "r_multiple": 0.6}]}
        db.save_ledger(self.p("trader.db"), "paper", led)
        write(self.p("state", "paper.json"), {"trades": [], "skipped": []})              # a stale, empty copy
        o = io.StringIO()
        with redirect_stdout(o):
            code = golive.main(["check", "--config", self.p("config.json")])
        self.assertEqual(code, 0)
        self.assertIn("Trades: 1 closed", o.getvalue())
        e = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(e):
            code = golive.main(["check", "--config", self.p("config.json"), "--mode", "live"])
        self.assertEqual(code, 1)
        self.assertIn("no live trades yet", e.getvalue())

    def test_rings_are_logged(self):
        alerts = load("trader-alerts", "alerts")
        alerts.log_rings(self.p("state", "alerts.json"), [{"id": "r1", "symbol": "SYNTHA", "message": "Stop hit"}],
                         datetime.datetime(2026, 10, 30, 5, 0))
        self.assertEqual(self.rows("SELECT rule, symbol, detail FROM alerts"), [("r1", "SYNTHA", "Stop hit")])

    def test_scans_are_logged(self):
        scan = load("trader-scanner", "scan")
        a = type("A", (), {"out": self.p("watchlist.json")})()
        scan.log_scan(a, {"mode": "daily", "universe": 5, "hits": [{"symbol": "X"}]}, datetime.datetime(2026, 10, 30, 3, 0))
        self.assertEqual(self.rows("SELECT kind, universe, hits FROM scans"), [("daily", "5", 1)])


class Source(unittest.TestCase):
    def test_nothing_in_the_new_modules_reaches_the_network_or_reads_secrets(self):
        for folder, name in (("trader-control", "control.py"), ("trader-risk", "guards.py")):
            with open(os.path.join(ROOT, folder, name), encoding="utf-8") as fh:
                text = fh.read()
            for bad in ("urllib", "socket", "requests", "os.environ"):
                self.assertNotIn(bad, text, (folder, bad))


if __name__ == "__main__":
    unittest.main()
