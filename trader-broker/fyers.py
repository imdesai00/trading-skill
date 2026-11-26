#!/usr/bin/env python3
"""The FYERS adapter for trader-broker. Standard library only. DISABLED unless you turn it on.

Nothing here talks to FYERS. It has no network code and holds no credentials. In live mode the orders go through FYERS's
MCP tools, called by Claude after the gates below pass and after Claude's own "Allow once" card. This file builds the exact
tool call from the approved order, refuses a call that differs from the one you approved, refuses duplicates, and records
what happened. It reuses the Kite adapter's gates and bookkeeping (`kite.py`, same folder).

It runs only when ALL of these are true, set or done by you: "mode": "live", "broker": "fyers", "fyers_enabled": true, and
a FYERS check from today (fyers.py check).

    python3 fyers.py check --profile F --margins F --positions F --tools-need-approval --static-ip-confirmed
    python3 fyers.py prepare --index 1 --open-orders F --ltp F --margins F   # all gates -> the one call to make
    python3 fyers.py record --index 1 --order-id ID      # after Claude's Allow once card (or --failed / --denied)
    python3 fyers.py stop-ticket --trade LT-...          # after the entry fills: the protective stop ticket
    python3 fyers.py sync --orders-file F --trades-file F --positions-file F
    python3 fyers.py reconcile --orders-file F --trades-file F --positions-file F --margins F
    python3 fyers.py set-charges --trade LT-... --amount 70.25
    python3 fyers.py prices --ltp F
    python3 fyers.py status

How a FYERS trade works here: the same three orders as the Zerodha design. The entry is a plain INTRADAY order; after it fills
a separate stop-loss order (SL-M) rests at the broker; the exit is "cancel the stop (own yes), then sell (own yes)". The
target is an alert, not a resting order. FYERS's bracket and cover products are reported as deprecated and its attached
take-profit and stop-loss option is not verified, so neither is used. FYERS names a stock "NSE:SBIN-EQ", so no id map is
needed.

None of the tool names or field names below has been run against your account. The tool names are a table you can override
in the config ("fyers_tools"); the field names come from FYERS's public API pages, SDK references and community examples
(VERIFY.md section 16, RECHECK-AT-FYERS). Where a name is unknown the code fails loudly.
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db  # noqa: E402  (a copy of trader-db/db.py; a test keeps the copies identical)
import kite  # noqa: E402  (the Kite adapter, same folder: the gates and the bookkeeping are shared)

DEFAULT_TOOLS = {"profile": "get_profile", "orders": "get_orders", "positions": "get_positions", "trades": "get_trades",
                 "place": "place_order", "cancel": "cancel_order", "ltp": "get_quotes", "margins": "get_funds",
                 "holdings": "get_holdings"}
# FYERS order type and side codes (community answers to FYERS API questions): 1 limit, 2 market, 3 stop (SL-M), 4 stop-limit
MARKET, LIMIT, STOP_M, STOP_L = 2, 1, 3, 4
BUY, SELL = 1, -1
# orderbook status codes: 1 cancelled, 2 traded or filled, 4 transit, 5 rejected, 6 pending (the GTT page confirms only 1 and 2)
STATUS = {1: "CANCELLED", 2: "COMPLETE", 3: "OPEN", 4: "OPEN", 5: "REJECTED", 6: "OPEN"}


class FyersError(kite.KiteError):
    pass


class FyersDisabled(kite.KiteDisabled):
    pass


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def first(d, *names):
    for n in names:
        if isinstance(d, dict) and d.get(n) is not None:
            return d[n]
    return None


def tools_for(cfg):
    t = dict(DEFAULT_TOOLS)
    t.update({k: str(v) for k, v in ((cfg or {}).get("fyers_tools") or {}).items() if k in DEFAULT_TOOLS})
    return t


def fy_symbol(sym):
    """NSE:SBIN-EQ for SBIN. Symbols with & or - keep them (M&M, BAJAJ-AUTO)."""
    return "NSE:%s-EQ" % str(sym).strip().upper()


def plain_symbol(fy):
    m = re.match(r"^[A-Z]+:(.+)-EQ$", str(fy or "").strip().upper())
    return m.group(1) if m else str(fy or "").strip().upper()


def enabled(cfg, state, today):
    cfg = cfg or {}
    if str(cfg.get("mode") or "paper").lower() != "live":
        return False, "the FYERS adapter is off: \"mode\" is not \"live\" (paper mode never contacts a broker)"
    if str(cfg.get("broker") or "").lower() != "fyers":
        return False, "the FYERS adapter is off: \"broker\" is not \"fyers\" (only you set it)"
    if not cfg.get("fyers_enabled"):
        return False, "the FYERS adapter is off: \"fyers_enabled\" is not true (only you set it)"
    st = state or {}
    if not st.get("verified") or str(st.get("date")) != today:
        return False, ("FYERS is not verified today: connect FYERS yourself, then fyers.py check --profile F --margins F "
                       "--positions F --tools-need-approval --static-ip-confirmed (the login lasts one day)")
    return True, ""


def tag_for(order):
    return "IDT" + kite.fingerprint(order)[:14]


payload_hash = kite.payload_hash


def build_call(order, cfg=None):
    """The ONE FYERS tool call for an approved order, built only from the order's own (fingerprinted) fields and the config.
    Returns {"tool", "arguments", "tag"}."""
    cfg = cfg or {}
    tools = tools_for(cfg)
    sym = str(order.get("symbol", "")).strip().upper()
    side = str(order.get("side", "")).lower()
    qty = int(_f(order.get("qty")) or 0)
    if str(order.get("exchange", "NSE")).upper() != "NSE" or str(order.get("product", "MIS")).upper() != "MIS":
        raise FyersError("only NSE and MIS (FYERS INTRADAY) orders can be built")
    if order.get("action") == "cancel":
        if not order.get("order_ref"):
            raise FyersError("a cancel needs order_ref, the FYERS order id of the resting stop")
        return {"tool": tools["cancel"], "arguments": {"id": str(order["order_ref"])}, "tag": None}
    if not sym or qty <= 0 or side not in ("buy", "sell"):
        raise FyersError("the order needs a symbol, a quantity and a side")
    tag = tag_for(order)
    a = {"symbol": fy_symbol(sym), "qty": qty, "side": BUY if side == "buy" else SELL, "productType": "INTRADAY",
         "validity": "DAY", "disclosedQty": 0, "offlineOrder": False, "orderTag": tag, "limitPrice": 0, "stopPrice": 0}
    etype = str(order.get("entry_type", "market")).lower()
    if etype == "limit":
        lp = _f(order.get("limit_price"))
        if not lp:
            raise FyersError("a limit order needs limit_price")
        a.update(type=LIMIT, limitPrice=lp)
    elif etype == "stop":
        trig = _f(order.get("stop"))
        if side != "sell" or not trig:
            raise FyersError("a stop order is a sell with a trigger price")
        if str(cfg.get("stop_order_type", "SL-M")).upper() == "SL":
            buf = float(cfg.get("stop_limit_buffer_pct") or 0.5) / 100.0
            tick = _f(order.get("tick")) or kite.tick_size(trig)
            a.update(type=STOP_L, stopPrice=trig, limitPrice=kite.to_tick(trig * (1 - buf), tick, "down"))
        else:
            a.update(type=STOP_M, stopPrice=trig)
    elif etype == "market":
        a.update(type=MARKET)
    else:
        raise FyersError("entry_type must be market, limit or stop")
    return {"tool": tools["place"], "arguments": a, "tag": tag}


def _deep_find(doc, *keys):
    if isinstance(doc, dict):
        for k in keys:
            if k in doc:
                return doc[k]
        for v in doc.values():
            r = _deep_find(v, *keys)
            if r is not None:
                return r
    elif isinstance(doc, list):
        for v in doc:
            r = _deep_find(v, *keys)
            if r is not None:
                return r
    return None


class FyersAdapter(kite.KiteAdapter):
    NAME = "FYERS"
    STATE_FILE = "fyers.json"
    STOP_WITH_ENTRY = False
    MATCH_SELLS_BY_SYMBOL = True

    def __init__(self, cfg, call=None, base="trader", now=None):
        kite.KiteAdapter.__init__(self, cfg, call, base, now)
        self.tools = tools_for(self.cfg)

    def err(self, msg):
        return FyersError(str(msg).replace("Kite", self.NAME))

    def dis(self, msg):
        return FyersDisabled(str(msg).replace("Kite", self.NAME))

    def enabled_fn(self, cfg, state, today):
        return enabled(cfg, state, today)

    def require_enabled(self):
        ok, why = enabled(self.cfg, self._state(self.STATE_FILE), self.today)
        if not ok:
            raise self.dis(why)

    def build(self, order):
        return build_call(order, self.cfg)

    def tag(self, order):
        return tag_for(order)

    def tag_of_call(self, call):
        return call.get("tag")

    def tag_of_order(self, o):
        t = o.get("tag")
        return re.sub(r"^\d+:", "", str(t)) if t else t       # FYERS may put "1:" in front of the tag it returns

    def tool(self, key):
        return self.tools[key]

    def ltp_args(self, order):
        return {"symbols": fy_symbol(order.get("symbol", ""))}

    def price_of(self, ltp, sym):
        doc = ltp.get("data", ltp) if isinstance(ltp, dict) else {}
        want = fy_symbol(sym)
        rows = doc.get("d") if isinstance(doc, dict) else None
        if isinstance(rows, list):
            for r in rows:
                if isinstance(r, dict) and first(r, "n", "symbol") == want:
                    return _f(first(r.get("v") or r, "lp", "ltp", "last_price"))
        node = doc.get(want) if isinstance(doc, dict) else None
        if isinstance(node, dict):
            node = first(node, "lp", "ltp", "last_price")
        return _f(node)

    def cash_of(self, margins):
        doc = margins.get("data", margins) if isinstance(margins, dict) else {}
        rows = doc.get("fund_limit") if isinstance(doc, dict) else None
        if isinstance(rows, list):
            for r in rows:
                if "available" in str(first(r, "title") or "").lower():
                    return _f(first(r, "equityAmount", "amount"))
        return _f(first(doc, "availableBalance", "available_balance", "equityAmount"))

    def order_id_from(self, res):
        """FYERS answers {"s": "ok", "code": 1101, "message": "...", "id": "..."}. A reply that is not "ok" gives no id."""
        if not isinstance(res, dict):
            return res
        if str(res.get("s") or "ok").lower() not in ("ok", "success"):
            return None
        oid = first(res.get("data") if isinstance(res.get("data"), dict) else res, "id", "orderId", "order_id")
        return str(oid) if oid else None

    def norm_order(self, o):
        code = first(o, "status")
        status = STATUS.get(int(code), str(code)) if isinstance(code, (int, float)) or str(code).isdigit() else str(code or "").upper()
        side = first(o, "side")
        return {"order_id": str(first(o, "id", "orderId", "order_id") or ""), "status": status,
                "average_price": _f(first(o, "tradedPrice", "averageTradedPrice", "average_price")) or 0,
                "filled_quantity": int(_f(first(o, "filledQty", "tradedQty")) or 0),
                "quantity": int(_f(first(o, "qty", "quantity")) or 0),
                "order_timestamp": first(o, "orderDateTime", "order_timestamp"), "tag": first(o, "orderTag", "tag"),
                "status_message": first(o, "message", "status_message"),
                "tradingsymbol": plain_symbol(first(o, "symbol")),
                "transaction_type": "BUY" if side in (1, "1", "BUY") else "SELL" if side in (-1, "-1", "SELL") else ""}

    def norm_trade(self, x):
        side = first(x, "side")
        return {"order_id": str(first(x, "orderNumber", "id", "order_id") or ""),
                "tradingsymbol": plain_symbol(first(x, "symbol")),
                "transaction_type": "BUY" if side in (1, "1", "BUY") else "SELL" if side in (-1, "-1", "SELL") else "",
                "quantity": int(_f(first(x, "tradedQty", "qty", "quantity")) or 0),
                "average_price": _f(first(x, "tradePrice", "tradedPrice", "average_price")) or 0,
                "fill_timestamp": first(x, "orderDateTime", "fill_timestamp"),
                "product": "MIS" if str(first(x, "productType", "product") or "INTRADAY").upper() in ("INTRADAY", "MIS")
                else str(first(x, "productType", "product")).upper()}

    def norm_position(self, p):
        return {"tradingsymbol": plain_symbol(first(p, "symbol", "tradingsymbol")),
                "quantity": int(_f(first(p, "netQty", "quantity")) or 0),
                "product": "MIS" if str(first(p, "productType", "product") or "INTRADAY").upper() in ("INTRADAY", "MIS")
                else str(first(p, "productType", "product")).upper()}

    def exit_reason(self, t, sells, vwap, last_t, stop_id):
        return kite.price_exit_reason(t, vwap, last_t)

    def known_trade(self, x, led, known_orders):
        return str(x.get("order_id")) in known_orders or any(t["symbol"] == x.get("tradingsymbol") for t in led["trades"])

    def closed_trade_at_broker(self, t, broker_trades):
        return any(x.get("tradingsymbol") == t["symbol"] and x.get("transaction_type") == "SELL" for x in broker_trades)


# ---------------------------------------------------------------- the command line

def _cfg(a):
    return kite._cfg(a)


def cmd_check(a):
    return kite.live_check(a, "fyers", "FYERS", FyersAdapter, None, [
        ("static_ip_confirmed", "FYERS's order APIs may need a registered static IP under the 2026 retail algo rules (not "
                                "verified). Check on FYERS whether your route needs one and that it is registered, then run "
                                "this again with --static-ip-confirmed.")])


def cmd_prices(a):
    now = kite.parse_now(a.now)
    ltp = kite._load_saved(a.ltp, "the saved quotes result")
    ad = FyersAdapter(_cfg(a), None, a.base, now)
    doc = ltp.get("data", ltp) if isinstance(ltp, dict) else {}
    prices, detail = {}, {}
    for r in (doc.get("d") if isinstance(doc, dict) else None) or []:
        if isinstance(r, dict):
            sym = plain_symbol(first(r, "n", "symbol"))
            px = ad.price_of(ltp, sym)
            if px is not None:
                prices[sym] = px
                detail[sym] = {"time": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "fyers quotes"}
    if not prices:
        raise FyersError("no prices in that result")
    kite.write_json(a.out, {"as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "fyers", "prices": prices, "detail": detail})
    kite.say("Saved %d FYERS price%s to %s: %s." % (len(prices), "" if len(prices) == 1 else "s", a.out,
                                                    ", ".join("%s %.2f" % kv for kv in sorted(prices.items()))))
    return 0


def cmd_status(a):
    cfg = _cfg(a)
    now = kite.parse_now(a.now)
    ok, why = enabled(cfg, kite.read_json(os.path.join(a.base, "state", "fyers.json"), "fyers state", required=False),
                      kite.to_ist(now).date().isoformat())
    kite.say("FYERS adapter: %s" % ("ON (live mode, enabled, verified today)" if ok else "OFF. " + why))
    _, led = FyersAdapter(cfg, None, a.base, now).load_live()
    if led and led.get("trades"):
        kite.say("Live ledger: balance %s, %d trades." % (kite.inr(led["balance"], True), len(led["trades"])))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="The FYERS adapter (trader-broker). Off unless live, broker fyers and enabled.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--base", default="trader", help="the trader/ folder")
        p.add_argument("--now")

    p = sub.add_parser("check")
    common(p)
    p.add_argument("--profile", required=True)
    p.add_argument("--margins")
    p.add_argument("--positions")
    p.add_argument("--tools-need-approval", action="store_true", help="you confirm every FYERS order tool is on Needs approval")
    p.add_argument("--static-ip-confirmed", dest="static_ip_confirmed", action="store_true",
                   help="you confirm FYERS's static-IP requirement is met for your route")
    p.add_argument("--out", default="trader/state/fyers.json")
    p = sub.add_parser("prepare")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--orders", default="trader/orders.json")
    p.add_argument("--open-orders", required=True, help="the saved orders result")
    p.add_argument("--ltp", help="the saved quotes result (required for an entry)")
    p.add_argument("--margins", help="the saved funds result (required for an entry)")
    p = sub.add_parser("record")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--orders", default="trader/orders.json")
    p.add_argument("--order-id")
    p.add_argument("--failed", action="store_true")
    p.add_argument("--denied", action="store_true")
    p.add_argument("--note")
    p = sub.add_parser("stop-ticket")
    common(p)
    p.add_argument("--trade", required=True)
    p.add_argument("--orders", default="trader/orders.json")
    p = sub.add_parser("sync")
    common(p)
    p.add_argument("--orders-file", required=True)
    p.add_argument("--trades-file", required=True)
    p.add_argument("--positions-file")
    p = sub.add_parser("reconcile")
    common(p)
    p.add_argument("--orders-file", required=True)
    p.add_argument("--trades-file", required=True)
    p.add_argument("--positions-file", required=True)
    p.add_argument("--margins")
    p = sub.add_parser("set-charges")
    common(p)
    p.add_argument("--trade", required=True)
    p.add_argument("--amount", type=float, required=True, help="total charges from the contract note, rupees")
    p = sub.add_parser("prices")
    common(p)
    p.add_argument("--ltp", required=True)
    p.add_argument("--out", default="trader/prices.json")
    p = sub.add_parser("status")
    common(p)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    saved = (kite.NAME, kite.PROG, kite.ADAPTER_CLS)
    kite.NAME, kite.PROG, kite.ADAPTER_CLS = "FYERS", "fyers.py", FyersAdapter
    try:
        fn = {"check": cmd_check, "prepare": kite.cmd_prepare, "record": kite.cmd_record, "stop-ticket": kite.cmd_stop_ticket,
              "sync": kite.cmd_sync, "reconcile": kite.cmd_reconcile, "set-charges": kite.cmd_set_charges,
              "prices": cmd_prices, "status": cmd_status}[a.cmd]
        return fn(a)
    except kite.KiteDisabled as e:
        kite.say("fyers.py: OFF. %s" % e, file=sys.stderr)
        return 2
    except kite.KiteError as e:
        kite.say("fyers.py: %s" % e, file=sys.stderr)
        return 1
    finally:
        kite.NAME, kite.PROG, kite.ADAPTER_CLS = saved


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):      # Windows consoles default to a code page that cannot print the rupee sign
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
