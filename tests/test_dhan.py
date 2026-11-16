import copy
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import ROOT, ist, load

dhan = load("trader-broker", "dhan")
kite = load("trader-broker", "kite")
ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")
size = load("trader-budget", "size")
control = load("trader-control", "control")
db = load("trader-db", "db")

PLAN = {"symbol": "SYNTHA", "side": "buy", "date": "2026-10-30", "setup": "orb", "entry": 1416.0,
        "entry_time": "09:50", "stop": 1408.0, "target": 1440.0, "tick": 0.1, "flat_by": "15:00"}
DHAN = {"mode": "live", "broker": "dhan", "dhan_enabled": True, "protection_data_required": False, "live_start_weeks": 0}
NOW = "2026-10-30 09:51"
ENTRY = size.order_from(size.size_trade(PLAN, {}, limit=1416.0, today="2026-10-30"), {})
SECURITY_CSV = """SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL,SEM_SERIES
NSE,E,1001,EQUITY,SYNTHA,EQ
NSE,E,1002,EQUITY,SYNTHB,EQ
NSE,E,1003,EQUITY,SYNTHC,EQ
NSE,E,9999,EQUITY,SYNTHC,EQ
NSE,E,2001,EQUITY,SYNTHD,BE
BSE,E,3001,EQUITY,SYNTHE,A
NSE,I,13,INDEX,NIFTY,
NSE,E,1004,EQUITY,SYNTHF-EQ,EQ
"""


class FakeDhan(object):
    """A stand-in for Dhan's MCP tools. A super order's entry fills at once; the stop and target legs rest until
    one is triggered. Field names are the guessed ones the adapter reads (VERIFY.md section 15)."""

    def __init__(self, fill=1416.0):
        self.orders, self.trades, self.calls, self.n = [], [], [], 0
        self.fill, self.refuse = fill, None
        self.ltp, self.cash = 1416.2, 100000.0

    def __call__(self, tool, args):
        self.calls.append((tool, copy.deepcopy(args)))
        if tool == "get_orders":
            return copy.deepcopy(self.orders)
        if tool == "get_trades":
            return copy.deepcopy(self.trades)
        if tool == "get_positions":
            return self.positions()
        if tool == "get_market_ltp":
            return {"status": "success", "data": {"NSE_EQ": {str(i): {"last_price": self.ltp} for i in args["NSE_EQ"]}}}
        if tool == "get_fund_limits":
            return {"availabelBalance": self.cash, "sodLimit": self.cash}
        if tool == "cancel_super_order":
            o = next(o for o in self.orders if o["orderId"] == args["orderId"])
            o["orderStatus"] = "CANCELLED"
            return {"orderId": o["orderId"], "orderStatus": "CANCELLED"}
        if tool not in ("place_super_order", "place_order"):
            raise AssertionError("unexpected tool " + tool)
        if self.refuse:
            raise RuntimeError(self.refuse)
        self.n += 1
        oid = "11261030%04d" % self.n
        buy = args["transactionType"] == "BUY"
        o = {"orderId": oid, "correlationId": args["correlationId"], "orderStatus": "TRADED", "tradingSymbol": "SYNTHA",
             "transactionType": args["transactionType"], "quantity": args["quantity"], "filledQty": args["quantity"],
             "averageTradedPrice": self.fill if buy else args.get("price") or 1424.0, "productType": "INTRADAY",
             "createTime": "2026-10-30 09:52:01"}
        if tool == "place_super_order":
            o["legs"] = {"stop": args["stopLossPrice"], "target": args["targetPrice"]}
        self.orders.append(o)
        self.add_trade(o, o["averageTradedPrice"])
        return {"status": "success", "data": {"orderId": oid, "orderStatus": "TRADED"}}

    def add_trade(self, o, price, oid=None, side=None):
        self.trades.append({"orderId": oid or o["orderId"], "tradingSymbol": o["tradingSymbol"],
                            "transactionType": side or o["transactionType"], "tradedQuantity": o["quantity"],
                            "tradedPrice": price, "productType": "INTRADAY", "createTime": "2026-10-30 10:09:11"})

    def leg_fill(self, price):
        o = next(o for o in self.orders if "legs" in o)
        self.add_trade(o, price, oid=o["orderId"] + "L", side="SELL")

    def positions(self):
        net = {}
        for t in self.trades:
            q = t["tradedQuantity"] * (1 if t["transactionType"] == "BUY" else -1)
            net[t["tradingSymbol"]] = net.get(t["tradingSymbol"], 0) + q
        return [{"tradingSymbol": s, "productType": "INTRADAY", "netQty": q} for s, q in net.items() if q]


class Base(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.d = tempfile.mkdtemp()
        os.chdir(self.d)
        self.cfg = dict(DHAN)
        self.write("trader/config.json", self.cfg)
        self.write("trader/state/dhan_securities.json", {"date": "2026-10-30", "ids": {"SYNTHA": "1001", "SYNTHB": "1002"}})
        self.verified()
        self.fake = FakeDhan()

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
        self.write("trader/state/dhan.json", {"verified": True, "date": date})

    def adapter(self, now=NOW, cfg=None):
        return dhan.DhanAdapter(cfg or self.cfg, self.fake, "trader", ist(now))

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

    def rows(self, sql):
        conn = sqlite3.connect(os.path.join("trader", "trader.db"))
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()


class TheCall(Base):
    def test_a_long_entry_is_one_super_order(self):
        call = dhan.build_call(ENTRY, self.cfg)
        a = call["arguments"]
        self.assertEqual(call["tool"], "place_super_order")
        self.assertEqual((a["transactionType"], a["exchangeSegment"], a["productType"], a["orderType"]),
                         ("BUY", "NSE_EQ", "INTRADAY", "LIMIT"))
        self.assertEqual((a["securityId"], a["quantity"], a["price"], a["stopLossPrice"], a["targetPrice"], a["trailingJump"]),
                         ("1001", 45, 1416.0, 1408.0, 1440.0, 0))
        self.assertEqual(a["correlationId"], call["tag"])
        self.assertTrue(call["tag"].startswith("IDT") and len(call["tag"]) <= 20)
        self.assertNotIn("dhanClientId", json.dumps(call))                  # no account identifier goes in the call

    def test_the_call_is_built_only_from_fingerprinted_fields(self):
        other = dict(ENTRY, why="other words", plan={"entry": 9999}, kite={})
        self.assertEqual(dhan.build_call(ENTRY, self.cfg), dhan.build_call(other, self.cfg))
        self.assertNotEqual(dhan.build_call(ENTRY, self.cfg)["tag"], dhan.build_call(dict(ENTRY, qty=44), self.cfg)["tag"])

    def test_entries_must_be_limit_orders_and_levels_must_make_sense(self):
        market = size.order_from(size.size_trade(PLAN, {}, today="2026-10-30"), {})
        with self.assertRaises(dhan.DhanError) as e:
            dhan.build_call(market, self.cfg)
        self.assertIn("size.py --limit", str(e.exception))
        with self.assertRaises(dhan.DhanError):
            dhan.build_call(dict(ENTRY, stop=1420.0), self.cfg)
        with self.assertRaises(dhan.DhanError):
            dhan.build_call(dict(ENTRY, target=None), self.cfg)
        stop_order = {"symbol": "SYNTHA", "exchange": "NSE", "side": "sell", "qty": 45, "product": "MIS",
                      "entry_type": "stop", "stop": 1408.0}
        with self.assertRaises(dhan.DhanError) as e:
            dhan.build_call(stop_order, self.cfg)
        self.assertIn("leg of the entry super order", str(e.exception))

    def test_exit_and_cancel(self):
        sell = {"symbol": "SYNTHA", "exchange": "NSE", "side": "sell", "qty": 45, "product": "MIS", "entry_type": "market"}
        c = dhan.build_call(sell, self.cfg)
        self.assertEqual((c["tool"], c["arguments"]["transactionType"], c["arguments"]["orderType"],
                          c["arguments"]["productType"]), ("place_order", "SELL", "MARKET", "INTRADAY"))
        cancel = dict(sell, action="cancel", order_ref="1126103000001")
        c = dhan.build_call(cancel, self.cfg)
        self.assertEqual((c["tool"], c["arguments"]), ("cancel_super_order", {"orderId": "1126103000001", "orderLeg": "ENTRY_LEG"}))
        with self.assertRaises(dhan.DhanError):
            dhan.build_call(dict(sell, action="cancel"), self.cfg)

    def test_other_markets_and_unknown_symbols_are_refused(self):
        with self.assertRaises(dhan.DhanError):
            dhan.build_call(dict(ENTRY, product="NRML"), self.cfg)
        with self.assertRaises(dhan.DhanError) as e:
            dhan.build_call(dict(ENTRY, symbol="NOPE"), self.cfg)
        self.assertIn("no securityId", str(e.exception))
        os.remove("trader/state/dhan_securities.json")
        with self.assertRaises(dhan.DhanError) as e:
            dhan.build_call(ENTRY, self.cfg)
        self.assertIn("dhan.py securities", str(e.exception))

    def test_tool_names_can_be_set_in_the_config_after_you_read_the_real_list(self):
        cfg = dict(self.cfg, dhan_tools={"place_super": "create_super_order", "bogus": "x"})
        self.assertEqual(dhan.build_call(ENTRY, cfg)["tool"], "create_super_order")
        self.assertEqual(dhan.tools_for(cfg)["place"], "place_order")
        self.assertNotIn("bogus", dhan.tools_for(cfg))


class TheSecurityMap(Base):
    def test_one_id_per_nse_equity_and_ambiguity_is_named(self):
        ids, amb, counts = dhan.read_security_list(SECURITY_CSV)
        self.assertEqual(ids, {"SYNTHA": "1001", "SYNTHB": "1002", "SYNTHF": "1004"})      # no BE series, BSE, index
        self.assertEqual(amb, ["SYNTHC"])
        self.assertEqual((counts["rows"], counts["kept"]), (8, 3))

    def test_missing_columns_and_empty_files(self):
        with self.assertRaises(dhan.DhanError) as e:
            dhan.read_security_list("a,b\n1,2\n")
        self.assertIn("columns", str(e.exception))
        with self.assertRaises(dhan.DhanError):
            dhan.read_security_list("a,b\n")

    def test_the_command_writes_the_map(self):
        with open("list.csv", "w") as fh:
            fh.write(SECURITY_CSV)
        code, out, _ = self.run_cli(dhan, ["securities", "--csv", "list.csv", "--now", NOW])
        self.assertEqual(code, 0, out)
        self.assertIn("kept 3 NSE equity symbols", out)
        self.assertIn("AMBIGUOUS", out)
        doc = self.read("trader/state/dhan_securities.json")
        self.assertEqual((doc["date"], doc["ids"]["SYNTHA"], doc["ambiguous"]), ("2026-10-30", "1001", ["SYNTHC"]))


class TheGates(Base):
    def test_off_unless_every_switch_is_yours(self):
        cases = [({"mode": "paper"}, "\"mode\" is not \"live\""),
                 ({"mode": "live", "broker": "kite", "dhan_enabled": True}, "\"broker\" is not \"dhan\""),
                 ({"mode": "live", "broker": "dhan"}, "\"dhan_enabled\" is not true")]
        for cfg, word in cases:
            with self.assertRaises(dhan.DhanDisabled) as e:
                dhan.DhanAdapter(cfg, self.fake, "trader", ist(NOW)).require_enabled()
            self.assertIn(word, str(e.exception))
        self.verified("2026-10-29")
        with self.assertRaises(dhan.DhanDisabled) as e:
            self.adapter().require_enabled()
        self.assertIn("not verified today", str(e.exception))
        self.assertEqual(self.fake.calls, [])                                 # off means zero Dhan calls

    def test_execute_makes_no_call_when_off(self):
        ad = dhan.DhanAdapter({"mode": "paper"}, self.fake, "trader", ist(NOW))
        with self.assertRaises(dhan.DhanDisabled):
            ad.execute(ENTRY)
        self.assertEqual(self.fake.calls, [])

    def test_no_yes_no_order(self):
        self.write("trader/orders.json", {"orders": [ENTRY]})
        self.run_cli(check, ["--ledger", "trader/state/live.json", "--now", NOW])
        with self.assertRaises(dhan.DhanError) as e:
            self.adapter().execute(ENTRY)
        self.assertIn("you have not said yes", str(e.exception))
        self.assertNotIn("place_super_order", [c[0] for c in self.fake.calls])

    def test_one_yes_one_order(self):
        code, code2, out = self.approve(ENTRY)
        self.assertEqual((code, code2), (0, 0), out)
        self.assertIn("dhan.py prepare", out)
        oid = self.adapter().execute(ENTRY)
        self.assertTrue(oid.startswith("1126103"))
        with self.assertRaises(dhan.DhanError) as e:
            self.adapter().execute(ENTRY)
        self.assertIn("already placed", str(e.exception))
        self.assertEqual(sum(1 for c in self.fake.calls if c[0] == "place_super_order"), 1)
        sent = [c for c in self.fake.calls if c[0] == "place_super_order"][0][1]
        self.assertEqual(sent, dhan.build_call(ENTRY, self.cfg)["arguments"])      # exactly what was approved

    def test_a_changed_order_needs_a_new_yes(self):
        self.approve(ENTRY)
        cfg = dict(self.cfg, dhan_tools={"place_super": "other_tool"})            # changes the call, not the order
        with self.assertRaises(dhan.DhanError) as e:
            self.adapter(cfg=cfg).execute(ENTRY)
        self.assertIn("differs from the one you approved", str(e.exception))

    def test_the_duplicate_guard_reads_dhans_correlation_id(self):
        self.approve(ENTRY)
        call = dhan.build_call(ENTRY, self.cfg)
        with self.assertRaises(dhan.DhanError) as e:
            self.adapter().prepare(ENTRY, [{"orderId": "5", "correlationId": call["tag"]}],
                                   {"data": {"NSE_EQ": {"1001": {"last_price": 1416.0}}}}, {"availabelBalance": 1e5})
        self.assertIn("already at Dhan", str(e.exception))

    def test_a_refused_order_uses_the_yes_up(self):
        self.approve(ENTRY)
        self.fake.refuse = "Insufficient funds"
        with self.assertRaises(dhan.DhanError) as e:
            self.adapter().execute(ENTRY)
        self.assertIn("Dhan refused", str(e.exception))
        with self.assertRaises(dhan.DhanError):
            self.adapter().execute(ENTRY)
        self.assertEqual(sum(1 for c in self.fake.calls if c[0] == "place_super_order"), 1)


class PriceAndCash(Base):
    def go(self, ltp=1416.0, cash=100000.0):
        return self.adapter().prepare(ENTRY, [], {"data": {"NSE_EQ": {"1001": {"last_price": ltp}}}},
                                      {"availabelBalance": cash})

    def test_an_entry_needs_dhans_price_and_cash(self):
        self.approve(ENTRY)
        with self.assertRaises(dhan.DhanError) as e:
            self.adapter().prepare(ENTRY, [], None, {"availabelBalance": 1e5})
        self.assertIn("get_ltp", str(e.exception))
        with self.assertRaises(dhan.DhanError) as e:
            self.adapter().prepare(ENTRY, [], {"data": {"NSE_EQ": {"1001": {"last_price": 1416.0}}}}, None)
        self.assertIn("get_margins", str(e.exception))
        self.assertEqual(self.go()["tool"], "place_super_order")

    def test_a_price_that_moved_more_than_0_3_percent_uses_the_yes_up(self):
        self.approve(ENTRY)
        with self.assertRaises(dhan.DhanError) as e:
            self.go(ltp=1421.0)
        self.assertIn("+0.35%", str(e.exception))
        self.assertEqual(self.read("trader/state/broker.json")["orders"][-1]["status"], "voided")
        with self.assertRaises(dhan.DhanError) as e:
            self.go()
        self.assertIn("voided", str(e.exception))

    def test_not_enough_cash(self):
        self.approve(ENTRY)
        with self.assertRaises(dhan.DhanError) as e:
            self.go(cash=50000.0)
        self.assertIn("leverage is off", str(e.exception))

    def test_readers_accept_several_spellings(self):
        ad = self.adapter()
        self.assertEqual(ad.price_of({"NSE_EQ": {"1001": {"lastPrice": 5.5}}}, "SYNTHA"), 5.5)
        self.assertEqual(ad.price_of({"data": {"SYNTHA": 7}}, "SYNTHA"), 7.0)
        self.assertIsNone(ad.price_of({"data": {}}, "SYNTHA"))
        self.assertEqual(ad.cash_of({"data": {"availableBalance": 12.5}}), 12.5)
        self.assertEqual(ad.cash_of({"availabelBalance": 3}), 3.0)
        self.assertIsNone(ad.cash_of({}))


class WholeTrade(Base):
    def open_trade(self):
        self.approve(ENTRY)
        return self.adapter().execute(ENTRY)

    def test_entry_fill_has_a_stop_from_the_start_and_closes_at_the_stop(self):
        oid = self.open_trade()
        led = self.read("trader/state/live.json")
        self.assertEqual((led["trades"][0]["status"], led["trades"][0]["stop_order_id"]), ("pending_fill", oid))
        msgs = self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}), self.fake("get_positions", {}))
        self.assertTrue(any("entry filled, 45 shares at 1416.00" in m for m in msgs), msgs)
        self.assertFalse(any(m.startswith("WARNING") for m in msgs), msgs)       # no "no stop" warning: the stop is a leg
        self.fake.leg_fill(1407.3)
        msgs = self.adapter("2026-10-30 10:10").sync(self.fake("get_orders", {}), self.fake("get_trades", {}),
                                                    self.fake("get_positions", {}))
        t = self.read("trader/state/live.json")["trades"][0]
        self.assertEqual((t["status"], t["exit_reason"], t["exit_fill"]), ("closed", "stop", 1407.3))
        c = kite.chg.charges(45 * 1416.0, 45 * 1407.3)
        self.assertEqual((t["charges"], t["net_pnl"]), (c["total"], round(45 * (1407.3 - 1416.0) - c["total"], 2)))
        self.assertTrue(any("closed (stop)" in m for m in msgs))

    def test_the_target_leg(self):
        self.open_trade()
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}), None)
        self.fake.leg_fill(1440.0)
        self.adapter("2026-10-30 11:00").sync(self.fake("get_orders", {}), self.fake("get_trades", {}), None)
        t = self.read("trader/state/live.json")["trades"][0]
        self.assertEqual((t["status"], t["exit_reason"], t["exit_fill"]), ("closed", "target", 1440.0))
        self.assertGreater(t["net_pnl"], 0)

    def test_a_cancelled_super_order_is_not_an_open_position(self):
        self.open_trade()
        self.fake.orders[0]["orderStatus"] = "REJECTED"
        self.fake.orders[0]["omsErrorDescription"] = "RMS: margin shortfall"
        self.fake.trades.clear()
        msgs = self.adapter().sync(self.fake("get_orders", {}), [], None)
        self.assertTrue(any("rejected" in m and "margin shortfall" in m for m in msgs), msgs)
        self.assertEqual(self.read("trader/state/live.json")["trades"][0]["status"], "failed")

    def test_the_position_check_warns_in_dhans_own_field_names(self):
        self.open_trade()
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}), None)
        msgs = self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}),
                                   [{"tradingSymbol": "SYNTHA", "productType": "INTRADAY", "netQty": 10}])
        self.assertTrue(any(m.startswith("WARNING") and "shows 10 SYNTHA" in m for m in msgs), msgs)

    def test_leaving_early_is_cancel_then_sell_each_on_its_own_yes(self):
        oid = self.open_trade()
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}), None)
        cancel = {"symbol": "SYNTHA", "exchange": "NSE", "side": "sell", "qty": 45, "product": "MIS", "entry_type": "market",
                  "action": "cancel", "order_ref": oid}
        code, code2, out = self.approve(cancel, "2026-10-30 14:58")
        self.assertEqual((code, code2), (0, 0), out)
        self.adapter("2026-10-30 14:58").execute(cancel)
        self.assertEqual(self.fake.orders[0]["orderStatus"], "CANCELLED")
        sell = {"symbol": "SYNTHA", "exchange": "NSE", "side": "sell", "qty": 45, "product": "MIS", "entry_type": "market"}
        code, code2, out = self.approve(sell, "2026-10-30 14:59")
        self.assertEqual((code, code2), (0, 0), out)
        self.adapter("2026-10-30 14:59").execute(sell)
        self.adapter("2026-10-30 15:05").sync(self.fake("get_orders", {}), self.fake("get_trades", {}), None)
        t = self.read("trader/state/live.json")["trades"][0]
        self.assertEqual(t["status"], "closed")

    def test_reconcile_agrees_and_flags_what_it_does_not_know(self):
        self.open_trade()
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}), None)
        self.fake.leg_fill(1407.3)
        ad = self.adapter("2026-10-30 15:40")
        ad.sync(self.fake("get_orders", {}), self.fake("get_trades", {}), self.fake("get_positions", {}))
        bal = self.read("trader/state/live.json")["balance"]
        msgs, diffs = ad.reconcile(self.fake("get_orders", {}), self.fake("get_trades", {}), self.fake("get_positions", {}),
                                   {"availabelBalance": bal})
        self.assertEqual(diffs, [], msgs)
        stray = {"orderId": "999", "tradingSymbol": "SYNTHZ", "transactionType": "BUY", "tradedQuantity": 5,
                 "tradedPrice": 100.0, "productType": "INTRADAY"}
        msgs, diffs = ad.reconcile(self.fake("get_orders", {}), self.fake("get_trades", {}) + [stray], self.fake("get_positions", {}),
                                   {"availabelBalance": 120000.0})
        kinds = [d[0] for d in diffs]
        self.assertIn("trade not in the ledger", kinds)
        self.assertIn("balance", kinds)
        self.assertTrue(any("Not fixed silently" in m for m in msgs))


class TheCheckStep(Base):
    def base(self, positions=None):
        self.write("trader/profile.json", {"dhanClientId": "x", "status": "ok"})
        self.write("trader/funds.json", {"availabelBalance": 100000.0})
        self.write("trader/positions.json", positions if positions is not None else [])
        return ["check", "--profile", "trader/profile.json", "--margins", "trader/funds.json", "--positions",
                "trader/positions.json", "--tools-need-approval", "--static-ip-confirmed", "--now", NOW,
                "--out", "trader/state/dhan2.json"]

    def test_it_needs_both_confirmations_from_you(self):
        argv = self.base()
        for flag, word in (("--tools-need-approval", "Needs approval"), ("--static-ip-confirmed", "static IP")):
            code, _, err = self.run_cli(dhan, [x for x in argv if x != flag])
            self.assertEqual(code, 1)
            self.assertIn(word, err)
            self.assertFalse(os.path.exists("trader/state/dhan2.json"))

    def test_an_empty_account_with_a_map_passes(self):
        code, out, err = self.run_cli(dhan, self.base())
        self.assertEqual(code, 0, err)
        self.assertIn("Dhan verified for 2026-10-30", out)
        st = self.read("trader/state/dhan2.json")
        self.assertEqual((st["verified"], st["broker"]), (True, "dhan"))
        self.assertTrue("static_ip_confirmed" in st["extra_confirmations_by_user"] and st["order_tools_need_approval_confirmed_by_user"])
        self.assertNotIn("dhanClientId", json.dumps(st))                       # no account details are stored

    def test_an_unexpected_position_or_no_map_fails(self):
        code, _, err = self.run_cli(dhan, self.base([{"tradingSymbol": "SYNTHZ", "productType": "INTRADAY", "netQty": 10}]))
        self.assertEqual(code, 1)
        self.assertIn("unexpected position", err)
        os.remove("trader/state/dhan_securities.json")
        code, _, err = self.run_cli(dhan, self.base())
        self.assertEqual(code, 1)
        self.assertIn("dhan.py securities", err)

    def test_refuses_unless_the_config_is_yours(self):
        self.write("trader/config.json", {"mode": "live", "broker": "kite", "dhan_enabled": True})
        code, _, err = self.run_cli(dhan, self.base())
        self.assertEqual(code, 1)
        self.assertIn("\"broker\" must be \"dhan\"", err)

    def test_bad_profile(self):
        argv = self.base()
        self.write("trader/profile.json", {"error": "token expired"})
        code, _, err = self.run_cli(dhan, argv)
        self.assertEqual(code, 1)
        self.assertIn("does not look like a Dhan result", err)

    def test_prices_and_status(self):
        self.write("trader/ltp.json", {"data": {"NSE_EQ": {"1001": {"last_price": 1416.4}}}})
        code, out, _ = self.run_cli(dhan, ["prices", "--ltp", "trader/ltp.json", "--out", "trader/prices.json", "--now", NOW])
        self.assertEqual(code, 0)
        self.assertIn("SYNTHA 1416.40", out)
        prices, times = check.load_prices(self.read("trader/prices.json"))
        self.assertEqual((prices, times["SYNTHA"]), ({"SYNTHA": 1416.4}, ist(NOW)))
        code, out, _ = self.run_cli(dhan, ["status", "--now", NOW])
        self.assertIn("Dhan adapter: ON", out)
        self.verified("2026-10-29")
        code, out, _ = self.run_cli(dhan, ["status", "--now", NOW])
        self.assertIn("OFF. Dhan is not verified today", out)

    def test_messages_say_dhan_not_kite(self):
        code, out, err = self.run_cli(dhan, ["status", "--now", NOW])
        self.assertNotIn("Kite", out + err)
        self.assertEqual(kite.NAME, "Kite")                                 # the Kite commands are not left renamed


class Routing(Base):
    def test_the_ticket_and_the_hash_follow_the_broker(self):
        self.assertEqual(ticket.broker_of({}), "kite")                       # no key means the older behaviour
        self.assertEqual(ticket.broker_of({"broker": "dhan"}), "dhan")
        self.assertEqual(ticket.live_call(ENTRY, self.cfg)["tool"], "place_super_order")
        self.approve(ENTRY)
        rec = self.read("trader/state/broker.json")["orders"][-1]
        self.assertEqual(rec["payload_hash"], dhan.payload_hash(dhan.build_call(ENTRY, self.cfg)))
        code, out, _ = self.run_cli(ticket, ["show", "--now", NOW])
        self.assertIn("LIVE ACCOUNT, REAL MONEY, Dhan", out)
        self.assertIn("Claude will call the Dhan place_super_order tool", out)

    def test_the_kite_route_is_unchanged(self):
        cfg = {"mode": "live", "kite_enabled": True}
        self.assertEqual(ticket.live_call(dict(ENTRY, entry_type="market", limit_price=None), cfg)["tool"], "place_order")

    def test_trader_risk_gates_on_the_chosen_broker(self):
        self.write("trader/state/dhan.json", {"verified": True, "date": "2026-10-29"})
        self.write("trader/orders.json", {"orders": [ENTRY]})
        code, out, _ = self.run_cli(check, ["--ledger", "trader/state/live.json", "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("Dhan is not verified today", out)
        self.write("trader/config.json", dict(DHAN, dhan_enabled=False))
        code, out, _ = self.run_cli(check, ["--ledger", "trader/state/live.json", "--now", NOW])
        self.assertIn("\"dhan_enabled\" is not true", out)
        self.write("trader/config.json", DHAN)
        self.verified()
        code, out, _ = self.run_cli(check, ["--ledger", "trader/state/live.json", "--now", NOW])
        self.assertEqual(code, 0, out)

    def test_the_tools_never_change_the_broker_switches(self):
        db.init(os.path.join("trader", "trader.db"))
        for key in ("broker", "dhan_enabled", "kite_enabled", "mode"):
            code, _, err = self.run_cli(control, ["set", key, "true", "--reply", "yes", "--config", "trader/config.json"])
            self.assertEqual(code, 1, key)
            self.assertIn("yours alone", err)
        self.assertEqual(self.read("trader/config.json")["broker"], "dhan")

    def test_init_writes_dhan_off(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "trader", "config.json")
            with redirect_stdout(io.StringIO()):
                size.main(["init", "--config", p])
            with open(p) as fh:
                cfg = json.load(fh)
            self.assertEqual((cfg["mode"], cfg["broker"], cfg["dhan_enabled"], cfg["kite_enabled"]), ("paper", "dhan", False, False))
        finally:
            shutil.rmtree(d)

    def test_the_new_module_holds_no_secrets_and_no_network(self):
        with open(os.path.join(ROOT, "trader-broker", "dhan.py"), encoding="utf-8") as fh:
            text = fh.read()
        body = text.split('"""')[2]
        for bad in ("urllib", "socket", "requests", "os.environ", "access_token", "DHAN_ACCESS_TOKEN", "password"):
            self.assertNotIn(bad, body, bad)


class InTheDatabase(Base):
    def setUp(self):
        Base.setUp(self)
        db.init(os.path.join("trader", "trader.db"))

    def test_orders_events_trades_and_fills_are_recorded_with_the_dhan_id(self):
        self.approve(ENTRY)
        oid = self.adapter().execute(ENTRY)
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}), None)
        ev = self.rows("SELECT status, broker_order_id FROM order_events ORDER BY seq")
        self.assertEqual([e[0] for e in ev], ["approved", "placed", "filled"])
        self.assertEqual(ev[1][1], oid)
        self.assertEqual([r[0] for r in self.rows("SELECT leg FROM fills")], ["entry"])
        self.assertEqual(self.rows("SELECT trade_id, status, mode FROM trades ORDER BY seq")[-1], ("LT-20261030-01", "open", "live"))
        self.assertTrue(db.check(os.path.join("trader", "trader.db"))[0])


if __name__ == "__main__":
    unittest.main()
