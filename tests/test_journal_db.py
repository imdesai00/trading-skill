import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import load, write
from test_journal import ledger, run, skipped, trade

journal = load("trader-journal", "journal")
db = load("trader-db", "db")
CFG = dict(journal.LIMIT_DEFAULTS, notion={"databases": {k: "ds-" + k for k in journal.DB_ORDER}})
NOW = "2026-10-30 15:50"


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = lambda *x: os.path.join(self.d, *x)
        write(self.p("config.json"), CFG)
        self.dbp = self.p("trader.db")
        db.init(self.dbp)
        self.led = ledger([trade(1, "2026-10-30", -571.82, order_id=None)], [skipped(1, "2026-10-30", 400.0)])
        db.save_ledger(self.dbp, "paper", self.led)

    def tearDown(self):
        shutil.rmtree(self.d)

    def j(self, *argv, mode=None):
        extra = ["--mode", mode] if mode else []
        return run(list(argv) + ["--config", self.p("config.json"), "--outbox", self.p("outbox.json")] + extra)

    def rows(self):
        return {r["key"]: r for r in db.load_outbox(self.dbp)}


class Queueing(Base):
    def test_the_database_is_the_ledger_and_the_outbox(self):
        code, out, _ = self.j("queue", "--now", NOW)
        self.assertEqual(code, 0, out)
        keys = sorted(self.rows())
        self.assertIn("paper_trades:PT-20261030-01", keys)
        self.assertIn("accounts:paper", keys)
        self.assertIn("daily_summary:paper:2026-10-30", keys)
        self.assertFalse(os.path.exists(self.p("outbox.json")))               # the file outbox is not used any more
        code, out, _ = self.j("queue", "--now", "2026-10-30 15:51")
        self.assertIn("0 new, 0 changed", out)

    def test_why_and_feel_travel_with_the_trade(self):
        db.add_note(self.dbp, "PT-20261030-01", "why", "clean ORB, news clear", "paper")
        db.add_note(self.dbp, "PT-20261030-01", "feel", "calm, the stop did its job", "paper")
        self.j("queue", "--now", NOW)
        props = self.rows()["paper_trades:PT-20261030-01"]["properties"]
        self.assertEqual((props["Why I took it"], props["How I felt"]), ("clean ORB, news clear", "calm, the stop did its job"))
        self.assertEqual(props["Charges type"], "estimated")

    def test_account_orders_capital_pauses_discrepancies(self):
        order = {"symbol": "SYNTHA", "side": "buy", "qty": 45, "entry_type": "market", "stop": 1408.0, "target": 1440.0}
        rec = {"at": "2026-10-30T09:51:20+05:30", "date": "2026-10-30", "fingerprint": "a" * 16, "symbol": "SYNTHA",
               "status": "approved", "reply": "yes", "mode": "paper"}
        db.record_verdicts(self.dbp, "paper", "2026-10-30", [{"fingerprint": "a" * 16, "symbol": "SYNTHA", "verdict": "PASS"}])
        oid = db.record_decision(self.dbp, rec, order, why="clean ORB")
        db.record_order_event(self.dbp, oid, "filled", None, "paper fill")
        db.add_capital_change(self.dbp, "paper", "set", 100000, 150000, "added funds", {"a": 1})
        db.add_pause(self.dbp, "all", "pause", "2026-11-06", "review")
        db.add_discrepancy(self.dbp, "paper", "position mismatch", "SYNTHA", "ledger 45, other 0")
        db.set_account(self.dbp, "paper", capital=150000, balance=149428.18, baseline=150000)
        self.j("queue", "--now", NOW)
        rows = self.rows()
        o = rows["orders:" + oid]["properties"]
        self.assertEqual((o["Status"], o["Risk verdict"], o["My approval (exact words)"], o["Why I took it"], o["Stop"]),
                         ("filled", "PASS", "yes", "clean ORB", 1408.0))
        a = rows["accounts:paper"]["properties"]
        self.assertEqual((a["Account"], a["Balance ₹"], a["Capital baseline ₹"], a["Realised P&L ₹"]),
                         ("Paper Account", 149428.18, 150000.0, -571.82))
        c = rows["capital_changes:CAP-0001"]["properties"]
        self.assertEqual((c["Old capital ₹"], c["New capital ₹"], c["Reason"]), (100000.0, 150000.0, "added funds"))
        self.assertEqual(rows["pauses:PAUSE-0001"]["properties"]["Until"], "2026-11-06")
        self.assertEqual(rows["discrepancies:DISC-0001"]["properties"]["Status"], "open")
        # and Claude gets a create call for each, against that database's own id
        code, out, _ = self.j("pending", "--json")
        calls = {r["key"]: r["calls"] for r in json.loads(out)}
        self.assertEqual(calls["orders:" + oid][1]["arguments"]["parent"]["data_source_id"], "ds-orders")

    def test_paper_and_live_are_kept_apart(self):
        live = ledger([trade(1, "2026-10-30", 300.0, mode="live", reason="target")])
        live["mode"] = "live"
        live["trades"][0]["trade_id"] = "LT-20261030-01"
        db.save_ledger(self.dbp, "live", live, source="kite")
        db.set_account(self.dbp, "live", capital=100000, balance=100300, baseline=100000)
        self.j("queue", "--now", NOW)
        self.j("queue", "--now", NOW, mode="live")
        keys = set(self.rows())
        self.assertTrue({"daily_summary:paper:2026-10-30", "daily_summary:live:2026-10-30", "accounts:paper",
                         "accounts:live", "paper_trades:LT-20261030-01"} <= keys)
        self.assertEqual(self.rows()["daily_summary:live:2026-10-30"]["properties"]["Net P&L ₹"], 300.0)
        self.assertEqual(self.rows()["daily_summary:paper:2026-10-30"]["properties"]["Net P&L ₹"], -571.82)
        code, out, _ = self.j("weekly", "--week-of", "2026-10-30", mode="live")
        self.assertIn("LIVE only", out)
        self.assertEqual(self.rows()["accounts:live"]["properties"]["Source"],
                         "Kite, as last read by kite.py sync (Kite is the truth in live mode)")

    def test_mark_sent_survives_in_the_database(self):
        self.j("queue", "--now", NOW)
        row = self.rows()["paper_trades:PT-20261030-01"]
        self.j("mark-sent", "--key", row["key"], "--page-id", "page-1", "--hash", row["hash"])
        after = self.rows()["paper_trades:PT-20261030-01"]
        self.assertEqual((after["notion_page_id"], after["sent_hash"]), ("page-1", row["hash"]))
        code, out, _ = self.j("pending")
        self.assertNotIn("PT-20261030-01", out)


class VerifyAndRebuild(Base):
    def export(self, **over):
        rows = [{"Trade ID": "PT-20261030-01", "Date": "2026-10-30", "Symbol": "SYNTHA", "Side": "buy", "Qty": 45,
                 "Entry fill": 1416.7, "Exit price": 1407.3, "Exit time": "10:09", "Exit reason": "stop",
                 "Gross P&L ₹": -504.45, "Charges ₹": 67.37, "Net P&L ₹": -571.82, "R multiple": -1.16,
                 "Risk at stop ₹": 490.88, "Mode": "paper", "Status": "closed", "Account balance after ₹": 99428.18,
                 "Stop": 1408.0, "Target": 1440.0}]
        doc = {"paper_trades": rows, "skipped_signals": [{"Signal ID": "SK-20261030-01", "Date": "2026-10-30",
                                                          "Symbol": "SYNTHD", "Mode": "paper"}]}
        doc.update(over)
        write(self.p("export.json"), doc)
        return doc

    def test_verify_agrees(self):
        self.export()
        code, out, _ = self.j("verify", "--from", self.p("export.json"))
        self.assertEqual(code, 0, out)
        self.assertIn("Everything agrees", out)

    def test_verify_finds_a_changed_total_and_a_missing_row(self):
        doc = self.export()
        doc["paper_trades"][0]["Net P&L ₹"] = -500.0
        doc["paper_trades"].append(dict(doc["paper_trades"][0], **{"Trade ID": "PT-20261030-09"}))
        write(self.p("export.json"), doc)
        code, out, _ = self.j("verify", "--from", self.p("export.json"))
        self.assertEqual(code, 1)
        self.assertIn("MISMATCH", out)
        self.assertIn("in Notion but not in the database: PT-20261030-09", out)
        self.assertIn("net P&L after charges", out)

    def test_verify_counts_the_other_databases(self):
        db.add_pause(self.dbp, "all", "pause", None, "x")
        self.export(pauses=[])
        code, out, _ = self.j("verify", "--from", self.p("export.json"))
        self.assertEqual(code, 1)
        self.assertIn("pauses", out)

    def test_rebuild_the_lost_database_from_notion(self):
        self.export()
        os.remove(self.dbp)
        write(self.p("config.json"), CFG)
        code, out, err = self.j("rebuild", "--from", self.p("export.json"), "--ledger", self.p("paper.json"), "--into-db")
        self.assertEqual(code, 0, out + err)
        self.assertIn("run db.py check", out)
        self.assertTrue(db.check(self.dbp)[0])
        led = db.load_ledger(self.dbp, "paper", CFG)
        self.assertEqual([t["trade_id"] for t in led["trades"]], ["PT-20261030-01"])
        self.assertEqual(led["balance"], 99428.18)

    def test_rebuild_refuses_to_overwrite_a_database_that_has_records(self):
        self.export()
        code, _, err = self.j("rebuild", "--from", self.p("export.json"), "--ledger", self.p("paper.json"))
        self.assertEqual(code, 1)
        self.assertIn("already holds paper records", err)

    def test_requeue_restores_notion_from_the_database(self):
        self.j("queue", "--now", NOW)
        for k, r in self.rows().items():
            self.j("mark-sent", "--key", k, "--page-id", "pg-" + k, "--hash", r["hash"])
        code, out, _ = self.j("pending")
        self.assertEqual(code, 1)
        code, out, _ = self.j("requeue")
        self.assertIn("will be written to Notion again", out)
        code, out, _ = self.j("pending", "--json")
        calls = json.loads(out)[0]["calls"]
        self.assertEqual(calls[0]["tool"], "notion-query-data-sources")        # looked up by title first, no duplicates


if __name__ == "__main__":
    unittest.main()
