import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT

ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")
size = load("trader-budget", "size")
PLAN = {"symbol": "SYNTHA", "side": "buy", "date": "2026-10-30", "setup": "orb", "entry": 1416.0,
        "entry_time": "09:50", "stop": 1408.0, "target": 1440.0, "tick": 0.1, "flat_by": "15:00"}
NOW = "2026-10-30 09:51"
ORDER = size.order_from(size.size_trade(PLAN, {}, today="2026-10-30"), {})
LOSS = ORDER["plan"]["max_loss"]


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


class ExplicitYes(unittest.TestCase):
    def yes(self, reply, order=ORDER, batch=("SYNTHA", "SYNTHD")):
        return ticket.parse_yes(reply, order, batch)[0]

    def test_plain_yes_counts(self):
        for r in ("yes", "Yes.", "YES", "y", "yes place it", "yes 45 SYNTHA", "yes 1408", "yes target 1440",
                  "yes max loss %.2f" % LOSS):
            self.assertTrue(self.yes(r), r)

    def test_anything_else_does_not(self):
        for r in ("", "sure", "ok", "okay", "go", "do it", "haan", "yeah", "yep", "send it", "yes to all",
                  "yes, all of them", "yes both", "yes SYNTHD", "yes 50", "yes but make it 40", "yes?", "no",
                  "not yes", "yes, wait", "yes if it is under 1420", "yes stop 1408"):
            self.assertFalse(self.yes(r), r)


class TicketShape(unittest.TestCase):
    def test_plain_words(self):
        parts = ticket.plain_parts(ORDER)
        self.assertEqual(parts[0], "BUY 45 SYNTHA on NSE, market, intraday (MIS)")
        self.assertIn("stop 1408.00 (a stop-loss sell placed after the buy fills)", parts)
        self.assertIn("loss about ₹%s if the stop fills at 1408.00" % format(LOSS, ".2f"), parts[3])

    def test_same_fingerprint_as_trader_risk(self):
        self.assertEqual(ticket.fingerprint(ORDER), check.fingerprint(ORDER))
        self.assertEqual(ticket.NEUTRAL_FIELDS, check.NEUTRAL_FIELDS)
        self.assertEqual(ticket.NEUTRAL_FIELDS, size.NEUTRAL_FIELDS)

    def test_kite_example_config_holds_no_secret(self):
        with open(os.path.join(ROOT, "trader-broker", "claude_desktop_config.example.json")) as fh:
            server = json.load(fh)["mcpServers"]["kite"]
        self.assertEqual((server["command"], server["args"]), ("npx", ["mcp-remote", "https://mcp.kite.trade/mcp"]))
        self.assertNotIn("env", server)


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        with open(self.orders, "w") as fh:
            json.dump({"orders": [ORDER]}, fh)
        self.cfg = os.path.join(self.d, "config.json")
        with open(self.cfg, "w") as fh:
            json.dump({"mode": "paper"}, fh)
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "broker.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def common(self):
        return ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk, "--state", self.state,
                "--now", NOW]

    def risk_check(self, now=NOW):
        out = io.StringIO()
        with redirect_stdout(out):
            check.main(["--orders", self.orders, "--config", self.cfg, "--ledger", os.path.join(self.d, "x"),
                        "--prices", os.path.join(self.d, "x"), "--kite-state", os.path.join(self.d, "x"),
                        "--out", self.risk, "--now", now])

    def test_not_ready_until_risk_passes_this_exact_order(self):
        code, out, _ = run(["show"] + self.common())
        self.assertEqual(code, 1)
        self.assertIn("NOT READY: trader-risk has not checked these orders", out)
        self.risk_check()
        code, out, _ = run(["show"] + self.common())
        self.assertEqual(code, 0)
        self.assertIn("ORDER 1 of 1, PAPER: no broker is contacted", out)
        self.assertIn("READY: trader-risk PASS", out)
        with open(self.orders, "w") as fh:
            json.dump({"orders": [dict(ORDER, qty=44)]}, fh)
        code, out, _ = run(["show"] + self.common())
        self.assertIn("this exact order was not in the last risk check", out)

    def test_a_blocked_order_cannot_be_approved(self):
        self.risk_check("2026-10-30 09:20")
        code, _, err = run(["approve", "--index", "1", "--reply", "yes"] + self.common())
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED by trader-risk", err)

    def test_approve_once_then_done(self):
        self.risk_check()
        code, out, _ = run(["approve", "--index", "1", "--reply", "sure"] + self.common())
        self.assertEqual(code, 1)
        self.assertIn("Not recorded", out)
        code, out, _ = run(["approve", "--index", "1", "--reply", "yes 45 SYNTHA"] + self.common())
        self.assertEqual(code, 0)
        self.assertIn("Paper mode: no broker is contacted", out)
        with open(self.state) as fh:
            rec = json.load(fh)["orders"][-1]
        self.assertEqual((rec["status"], rec["reply"], rec["mode"], rec["kind"]),
                         ("approved", "yes 45 SYNTHA", "paper", "entry"))
        code, _, err = run(["approve", "--index", "1", "--reply", "yes"] + self.common())
        self.assertEqual(code, 1)
        self.assertIn("APPROVED by you", err)

    def test_live_shows_the_kite_payload_and_result_is_live_only(self):
        self.risk_check()
        code, _, err = run(["result", "--index", "1", "--status", "placed"] + self.common())
        self.assertIn("live orders only", err)
        with open(self.cfg, "w") as fh:
            json.dump({"mode": "live", "kite_enabled": True}, fh)
        code, out, _ = run(["show"] + self.common())
        self.assertIn("LIVE ACCOUNT, REAL MONEY", out)
        self.assertIn("NOT READY: the risk check ran in paper mode and this is live mode", out)
        code, _, err = run(["approve", "--index", "1", "--reply", "yes"] + self.common())
        self.assertEqual(code, 1)
        self.assertIn("not ready", err)

    def test_decline_and_log(self):
        self.risk_check()
        code, out, _ = run(["decline", "--index", "1", "--reply", "no, too late"] + self.common())
        self.assertEqual(code, 0)
        code, out, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertIn('declined  BUY 45 SYNTHA', out)
        self.assertIn('reply "no, too late"', out)

    def test_flatten_writes_an_exit_ticket(self):
        code, out, _ = run(["flatten", "--symbol", "SYNTHA", "--qty", "45", "--orders", self.orders,
                            "--config", self.cfg, "--plan", os.path.join(self.d, "x"), "--now", "2026-10-30 15:00"])
        self.assertEqual(code, 0)
        with open(self.orders) as fh:
            o = json.load(fh)["orders"][0]
        self.assertEqual((o["side"], o["qty"], o["product"], o["entry_type"]), ("sell", 45, "MIS", "market"))
        self.assertIn("cancel the resting stop-loss", o["kite"]["cancel_first"])
        code, _, err = run(["flatten", "--symbol", "SYNTHA", "--qty", "4.5", "--orders", self.orders])
        self.assertIn("whole number", err)


if __name__ == "__main__":
    unittest.main()
