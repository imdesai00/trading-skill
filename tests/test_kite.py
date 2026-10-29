import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, ist

kite = load("trader-broker", "kite")
ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")
size = load("trader-budget", "size")
PLAN = {"symbol": "SYNTHA", "side": "buy", "date": "2026-10-30", "setup": "orb", "entry": 1416.0,
        "entry_time": "09:50", "stop": 1408.0, "target": 1440.0, "tick": 0.1, "flat_by": "15:00"}
LIVE = {"mode": "live", "kite_enabled": True, "protection_data_required": False, "live_start_weeks": 0}   # these tests are about the gates, not the protections or the half-risk start
NOW = "2026-10-30 09:51"
ENTRY = size.order_from(size.size_trade(PLAN, {}, today="2026-10-30"), {})


class FakeKite(object):
    """A stand-in for the Kite MCP tools. Market buys fill at once; the stop rests until trigger_stop()."""

    def __init__(self, buy_price=1416.7, sell_price=1424.0):
        self.orders, self.trades, self.calls, self.n = [], [], [], 0
        self.buy_price, self.sell_price, self.refuse = buy_price, sell_price, None
        self.ltp, self.cash = 1416.2, 100000.0

    def __call__(self, tool, args):
        self.calls.append((tool, copy.deepcopy(args)))
        if tool == "get_orders":
            return copy.deepcopy(self.orders)
        if tool == "get_trades":
            return copy.deepcopy(self.trades)
        if tool == "get_ltp":
            return {k: {"instrument_token": 1, "last_price": self.ltp} for k in args["instruments"]}
        if tool == "get_margins":
            return {"equity": {"enabled": True, "net": self.cash, "available": {"live_balance": self.cash, "cash": self.cash}}}
        if tool == "get_profile":
            return {"user_id": "AB1234"}
        if tool == "get_positions":
            return {"net": self.positions()}
        if tool == "cancel_order":
            o = self.find(args["order_id"])
            if o["status"] == "COMPLETE":
                raise RuntimeError("cannot cancel a completed order")
            o["status"] = "CANCELLED"
            return {"order_id": o["order_id"]}
        if tool != "place_order":
            raise AssertionError("unexpected tool " + tool)
        if self.refuse:
            raise RuntimeError(self.refuse)
        self.n += 1
        oid = "2610300000%03d" % self.n
        o = {"order_id": oid, "tradingsymbol": args["tradingsymbol"], "transaction_type": args["transaction_type"],
             "quantity": args["quantity"], "filled_quantity": 0, "average_price": 0, "order_type": args["order_type"],
             "tag": args["tag"], "status": "OPEN", "order_timestamp": "2026-10-30 09:52:01"}
        if args["order_type"] == "MARKET":
            price = self.buy_price if args["transaction_type"] == "BUY" else self.sell_price
            self.fill(o, price)
        elif args["order_type"] in ("SL-M", "SL"):
            o["status"] = "TRIGGER PENDING"
            o["trigger_price"] = args["trigger_price"]
        self.orders.append(o)
        return {"order_id": oid}

    def find(self, oid):
        return next(o for o in self.orders if o["order_id"] == oid)

    def fill(self, o, price):
        o.update(status="COMPLETE", filled_quantity=o["quantity"], average_price=price)
        self.trades.append({"trade_id": "T%d" % (len(self.trades) + 1), "order_id": o["order_id"],
                            "tradingsymbol": o["tradingsymbol"], "transaction_type": o["transaction_type"],
                            "quantity": o["quantity"], "average_price": price, "product": "MIS",
                            "fill_timestamp": "2026-10-30 10:09:11"})

    def trigger_stop(self, price=1407.3):
        o = next(o for o in self.orders if o["status"] == "TRIGGER PENDING")
        self.fill(o, price)

    def positions(self):
        net = {}
        for t in self.trades:
            q = t["quantity"] * (1 if t["transaction_type"] == "BUY" else -1)
            net[t["tradingsymbol"]] = net.get(t["tradingsymbol"], 0) + q
        return [{"tradingsymbol": s, "product": "MIS", "quantity": q} for s, q in net.items() if q]


class LiveBase(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.d = tempfile.mkdtemp()
        os.chdir(self.d)
        self.cfg = dict(LIVE)
        self.write("trader/config.json", self.cfg)
        self.kite_ok()
        self.fake = FakeKite()

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def read(self, path):
        with open(path) as fh:
            return json.load(fh)

    def kite_ok(self, date="2026-10-30"):
        self.write("trader/state/kite.json", {"verified": True, "date": date})

    def adapter(self, now=NOW, cfg=None):
        return kite.KiteAdapter(cfg or self.cfg, self.fake, "trader", ist(now))

    def run_cli(self, mod, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = mod.main(argv)
        return code, out.getvalue(), err.getvalue()

    def approve(self, order, now=NOW, reply="yes"):
        """orders.json -> trader-risk -> your yes, the way a real session does it."""
        self.write("trader/orders.json", {"orders": [order]})
        code, out, _ = self.run_cli(check, ["--ledger", "trader/state/live.json", "--now", now])
        code2, out2, err2 = self.run_cli(ticket, ["approve", "--index", "1", "--reply", reply, "--why", "test: planned trade", "--now", now + ":20"])
        return code, code2, out, out2 + err2


class Gates(LiveBase):
    def test_off_in_paper_mode_and_nothing_is_called(self):
        ad = kite.KiteAdapter({"mode": "paper"}, self.fake, "trader", ist(NOW))
        with self.assertRaises(kite.KiteDisabled) as e:
            ad.execute(ENTRY)
        self.assertIn("\"mode\" is not \"live\"", str(e.exception))
        self.assertEqual(self.fake.calls, [])

    def test_off_without_kite_enabled_or_a_check_from_today(self):
        for cfg, word in (({"mode": "live"}, "kite_enabled"), ({"mode": "live", "kite_enabled": False}, "kite_enabled")):
            with self.assertRaises(kite.KiteDisabled) as e:
                kite.KiteAdapter(cfg, self.fake, "trader", ist(NOW)).require_enabled()
            self.assertIn(word, str(e.exception))
        self.kite_ok("2026-10-29")
        with self.assertRaises(kite.KiteDisabled) as e:
            self.adapter().require_enabled()
        self.assertIn("not verified today", str(e.exception))
        self.assertEqual(self.fake.calls, [])

    def test_no_yes_no_order(self):
        self.write("trader/orders.json", {"orders": [ENTRY]})
        self.run_cli(check, ["--ledger", "trader/state/live.json", "--now", NOW])
        with self.assertRaises(kite.KiteError) as e:
            self.adapter().execute(ENTRY)
        self.assertIn("you have not said yes", str(e.exception))
        self.assertNotIn("place_order", [c[0] for c in self.fake.calls])

    def test_risk_must_have_passed_in_live_mode_today(self):
        code, code2, out, _ = self.approve(ENTRY, "2026-10-30 09:20")        # before 09:30: BLOCK
        self.assertEqual(code, 1)
        with self.assertRaises(kite.KiteError) as e:
            self.adapter("2026-10-30 09:20").execute(ENTRY)
        self.assertIn("did not pass", str(e.exception))

    def test_one_yes_one_order(self):
        self.approve(ENTRY)
        oid = self.adapter().execute(ENTRY)
        self.assertTrue(oid.startswith("261030"))
        with self.assertRaises(kite.KiteError) as e:
            self.adapter().execute(ENTRY)
        self.assertIn("already placed", str(e.exception))
        self.assertEqual(sum(1 for c in self.fake.calls if c[0] == "place_order"), 1)

    def test_a_call_that_differs_from_the_approved_one_needs_a_new_yes(self):
        self.approve(ENTRY)
        state = self.read("trader/state/broker.json")
        state["orders"][-1]["payload_hash"] = "0123456789abcdef"      # as if the config changed after the yes
        self.write("trader/state/broker.json", state)
        with self.assertRaises(kite.KiteError) as e:
            self.adapter().execute(ENTRY)
        self.assertIn("differs from the one you approved", str(e.exception))
        self.assertNotIn("place_order", [c[0] for c in self.fake.calls])

    def test_a_retry_cannot_double_a_position(self):
        self.approve(ENTRY)
        call = kite.build_call(ENTRY, self.cfg)
        self.fake.orders.append({"order_id": "1", "tag": call["tag"], "status": "COMPLETE"})
        with self.assertRaises(kite.KiteError) as e:
            self.adapter().execute(ENTRY)
        self.assertIn("already at Kite", str(e.exception))
        self.assertNotIn("place_order", [c[0] for c in self.fake.calls])

    def test_a_kite_error_is_logged_and_uses_the_yes_up(self):
        self.approve(ENTRY)
        self.fake.refuse = "InputException: insufficient funds"
        with self.assertRaises(kite.KiteError) as e:
            self.adapter().execute(ENTRY)
        self.assertIn("The yes is used up", str(e.exception))
        rec = self.read("trader/state/broker.json")["orders"][-1]
        self.assertEqual((rec["status"], rec["mode"]), ("failed", "live"))
        self.fake.refuse = None
        with self.assertRaises(kite.KiteError):
            self.adapter().execute(ENTRY)                 # no retry without a new check and a new yes

    def test_a_yes_given_in_paper_mode_is_no_use_live(self):
        self.write("trader/config.json", {"mode": "paper"})
        self.write("trader/orders.json", {"orders": [ENTRY]})
        self.run_cli(check, ["--now", NOW])
        self.run_cli(ticket, ["approve", "--index", "1", "--reply", "yes", "--why", "test", "--now", NOW + ":20"])
        self.write("trader/config.json", self.cfg)
        self.run_cli(check, ["--ledger", "trader/state/live.json", "--now", NOW])
        with self.assertRaises(kite.KiteError) as e:
            self.adapter().execute(ENTRY)
        self.assertIn("given in paper mode, not live", str(e.exception))


class CallsAreBuiltFromTheApprovedFields(unittest.TestCase):
    def test_entry_market_and_limit(self):
        c = kite.build_call(ENTRY, LIVE)
        self.assertEqual(c["tool"], "place_order")
        a = c["arguments"]
        self.assertEqual((a["variety"], a["exchange"], a["tradingsymbol"], a["transaction_type"], a["quantity"],
                          a["product"], a["order_type"], a["market_protection"], a["validity"]),
                         ("regular", "NSE", "SYNTHA", "BUY", 45, "MIS", "MARKET", -1, "DAY"))
        self.assertTrue(a["tag"].startswith("IDT") and len(a["tag"]) <= 20 and a["tag"].isalnum())
        lim = size.order_from(size.size_trade(PLAN, {}, limit=1416.0, today="2026-10-30"), {})
        a = kite.build_call(lim, LIVE)["arguments"]
        self.assertEqual((a["order_type"], a["price"]), ("LIMIT", 1416.0))
        self.assertNotIn("market_protection", a)

    def test_the_embedded_kite_block_is_ignored(self):
        tampered = copy.deepcopy(ENTRY)
        tampered["kite"]["entry"]["quantity"] = 4500
        self.assertEqual(kite.build_call(tampered, LIVE)["arguments"]["quantity"], 45)
        self.assertEqual(kite.fingerprint(tampered), kite.fingerprint(ENTRY))

    def test_stop_exit_and_cancel(self):
        stop = {"symbol": "SYNTHA", "exchange": "NSE", "side": "sell", "qty": 45, "product": "MIS",
                "entry_type": "stop", "stop": 1408.0}
        a = kite.build_call(stop, LIVE)["arguments"]
        self.assertEqual((a["transaction_type"], a["order_type"], a["trigger_price"], a["market_protection"]),
                         ("SELL", "SL-M", 1408.0, -1))
        a = kite.build_call(stop, dict(LIVE, stop_order_type="SL", stop_limit_buffer_pct=0.5))["arguments"]
        self.assertEqual((a["order_type"], a["trigger_price"], a["price"]), ("SL", 1408.0, 1400.9))
        a = kite.build_call(dict(stop, entry_type="market", stop=None), LIVE)["arguments"]
        self.assertEqual((a["transaction_type"], a["order_type"]), ("SELL", "MARKET"))
        c = kite.build_call({"symbol": "SYNTHA", "side": "sell", "qty": 45, "action": "cancel", "order_ref": "99"}, LIVE)
        self.assertEqual((c["tool"], c["arguments"]), ("cancel_order", {"variety": "regular", "order_id": "99"}))

    def test_refuses_what_it_cannot_build(self):
        for bad in (dict(ENTRY, exchange="BSE"), dict(ENTRY, product="CNC"), dict(ENTRY, qty=0),
                    dict(ENTRY, entry_type="iceberg"), dict(ENTRY, side="buy", entry_type="stop")):
            with self.assertRaises(kite.KiteError):
                kite.build_call(bad, LIVE)


class WholeLiveTrade(LiveBase):
    def open_trade(self):
        self.approve(ENTRY)
        oid = self.adapter().execute(ENTRY)
        self.write("trader/state/risk.json", self.read("trader/state/risk.json"))
        return oid

    def test_entry_fill_stop_ticket_stop_and_close(self):
        self.open_trade()
        led = self.read("trader/state/live.json")
        self.assertEqual((led["trades"][0]["trade_id"], led["trades"][0]["status"]), ("LT-20261030-01", "pending_fill"))
        msgs = self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}))
        self.assertTrue(any("entry filled, 45 shares at 1416.70" in m for m in msgs))
        self.assertTrue(any(m.startswith("WARNING") and "NO stop-loss" in m for m in msgs))
        # the stop is a separate ticket with its own risk check and its own yes
        code, out, _ = self.run_cli(kite, ["stop-ticket", "--trade", "LT-20261030-01", "--now", NOW])
        self.assertEqual(code, 0)
        stop = self.read("trader/orders.json")["orders"][0]
        self.assertEqual((stop["side"], stop["entry_type"], stop["stop"], stop["qty"]), ("sell", "stop", 1408.0, 45))
        code, code2, out, out2 = self.approve(stop, "2026-10-30 09:53")
        self.assertEqual((code, code2), (0, 0), out + out2)
        self.assertIn("SL-M", json.dumps(kite.build_call(stop, self.cfg)))
        stop_id = self.adapter("2026-10-30 09:53").execute(stop)
        led = self.read("trader/state/live.json")
        self.assertEqual(led["trades"][0]["stop_order_id"], stop_id)
        self.assertEqual(self.fake.find(stop_id)["status"], "TRIGGER PENDING")
        msgs = self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}),
                                   {"net": self.fake.positions()})
        self.assertFalse(any(m.startswith("WARNING") for m in msgs), msgs)
        # the stop is hit
        self.fake.trigger_stop(1407.3)
        msgs = self.adapter("2026-10-30 10:10").sync(self.fake("get_orders", {}), self.fake("get_trades", {}),
                                                    {"net": self.fake.positions()})
        led = self.read("trader/state/live.json")
        t = led["trades"][0]
        self.assertEqual((t["status"], t["exit_reason"], t["exit_fill"]), ("closed", "stop", 1407.3))
        self.assertEqual(t["gross_pnl"], round(45 * (1407.3 - 1416.7), 2))
        c = kite.chg.charges(45 * 1416.7, 45 * 1407.3)
        self.assertEqual((t["charges"], t["net_pnl"]), (c["total"], round(t["gross_pnl"] - c["total"], 2)))
        self.assertEqual(led["balance"], round(100000 + t["net_pnl"], 2))
        self.assertTrue(any("closed (stop)" in m for m in msgs))

    def test_cancel_the_stop_then_exit_each_on_its_own_yes(self):
        self.open_trade()
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}))
        self.run_cli(kite, ["stop-ticket", "--trade", "LT-20261030-01", "--now", NOW])
        stop = self.read("trader/orders.json")["orders"][0]
        self.approve(stop, "2026-10-30 09:53")
        stop_id = self.adapter("2026-10-30 09:53").execute(stop)
        cancel = {"symbol": "SYNTHA", "exchange": "NSE", "side": "sell", "qty": 45, "product": "MIS",
                  "entry_type": "market", "action": "cancel", "order_ref": stop_id}
        code, code2, out, out2 = self.approve(cancel, "2026-10-30 14:58")
        self.assertEqual((code, code2), (0, 0), out + out2)
        self.adapter("2026-10-30 14:58").execute(cancel)
        self.assertEqual(self.fake.find(stop_id)["status"], "CANCELLED")
        sell = {"symbol": "SYNTHA", "exchange": "NSE", "side": "sell", "qty": 45, "product": "MIS",
                "entry_type": "market", "plan": {"reason": "flat-by"}}
        self.approve(sell, "2026-10-30 14:59")
        self.adapter("2026-10-30 14:59").execute(sell)
        self.fake.trades[-1]["fill_timestamp"] = "2026-10-30 14:59:30"
        msgs = self.adapter("2026-10-30 15:01").sync(self.fake("get_orders", {}), self.fake("get_trades", {}),
                                                    {"net": self.fake.positions()})
        t = self.read("trader/state/live.json")["trades"][0]
        self.assertEqual((t["status"], t["exit_reason"], t["exit_fill"]), ("closed", "flat-by", 1424.0))
        self.assertGreater(t["net_pnl"], 0)
        self.assertEqual(len([c for c in self.fake.calls if c[0] == "place_order"]), 3)

    def test_a_stop_approved_as_sl_m_is_refused_if_the_config_turns_it_into_sl(self):
        self.open_trade()
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}))
        self.run_cli(kite, ["stop-ticket", "--trade", "LT-20261030-01", "--now", NOW])
        stop = self.read("trader/orders.json")["orders"][0]
        self.approve(stop, "2026-10-30 09:53")
        with self.assertRaises(kite.KiteError) as e:
            self.adapter("2026-10-30 09:53", dict(self.cfg, stop_order_type="SL")).execute(stop)
        self.assertIn("differs from the one you approved", str(e.exception))

    def test_a_placed_but_unfilled_entry_already_counts_as_an_open_position(self):
        self.open_trade()                                    # placed, not yet synced as filled
        other = dict(ENTRY, symbol="SYNTHD", qty=10, stop=3190.0, target=3250.0)
        self.write("trader/orders.json", {"orders": [other]})
        code, out, _ = self.run_cli(check, ["--ledger", "trader/state/live.json", "--now", "2026-10-30 10:00"])
        self.assertEqual(code, 1)
        self.assertIn("open: 1 position already open", out)

    def test_each_of_the_three_orders_needed_its_own_yes(self):
        self.open_trade()
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}))
        sell = {"symbol": "SYNTHA", "exchange": "NSE", "side": "sell", "qty": 45, "product": "MIS",
                "entry_type": "market"}
        self.write("trader/orders.json", {"orders": [sell]})
        self.run_cli(check, ["--ledger", "trader/state/live.json", "--now", NOW])
        with self.assertRaises(kite.KiteError) as e:
            self.adapter().execute(sell)
        self.assertIn("you have not said yes", str(e.exception))

    def test_sync_warns_when_the_stop_is_gone_or_positions_disagree(self):
        self.open_trade()
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}))
        led = self.read("trader/state/live.json")
        led["trades"][0]["stop_order_id"] = "555"
        self.write("trader/state/live.json", led)
        msgs = self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}), {"net": []})
        self.assertTrue(any("not in Kite's order list" in m for m in msgs))
        self.assertTrue(any("no MIS position" in m for m in msgs))
        self.fake.orders.append({"order_id": "555", "status": "REJECTED", "status_message": "RMS: margin"})
        msgs = self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}),
                                   {"net": [{"tradingsymbol": "SYNTHB", "product": "MIS", "quantity": 10}]})
        self.assertTrue(any("UNPROTECTED" in m for m in msgs))
        self.assertTrue(any("Kite shows 10 SYNTHB" in m for m in msgs))

    def test_a_rejected_entry_leaves_no_position(self):
        self.open_trade()
        self.fake.orders[0].update(status="REJECTED", status_message="insufficient funds", average_price=0)
        msgs = self.adapter().sync(self.fake("get_orders", {}), [])
        self.assertTrue(any("was rejected" in m and "No position" in m for m in msgs))
        self.assertEqual(self.read("trader/state/live.json")["trades"][0]["status"], "failed")

    def test_the_stop_ticket_needs_a_filled_entry_and_only_one_stop(self):
        self.open_trade()
        code, _, err = self.run_cli(kite, ["stop-ticket", "--trade", "LT-20261030-01", "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("has not filled yet", err)
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}))
        self.assertEqual(self.run_cli(kite, ["stop-ticket", "--trade", "LT-20261030-01", "--now", NOW])[0], 0)


class Cli(LiveBase):
    def test_check_writes_todays_state_only_when_live_and_enabled(self):
        self.write("trader/profile.json", {"data": {"user_id": "AB1234", "user_name": "X"}})
        os.remove("trader/state/kite.json")
        self.write("trader/margins.json", {"equity": {"available": {"live_balance": 100000.0}}})
        self.write("trader/positions.json", {"net": [], "day": []})
        base = ["check", "--profile", "trader/profile.json", "--margins", "trader/margins.json", "--positions",
                "trader/positions.json", "--now", NOW]
        code, _, err = self.run_cli(kite, base)
        self.assertEqual(code, 1)                                   # nobody confirmed the "Needs approval" setting
        self.assertIn("Needs approval", err)
        self.assertFalse(os.path.exists("trader/state/kite.json"))
        code, out, _ = self.run_cli(kite, base + ["--tools-need-approval"])
        self.assertEqual(code, 0)
        self.assertIn("expires every day", out)
        st = self.read("trader/state/kite.json")
        self.assertEqual((st["verified"], st["date"]), (True, "2026-10-30"))
        self.assertNotIn("AB1234", json.dumps(st))                 # no user details are kept
        self.write("trader/config.json", {"mode": "paper"})
        code, _, err = self.run_cli(kite, base + ["--tools-need-approval"])
        self.assertEqual(code, 1)
        self.assertIn("\"mode\" must be \"live\"", err)
        self.write("trader/config.json", dict(LIVE))
        self.write("trader/profile.json", {"error": "token expired"})
        code, _, err = self.run_cli(kite, base + ["--tools-need-approval"])
        self.assertIn("does not look like a Kite profile", err)

    def test_prepare_and_record(self):
        self.approve(ENTRY)
        self.write("trader/open_orders.json", [])
        self.write("trader/ltp.json", {"NSE:SYNTHA": {"last_price": 1416.2}})
        self.write("trader/margins.json", {"equity": {"available": {"live_balance": 100000.0}}})
        extra = ["--ltp", "trader/ltp.json", "--margins", "trader/margins.json"]
        code, out, _ = self.run_cli(kite, ["prepare", "--index", "1", "--open-orders", "trader/open_orders.json",
                                           "--now", NOW])
        self.assertEqual(code, 1)                                   # an entry is never placed without Kite's own price
        code, out, _ = self.run_cli(kite, ["prepare", "--index", "1", "--open-orders", "trader/open_orders.json",
                                           "--now", NOW] + extra)
        self.assertEqual(code, 0)
        self.assertIn('"tool": "place_order"', out)
        self.assertIn("Make exactly this one Kite tool call", out)
        code, out, _ = self.run_cli(kite, ["record", "--index", "1", "--order-id", "2610300000001", "--now", NOW])
        self.assertEqual(code, 0)
        self.assertIn("The yes is used up", out)
        code, _, err = self.run_cli(kite, ["prepare", "--index", "1", "--open-orders", "trader/open_orders.json",
                                           "--now", NOW] + extra)
        self.assertEqual(code, 1)
        self.assertIn("already placed", err)

    def test_off_means_exit_code_2_and_no_work(self):
        self.write("trader/config.json", {"mode": "paper"})
        self.write("trader/open_orders.json", [])
        self.write("trader/orders.json", {"orders": [ENTRY]})
        code, _, err = self.run_cli(kite, ["prepare", "--index", "1", "--open-orders", "trader/open_orders.json",
                                           "--now", NOW])
        self.assertEqual(code, 2)
        self.assertIn("OFF", err)
        code, out, _ = self.run_cli(kite, ["status", "--now", NOW])
        self.assertIn("Kite adapter: OFF", out)

    def test_ticket_py_result_points_at_kite_record(self):
        self.approve(ENTRY)
        code, _, err = self.run_cli(ticket, ["result", "--index", "1", "--status", "placed", "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("kite.py record", err)

    def test_the_live_ticket_shows_the_adapters_call_and_the_approval_stores_its_hash(self):
        self.approve(ENTRY)
        code, out, _ = self.run_cli(ticket, ["show", "--now", NOW])
        self.assertIn("LIVE ACCOUNT, REAL MONEY", out)
        rec = self.read("trader/state/broker.json")["orders"][-1]
        self.assertEqual(rec["payload_hash"], kite.payload_hash(kite.build_call(ENTRY, self.cfg)))
        self.write("trader/orders.json", {"orders": [ENTRY]})
        code, out, _ = self.run_cli(ticket, ["show", "--now", NOW])
        self.assertIn("Claude will call the Kite place_order tool with exactly", out)
        self.assertIn('"tradingsymbol": "SYNTHA"', out)


class NoKeysNoNetwork(unittest.TestCase):
    def test_kite_py_opens_no_connection_and_holds_no_secret(self):
        with open(os.path.join(ROOT, "trader-broker", "kite.py"), encoding="utf-8") as fh:
            text = fh.read()
        for bad in ("urllib", "http.client", "socket", "requests", "api_key", "api_secret", "access_token",
                    "KITE_API", "Authorization", "environ", "getpass"):
            self.assertNotIn(bad, text, bad)


if __name__ == "__main__":
    unittest.main()
