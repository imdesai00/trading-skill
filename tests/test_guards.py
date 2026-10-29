import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, ROOT, load, ist, write

guards = load("trader-risk", "guards")
check = load("trader-risk", "check")
size = load("trader-budget", "size")
scan = load("trader-scanner", "scan")

TODAY = "2026-10-30"          # a Friday
PLAN = {"symbol": "SYNTHA", "side": "buy", "date": TODAY, "setup": "orb", "entry": 1416.0, "entry_time": "09:50",
        "stop": 1408.0, "target": 1440.0, "tick": 0.1, "flat_by": "15:00"}
NOW = ist("2026-10-30 10:30")


def order(plan=PLAN, cfg=None, **over):
    o = size.order_from(size.size_trade(plan, cfg or {}, today=TODAY), cfg or {})
    o.update(over)
    return o


def prot(**kw):
    p = {"surveillance.json": {"date": TODAY, "asm": [], "gsm": []},
         "mis.json": {"date": TODAY, "symbols": ["SYNTHA", "SYNTHB"]},
         "bands.json": {"date": TODAY, "bands": {"SYNTHA": 20.0, "SYNTHB": None}},
         "market.json": {"date": TODAY, "vix": 14.0, "nifty_gap_pct": 0.2},
         "results_today.json": None, "events.json": None}
    p.update(kw)
    return p


def tr(i, net, exit_time, **kw):
    t = {"trade_id": "PT-%d" % i, "date": TODAY, "exit_date": TODAY, "symbol": "SYNTHB", "qty": 10, "status": "closed",
         "net_pnl": net, "exit_time": exit_time, "max_loss": 100.0, "entry_fill": 100.0}
    t.update(kw)
    return t


def ck(cfg=None, ledger=None, now=NOW, p=None, control=None, orders=None, prices=None, times=None, prev=None):
    return check.check(orders or [order()], cfg or {}, ledger, prices, None, TODAY, now, times, p, control, prev)


def why(res):
    return " | ".join(res["results"][0]["reasons"])


class Limits(unittest.TestCase):
    def test_percent_to_rupees(self):
        self.assertEqual(guards.derive_inr(100000, guards.DEFAULT_PCT),
                         {"max_loss_per_trade_inr": 500.0, "max_daily_loss_inr": 1500.0, "max_weekly_loss_inr": 4000.0,
                          "max_drawdown_pause_inr": 8000.0, "max_position_value_inr": 75000.0})
        self.assertEqual(guards.derive_inr(250000, guards.DEFAULT_PCT)["max_loss_per_trade_inr"], 1250.0)

    def test_percent_wins_over_a_hand_edited_rupee_number_and_says_so(self):
        cfg, notes = guards.effective_limits({"capital_inr": 100000, "limits_pct": guards.DEFAULT_PCT,
                                              "max_loss_per_trade_inr": 900})
        self.assertEqual(cfg["max_loss_per_trade_inr"], 500.0)
        self.assertEqual(len(notes), 1)
        self.assertIn("using 500.0", notes[0])
        res = ck(cfg={"capital_inr": 100000, "limits_pct": guards.DEFAULT_PCT, "max_loss_per_trade_inr": 900})
        self.assertTrue(any("max_loss_per_trade_inr" in w for w in res["warnings"]))

    def test_without_percentages_the_rupee_values_stand(self):
        cfg, notes = guards.effective_limits({"capital_inr": 100000, "max_loss_per_trade_inr": 300})
        self.assertEqual((cfg["max_loss_per_trade_inr"], notes), (300, []))

    def test_table(self):
        rows = guards.limits_table(100000, guards.DEFAULT_PCT)
        self.assertEqual(rows[0], ("max loss per trade", 0.5, 500.0))


class Brakes(unittest.TestCase):
    def test_two_losses_in_a_row_stop_the_day(self):
        led = {"trades": [tr(1, -300, "10:00"), tr(2, -200, "10:10")]}
        res = ck(ledger=led, now=ist("2026-10-30 11:30"))
        self.assertEqual(res["results"][0]["verdict"], "BLOCK")
        self.assertIn("2 losing trades in a row today", why(res))

    def test_a_win_between_resets_the_streak(self):
        led = {"trades": [tr(1, -300, "10:00"), tr(2, 100, "10:10"), tr(3, -200, "10:20")]}
        res = ck(ledger=led, now=ist("2026-10-30 11:30"))
        self.assertNotIn("in a row", why(res))

    def test_cooldown_is_15_minutes_after_a_losing_exit(self):
        led = {"trades": [tr(1, -300, "10:20")]}
        res = ck(ledger=led, now=ist("2026-10-30 10:30"))
        self.assertIn("cooldown", why(res))
        self.assertIn("6 more minutes", why(res))
        self.assertNotIn("cooldown", why(ck(ledger=led, now=ist("2026-10-30 10:36"))))

    def test_no_cooldown_after_a_win(self):
        self.assertNotIn("cooldown", why(ck(ledger={"trades": [tr(1, 300, "10:25")]}, now=ist("2026-10-30 10:30"))))

    def test_yesterdays_losses_do_not_count(self):
        led = {"trades": [tr(1, -300, "10:00", date="2026-10-29", exit_date="2026-10-29"),
                          tr(2, -200, "10:10", date="2026-10-29", exit_date="2026-10-29")]}
        self.assertNotIn("in a row", why(ck(ledger=led)))


class KillAndPause(unittest.TestCase):
    def test_control_reasons_block_entries_but_not_exits(self):
        res = ck(control=["KILL SWITCH: the file trader/STOP exists"])
        self.assertEqual(res["results"][0]["verdict"], "BLOCK")
        self.assertIn("KILL SWITCH", why(res))
        led = {"trades": [{"trade_id": "PT-1", "date": TODAY, "symbol": "SYNTHA", "qty": 45, "status": "open",
                           "max_loss": 400.0, "entry_fill": 1416.0}]}
        sell = {"symbol": "SYNTHA", "side": "sell", "qty": 45, "product": "MIS", "entry_type": "market"}
        res = ck(ledger=led, orders=[sell], control=["KILL SWITCH"])
        self.assertEqual(res["results"][0]["verdict"], "PASS")          # getting out is never blocked


class HalfSize(unittest.TestCase):
    def test_a_results_day_halves_the_loss_limit(self):
        p = prot(**{"results_today.json": {"date": TODAY, "symbols": {"SYNTHA": "Q2 results"}}})
        res = ck(p=p)
        self.assertEqual(res["results"][0]["verdict"], "BLOCK")
        self.assertIn("over your ₹250 max loss per trade (halved today: SYNTHA reports results today", why(res))
        self.assertTrue(any("HALF SIZE" in i for i in res["results"][0]["info"]))

    def test_the_order_sized_at_half_passes(self):
        p = prot(**{"results_today.json": {"date": TODAY, "symbols": {"SYNTHA": "Q2 results"}}})
        s = size.size_trade(PLAN, {}, today=TODAY, prot=p)
        self.assertEqual(s["half_size"]["factor"], 0.5)
        self.assertLess(s["qty"], 30)
        self.assertEqual(s["max_loss_per_trade_inr"], 250.0)
        o = size.order_from(s, {})
        self.assertEqual(ck(p=p, orders=[o])["results"][0]["verdict"], "PASS")

    def test_reasons_do_not_stack(self):
        p = prot(**{"results_today.json": {"date": TODAY, "symbols": {"SYNTHA": "x"}},
                    "market.json": {"date": TODAY, "vix": 22.0, "nifty_gap_pct": 1.5}, "events.json": {TODAY: "RBI policy"}})
        f, reasons, stops = guards.size_factor("SYNTHA", TODAY, {}, p, "paper")
        self.assertEqual((f, stops), (0.5, []))
        self.assertGreaterEqual(len(reasons), 3)

    def test_vix_and_gap(self):
        self.assertEqual(guards.size_factor("X", TODAY, {}, prot(**{"market.json": {"date": TODAY, "vix": 20.5}}), "paper")[0], 0.5)
        self.assertEqual(guards.size_factor("X", TODAY, {}, prot(**{"market.json": {"date": TODAY, "vix": 20.0}}), "paper")[0], 1.0)
        self.assertEqual(guards.size_factor("X", TODAY, {}, prot(**{"market.json": {"date": TODAY, "nifty_gap_pct": -1.2}}), "paper")[0], 0.5)
        self.assertEqual(guards.size_factor("X", TODAY, {}, prot(**{"market.json": {"date": TODAY, "nifty_gap_pct": 1.0}}), "paper")[0], 1.0)

    def test_vix_above_25_is_no_entries(self):
        res = ck(p=prot(**{"market.json": {"date": TODAY, "vix": 26.0}}))
        self.assertIn("India VIX is 26.0, above 25: no entries", why(res))
        refused = size.size_trade(PLAN, {}, today=TODAY, prot=prot(**{"market.json": {"date": TODAY, "vix": 26.0}}))
        self.assertIn("No order", refused["refusal"])

    def test_events(self):
        self.assertIn("Union Budget", guards.events_today("2027-02-01", "X", {}, {})[0][0])
        self.assertIn("index expiry day", guards.events_today("2026-11-03", "X", {}, {})[0][0])        # a Tuesday
        self.assertEqual(guards.events_today("2026-11-04", "X", {}, {})[0], [])
        self.assertEqual(guards.events_today("2026-11-04", "X", {"event_days": {"2026-11-04": "RBI policy"}}, {})[0],
                         ["RBI policy"])
        self.assertEqual(guards.events_today("2026-11-04", "X", {}, {"events.json": {"2026-11-04": "Fed day"}})[0], ["Fed day"])

    def test_old_files_do_not_count(self):
        p = prot(**{"results_today.json": {"date": "2026-10-29", "symbols": {"SYNTHA": "x"}}})
        self.assertEqual(guards.size_factor("SYNTHA", TODAY, {}, p, "paper")[0], 1.0)

    def test_the_first_two_live_weeks_run_at_half(self):
        cfg = {"mode": "live", "kite_enabled": True, "protection_data_required": False}
        f, r, _ = guards.size_factor("X", TODAY, cfg, {}, "live", [])
        self.assertEqual(f, 0.5)
        self.assertIn("first 2 live weeks", r[0])
        old = [{"date": "2026-10-01", "status": "closed"}]
        self.assertEqual(guards.size_factor("X", TODAY, cfg, {}, "live", old)[0], 1.0)
        young = [{"date": "2026-10-20", "status": "closed"}]
        self.assertEqual(guards.size_factor("X", TODAY, cfg, {}, "live", young)[0], 0.5)
        self.assertEqual(guards.size_factor("X", TODAY, cfg, {}, "paper", [])[0], 1.0)
        res = ck(cfg=cfg, p=prot(), ledger={"trades": []})
        self.assertIn("over your ₹250 max loss per trade (halved today: the first 2 live weeks", why(res))


class Protection(unittest.TestCase):
    def test_asm_gsm_and_mis(self):
        res = ck(p=prot(**{"surveillance.json": {"date": TODAY, "asm": ["SYNTHA"], "gsm": []}}))
        self.assertIn("SYNTHA is on the ASM", why(res))
        res = ck(p=prot(**{"surveillance.json": {"date": TODAY, "asm": [], "gsm": ["SYNTHA"]}}))
        self.assertIn("GSM", why(res))
        res = ck(p=prot(**{"mis.json": {"date": TODAY, "symbols": ["SYNTHB"]}}))
        self.assertIn("not on today's MIS-eligible list", why(res))
        self.assertEqual(ck(p=prot())["results"][0]["verdict"], "PASS")

    def test_circuit_margin(self):
        # prev close 1180 and a 20 % band: upper 1416.00, the entry 1416 sits on the circuit
        res = ck(p=prot(), prev={"SYNTHA": 1180.0})
        self.assertIn("within 2% of the upper circuit 1416.00", why(res))
        res = ck(p=prot(), prev={"SYNTHA": 1300.0})                    # upper 1560: far away
        self.assertNotIn("circuit", why(res))
        res = ck(p=prot(), prev={"SYNTHA": 1770.0})                    # lower 1416: sits on the lower circuit
        self.assertIn("lower circuit", why(res))
        res = ck(p=prot(**{"bands.json": {"date": TODAY, "bands": {"SYNTHA": None}}}), prev={"SYNTHA": 1180.0})
        self.assertNotIn("circuit", why(res))                          # no band (F&O stock): nothing to measure

    def test_missing_data_warns_in_paper_and_blocks_in_live(self):
        empty = {k: None for k in prot()}
        res = ck(p=empty)
        self.assertEqual(res["results"][0]["verdict"], "PASS")
        self.assertTrue(any("could NOT run" in w for w in res["warnings"]))
        res = ck(cfg={"protection_data_required": True}, p=empty)
        self.assertEqual(res["results"][0]["verdict"], "BLOCK")
        self.assertIn("protection data missing", why(res))
        res = ck(cfg={"mode": "live", "kite_enabled": True}, p=empty)
        self.assertIn("protection data missing", why(res))
        res = ck(cfg={"mode": "live", "kite_enabled": True, "protection_data_required": False}, p=empty)
        self.assertNotIn("protection data missing", why(res))

    def test_stale_prices_block_entries_after_three_minutes(self):
        t_old = ist("2026-10-30 10:25")
        res = ck(prices={"SYNTHA": 1416.0}, times={"SYNTHA": t_old})
        self.assertIn("stale: the latest SYNTHA price is 5 minutes old", why(res))
        res = ck(prices={"SYNTHA": 1416.0}, times={"SYNTHA": ist("2026-10-30 10:28")})
        self.assertNotIn("stale", why(res))
        res = ck(prices={"SYNTHA": 1416.0}, times={})
        self.assertTrue(any("age could not be checked" in w for w in res["results"][0]["warnings"]))


class SizeIntegration(unittest.TestCase):
    def test_surveillance_refuses_the_order(self):
        s = size.size_trade(PLAN, {}, today=TODAY, prot=prot(**{"surveillance.json": {"date": TODAY, "asm": ["SYNTHA"], "gsm": []}}))
        self.assertIn("ASM", s["refusal"])

    def test_init_writes_percentages_and_a_database(self):
        d = tempfile.mkdtemp()
        try:
            cfgp = os.path.join(d, "trader", "config.json")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(size.main(["init", "--config", cfgp]), 0)
            with open(cfgp, encoding="utf-8") as fh:
                cfg = json.load(fh)
            self.assertEqual(cfg["limits_pct"]["max_loss_per_trade_pct"], 0.5)
            self.assertEqual(cfg["max_loss_per_trade_inr"], 500.0)
            self.assertTrue(os.path.isfile(os.path.join(d, "trader", "trader.db")))
            self.assertIn("percent of capital", out.getvalue())
        finally:
            shutil.rmtree(d)


class ScannerProtect(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.out = os.path.join(self.d, "trader", "watchlist.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def run_scan(self, *argv):
        o, e = io.StringIO(), io.StringIO()
        with redirect_stdout(o), redirect_stderr(e):
            code = scan.main(list(argv))
        return code, o.getvalue(), e.getvalue()

    def csv(self, name, text):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def test_protect_converts_exports(self):
        asm = self.csv("asm.csv", "Symbol,Name,Stage\nSYNTHD,Delta,I\nSYNTHE,Eps,II\n")
        gsm = self.csv("gsm.csv", "SYMBOL\nSYNTHC\n")
        mis = self.csv("mis.csv", "Symbol,Margin\nSYNTHA,5x\nSYNTHB,5x\nSYNTHD,5x\n")
        bands = self.csv("bands.csv", "Symbol,Series,Security Name,Band\nSYNTHA,EQ,Alpha,20\nSYNTHB,EQ,Beta,No Band\n")
        code, out, _ = self.run_scan("protect", "--asm", asm, "--gsm", gsm, "--mis", mis, "--bands", bands,
                                     "--out", self.out, "--now", "2026-10-30 08:30")
        self.assertEqual(code, 0, out)
        sd = os.path.join(self.d, "trader", "state")
        with open(os.path.join(sd, "surveillance.json")) as fh:
            self.assertEqual(json.load(fh), {"date": TODAY, "asm": ["SYNTHD", "SYNTHE"], "gsm": ["SYNTHC"]})
        with open(os.path.join(sd, "mis.json")) as fh:
            self.assertEqual(json.load(fh)["symbols"], ["SYNTHA", "SYNTHB", "SYNTHD"])
        with open(os.path.join(sd, "bands.json")) as fh:
            self.assertEqual(json.load(fh)["bands"], {"SYNTHA": 20.0, "SYNTHB": None})

    def test_protect_needs_something_and_a_band_column(self):
        self.assertEqual(self.run_scan("protect", "--out", self.out)[0], 1)
        bad = self.csv("b.csv", "Symbol,Series\nA,EQ\n")
        code, _, err = self.run_scan("protect", "--bands", bad, "--out", self.out)
        self.assertEqual(code, 1)
        self.assertIn("no price-band column", err)

    def test_the_scan_drops_surveillance_and_non_mis_stocks_and_says_so(self):
        asm = self.csv("asm.csv", "Symbol\nSYNTHD\n")
        mis = self.csv("mis.csv", "Symbol\nSYNTHA\nSYNTHB\nSYNTHC\nSYNTHD\n")
        self.run_scan("protect", "--asm", asm, "--mis", mis, "--out", self.out, "--now", "2026-10-30 08:30")
        code, out, _ = self.run_scan("daily", "--offline", os.path.join(FIX, "nse", "daily"), "--now", "2026-10-30 08:30",
                                     "--out", self.out, "--quiet", "--min-avg-volume", "1")
        self.assertIn("Removed before scanning", out)
        with open(self.out) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["excluded"]["asm"], 1)
        self.assertGreaterEqual(doc["excluded"]["not_mis"], 1)
        self.assertNotIn("SYNTHD", [h["symbol"] for h in doc["hits"]])

    def test_without_the_lists_nothing_is_dropped_and_the_scan_says_so(self):
        code, out, _ = self.run_scan("daily", "--offline", os.path.join(FIX, "nse", "daily"), "--now", "2026-10-30 08:30",
                                     "--out", self.out, "--quiet", "--min-avg-volume", "1")
        self.assertIn("ASM/GSM lists not loaded", out)
        self.assertIn("MIS-eligible list not loaded", out)

    def test_market_typed_in_and_offline_fetch(self):
        code, out, _ = self.run_scan("market", "--vix", "14.2", "--gap", "0.3", "--out", self.out, "--now", "2026-10-30 08:30")
        self.assertIn("India VIX 14.2", out)
        with open(os.path.join(self.d, "trader", "state", "market.json")) as fh:
            self.assertEqual(json.load(fh)["nifty_gap_pct"], 0.3)
        off = os.path.join(self.d, "off")
        def doc(sym, opens, closes, prev):
            return {"chart": {"result": [{"meta": {"symbol": sym, "chartPreviousClose": prev},
                                          "timestamp": [1, 2], "indicators": {"quote": [{"open": opens, "high": closes,
                                                                                         "low": opens, "close": closes, "volume": [1, 1]}]}}],
                              "error": None}}
        write(os.path.join(off, "INDIAVIX.json"), doc("^INDIAVIX", [13.5, 13.6], [13.6, 13.9], 13.0))
        write(os.path.join(off, "NSEI.json"), doc("^NSEI", [24300.0, 24310.0], [24310.0, 24320.0], 24000.0))
        code, out, _ = self.run_scan("market", "--fetch", "--offline", off, "--out", self.out, "--now", "2026-10-30 08:30")
        self.assertEqual(code, 0, out)
        self.assertIn("India VIX 13.9", out)
        self.assertIn("+1.25%", out)
        self.assertEqual(self.run_scan("market", "--out", self.out)[0], 1)


if __name__ == "__main__":
    unittest.main()
