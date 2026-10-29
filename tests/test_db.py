import csv
import datetime
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import ROOT, load, write

db = load("trader-db", "db")
NOW = datetime.datetime(2026, 10, 30, 10, 0, 0)


def trade(tid="PT-20261030-01", status="open", **kw):
    t = {"trade_id": tid, "date": "2026-10-30", "symbol": "SYNTHA", "setup": "orb", "side": "buy", "qty": 45,
         "planned_entry": 1416.0, "entry_fill": 1416.71, "entry_time": "09:51", "stop": 1408.0, "target": 1432.0,
         "exit_fill": None, "exit_time": None, "exit_reason": None, "exit_date": None, "gross_pnl": None,
         "charges": None, "net_pnl": None, "r_multiple": None, "risk_at_stop": 500.0, "mode": "paper",
         "status": status}
    t.update(kw)
    return t


def closed(tid="PT-20261030-01"):
    return trade(tid, "closed", exit_fill=1431.28, exit_time="11:20", exit_reason="target", exit_date="2026-10-30",
                 gross_pnl=655.65, charges=68.03, net_pnl=587.62, r_multiple=1.18)


def ledger(trades=(), skipped=(), balance=100000.0):
    return {"mode": "paper", "capital": 100000.0, "balance": balance, "trades": list(trades), "skipped": list(skipped)}


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "trader", "trader.db")
        db.init(self.path, NOW)

    def tearDown(self):
        shutil.rmtree(self.d)

    def q(self, sql, *args):
        conn = sqlite3.connect(self.path)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()


class Schema(Base):
    def test_every_table_in_the_spec_exists(self):
        names = {r[0] for r in self.q("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("account", "capital_changes", "positions", "orders", "approvals", "fills", "trades",
                  "skipped_signals", "risk_verdicts", "daily_summary", "weekly_reports", "alerts", "news_items",
                  "scans", "pauses", "config_history", "notion_outbox", "schema_version"):
            self.assertIn(t, names)

    def test_init_twice_is_safe(self):
        db.init(self.path, NOW)
        self.assertEqual(self.q("SELECT COUNT(*) FROM schema_version")[0][0], 1)

    def test_indexes_on_date_and_symbol(self):
        idx = {r[0] for r in self.q("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertTrue({"ix_trades_date", "ix_trades_symbol", "ix_orders_date", "ix_orders_symbol"} <= idx)

    def test_connecting_to_nothing_is_an_error(self):
        with self.assertRaises(db.DbError):
            db.connect(os.path.join(self.d, "none.db"))

    def test_money_is_exact_paise(self):
        self.assertEqual(db.paise(1416.71), 141671)
        self.assertEqual(db.paise("0.1"), 10)
        self.assertEqual(db.paise(-587.62), -58762)
        self.assertEqual(db.paise(1.005), 101)
        self.assertIsNone(db.paise(None))
        self.assertEqual(db.rupees(141671), 1416.71)


class AppendOnly(Base):
    def setUp(self):
        Base.setUp(self)
        db.save_ledger(self.path, "paper", ledger([trade()]), NOW)

    def test_update_and_delete_are_refused_on_every_append_only_table(self):
        conn = sqlite3.connect(self.path)
        for t in db.APPEND_ONLY:
            for sql in ("UPDATE %s SET recorded_at='x'" % t, "DELETE FROM %s" % t):
                try:
                    conn.execute(sql)
                except sqlite3.DatabaseError as e:
                    self.assertIn("append-only", str(e), t)
                else:
                    if conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]:
                        self.fail("%s allowed: %s" % (t, sql))
        conn.close()

    def test_a_changed_trade_is_a_new_linked_version(self):
        db.save_ledger(self.path, "paper", ledger([closed()], balance=100587.62), NOW)
        rows = self.q("SELECT version, supersedes_seq, status, net_paise FROM trades ORDER BY seq")
        self.assertEqual(rows, [(1, None, "open", None), (2, 1, "closed", 58762)])

    def test_an_unchanged_ledger_adds_nothing(self):
        self.assertEqual(db.save_ledger(self.path, "paper", ledger([trade()]), NOW), 0)
        self.assertEqual(self.q("SELECT COUNT(*) FROM trades")[0][0], 1)

    def test_fills_are_written_once_per_leg(self):
        db.save_ledger(self.path, "paper", ledger([closed()]), NOW)
        db.save_ledger(self.path, "paper", ledger([closed()]), NOW)
        self.assertEqual(self.q("SELECT leg, price_paise FROM fills ORDER BY seq"), [("entry", 141671), ("exit", 143128)])


class Chain(Base):
    def setUp(self):
        Base.setUp(self)
        db.save_ledger(self.path, "paper", ledger([trade()]), NOW)
        db.save_ledger(self.path, "paper", ledger([closed()]), NOW)

    def test_a_clean_database_passes(self):
        ok, lines = db.check(self.path)
        self.assertTrue(ok, lines)
        self.assertTrue(any("trades" in l and "hash chain intact" in l for l in lines))

    def test_an_edited_row_is_found_even_if_the_trigger_was_dropped(self):
        conn = sqlite3.connect(self.path)
        conn.execute("DROP TRIGGER trades_no_update")
        conn.execute("UPDATE trades SET net_paise = 999999 WHERE seq = 2")
        conn.commit()
        conn.close()
        ok, lines = db.check(self.path)
        self.assertFalse(ok)
        self.assertTrue(any("was edited" in l for l in lines))

    def test_a_removed_middle_row_breaks_the_chain(self):
        db.save_ledger(self.path, "paper", ledger([closed(), trade("PT-20261030-02")]), NOW)
        conn = sqlite3.connect(self.path)
        conn.execute("DROP TRIGGER trades_no_delete")
        conn.execute("DELETE FROM trades WHERE seq = 2")
        conn.commit()
        conn.close()
        ok, lines = db.check(self.path)
        self.assertFalse(ok)
        self.assertTrue(any("chain is broken" in l for l in lines))

    def test_missing_triggers_are_reported(self):
        conn = sqlite3.connect(self.path)
        conn.execute("DROP TRIGGER orders_no_update")
        conn.commit()
        conn.close()
        ok, lines = db.check(self.path)
        self.assertFalse(ok)
        self.assertTrue(any("triggers missing" in l for l in lines))

    def test_foreign_keys_are_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with db.Tx(self.path) as conn:
                db.append(conn, "order_events", {"order_id": "NOPE", "at": "x", "status": "approved",
                                                 "broker_order_id": None, "detail": None})

    def test_a_failed_write_leaves_nothing_behind(self):
        before = self.q("SELECT COUNT(*) FROM trades")[0][0]
        bad = ledger([trade("PT-X-01"), {"trade_id": "PT-X-02"}])      # the second is broken
        with self.assertRaises(KeyError):
            db.save_ledger(self.path, "paper", bad, NOW)
        self.assertEqual(self.q("SELECT COUNT(*) FROM trades")[0][0], before)


class Ledger(Base):
    def test_round_trip_and_modes_are_separate(self):
        sk = {"signal_id": "SK-20261030-01", "date": "2026-10-30", "symbol": "SYNTHB", "why_skipped": "no",
              "hypothetical_net_pnl": None}
        db.save_ledger(self.path, "paper", ledger([closed()], [sk], 100587.62), NOW)
        live = ledger([trade("LT-20261030-01", mode="live")])
        live["mode"] = "live"
        db.save_ledger(self.path, "live", live, NOW, "kite")
        led = db.load_ledger(self.path, "paper", {})
        self.assertEqual([t["trade_id"] for t in led["trades"]], ["PT-20261030-01"])
        self.assertEqual(led["trades"][0]["net_pnl"], 587.62)
        self.assertEqual(led["balance"], 100587.62)
        self.assertEqual(len(led["skipped"]), 1)
        self.assertEqual([t["trade_id"] for t in db.load_ledger(self.path, "live", {})["trades"]], ["LT-20261030-01"])
        sk["hypothetical_net_pnl"] = -120.5
        db.save_ledger(self.path, "paper", ledger([closed()], [sk], 100587.62), NOW)
        self.assertEqual(db.load_ledger(self.path, "paper", {})["skipped"][0]["hypothetical_net_pnl"], -120.5)
        self.assertEqual(self.q("SELECT version FROM skipped_signals ORDER BY seq"), [(1,), (2,)])

    def test_positions_follow_open_trades(self):
        db.save_ledger(self.path, "paper", ledger([trade()]), NOW)
        self.assertEqual(self.q("SELECT symbol, qty, avg_price_paise FROM positions"), [("SYNTHA", 45, 141671)])
        db.save_ledger(self.path, "paper", ledger([closed()]), NOW)
        self.assertEqual(self.q("SELECT COUNT(*) FROM positions")[0][0], 0)

    def test_account_and_baseline(self):
        db.save_ledger(self.path, "paper", ledger([], [], 100587.62), NOW)
        a = db.get_account(self.path, "paper")
        self.assertEqual((a["balance"], a["capital"], a["baseline"]), (100587.62, 100000.0, 100000.0))
        db.set_account(self.path, "paper", capital=150000, baseline=150000)
        self.assertEqual(db.get_account(self.path, "paper")["baseline"], 150000.0)
        self.assertIsNone(db.get_account(self.path, "live"))


class AuditRows(Base):
    ORDER = {"symbol": "SYNTHA", "side": "buy", "qty": 45, "entry_type": "market", "stop": 1408.0, "target": 1432.0}

    def rec(self, **kw):
        r = {"at": "2026-10-30T09:51:20+05:30", "date": "2026-10-30", "fingerprint": "abcd1234abcd1234",
             "symbol": "SYNTHA", "status": "approved", "reply": "yes", "mode": "paper"}
        r.update(kw)
        return r

    def test_a_yes_creates_the_order_the_event_and_the_approval(self):
        oid = db.record_decision(self.path, self.rec(), self.ORDER, why="ORB with news clear", now=NOW)
        self.assertEqual(oid, "PAPER-20261030-01")
        self.assertEqual(self.q("SELECT status FROM order_events"), [("approved",)])
        self.assertEqual(self.q("SELECT decision, reply, why, order_id FROM approvals"),
                         [("yes", "yes", "ORB with news clear", oid)])
        self.assertEqual(self.q("SELECT stop_paise, qty FROM orders"), [(140800, 45)])
        self.assertEqual(db.order_for_fingerprint(self.path, "abcd1234abcd1234", "2026-10-30", "paper"), oid)

    def test_a_no_creates_only_an_approval(self):
        self.assertIsNone(db.record_decision(self.path, self.rec(status="declined", reply="no"), None, now=NOW))
        self.assertEqual(self.q("SELECT decision FROM approvals"), [("no",)])
        self.assertEqual(self.q("SELECT COUNT(*) FROM orders")[0][0], 0)

    def test_order_ids_count_per_day_and_mode(self):
        a = db.record_decision(self.path, self.rec(), self.ORDER, now=NOW)
        b = db.record_decision(self.path, self.rec(fingerprint="ffff0000ffff0000"), self.ORDER, now=NOW)
        c = db.record_decision(self.path, self.rec(mode="live"), self.ORDER, now=NOW)
        self.assertEqual((a, b, c), ("PAPER-20261030-01", "PAPER-20261030-02", "LIVE-20261030-01"))

    def test_order_events_follow(self):
        oid = db.record_decision(self.path, self.rec(), self.ORDER, now=NOW)
        db.record_order_event(self.path, oid, "placed", "250101000123456", "sent", NOW)
        self.assertEqual(self.q("SELECT status, broker_order_id FROM order_events ORDER BY seq")[-1],
                         ("placed", "250101000123456"))

    def test_verdicts(self):
        n = db.record_verdicts(self.path, "paper", "2026-10-30", [
            {"fingerprint": "a" * 16, "symbol": "SYNTHA", "verdict": "PASS", "reasons": [], "warnings": ["w"]},
            {"fingerprint": "b" * 16, "symbol": "SYNTHB", "verdict": "BLOCK", "reasons": ["too big"]}], NOW)
        self.assertEqual(n, 2)
        self.assertEqual(self.q("SELECT verdict, reasons FROM risk_verdicts ORDER BY seq")[1], ("BLOCK", '["too big"]'))

    def test_notes_pauses_discrepancies_capital_config(self):
        db.add_note(self.path, "PT-1", "why", "breakout on volume", "paper", NOW)
        db.add_note(self.path, "PT-1", "feel", "nervous", "paper", NOW)
        self.assertEqual([n["kind"] for n in db.notes_for(self.path, "PT-1")], ["why", "feel"])
        db.add_pause(self.path, "all", "pause", "2026-11-05", "review", NOW)
        self.assertTrue(db.pause_state(self.path, "paper", "2026-11-02")["paused"])
        self.assertFalse(db.pause_state(self.path, "paper", "2026-11-06")["paused"])      # it ended itself
        db.add_pause(self.path, "all", "resume", None, "back", NOW)
        self.assertFalse(db.pause_state(self.path, "paper", "2026-11-02")["paused"])
        db.add_discrepancy(self.path, "live", "position mismatch", "SYNTHA", "kite 45, ledger 0", NOW)
        self.assertEqual(self.q("SELECT status FROM discrepancies"), [("open",)])
        db.add_capital_change(self.path, "paper", "set", 100000, 150000, "added funds", {"max_loss": 750}, NOW)
        self.assertEqual(self.q("SELECT change_id, new_paise FROM capital_changes"), [("CAP-0001", 15000000)])
        self.assertTrue(db.log_config(self.path, {"a": 1}, "tool", now=NOW))
        self.assertFalse(db.log_config(self.path, {"a": 1}, "tool", now=NOW))        # unchanged: no row
        self.assertTrue(db.log_config(self.path, {"a": 2}, "manual-edit", "hand edit", NOW))
        self.assertEqual(self.q("SELECT source FROM config_history ORDER BY seq"), [("initial",), ("manual-edit",)])
        self.assertEqual(db.last_config(self.path), {"a": 2})
        ok, _ = db.check(self.path)
        self.assertTrue(ok)

    def test_log_rows_dedupe(self):
        row = {"item_hash": "h1", "at": "x", "source": "Yahoo", "symbol": "SYNTHA", "title": "t", "tags": "", "link": ""}
        self.assertTrue(db.log_row(self.path, "news_items", row))
        self.assertFalse(db.log_row(self.path, "news_items", row))
        with self.assertRaises(db.DbError):
            db.log_row(self.path, "orders", row)


class Outbox(Base):
    def test_round_trip(self):
        rows = [{"key": "paper_trades:PT-1", "db": "paper_trades", "id": "PT-1", "hash": "h1", "props": {"a": 1},
                 "notion_page_id": None, "sent_hash": None, "queued_at": "t0"}]
        db.save_outbox(self.path, rows)
        rows[0].update(notion_page_id="pg", sent_hash="h1", sent_at="t1")
        db.save_outbox(self.path, rows)
        got = db.load_outbox(self.path)
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["notion_page_id"], got[0]["sent_hash"], got[0]["props"]), ("pg", "h1", {"a": 1}))


class BackupRestore(Base):
    def test_backup_rotation_keeps_thirty(self):
        folder = os.path.join(self.d, "trader", "backups")
        for i in range(34):
            db.backup(self.path, folder, datetime.datetime(2026, 10, 1) + datetime.timedelta(minutes=i))
        files = sorted(os.listdir(folder))
        self.assertEqual(len(files), 30)
        self.assertEqual(files[0], "trader-2026-10-01-000400.db")
        self.assertTrue(db.check(os.path.join(folder, files[-1]))[0])

    def test_restore(self):
        db.save_ledger(self.path, "paper", ledger([trade()]), NOW)
        dest = db.backup(self.path, None, NOW)
        db.save_ledger(self.path, "paper", ledger([closed()]), NOW)
        db.restore(dest, self.path)
        self.assertEqual(len(db.load_ledger(self.path, "paper", {})["trades"]), 1)
        self.assertEqual(db.load_ledger(self.path, "paper", {})["trades"][0]["status"], "open")
        self.assertTrue(os.path.exists(self.path + ".before-restore"))

    def test_a_damaged_backup_is_not_restored(self):
        db.save_ledger(self.path, "paper", ledger([trade()]), NOW)
        dest = db.backup(self.path, None, NOW)
        conn = sqlite3.connect(dest)
        conn.execute("DROP TRIGGER trades_no_update")
        conn.execute("UPDATE trades SET qty = 1")
        conn.commit()
        conn.close()
        with self.assertRaises(db.DbError):
            db.restore(dest, self.path)


class Migrate(unittest.TestCase):
    def test_json_files_are_imported_counted_and_kept(self):
        d = tempfile.mkdtemp()
        try:
            base = os.path.join(d, "trader")
            write(os.path.join(base, "state", "paper.json"), ledger([closed(), trade("PT-20261030-02")],
                                                                    [{"signal_id": "SK-1", "date": "2026-10-30",
                                                                      "symbol": "SYNTHB"}], 100587.62))
            write(os.path.join(base, "state", "broker.json"), {"orders": [
                {"at": "2026-10-30T09:51:20+05:30", "date": "2026-10-30", "fingerprint": "a" * 16, "symbol": "SYNTHA",
                 "status": "approved", "reply": "yes", "mode": "paper"}]})
            write(os.path.join(base, "state", "risk.json"), {"date": "2026-10-30", "mode": "paper", "results": [
                {"fingerprint": "a" * 16, "symbol": "SYNTHA", "verdict": "PASS"}]})
            write(os.path.join(base, "config.json"), {"capital_inr": 100000})
            with open(os.path.join(base, "state", "paper.json"), "rb") as fh:
                before = fh.read()
            rep = db.migrate(base, None, NOW)
            self.assertEqual(rep["paper trades"], (2, 2))
            self.assertEqual(rep["paper skipped signals"], (1, 1))
            self.assertEqual(rep["decisions (approvals)"], (1, 1))
            self.assertEqual(rep["risk verdicts"], (1, 1))
            again = db.migrate(base, None, NOW)
            self.assertEqual(again["paper trades"], (2, 0))              # nothing is added twice
            self.assertEqual(again["decisions (approvals)"], (1, 0))
            with open(os.path.join(base, "state", "paper.json"), "rb") as fh:
                self.assertEqual(fh.read(), before)
            self.assertTrue(db.check(os.path.join(base, "trader.db"))[0])
            self.assertEqual(db.load_ledger(os.path.join(base, "trader.db"), "paper", {})["balance"], 100587.62)
        finally:
            shutil.rmtree(d)


class Export(Base):
    def test_csv_per_table_and_tax_file(self):
        c = closed()
        live = dict(closed("LT-20261030-01"), mode="live", charges_kind="estimated")
        l2 = dict(live, trade_id="LT-20270410-01", date="2027-04-10", exit_date="2027-04-10")     # next FY
        led = ledger([live, l2])
        led["mode"] = "live"
        db.save_ledger(self.path, "live", led, NOW, "kite")
        db.save_ledger(self.path, "paper", ledger([c]), NOW)
        out = db.export_csv(self.path, os.path.join(self.d, "x"))
        self.assertEqual(out["trades"], 3)
        self.assertEqual(out["fills"], 6)
        dest, n, (g, ch, net) = db.export_tax(self.path, os.path.join(self.d, "x"), "2026-27", "live")
        self.assertEqual((n, g, ch, net), (1, 65565, 6803, 58762))          # paper and the other year are out
        with open(dest, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("NOT TAX ADVICE", text)
        self.assertIn("speculative business income", text)
        self.assertIn("LT-20261030-01", text)
        self.assertNotIn("PT-", text)
        self.assertNotIn("LT-20270410", text)
        with self.assertRaises(db.DbError):
            db.fy_range("nope")
        self.assertEqual(db.fy_range("2026-27"), ("2026-04-01", "2027-03-31"))


class StoreAndCli(Base):
    def make(self, path):
        import json as _j

        def rd(p):
            if not os.path.exists(p):
                return None
            with open(p, encoding="utf-8") as fh:
                return _j.load(fh)

        def wr(p, d):
            write(p, d)
        return db.LedgerStore(os.path.join(self.d, "paper.json"), path, "paper", {"capital_inr": 100000},
                              lambda cfg: ledger(), rd, wr)

    def test_without_a_database_the_json_file_is_the_ledger(self):
        s = self.make(os.path.join(self.d, "absent.db"))
        self.assertFalse(s.uses_db)
        led = s.load()
        led["trades"].append(trade())
        s.save(led)
        self.assertEqual(len(self.make(os.path.join(self.d, "absent.db")).load()["trades"]), 1)
        self.assertFalse(os.path.exists(os.path.join(self.d, "absent.db")))

    def test_with_a_database_it_is_the_truth_and_the_file_is_a_copy(self):
        s = self.make(self.path)
        led = s.load()
        led["trades"].append(trade())
        s.save(led, NOW)
        self.assertEqual(self.q("SELECT COUNT(*) FROM trades")[0][0], 1)
        s2 = self.make(self.path)
        self.assertEqual(len(s2.load()["trades"]), 1)
        self.assertEqual(s2.warnings, [])
        with open(os.path.join(self.d, "paper.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["trades"][0]["qty"] = 9999                         # someone edits the copy by hand
        write(os.path.join(self.d, "paper.json"), doc)
        s3 = self.make(self.path)
        self.assertEqual(s3.load()["trades"][0]["qty"], 45)    # the database wins
        self.assertTrue(s3.warnings and "changed outside the tools" in s3.warnings[0])

    def test_records_only_in_the_json_file_are_used_until_they_are_saved(self):
        write(os.path.join(self.d, "paper.json"), ledger([trade()]))
        s = self.make(self.path)
        led = s.load()
        self.assertEqual(len(led["trades"]), 1)
        self.assertTrue(s.warnings and "do not have yet" in s.warnings[0] or "does not have yet" in s.warnings[0])
        s.save(led, NOW)
        self.assertEqual(self.q("SELECT COUNT(*) FROM trades")[0][0], 1)
        self.assertEqual(self.make(self.path).load()["trades"][0]["trade_id"], "PT-20261030-01")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = db.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_cli(self):
        code, out, _ = self.run_cli("init", "--db", self.path)
        self.assertIn("Checked", out)
        db.save_ledger(self.path, "paper", ledger([closed()]), NOW)
        self.assertEqual(self.run_cli("check", "--db", self.path)[0], 0)
        code, out, _ = self.run_cli("status", "--db", self.path)
        self.assertIn("trades", out)
        self.assertIn("Last backup: none", out)
        code, out, _ = self.run_cli("backup", "--db", self.path)
        self.assertIn("Backup written", out)
        code, out, _ = self.run_cli("export", "--db", self.path)
        self.assertIn("trades", out)
        code, out, _ = self.run_cli("export", "--db", self.path, "--tax", "--fy", "2026-27", "--mode", "paper")
        self.assertIn("NOT TAX ADVICE", out)
        code, _, err = self.run_cli("check", "--db", os.path.join(self.d, "none.db"))
        self.assertEqual(code, 1)
        self.assertIn("db.py init", err)

    def test_no_secrets_or_network_in_the_module(self):
        with open(os.path.join(ROOT, "trader-db", "db.py"), encoding="utf-8") as fh:
            text = fh.read()
        for bad in ("urllib", "socket", "requests", "os.environ", "token", "password"):
            self.assertNotIn(bad, text.split('"""')[2], bad)


if __name__ == "__main__":
    unittest.main()
