import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import ROOT, ist, load

fyers = load("trader-broker", "fyers")
kite = load("trader-broker", "kite")
ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")
size = load("trader-budget", "size")
control = load("trader-control", "control")
db = load("trader-db", "db")

PLAN = {"symbol": "SYNTHA", "side": "buy", "date": "2026-10-30", "setup": "orb", "entry": 1416.0,
        "entry_time": "09:50", "stop": 1408.0, "target": 1440.0, "tick": 0.1, "flat_by": "15:00"}
FYERS = {"mode": "live", "broker": "fyers", "fyers_enabled": True, "protection_data_required": False, "live_start_weeks": 0}
NOW = "2026-10-30 09:51"
ENTRY = size.order_from(size.size_trade(PLAN, {}, today="2026-10-30"), {})


class FakeFyers(object):
    """A stand-in for FYERS's MCP tools. Market buys fill at once; the stop rests until trigger_stop(). Field names are the
    guessed ones the adapter reads (VERIFY.md section 16)."""

    def __init__(self, buy=1416.7, sell=1424.0):
        self.orders, self.trades, self.calls, self.n = [], [], [], 0
        self.buy, self.sell, self.refuse = buy, sell, None
        self.ltp, self.cash = 1416.2, 100000.0

    def __call__(self, tool, args):
        self.calls.append((tool, copy.deepcopy(args)))
        if tool == "get_orders":
            return {"s": "ok", "orderBook": copy.deepcopy(self.orders)}
        if tool == "get_trades":
            return {"s": "ok", "tradeBook": copy.deepcopy(self.trades)}
        if tool == "get_positions":
            return {"s": "ok", "netPositions": self.positions()}
        if tool == "get_quotes":
            return {"s": "ok", "d": [{"n": args["symbols"], "s": "ok", "v": {"lp": self.ltp}}]}
        if tool == "get_funds":
            return {"s": "ok", "fund_limit": [{"id": 1, "title": "Total Balance", "equityAmount": 5},
                                              {"id": 10, "title": "Available Balance", "equityAmount": self.cash}]}
        if tool == "cancel_order":
            o = next(o for o in self.orders if o["id"] == args["id"])
            if o["status"] == 2:
                raise RuntimeError("cannot cancel a completed order")
            o["status"] = 1
            return {"s": "ok", "code": 1103, "id": o["id"]}
        if tool != "place_order":
            raise AssertionError("unexpected tool " + tool)
        if self.refuse:
            raise RuntimeError(self.refuse)
        self.n += 1
        oid = "5210%07d" % self.n
        o = {"id": oid, "symbol": args["symbol"], "side": args["side"], "qty": args["qty"], "filledQty": 0, "tradedPrice": 0,
             "productType": "INTRADAY", "orderTag": "1:" + args["orderTag"], "status": 6, "type": args["type"],
             "orderDateTime": "30-Oct-2026 09:52:01"}
        if args["type"] in (1, 2):
            self.fill(o, self.buy if args["side"] == 1 else self.sell)
        self.orders.append(o)
        return {"s": "ok", "code": 1101, "message": "Order submitted", "id": oid}

    def fill(self, o, price):
        o.update(status=2, filledQty=o["qty"], tradedPrice=price)
        self.trades.append({"tradeNumber": "T%d" % (len(self.trades) + 1), "orderNumber": o["id"], "symbol": o["symbol"],
                            "side": o["side"], "tradedQty": o["qty"], "tradePrice": price, "productType": "INTRADAY",
                            "orderDateTime": "30-Oct-2026 10:09:11"})

    def trigger_stop(self, price=1407.3):
        o = next(o for o in self.orders if o["status"] == 6)
        self.fill(o, price)

    def positions(self):
        net = {}
        for t in self.trades:
            net[t["symbol"]] = net.get(t["symbol"], 0) + t["tradedQty"] * t["side"]
        return [{"symbol": s, "productType": "INTRADAY", "netQty": q} for s, q in net.items() if q]


class Base(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.d = tempfile.mkdtemp()
        os.chdir(self.d)
        self.cfg = dict(FYERS)
        self.write("trader/config.json", self.cfg)
        self.verified()
        self.fake = FakeFyers()

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

    def verified(self, date="2026-10-30"):
        self.write("trader/state/fyers.json", {"verified": True, "date": date})

    def adapter(self, now=NOW, cfg=None):
        return fyers.FyersAdapter(cfg or self.cfg, self.fake, "trader", ist(now))

    def run_cli(self, mod, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = mod.main(argv)
        return code, out.getvalue(), err.getvalue()

    def approve(self, order, now=NOW, reply="yes"):
        self.write("trader/orders.json", {"orders": [order]})
        code, _, _ = self.run_cli(check, ["--ledger", "trader/state/live.json", "--now", now])
        code2, out2, err2 = self.run_cli(ticket, ["approve", "--index", "1", "--reply", reply, "--why", "test: planned trade",
                                                  "--now", now + ":20"])
        return code, code2, out2 + err2


class TheCall(Base):
    def test_symbols_and_the_entry(self):
        self.assertEqual(fyers.fy_symbol("sbin"), "NSE:SBIN-EQ")
        self.assertEqual(fyers.fy_symbol("M&M"), "NSE:M&M-EQ")
        self.assertEqual(fyers.fy_symbol("BAJAJ-AUTO"), "NSE:BAJAJ-AUTO-EQ")
        self.assertEqual(fyers.plain_symbol("NSE:BAJAJ-AUTO-EQ"), "BAJAJ-AUTO")
        call = fyers.build_call(ENTRY, self.cfg)
        a = call["arguments"]
        self.assertEqual(call["tool"], "place_order")
        self.assertEqual((a["symbol"], a["qty"], a["side"], a["type"], a["productType"], a["validity"]),
                         ("NSE:SYNTHA-EQ", 45, 1, 2, "INTRADAY", "DAY"))
        self.assertEqual(a["orderTag"], call["tag"])
        self.assertTrue(call["tag"].startswith("IDT") and len(call["tag"]) <= 20)
        self.assertNotIn("BO", json.dumps(call))                                    # no bracket or cover product

    def test_limit_stop_sell_and_cancel(self):
        lim = size.order_from(size.size_trade(PLAN, {}, limit=1416.0, today="2026-10-30"), {})
        a = fyers.build_call(lim, self.cfg)["arguments"]
        self.assertEqual((a["type"], a["limitPrice"]), (1, 1416.0))
        stop = {"symbol": "SYNTHA", "exchange": "NSE", "side": "sell", "qty": 45, "product": "MIS", "entry_type": "stop", "stop": 1408.0}
        a = fyers.build_call(stop, self.cfg)["arguments"]
        self.assertEqual((a["type"], a["side"], a["stopPrice"], a["limitPrice"]), (3, -1, 1408.0, 0))
        a = fyers.build_call(stop, dict(self.cfg, stop_order_type="SL"))["arguments"]
        self.assertEqual((a["type"], a["stopPrice"], a["limitPrice"]), (4, 1408.0, 1400.9))
        sell = dict(stop, entry_type="market")
        sell.pop("stop")
        a = fyers.build_call(sell, self.cfg)["arguments"]
        self.assertEqual((a["type"], a["side"]), (2, -1))
        c = fyers.build_call(dict(sell, action="cancel", order_ref="52100000001"), self.cfg)
        self.assertEqual((c["tool"], c["arguments"]), ("cancel_order", {"id": "52100000001"}))
        with self.assertRaises(fyers.FyersError):
            fyers.build_call(dict(sell, action="cancel"), self.cfg)

    def test_only_nse_mis_and_sane_orders(self):
        for bad in (dict(ENTRY, product="NRML"), dict(ENTRY, exchange="BSE"), dict(ENTRY, qty=0), dict(ENTRY, side="hold")):
            with self.assertRaises(fyers.FyersError):
                fyers.build_call(bad, self.cfg)
        with self.assertRaises(fyers.FyersError):
            fyers.build_call(dict(ENTRY, entry_type="limit", limit_price=None), self.cfg)

    def test_tool_names_can_be_set_in_the_config(self):
        cfg = dict(self.cfg, fyers_tools={"place": "send_order", "bogus": "x"})
        self.assertEqual(fyers.build_call(ENTRY, cfg)["tool"], "send_order")
        self.assertNotIn("bogus", fyers.tools_for(cfg))


class TheGates(Base):
    def test_off_unless_every_switch_is_yours(self):
        cases = [({"mode": "paper"}, "\"mode\" is not \"live\""),
                 ({"mode": "live", "broker": "dhan", "fyers_enabled": True}, "\"broker\" is not \"fyers\""),
                 ({"mode": "live", "broker": "fyers"}, "\"fyers_enabled\" is not true")]
        for cfg, word in cases:
            with self.assertRaises(fyers.FyersDisabled) as e:
                fyers.FyersAdapter(cfg, self.fake, "trader", ist(NOW)).require_enabled()
            self.assertIn(word, str(e.exception))
        self.verified("2026-10-29")
        with self.assertRaises(fyers.FyersDisabled) as e:
            self.adapter().require_enabled()
        self.assertIn("not verified today", str(e.exception))
        self.assertEqual(self.fake.calls, [])

    def test_one_yes_one_order_exactly_as_approved(self):
        code, code2, out = self.approve(ENTRY)
        self.assertEqual((code, code2), (0, 0), out)
        self.assertIn("fyers.py prepare", out)
        oid = self.adapter().execute(ENTRY)
        self.assertTrue(oid.startswith("5210"))
        with self.assertRaises(fyers.FyersError) as e:
            self.adapter().execute(ENTRY)
        self.assertIn("already placed", str(e.exception))
        sent = [c for c in self.fake.calls if c[0] == "place_order"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], fyers.build_call(ENTRY, self.cfg)["arguments"])

    def test_no_yes_no_order_and_a_refusal_uses_the_yes_up(self):
        self.write("trader/orders.json", {"orders": [ENTRY]})
        self.run_cli(check, ["--ledger", "trader/state/live.json", "--now", NOW])
        with self.assertRaises(fyers.FyersError) as e:
            self.adapter().execute(ENTRY)
        self.assertIn("you have not said yes", str(e.exception))
        self.approve(ENTRY)
        self.fake.refuse = "Insufficient funds"
        with self.assertRaises(fyers.FyersError) as e:
            self.adapter().execute(ENTRY)
        self.assertIn("FYERS refused", str(e.exception))
        with self.assertRaises(fyers.FyersError):
            self.adapter().execute(ENTRY)
        self.assertEqual(sum(1 for c in self.fake.calls if c[0] == "place_order"), 1)

    def test_the_duplicate_guard_reads_the_tag_fyers_returns(self):
        self.approve(ENTRY)
        tag = fyers.build_call(ENTRY, self.cfg)["tag"]
        with self.assertRaises(fyers.FyersError) as e:
            self.adapter().prepare(ENTRY, {"s": "ok", "orderBook": [{"id": "9", "orderTag": "1:" + tag}]},
                                   {"s": "ok", "d": [{"n": "NSE:SYNTHA-EQ", "v": {"lp": 1416.0}}]},
                                   {"fund_limit": [{"title": "Available Balance", "equityAmount": 1e5}]})
        self.assertIn("already at FYERS", str(e.exception))

    def test_price_and_cash(self):
        self.approve(ENTRY)
        ltp = lambda p: {"s": "ok", "d": [{"n": "NSE:SYNTHA-EQ", "s": "ok", "v": {"lp": p}}]}
        cash = lambda c: {"fund_limit": [{"title": "Available Balance", "equityAmount": c}]}
        with self.assertRaises(fyers.FyersError) as e:
            self.adapter().prepare(ENTRY, [], None, cash(1e5))
        self.assertIn("get_ltp", str(e.exception))
        with self.assertRaises(fyers.FyersError) as e:
            self.adapter().prepare(ENTRY, [], ltp(1421.0), cash(1e5))
        self.assertIn("+0.35%", str(e.exception))
        self.assertEqual(self.read("trader/state/broker.json")["orders"][-1]["status"], "voided")
        self.approve(ENTRY, "2026-10-30 09:52")
        with self.assertRaises(fyers.FyersError) as e:
            self.adapter().prepare(ENTRY, [], ltp(1416.0), cash(50000.0))
        self.assertIn("leverage is off", str(e.exception))
        self.assertEqual(self.adapter().prepare(ENTRY, [], ltp(1416.0), cash(1e5))["tool"], "place_order")

    def test_readers(self):
        ad = self.adapter()
        self.assertEqual(ad.price_of({"s": "ok", "d": [{"n": "NSE:SYNTHA-EQ", "v": {"lp": 5.5}}]}, "SYNTHA"), 5.5)
        self.assertIsNone(ad.price_of({"s": "ok", "d": []}, "SYNTHA"))
        self.assertEqual(ad.cash_of({"fund_limit": [{"title": "Total Balance", "equityAmount": 9},
                                                    {"title": "Available Balance", "equityAmount": 12.5}]}), 12.5)
        self.assertIsNone(ad.cash_of({}))
        self.assertEqual(ad.order_id_from({"s": "ok", "id": "77"}), "77")
        self.assertIsNone(ad.order_id_from({"s": "error", "message": "no"}))
        self.assertEqual(ad.unwrap({"s": "ok", "orderBook": [1]}, "orders"), [1])
        self.assertEqual(ad.unwrap({"data": {"netPositions": [2]}}, "positions"), [2])
        self.assertEqual(ad.unwrap(None, "trades"), [])


class WholeTrade(Base):
    def open_trade(self):
        self.approve(ENTRY)
        return self.adapter().execute(ENTRY)

    def test_entry_stop_ticket_stop_and_close(self):
        self.open_trade()
        msgs = self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}), self.fake("get_positions", {}))
        self.assertTrue(any("entry filled, 45 shares at 1416.70" in m for m in msgs), msgs)
        self.assertTrue(any(m.startswith("WARNING") and "NO stop-loss" in m for m in msgs), msgs)    # separate stop: not yet placed
        code, out, _ = self.run_cli(fyers, ["stop-ticket", "--trade", "LT-20261030-01", "--now", NOW])
        self.assertEqual(code, 0, out)
        stop = self.read("trader/orders.json")["orders"][0]
        self.assertEqual((stop["side"], stop["entry_type"], stop["stop"]), ("sell", "stop", 1408.0))
        code, code2, out = self.approve(stop, "2026-10-30 09:53")
        self.assertEqual((code, code2), (0, 0), out)
        stop_id = self.adapter("2026-10-30 09:53").execute(stop)
        self.assertEqual(self.read("trader/state/live.json")["trades"][0]["stop_order_id"], stop_id)
        msgs = self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}), self.fake("get_positions", {}))
        self.assertFalse(any(m.startswith("WARNING") for m in msgs), msgs)
        self.fake.trigger_stop(1407.3)
        self.adapter("2026-10-30 10:10").sync(self.fake("get_orders", {}), self.fake("get_trades", {}), self.fake("get_positions", {}))
        t = self.read("trader/state/live.json")["trades"][0]
        self.assertEqual((t["status"], t["exit_reason"], t["exit_fill"]), ("closed", "stop", 1407.3))
        c = kite.chg.charges(45 * 1416.7, 45 * 1407.3)
        self.assertEqual((t["charges"], t["net_pnl"]), (c["total"], round(45 * (1407.3 - 1416.7) - c["total"], 2)))

    def test_a_rejected_entry_leaves_no_position(self):
        self.open_trade()
        self.fake.orders[0]["status"] = 5
        self.fake.orders[0]["message"] = "RMS: margin shortfall"
        self.fake.trades.clear()
        msgs = self.adapter().sync(self.fake("get_orders", {}), [], None)
        self.assertTrue(any("rejected" in m and "margin shortfall" in m for m in msgs), msgs)
        self.assertEqual(self.read("trader/state/live.json")["trades"][0]["status"], "failed")

    def test_cancel_the_stop_then_exit_each_on_its_own_yes(self):
        self.open_trade()
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}), None)
        self.run_cli(fyers, ["stop-ticket", "--trade", "LT-20261030-01", "--now", NOW])
        stop = self.read("trader/orders.json")["orders"][0]
        self.approve(stop, "2026-10-30 09:53")
        stop_id = self.adapter("2026-10-30 09:53").execute(stop)
        cancel = {"symbol": "SYNTHA", "exchange": "NSE", "side": "sell", "qty": 45, "product": "MIS", "entry_type": "market",
                  "action": "cancel", "order_ref": stop_id}
        code, code2, out = self.approve(cancel, "2026-10-30 14:58")
        self.assertEqual((code, code2), (0, 0), out)
        self.adapter("2026-10-30 14:58").execute(cancel)
        self.assertEqual(self.fake.orders[1]["status"], 1)
        sell = {"symbol": "SYNTHA", "exchange": "NSE", "side": "sell", "qty": 45, "product": "MIS", "entry_type": "market"}
        code, code2, out = self.approve(sell, "2026-10-30 14:59")
        self.assertEqual((code, code2), (0, 0), out)
        self.adapter("2026-10-30 14:59").execute(sell)
        self.adapter("2026-10-30 15:05").sync(self.fake("get_orders", {}), self.fake("get_trades", {}), None)
        self.assertEqual(self.read("trader/state/live.json")["trades"][0]["status"], "closed")

    def test_reconcile(self):
        self.open_trade()
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}), None)
        ad = self.adapter("2026-10-30 15:40")
        bal = self.read("trader/state/live.json")["balance"]
        msgs, diffs = ad.reconcile(self.fake("get_orders", {}), self.fake("get_trades", {}), self.fake("get_positions", {}),
                                   {"fund_limit": [{"title": "Available Balance", "equityAmount": bal}]})
        self.assertEqual([d for d in diffs if d[0] != "closed trade not at broker"], [], msgs)
        stray = {"orderNumber": "999", "symbol": "NSE:SYNTHZ-EQ", "side": 1, "tradedQty": 5, "tradePrice": 100.0, "productType": "INTRADAY"}
        msgs, diffs = ad.reconcile(self.fake("get_orders", {}), {"tradeBook": self.fake.trades + [stray]}, self.fake("get_positions", {}),
                                   {"fund_limit": [{"title": "Available Balance", "equityAmount": 120000.0}]})
        kinds = [d[0] for d in diffs]
        self.assertIn("trade not in the ledger", kinds)
        self.assertIn("balance", kinds)


class TheCheckAndRouting(Base):
    def base(self, positions=None):
        self.write("trader/profile.json", {"s": "ok", "data": {"name": "x"}})
        self.write("trader/funds.json", {"fund_limit": [{"title": "Available Balance", "equityAmount": 100000.0}]})
        self.write("trader/positions.json", {"s": "ok", "netPositions": positions or []})
        return ["check", "--profile", "trader/profile.json", "--margins", "trader/funds.json", "--positions",
                "trader/positions.json", "--tools-need-approval", "--static-ip-confirmed", "--now", NOW,
                "--out", "trader/state/fyers2.json"]

    def test_the_check_needs_your_two_confirmations_and_a_clean_account(self):
        argv = self.base()
        for flag, word in (("--tools-need-approval", "Needs approval"), ("--static-ip-confirmed", "static IP")):
            code, _, err = self.run_cli(fyers, [x for x in argv if x != flag])
            self.assertEqual(code, 1)
            self.assertIn(word, err)
        self.assertFalse(os.path.exists("trader/state/fyers2.json"))
        code, out, err = self.run_cli(fyers, argv)
        self.assertEqual(code, 0, err)
        self.assertIn("FYERS verified for 2026-10-30", out)
        self.assertEqual(self.read("trader/state/fyers2.json")["broker"], "fyers")
        code, _, err = self.run_cli(fyers, self.base([{"symbol": "NSE:SYNTHZ-EQ", "productType": "INTRADAY", "netQty": 10}]))
        self.assertEqual(code, 1)
        self.assertIn("unexpected position", err)
        self.write("trader/config.json", dict(FYERS, broker="dhan"))
        code, _, err = self.run_cli(fyers, self.base())
        self.assertIn("\"broker\" must be \"fyers\"", err)

    def test_the_ticket_and_the_risk_check_follow_the_broker(self):
        self.assertEqual(ticket.broker_of({"broker": "fyers"}), "fyers")
        self.assertEqual(ticket.broker_of({"broker": "nonsense"}), "kite")
        self.approve(ENTRY)
        rec = self.read("trader/state/broker.json")["orders"][-1]
        self.assertEqual(rec["payload_hash"], fyers.payload_hash(fyers.build_call(ENTRY, self.cfg)))
        code, out, _ = self.run_cli(ticket, ["show", "--now", NOW])
        self.assertIn("LIVE ACCOUNT, REAL MONEY, FYERS", out)
        self.assertIn("Claude will call the FYERS place_order tool", out)
        self.verified("2026-10-29")
        self.write("trader/orders.json", {"orders": [ENTRY]})
        code, out, _ = self.run_cli(check, ["--ledger", "trader/state/live.json", "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("FYERS is not verified today", out)
        self.write("trader/config.json", dict(FYERS, fyers_enabled=False))
        code, out, _ = self.run_cli(check, ["--ledger", "trader/state/live.json", "--now", NOW])
        self.assertIn("\"fyers_enabled\" is not true", out)

    def test_prices_status_and_messages(self):
        self.write("trader/ltp.json", {"s": "ok", "d": [{"n": "NSE:SYNTHA-EQ", "v": {"lp": 1416.4}}]})
        code, out, _ = self.run_cli(fyers, ["prices", "--ltp", "trader/ltp.json", "--out", "trader/prices.json", "--now", NOW])
        self.assertEqual(code, 0, out)
        self.assertIn("SYNTHA 1416.40", out)
        code, out, err = self.run_cli(fyers, ["status", "--now", NOW])
        self.assertIn("FYERS adapter: ON", out)
        self.assertNotIn("Kite", out + err)
        self.assertEqual((kite.NAME, kite.PROG), ("Kite", "kite.py"))

    def test_the_tools_never_change_the_switches(self):
        db.init(os.path.join("trader", "trader.db"))
        for key in ("broker", "fyers_enabled", "dhan_enabled", "kite_enabled", "mode"):
            code, _, err = self.run_cli(control, ["set", key, "true", "--reply", "yes", "--config", "trader/config.json"])
            self.assertEqual(code, 1, key)
            self.assertIn("yours alone", err)

    def test_the_module_holds_no_network_and_no_credentials(self):
        with open(os.path.join(ROOT, "trader-broker", "fyers.py"), encoding="utf-8") as fh:
            body = fh.read().split('"""')[2]
        for bad in ("urllib", "socket", "requests", "os.environ", "access_token", "app_id", "secret_key", "password"):
            self.assertNotIn(bad, body, bad)


if __name__ == "__main__":
    unittest.main()
