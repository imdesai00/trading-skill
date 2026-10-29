import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ist

journal = load("trader-journal", "journal")
CFG = dict(journal.LIMIT_DEFAULTS)

# every property your spec lists for the Paper Trades database
SPEC_TRADE_COLUMNS = [
    "Trade ID", "Date", "Symbol", "Setup type", "Side", "Qty", "Planned entry", "Entry fill", "Stop", "Target",
    "Exit price", "Exit time", "Exit reason", "Gross P&L ₹", "Charges ₹", "Net P&L ₹", "R multiple", "Risk at stop ₹",
    "Time-machine odds (target/stop/open %)", "Risk verdict", "News tags", "My approval (exact words)", "Mode",
    "Status", "Account balance after ₹", "Notes"]
SPEC_DAILY_COLUMNS = ["Date", "Trades", "Wins", "Losses", "Net P&L ₹", "Charges ₹", "Win rate", "Avg R", "Balance ₹",
                      "Drawdown from ₹1,00,000", "Limits hit", "Rules broken"]


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
        code = journal.main(argv)
    return code, out.getvalue(), err.getvalue()


class Schema(unittest.TestCase):
    def test_your_spec_columns_are_all_there(self):
        names = [n for n, _ in journal.columns("paper_trades")]
        for c in SPEC_TRADE_COLUMNS:
            self.assertIn(c, names)
        self.assertEqual(names[0], "Trade ID")
        dnames = [n for n, _ in journal.columns("daily_summary")]
        for c in SPEC_DAILY_COLUMNS:
            self.assertIn(c, dnames)
        skipped_names = [n for n, _ in journal.columns("skipped_signals")]
        for c in ("Why skipped", "Hypothetical net P&L ₹", "Symbol", "Stop", "Target", "Risk at stop ₹"):
            self.assertIn(c, skipped_names)
        self.assertEqual([k for k in journal.DB_ORDER], ["paper_trades", "skipped_signals", "daily_summary",
                                                         "weekly_reports", "accounts", "orders", "capital_changes",
                                                         "pauses", "discrepancies"])
        for c in ("Why I took it", "How I felt", "Charges type", "Order ID", "Broker order ID"):
            self.assertIn(c, names)
        self.assertIn("Mode", dnames)

    def test_ddl_is_well_formed(self):
        for k in journal.DB_ORDER:
            d = journal.ddl(k)
            self.assertTrue(d.startswith("CREATE TABLE ("))
            self.assertEqual(d.count('"') % 2, 0)
            self.assertEqual(d.count("TITLE"), 1)
            self.assertNotIn("\n", d)
        self.assertIn("SELECT('target':green, 'stop':red, 'flat-by':yellow, 'manual':gray)",
                      journal.ddl("paper_trades"))
        self.assertIn('"Drawdown from ₹1,00,000" NUMBER', journal.ddl("daily_summary"))
        self.assertIn('"Drawdown from ₹2,00,000" NUMBER', journal.ddl("daily_summary", 200000))

    def test_schema_cli_names_the_parent_only_as_a_placeholder(self):
        code, out, _ = run(["schema", "--json", "--config", "none"])
        plan = json.loads(out)
        self.assertEqual(plan["page"]["title"], "India Day Trader")
        self.assertIn("the page you name", json.dumps(plan))
        self.assertEqual([d["title"] for d in plan["databases"]],
                         ["Trades", "Skipped Signals", "Daily Summary", "Weekly Reports", "Account", "Orders",
                          "Capital Changes", "Pauses", "Discrepancies"])


class Numbers(unittest.TestCase):
    def setUp(self):
        self.led = ledger([trade(1, "2026-10-27", -400.0), trade(1, "2026-10-28", 900.0, reason="target", odds=0.4),
                           trade(1, "2026-10-29", -1200.0, setup="level", odds=None)],
                          [skipped(1, "2026-10-28", 650.0), skipped(1, "2026-10-29", None)])

    def test_day_stats(self):
        st = journal.day_stats(self.led, CFG, "2026-10-28")
        self.assertEqual((st["trades"], st["wins"], st["losses"], st["net"], st["charges"]), (1, 1, 0, 900.0, 67.37))
        self.assertEqual((st["balance"], st["drawdown"], st["win_rate"]), (100500.0, 0.0, 100.0))
        st = journal.day_stats(self.led, CFG, "2026-10-29")
        self.assertEqual((st["balance"], st["drawdown"]), (99300.0, 700.0))
        self.assertEqual(st["limits"], [])

    def test_limits_hit_and_rules_broken(self):
        led = ledger([trade(1, "2026-10-26", -1600.0), trade(2, "2026-10-26", 0.0)])
        st = journal.day_stats(led, CFG, "2026-10-26")
        self.assertTrue(any("daily loss limit ₹1,500 reached" in x for x in st["limits"]))
        self.assertTrue(any("over the ₹500 per-trade limit" in x for x in st["rules"]))
        led = ledger([trade(1, "2026-10-26", -1900.0), trade(1, "2026-10-27", -1900.0), trade(1, "2026-10-28", -300.0)])
        self.assertTrue(any("weekly loss limit" in x for x in journal.day_stats(led, CFG, "2026-10-28")["limits"]))
        led = ledger([trade(i, "2026-10-26", -450.0) for i in range(1, 4)])
        self.assertTrue(any("3 trades today" in x for x in journal.day_stats(led, CFG, "2026-10-26")["limits"]))
        led = ledger([trade(1, d, -1700.0) for d in ("2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29",
                                                      "2026-10-30")])
        self.assertTrue(any("drawdown pause" in x for x in journal.day_stats(led, CFG, "2026-10-30")["limits"]))

    def test_a_no_trade_day(self):
        st = journal.day_stats(self.led, CFG, "2026-10-30")
        self.assertEqual((st["trades"], st["win_rate"], st["net"]), (0, None, 0.0))
        self.assertEqual(journal.daily_text(st)[0], "2026-10-30: no trades.")

    def test_weekly(self):
        w = journal.week_stats(self.led, CFG, "2026-10-30")
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
        md = journal.weekly_markdown(journal.week_stats(self.led, CFG, "2026-10-30"))
        for want in ("Trades: 3", "Win rate 33 %", "Net expectancy per trade after charges", "Charges paid",
                     "Best trade", "Worst trade", "## By setup type", "## Results against the time machine's odds",
                     "## Signals you skipped", "would have netted ₹650.00", "Limits hit:", "Rules broken:",
                     "₹1,00,000 you started with", "cannot tell skill from luck", "Not financial advice"):
            self.assertIn(want, md)
        self.assertNotRegex(md.lower(), r"amazing|great|crush|moon|guarantee")

    def test_an_empty_week(self):
        md = journal.weekly_markdown(journal.week_stats(self.led, CFG, "2026-11-04"))
        self.assertIn("No trades closed this week", md)


class Outbox(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = lambda *x: os.path.join(self.d, *x)
        self.led = ledger([trade(1, "2026-10-30", -571.82)], [skipped(1, "2026-10-30", 400.0)])
        with open(self.p("paper.json"), "w") as fh:
            json.dump(self.led, fh)
        cfg = dict(CFG, notion={"databases": {k: "ds-" + k for k in journal.DB_ORDER}})
        with open(self.p("config.json"), "w") as fh:
            json.dump(cfg, fh)

    def tearDown(self):
        shutil.rmtree(self.d)

    def j(self, *argv):
        return run(list(argv) + ["--config", self.p("config.json"), "--outbox", self.p("outbox.json")]
                   + (["--ledger", self.p("paper.json")] if argv[0] in ("queue", "daily", "weekly", "rebuild") else []))

    def pend(self):
        code, out, _ = self.j("pending", "--json")
        return json.loads(out) if out.strip().startswith("[") else []

    def test_queue_is_idempotent_and_updates_by_key(self):
        code, out, _ = self.j("queue", "--now", "2026-10-30 15:50")
        self.assertIn("3 new, 0 changed", out)
        self.assertIn("3 rows waiting", out)
        code, out, _ = self.j("queue", "--now", "2026-10-30 15:51")
        self.assertIn("0 new, 0 changed, 3 unchanged", out)
        keys = sorted(r["key"] for r in self.pend())
        self.assertEqual(keys, ["daily_summary:paper:2026-10-30", "paper_trades:PT-20261030-01",
                                "skipped_signals:SK-20261030-01"])

    def test_the_create_and_update_calls(self):
        self.j("queue", "--now", "2026-10-30 15:50")
        row = [r for r in self.pend() if r["db"] == "paper_trades"][0]
        self.assertEqual(row["action"], "create")
        q, c = row["calls"]
        self.assertEqual(q["tool"], "notion-query-data-sources")
        self.assertEqual(q["arguments"]["data"]["data_source_url"], "collection://ds-paper_trades")
        self.assertEqual(q["arguments"]["data"]["filter"]["filters"][0]["value"]["value"], "PT-20261030-01")
        self.assertEqual(c["tool"], "notion-create-pages")
        self.assertEqual(c["arguments"]["parent"], {"type": "data_source_id", "data_source_id": "ds-paper_trades"})
        props = c["arguments"]["pages"][0]["properties"]
        self.assertEqual((props["Trade ID"], props["date:Date:start"], props["date:Date:is_datetime"]),
                         ("PT-20261030-01", "2026-10-30", 0))
        self.assertEqual((props["Net P&L ₹"], props["Qty"], props["Exit reason"]), (-571.82, 45, "stop"))
        self.assertEqual(props["Time-machine odds (target/stop/open %)"], "18.0 / 69.5 / 12.8")
        self.assertEqual((props["News tags"], props["My approval (exact words)"]), ("results", "yes 45 SYNTHA"))
        self.j("mark-sent", "--key", row["key"], "--page-id", "page-1", "--hash", row["hash"])
        self.assertEqual(len(self.pend()), 2)
        # the trade changes: now an update by page id, never a second create
        self.led["trades"][0]["notes"] = "changed note"
        with open(self.p("paper.json"), "w") as fh:
            json.dump(self.led, fh)
        code, out, _ = self.j("queue", "--now", "2026-10-30 16:00")
        self.assertIn("1 changed", out)
        row = [r for r in self.pend() if r["db"] == "paper_trades"][0]
        self.assertEqual((row["action"], row["notion_page_id"]), ("update", "page-1"))
        self.assertEqual(row["calls"][0]["tool"], "notion-update-page")
        self.assertEqual(row["calls"][0]["arguments"]["page_id"], "page-1")
        self.assertEqual(row["calls"][0]["arguments"]["command"], "update_properties")
        self.assertEqual(len(row["calls"]), 1)

    def test_unsent_rows_stay_queued_and_a_changed_row_is_not_marked(self):
        self.j("queue", "--now", "2026-10-30 15:50")
        row = [r for r in self.pend() if r["db"] == "daily_summary"][0]
        self.led["trades"].append(trade(2, "2026-10-30", 300.0, reason="target"))
        with open(self.p("paper.json"), "w") as fh:
            json.dump(self.led, fh)
        self.j("queue", "--now", "2026-10-30 15:55")
        code, out, _ = self.j("mark-sent", "--key", row["key"], "--page-id", "p", "--hash", row["hash"])
        self.assertEqual(code, 1)
        self.assertIn("changed since you listed it", out)
        self.assertEqual(len([r for r in self.pend() if r["db"] == "daily_summary"]), 1)

    def test_mark_sent_needs_a_page_id_and_a_known_key(self):
        self.j("queue", "--now", "2026-10-30 15:50")
        code, _, err = self.j("mark-sent", "--key", "paper_trades:PT-20261030-01")
        self.assertIn("no Notion page id yet", err)
        code, _, err = self.j("mark-sent", "--key", "nope", "--page-id", "x")
        self.assertIn("no row nope", err)

    def test_no_setup_no_calls(self):
        with open(self.p("config.json"), "w") as fh:
            json.dump(CFG, fh)
        self.j("queue", "--now", "2026-10-30 15:50")
        code, _, err = self.j("pending", "--json")
        self.assertEqual(code, 1)
        self.assertIn("run the Notion setup first", err)
        code, out, _ = self.j("status")
        self.assertIn("NOT done (0 of 9 databases saved)", out)

    def test_setup_save_touches_only_the_notion_section(self):
        cfg = dict(CFG, max_loss_per_trade_inr=500)
        with open(self.p("config.json"), "w") as fh:
            json.dump(cfg, fh)
        code, out, _ = run(["setup-save", "--config", self.p("config.json"), "--page-id", "PAGE",
                            "--db", "paper_trades=collection://abc", "--db", "daily_summary=def"])
        self.assertEqual(code, 0)
        self.assertIn("Still missing: skipped_signals, weekly_reports, accounts", out)
        with open(self.p("config.json")) as fh:
            saved = json.load(fh)
        self.assertEqual(saved["notion"]["databases"], {"paper_trades": "abc", "daily_summary": "def"})
        self.assertEqual({k: v for k, v in saved.items() if k != "notion"}, cfg)
        code, _, err = run(["setup-save", "--config", self.p("config.json"), "--db", "bogus=1"])
        self.assertIn("unknown database", err)

    def test_weekly_row_carries_the_report_as_page_content(self):
        code, out, _ = self.j("weekly", "--week-of", "2026-10-30", "--now", "2026-10-30 16:00")
        self.assertEqual(code, 0)
        row = [r for r in self.pend() if r["db"] == "weekly_reports"][0]
        page = row["calls"][-1]["arguments"]["pages"][0]
        self.assertIn("## What happened", page["content"])
        self.assertEqual(page["properties"]["Week"], "2026-W44 (Mon 2026-10-26 to Fri 2026-10-30) (paper)")
        self.assertEqual(page["properties"]["Balance vs start ₹"], -571.82)

    def test_a_day_with_no_trades_can_still_be_queued(self):
        code, out, _ = self.j("daily", "--date", "2026-10-29")
        self.assertIn("2026-10-29: no trades.", out)
        self.assertTrue(any(r["key"] == "daily_summary:paper:2026-10-29" for r in self.pend()))


class Rebuild(unittest.TestCase):
    def notion_rows(self, led):
        """What the Notion connector would hand back for the queued rows (names as Notion shows them)."""
        out = {"paper_trades": [], "skipped_signals": []}
        for t in led["trades"]:
            p = dict(journal.trade_props(t))
            p["Date"] = p.pop("date:Date:start")
            p.pop("date:Date:is_datetime")
            out["paper_trades"].append(p)
        for s in led["skipped"]:
            p = dict(journal.skipped_props(s))
            p["Date"] = p.pop("date:Date:start")
            p.pop("date:Date:is_datetime")
            out["skipped_signals"].append(p)
        return out

    def test_round_trip(self):
        led = ledger([trade(1, "2026-10-29", -300.0), trade(1, "2026-10-30", 900.0, reason="target", odds=0.4),
                      trade(2, "2026-10-30", 0.0, status="open", exit_reason=None, exit_fill=None, exit_time=None,
                            exit_date=None, gross_pnl=None, charges=None, net_pnl=None, r_multiple=None,
                            balance_after=None)],
                     [skipped(1, "2026-10-30", 400.0)])
        back = journal.rebuild_ledger(self.notion_rows(led), CFG)
        self.assertEqual(back["balance"], led["balance"])
        for a, b in zip(led["trades"], back["trades"]):
            for k in ("trade_id", "date", "symbol", "setup", "qty", "entry_fill", "stop", "target", "exit_reason",
                      "gross_pnl", "charges", "net_pnl", "r_multiple", "status", "balance_after", "approval",
                      "news_tags", "risk_at_stop", "entry_time"):
                self.assertEqual(a[k], b[k], k)
        self.assertAlmostEqual(back["trades"][1]["tm_odds"]["target"], 0.4, places=3)
        self.assertEqual(back["skipped"][0]["hypothetical_net_pnl"], 400.0)
        self.assertEqual(back["skipped"][0]["why_skipped"], "you said no")
        # the rebuilt ledger gives the same daily numbers
        for d in ("2026-10-29", "2026-10-30"):
            self.assertEqual(journal.day_stats(back, CFG, d)["net"], journal.day_stats(led, CFG, d)["net"])

    def test_tolerates_other_shapes(self):
        row = {"Trade ID": "PT-20261030-01", "date:Date:start": "2026-10-30", "Symbol": "SYNTHA", "Qty": "45",
               "Status": {"name": "open"}, "Mode": "paper", "Side": "buy", "Net P&L ₹": None}
        t = journal.trade_from_row(row)
        self.assertEqual((t["date"], t["qty"], t["status"], t["net_pnl"]), ("2026-10-30", 45, "open", None))

    def test_refuses_bad_exports(self):
        for rows, word in (([{"Symbol": "X"}], "no Trade ID"),
                           ([{"Trade ID": "A", "Date": "2026-10-30", "Status": "closed"}], "missing its P&L"),
                           ([{"Trade ID": "A", "Date": "2026-10-30"}] * 2, "same Trade ID twice")):
            with self.assertRaises(journal.JournalError) as e:
                journal.rebuild_ledger({"paper_trades": rows}, CFG)
            self.assertIn(word, str(e.exception))

    def test_live_rows_stay_out_of_the_paper_ledger(self):
        rows = [dict(journal.trade_props(trade(1, "2026-10-30", -100.0, mode="live")), Date="2026-10-30")]
        self.assertEqual(journal.rebuild_ledger({"paper_trades": rows}, CFG)["trades"], [])
        self.assertEqual(len(journal.rebuild_ledger({"paper_trades": rows}, CFG, "live")["trades"]), 1)

    def test_cli_will_not_overwrite_without_force(self):
        d = tempfile.mkdtemp()
        try:
            led = ledger([trade(1, "2026-10-30", -300.0)])
            src = os.path.join(d, "export.json")
            with open(src, "w") as fh:
                json.dump(self.notion_rows(led), fh)
            out_path = os.path.join(d, "paper.json")
            argv = ["rebuild", "--from", src, "--config", os.path.join(d, "none.json"), "--ledger", out_path]
            code, out, _ = run(argv)
            self.assertEqual(code, 0)
            self.assertIn("Rebuilt", out)
            code, _, err = run(argv)
            self.assertEqual(code, 1)
            self.assertIn("already exists", err)
            code, _, _ = run(argv + ["--force"])
            self.assertEqual(code, 0)
            self.assertTrue(os.path.exists(out_path + ".bak"))
        finally:
            shutil.rmtree(d)


class NeverCallsNotion(unittest.TestCase):
    def test_no_network_and_no_token_in_the_script(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "trader-journal", "journal.py"), encoding="utf-8") as fh:
            text = fh.read()
        for bad in ("urllib", "requests", "http.client", "socket", "api.notion.com", "NOTION_TOKEN", "Bearer",
                    "secret_"):
            self.assertNotIn(bad, text, bad)


if __name__ == "__main__":
    unittest.main()
