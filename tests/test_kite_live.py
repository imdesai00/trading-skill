import json
import os
import sqlite3
import unittest

from _load import ist, load
from test_kite import ENTRY, NOW, LiveBase, check, kite, ticket

db = load("trader-db", "db")


class PriceAndMargin(LiveBase):
    def prepare(self, **kw):
        return self.adapter().prepare(ENTRY, [], **kw)

    def margins(self, cash=100000.0):
        return {"equity": {"available": {"live_balance": cash}}}

    def test_an_entry_needs_kites_own_price_and_margin(self):
        self.approve(ENTRY)
        with self.assertRaises(kite.KiteError) as e:
            self.prepare(margins=self.margins())
        self.assertIn("get_ltp", str(e.exception))
        with self.assertRaises(kite.KiteError) as e:
            self.prepare(ltp={"NSE:SYNTHA": {"last_price": 1416.0}})
        self.assertIn("get_margins", str(e.exception))
        call = self.prepare(ltp={"NSE:SYNTHA": {"last_price": 1416.0}}, margins=self.margins())
        self.assertEqual(call["tool"], "place_order")

    def test_a_price_that_moved_more_than_0_3_percent_uses_the_yes_up(self):
        self.approve(ENTRY)
        with self.assertRaises(kite.KiteError) as e:
            self.prepare(ltp={"NSE:SYNTHA": {"last_price": 1421.0}}, margins=self.margins())      # +0.35 %
        self.assertIn("+0.35%", str(e.exception))
        self.assertIn("ask for a new yes", str(e.exception))
        state = self.read("trader/state/broker.json")
        self.assertEqual(state["orders"][-1]["status"], "voided")
        with self.assertRaises(kite.KiteError) as e:                                         # the old yes is gone
            self.prepare(ltp={"NSE:SYNTHA": {"last_price": 1416.0}}, margins=self.margins())
        self.assertIn("voided", str(e.exception))
        self.assertEqual(sum(1 for c in self.fake.calls if c[0] == "place_order"), 0)

    def test_after_a_void_the_same_order_can_be_asked_again_but_only_after_a_new_risk_check(self):
        self.approve(ENTRY)
        with self.assertRaises(kite.KiteError):
            self.prepare(ltp={"NSE:SYNTHA": {"last_price": 1421.0}}, margins=self.margins())
        code, out, _ = self.run_cli(ticket, ["show", "--now", NOW + ":30"])
        self.assertIn("NOT READY: your yes was voided", out)
        code, code2, _, out = self.approve(ENTRY, NOW)                  # the risk check is not newer than the void
        self.assertIn("not ready", out)
        code, code2, _, out = self.approve(ENTRY, "2026-10-30 09:53")   # a new risk check, then a new yes
        self.assertEqual((code, code2), (0, 0), out)
        call = self.adapter("2026-10-30 09:53").prepare(ENTRY, [], {"NSE:SYNTHA": {"last_price": 1416.5}}, self.margins())
        self.assertEqual(call["tool"], "place_order")

    def test_the_edge_of_the_band_and_a_move_down(self):
        self.approve(ENTRY)
        self.prepare(ltp={"NSE:SYNTHA": {"last_price": 1420.2}}, margins=self.margins())       # +0.2972 %: inside
        self.approve(ENTRY)
        with self.assertRaises(kite.KiteError):
            self.prepare(ltp={"NSE:SYNTHA": {"last_price": 1411.0}}, margins=self.margins())  # -0.35 %

    def test_the_price_check_can_be_tightened_but_not_by_the_tools(self):
        cfg = dict(self.cfg, live_price_move_pct=0.1)
        self.write("trader/config.json", cfg)
        self.approve(ENTRY)
        with self.assertRaises(kite.KiteError):
            kite.KiteAdapter(cfg, self.fake, "trader", ist(NOW)).prepare(ENTRY, [], {"NSE:SYNTHA": {"last_price": 1417.8}},
                                                                          self.margins())

    def test_not_enough_cash_and_no_leverage(self):
        self.approve(ENTRY)
        with self.assertRaises(kite.KiteError) as e:
            self.prepare(ltp={"NSE:SYNTHA": {"last_price": 1416.0}}, margins=self.margins(50000.0))
        self.assertIn("leverage is off", str(e.exception))

    def test_margin_and_price_shapes(self):
        self.assertEqual(kite.last_price({"data": {"NSE:X": {"last_price": 10.5}}}, "X"), 10.5)
        self.assertEqual(kite.last_price({"X": 11}, "X"), 11.0)
        self.assertIsNone(kite.last_price({"Y": 1}, "X"))
        self.assertEqual(kite.available_cash({"data": {"equity": {"available": {"cash": 5.0}}}}), 5.0)
        self.assertEqual(kite.available_cash({"equity": {"net": 7}}), 7.0)
        self.assertIsNone(kite.available_cash({}))

    def test_exits_and_stops_need_no_price(self):
        self.approve(ENTRY)
        self.adapter().execute(ENTRY)
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}))
        self.run_cli(kite, ["stop-ticket", "--trade", "LT-20261030-01", "--now", NOW])
        stop = self.read("trader/orders.json")["orders"][0]
        self.approve(stop, "2026-10-30 09:53")
        self.assertTrue(self.adapter("2026-10-30 09:53").prepare(stop, []))

    def test_the_kill_switch_stops_a_live_entry_but_not_an_exit(self):
        self.approve(ENTRY)
        open(os.path.join("trader", "STOP"), "w").close()
        with self.assertRaises(kite.KiteError) as e:
            self.prepare(ltp={"NSE:SYNTHA": {"last_price": 1416.0}}, margins=self.margins())
        self.assertIn("KILL SWITCH", str(e.exception))

    def test_prices_command_writes_what_trader_risk_reads(self):
        self.write("trader/ltp.json", {"NSE:SYNTHA": {"last_price": 1416.4}})
        code, out, _ = self.run_cli(kite, ["prices", "--ltp", "trader/ltp.json", "--out", "trader/prices.json", "--now", NOW])
        self.assertEqual(code, 0)
        self.assertIn("SYNTHA 1416.40", out)
        doc = self.read("trader/prices.json")
        prices, times = check.load_prices(doc)
        self.assertEqual(prices, {"SYNTHA": 1416.4})
        self.assertEqual(times["SYNTHA"], ist(NOW))


class CheckStep(LiveBase):
    def base(self, **over):
        self.write("trader/profile.json", {"user_id": "AB1234"})
        self.write("trader/margins.json", {"equity": {"available": {"live_balance": 100000.0}}})
        self.write("trader/positions.json", over.get("positions", {"net": []}))
        return ["check", "--profile", "trader/profile.json", "--margins", "trader/margins.json", "--positions",
                "trader/positions.json", "--tools-need-approval", "--now", NOW, "--out", "trader/state/kite2.json"]

    def test_an_unexpected_open_position_fails_the_check(self):
        argv = self.base(positions={"net": [{"tradingsymbol": "SYNTHZ", "product": "MIS", "quantity": 10}]})
        code, _, err = self.run_cli(kite, argv)
        self.assertEqual(code, 1)
        self.assertIn("unexpected position", err)
        self.assertFalse(os.path.exists("trader/state/kite2.json"))

    def test_margins_and_positions_are_required(self):
        argv = self.base()
        argv = [x for x in argv if x not in ("--positions", "trader/positions.json")]
        code, _, err = self.run_cli(kite, argv)
        self.assertEqual(code, 1)
        self.assertIn("pass --positions", err)

    def test_empty_account_passes(self):
        code, out, _ = self.run_cli(kite, self.base())
        self.assertEqual(code, 0, out)
        self.assertTrue(self.read("trader/state/kite2.json")["order_tools_need_approval_confirmed_by_user"])


class InTheDatabase(LiveBase):
    def setUp(self):
        LiveBase.setUp(self)
        db.init(os.path.join("trader", "trader.db"))

    def rows(self, sql):
        conn = sqlite3.connect(os.path.join("trader", "trader.db"))
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def test_a_whole_live_trade_is_in_the_tables_with_the_broker_ids(self):
        self.approve(ENTRY)
        self.assertEqual(self.rows("SELECT order_id, mode FROM orders"), [("LIVE-20261030-01", "live")])
        oid = self.adapter().execute(ENTRY)
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}))
        ev = self.rows("SELECT status, broker_order_id FROM order_events ORDER BY seq")
        self.assertEqual([e[0] for e in ev], ["approved", "placed", "filled"])
        self.assertEqual(ev[1][1], oid)
        t = self.rows("SELECT trade_id, status, mode, order_id FROM trades ORDER BY seq")
        self.assertEqual(t[-1], ("LT-20261030-01", "open", "live", "LIVE-20261030-01"))
        self.assertEqual(self.rows("SELECT leg, source FROM fills"), [("entry", "kite")])
        self.assertTrue(db.check(os.path.join("trader", "trader.db"))[0])
        self.assertEqual(self.rows("SELECT kind FROM discrepancies")[0][0], "no stop")           # no stop yet: flagged

    def test_a_rejected_entry_is_an_event_not_a_position(self):
        self.approve(ENTRY)
        self.fake.refuse = "Insufficient funds"
        with self.assertRaises(kite.KiteError):
            self.adapter().execute(ENTRY)
        self.assertEqual([e[0] for e in self.rows("SELECT status FROM order_events ORDER BY seq")], ["approved", "failed"])

    def test_reconcile_agrees_when_everything_matches(self):
        self.approve(ENTRY)
        self.adapter().execute(ENTRY)
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}))
        self.run_cli(kite, ["stop-ticket", "--trade", "LT-20261030-01", "--now", NOW])
        stop = self.read("trader/orders.json")["orders"][0]
        self.approve(stop, "2026-10-30 09:53")
        self.adapter("2026-10-30 09:53").execute(stop)
        self.fake.trigger_stop(1407.3)
        orders, trades = self.fake("get_orders", {}), self.fake("get_trades", {})
        ad = self.adapter("2026-10-30 15:40")
        ad.sync(orders, trades, {"net": self.fake.positions()})            # the exit is recorded first
        bal = self.read("trader/state/live.json")["balance"]
        msgs, diffs = ad.reconcile(orders, trades, {"net": self.fake.positions()},
                                   {"equity": {"available": {"live_balance": bal}}})
        self.assertEqual(diffs, [], msgs)
        self.assertEqual(db.get_meta(os.path.join("trader", "trader.db"), "kite_account")["balance"], bal)

    def test_reconcile_flags_what_it_does_not_know_and_never_fixes_it_silently(self):
        self.approve(ENTRY)
        self.adapter().execute(ENTRY)
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}))
        stray = {"trade_id": "TX", "order_id": "999", "tradingsymbol": "SYNTHZ", "transaction_type": "BUY", "quantity": 5,
                 "average_price": 100.0, "product": "MIS"}
        trades = self.fake("get_trades", {}) + [stray]
        before = self.read("trader/state/live.json")
        msgs, diffs = self.adapter("2026-10-30 15:40").reconcile(
            self.fake("get_orders", {}), trades, {"net": self.fake.positions() + [{"tradingsymbol": "SYNTHZ", "product": "MIS", "quantity": 5}]},
            {"equity": {"available": {"live_balance": 120000.0}}})
        kinds = [d[0] for d in diffs]
        self.assertIn("trade not in the ledger", kinds)
        self.assertIn("balance", kinds)
        self.assertTrue(any(m.startswith("DISCREPANCY") and "Not fixed silently" in m for m in msgs))
        self.assertTrue(any("SYNTHZ" in m and m.startswith("WARNING") for m in msgs))
        rows = self.rows("SELECT kind, status FROM discrepancies")
        self.assertIn(("trade not in the ledger", "open"), rows)
        after = self.read("trader/state/live.json")
        self.assertEqual(before["trades"][0]["qty"], after["trades"][0]["qty"])               # the record was not rewritten

    def test_actual_charges_replace_the_estimate(self):
        self.approve(ENTRY)
        self.adapter().execute(ENTRY)
        self.adapter().sync(self.fake("get_orders", {}), self.fake("get_trades", {}))
        self.run_cli(kite, ["stop-ticket", "--trade", "LT-20261030-01", "--now", NOW])
        stop = self.read("trader/orders.json")["orders"][0]
        self.approve(stop, "2026-10-30 09:53")
        self.adapter("2026-10-30 09:53").execute(stop)
        self.fake.trigger_stop(1407.3)
        self.adapter("2026-10-30 10:10").sync(self.fake("get_orders", {}), self.fake("get_trades", {}), {"net": []})
        t = self.read("trader/state/live.json")["trades"][0]
        self.assertEqual(t["charges_kind"], "estimated")
        est, bal = t["charges"], self.read("trader/state/live.json")["balance"]
        code, out, _ = self.run_cli(kite, ["set-charges", "--trade", "LT-20261030-01", "--amount", "70.25", "--now", NOW])
        self.assertEqual(code, 0, out)
        t = self.read("trader/state/live.json")["trades"][0]
        led = self.read("trader/state/live.json")
        self.assertEqual((t["charges"], t["charges_kind"]), (70.25, "actual"))
        self.assertEqual(t["net_pnl"], round(t["gross_pnl"] - 70.25, 2))
        self.assertEqual(led["balance"], round(bal + est - 70.25, 2))
        code, _, err = self.run_cli(kite, ["set-charges", "--trade", "NOPE", "--amount", "1", "--now", NOW])
        self.assertEqual(code, 1)
        self.assertEqual(self.rows("SELECT version, charges_kind FROM trades WHERE trade_id='LT-20261030-01' ORDER BY seq")[-1][1], "actual")


if __name__ == "__main__":
    unittest.main()
