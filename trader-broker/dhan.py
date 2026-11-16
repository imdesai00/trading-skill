#!/usr/bin/env python3
"""The Dhan adapter for trader-broker. Standard library only. DISABLED unless you turn it on.

Nothing here talks to Dhan. It has no network code and holds no credentials. In live mode the orders go through
Dhan's MCP tools, called by Claude after the gates below pass and after Claude's own "Allow once" card. This file
builds the exact tool call from the approved order, refuses a call that differs from the one you approved,
refuses duplicates, and records what happened. It reuses the Kite adapter's gates and bookkeeping
(`kite.py`, same folder): only what is different about Dhan lives here.

It runs only when ALL of these are true, set or done by you: "mode": "live", "broker": "dhan",
"dhan_enabled": true, and a Dhan check from today (dhan.py check).

    python3 dhan.py securities --csv api-scrip-master.csv   # build the symbol -> securityId map (NSE equity)
    python3 dhan.py check --profile F --margins F --positions F --tools-need-approval --static-ip-confirmed
    python3 dhan.py prepare --index 1 --open-orders F --ltp F --margins F   # all gates -> the one call to make
    python3 dhan.py record --index 1 --order-id ID          # after Claude's Allow once card (or --failed / --denied)
    python3 dhan.py sync --orders-file F --trades-file F --positions-file F
    python3 dhan.py reconcile --orders-file F --trades-file F --positions-file F --margins F
    python3 dhan.py set-charges --trade LT-... --amount 70.25
    python3 dhan.py prices --ltp F
    python3 dhan.py status

How a Dhan trade differs from the Kite one. The entry is ONE super order: entry, stop-loss and target legs in a
single request, so the stop exists from the moment the entry fills and the target rests too. One ticket, one risk
PASS, one typed yes, one Allow once card. Leaving early is two steps like before: cancel the super order
(its own yes), then sell (its own yes). Dhan wants a numeric securityId, not a symbol, and API market orders are
turned into limit orders, so a Dhan ENTRY must be a limit order (size.py --limit): the price is then part of what
you approved.

None of the tool names or field names below has been run against your account. The tool names are a table you
can override in the config ("dhan_tools"); the field names come from DhanHQ's public API pages, SDK references
and community examples (VERIFY.md section 15, RECHECK-AT-DHAN). Where a name is unknown the code fails loudly.
"""

import argparse
import csv
import datetime
import hashlib
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db  # noqa: E402  (a copy of trader-db/db.py; a test keeps the copies identical)
import kite  # noqa: E402  (the Kite adapter, same folder: the gates and the bookkeeping are shared)

DEFAULT_TOOLS = {"profile": "get_profile", "orders": "get_orders", "positions": "get_positions",
                 "trades": "get_trades", "place": "place_order", "place_super": "place_super_order",
                 "cancel": "cancel_order", "cancel_super": "cancel_super_order", "ltp": "get_market_ltp",
                 "margins": "get_fund_limits", "holdings": "get_holdings"}
SEGMENT = "NSE_EQ"
STATUS = {"TRADED": "COMPLETE", "TRANSIT": "OPEN", "PENDING": "OPEN", "PART_TRADED": "OPEN",
          "CANCELLED": "CANCELLED", "EXPIRED": "CANCELLED", "REJECTED": "REJECTED"}
IDS_FILE = "trader/state/dhan_securities.json"


class DhanError(kite.KiteError):
    pass


class DhanDisabled(kite.KiteDisabled):
    pass


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def first(d, *names):
    """The first present key among several spellings (Dhan's pages and SDKs disagree on some names)."""
    for n in names:
        if isinstance(d, dict) and d.get(n) is not None:
            return d[n]
    return None


def tools_for(cfg):
    t = dict(DEFAULT_TOOLS)
    t.update({k: str(v) for k, v in ((cfg or {}).get("dhan_tools") or {}).items() if k in DEFAULT_TOOLS})
    return t


def enabled(cfg, state, today):
    """(ok, why). Live mode, broker dhan, dhan_enabled and a Dhan check from today. Nothing else comes before this."""
    cfg = cfg or {}
    if str(cfg.get("mode") or "paper").lower() != "live":
        return False, "the Dhan adapter is off: \"mode\" is not \"live\" (paper mode never contacts a broker)"
    if str(cfg.get("broker") or "").lower() != "dhan":
        return False, "the Dhan adapter is off: \"broker\" is not \"dhan\" (only you set it)"
    if not cfg.get("dhan_enabled"):
        return False, "the Dhan adapter is off: \"dhan_enabled\" is not true (only you set it)"
    st = state or {}
    if not st.get("verified") or str(st.get("date")) != today:
        return False, ("Dhan is not verified today: connect Dhan yourself, then dhan.py check --profile F --margins F "
                       "--positions F --tools-need-approval --static-ip-confirmed (the login lasts one day)")
    return True, ""


# ---------------------------------------------------------------- the security id map

def load_ids(cfg=None, path=None):
    path = path or (cfg or {}).get("dhan_securities_file") or IDS_FILE
    if not os.path.isfile(path):
        raise DhanError("no symbol-to-securityId map at %s: build it with dhan.py securities --csv "
                        "api-scrip-master.csv (Dhan identifies stocks by number, not by symbol)" % path)
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    return {str(k).upper(): str(v) for k, v in (doc.get("ids") or {}).items()}


def security_id(sym, cfg):
    ids = load_ids(cfg)
    if sym not in ids:
        raise DhanError("%s has no securityId in the map: it is not an NSE equity in Dhan's list, or the symbol is "
                        "ambiguous (dhan.py securities lists those)" % sym)
    return ids[sym]


def read_security_list(text):
    """Dhan's instrument CSV -> (ids {SYM: id}, ambiguous [SYM], counts). NSE equity only. Columns found by name."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise DhanError("the instrument list is empty")
    head = [c.strip().lower() for c in rows[0]]

    def col(*words):
        return next((i for i, h in enumerate(head) if all(w in h for w in words)), None)
    i_id, i_sym = col("security", "id"), col("trading", "symbol")
    i_exch, i_series = col("exch"), col("series")
    i_seg, i_inst = col("segment"), col("instrument")
    if i_id is None or i_sym is None:
        raise DhanError("no security id / trading symbol column in that file (columns: %s)" % ", ".join(head[:12]))
    ids, seen, amb = {}, {}, set()
    counts = {"rows": len(rows) - 1, "kept": 0}
    for r in rows[1:]:
        def cell(i):
            return r[i].strip().upper() if i is not None and i < len(r) else ""
        if i_exch is not None and cell(i_exch) != "NSE":
            continue
        if i_series is not None and cell(i_series) != "EQ":
            continue
        if i_seg is not None and cell(i_seg) not in ("E", "EQ", "EQUITY"):
            continue
        if i_inst is not None and cell(i_inst) not in ("EQUITY", "EQ", ""):
            continue
        sym, sid = cell(i_sym), r[i_id].strip()
        sym = re.sub(r"-EQ$", "", sym)
        if not sym or not sid:
            continue
        if sym in seen and seen[sym] != sid:
            amb.add(sym)
        seen[sym] = sid
    for sym, sid in seen.items():
        if sym not in amb:
            ids[sym] = sid
    counts["kept"] = len(ids)
    return ids, sorted(amb), counts


# ---------------------------------------------------------------- the call

payload_hash = kite.payload_hash


def tag_for(order):
    return "IDT" + kite.fingerprint(order)[:14]


def build_call(order, cfg=None):
    """The ONE Dhan tool call for an approved order, built only from the order's own (fingerprinted) fields and the
    config. Returns {"tool", "arguments", "tag"}."""
    cfg = cfg or {}
    tools = tools_for(cfg)
    sym = str(order.get("symbol", "")).strip().upper()
    side = str(order.get("side", "")).lower()
    qty = int(_f(order.get("qty")) or 0)
    if str(order.get("exchange", "NSE")).upper() != "NSE" or str(order.get("product", "MIS")).upper() != "MIS":
        raise DhanError("only NSE and MIS (Dhan INTRADAY) orders can be built")
    if order.get("action") == "cancel":
        if not order.get("order_ref"):
            raise DhanError("a cancel needs order_ref, the Dhan order id of the super order")
        return {"tool": tools["cancel_super"], "arguments": {"orderId": str(order["order_ref"]),
                                                              "orderLeg": "ENTRY_LEG"}, "tag": None}
    if not sym or qty <= 0 or side not in ("buy", "sell"):
        raise DhanError("the order needs a symbol, a quantity and a side")
    etype = str(order.get("entry_type", "market")).lower()
    if etype == "stop":
        raise DhanError("with Dhan the stop-loss is a leg of the entry super order: there is no separate stop order")
    sid = security_id(sym, cfg)
    tag = tag_for(order)
    if side == "buy":
        if etype != "limit":
            raise DhanError("a Dhan entry must be a limit order (the API turns market orders into limit orders "
                            "anyway): run size.py --limit so the price is part of what you approve")
        price, stop, target = _f(order.get("limit_price")), _f(order.get("stop")), _f(order.get("target"))
        if not price or not stop or not target:
            raise DhanError("a super order needs a limit price, a stop and a target")
        if not stop < price < target:
            raise DhanError("a long super order needs stop < entry < target (got %s, %s, %s)" % (stop, price, target))
        return {"tool": tools["place_super"], "tag": tag,
                "arguments": {"transactionType": "BUY", "exchangeSegment": SEGMENT, "productType": "INTRADAY",
                              "orderType": "LIMIT", "securityId": sid, "quantity": qty, "price": price,
                              "targetPrice": target, "stopLossPrice": stop, "trailingJump": 0, "correlationId": tag}}
    if etype not in ("market", "limit"):
        raise DhanError("a Dhan exit is a market or limit sell")
    a = {"transactionType": "SELL", "exchangeSegment": SEGMENT, "productType": "INTRADAY", "securityId": sid,
         "quantity": qty, "validity": "DAY", "correlationId": tag}
    if etype == "limit":
        lp = _f(order.get("limit_price"))
        if not lp:
            raise DhanError("a limit order needs limit_price")
        a.update(orderType="LIMIT", price=lp)
    else:
        a.update(orderType="MARKET", price=0)
    return {"tool": tools["place"], "arguments": a, "tag": tag}


# ---------------------------------------------------------------- the adapter

def _deep_find(doc, key):
    """Find doc[..][key] for a nested {"NSE_EQ": {"11536": {...}}} style result."""
    if isinstance(doc, dict):
        if key in doc:
            return doc[key]
        for v in doc.values():
            r = _deep_find(v, key)
            if r is not None:
                return r
    return None


class DhanAdapter(kite.KiteAdapter):
    NAME = "Dhan"
    STATE_FILE = "dhan.json"
    STOP_WITH_ENTRY = True
    MATCH_SELLS_BY_SYMBOL = True

    def __init__(self, cfg, call=None, base="trader", now=None):
        kite.KiteAdapter.__init__(self, cfg, call, base, now)
        self.tools = tools_for(self.cfg)

    def enabled_fn(self, cfg, state, today):
        ok, why = enabled(cfg, state, today)
        if not ok:
            return ok, why
        return True, ""

    def err(self, msg):
        return DhanError(str(msg).replace("Kite", self.NAME))

    def dis(self, msg):
        return DhanDisabled(str(msg).replace("Kite", self.NAME))

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
        return o.get("tag")

    def tool(self, key):
        return self.tools[key]

    def ltp_args(self, order):
        sym = str(order.get("symbol", "")).upper()
        return {SEGMENT: [int(security_id(sym, self.cfg))]}

    def price_of(self, ltp, sym):
        doc = ltp.get("data", ltp) if isinstance(ltp, dict) else {}
        try:
            sid = security_id(sym, self.cfg)
        except DhanError:
            sid = None
        node = _deep_find(doc, sid) if sid else None
        if node is None:
            node = doc.get(sym)
        if isinstance(node, dict):
            node = first(node, "last_price", "lastPrice", "ltp", "LTP")
        return _f(node)

    def cash_of(self, margins):
        doc = margins.get("data", margins) if isinstance(margins, dict) else {}
        return _f(first(doc, "availabelBalance", "availableBalance", "available_balance", "availableCash",
                        "net", "withdrawableBalance"))

    def order_id_from(self, res):
        """Dhan answers {"status": "success", "data": {"orderId": ..., "orderStatus": ...}}. A reply that says it
        did not succeed gives no id, so the order is recorded as failed."""
        if not isinstance(res, dict):
            return res
        if str(res.get("status") or "success").lower() not in ("success", "ok"):
            return None
        data = res.get("data") if isinstance(res.get("data"), dict) else res
        oid = first(data, "orderId", "order_id")
        return str(oid) if oid else None

    def norm_order(self, o):
        st = str(first(o, "orderStatus", "status") or "").upper()
        return {"order_id": str(first(o, "orderId", "order_id") or ""), "status": STATUS.get(st, st),
                "average_price": _f(first(o, "averageTradedPrice", "avgTradePrice", "average_price", "avgPrice")) or 0,
                "filled_quantity": int(_f(first(o, "filledQty", "tradedQuantity", "filled_quantity")) or 0),
                "quantity": int(_f(first(o, "quantity")) or 0),
                "order_timestamp": first(o, "createTime", "updateTime", "exchangeTime", "order_timestamp"),
                "tag": first(o, "correlationId", "tag"),
                "status_message": first(o, "omsErrorDescription", "status_message"),
                "tradingsymbol": str(first(o, "tradingSymbol", "tradingsymbol") or "").upper(),
                "transaction_type": str(first(o, "transactionType", "transaction_type") or "").upper()}

    def norm_trade(self, x):
        return {"order_id": str(first(x, "orderId", "order_id") or ""),
                "tradingsymbol": str(first(x, "tradingSymbol", "tradingsymbol") or "").upper(),
                "transaction_type": str(first(x, "transactionType", "transaction_type") or "").upper(),
                "quantity": int(_f(first(x, "tradedQuantity", "filledQty", "quantity")) or 0),
                "average_price": _f(first(x, "tradedPrice", "averageTradedPrice", "avgTradePrice", "average_price")) or 0,
                "fill_timestamp": first(x, "createTime", "updateTime", "exchangeTime", "fill_timestamp"),
                "product": "MIS" if str(first(x, "productType", "product") or "INTRADAY").upper() in ("INTRADAY", "INTRA", "MIS")
                else str(first(x, "productType", "product")).upper()}

    def norm_position(self, p):
        return {"tradingsymbol": str(first(p, "tradingSymbol", "tradingsymbol") or "").upper(),
                "quantity": int(_f(first(p, "netQty", "quantity")) or 0),
                "product": "MIS" if str(first(p, "productType", "product") or "INTRADAY").upper() in ("INTRADAY", "INTRA", "MIS")
                else str(first(p, "productType", "product")).upper()}

    def exit_reason(self, t, sells, vwap, last_t, stop_id):
        hint = t.get("exit_reason_hint")
        if hint in ("target", "flat-by", "manual"):
            return hint
        tgt, stop = t.get("target"), t.get("stop")
        if tgt and stop and vwap >= tgt - 0.005:
            return "target"
        if tgt and stop and abs(vwap - stop) <= abs(vwap - tgt) and vwap <= (t.get("entry_fill") or vwap):
            return "stop"
        hh = kite._hhmm(last_t)
        return "flat-by" if hh and hh >= t["flat_by"] else "manual"

    def known_trade(self, x, led, known_orders):
        return str(x.get("order_id")) in known_orders or any(t["symbol"] == x.get("tradingsymbol")
                                                              for t in led["trades"])

    def closed_trade_at_broker(self, t, broker_trades):
        return any(x.get("tradingsymbol") == t["symbol"] and x.get("transaction_type") == "SELL" for x in broker_trades)


# ---------------------------------------------------------------- the command line

def _cfg(a):
    return kite._cfg(a)


def cmd_securities(a):
    with open(a.csv, encoding="utf-8", errors="replace") as fh:
        ids, amb, counts = read_security_list(fh.read())
    out = a.out
    d = os.path.dirname(out)
    if d:
        os.makedirs(d, exist_ok=True)
    now = kite.parse_now(a.now)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"date": kite.to_ist(now).date().isoformat(), "source": os.path.basename(a.csv), "ids": ids,
                   "ambiguous": amb}, fh, indent=2)
        fh.write("\n")
    kite.say("Read %d rows; kept %d NSE equity symbols with one securityId each; wrote %s." % (
        counts["rows"], counts["kept"], out))
    if amb:
        kite.say("AMBIGUOUS (not mapped, because one symbol has several ids): " + ", ".join(amb[:20])
                 + (" ..." if len(amb) > 20 else ""))
    kite.say("Refresh this when Dhan updates its instrument list. An order for an unmapped symbol is refused.")
    return 0


def cmd_check(a):
    """The read-only connection check. No order is possible until this passes today."""
    cfg = _cfg(a)
    now = kite.parse_now(a.now)
    today = kite.to_ist(now).date().isoformat()
    for k, want, why in (("mode", "live", "\"mode\" must be \"live\""), ("broker", "dhan", "\"broker\" must be \"dhan\""),
                         ("dhan_enabled", True, "\"dhan_enabled\" must be true")):
        if (str(cfg.get(k)).lower() if k in ("mode", "broker") else cfg.get(k)) != want:
            kite.say("dhan.py: %s (set by you, in trader/config.json) before the adapter does anything." % why,
                     file=sys.stderr)
            return 1
    prof = kite.read_json(a.profile, "the saved profile or funds result (save what the tool returned)")
    if not isinstance(prof, dict) or not prof or "error" in prof:
        kite.say("dhan.py: that does not look like a Dhan result (empty or an error): connect Dhan again and call "
                 "the profile tool.", file=sys.stderr)
        return 1
    if not a.tools_need_approval:
        kite.say("dhan.py: not verified. Every Dhan tool that places, modifies, cancels or exits orders (orders, super "
                 "orders, forever orders) must be set to \"Needs approval\" in Claude (Customize, Connectors, the Dhan "
                 "connector). A script cannot see that setting: check it yourself, then run this again with "
                 "--tools-need-approval.", file=sys.stderr)
        return 1
    if not a.static_ip_confirmed:
        kite.say("dhan.py: not verified. Dhan's order APIs are documented as needing a registered static IP. Check on "
                 "Dhan whether your route needs one and that it is registered, then run this again with "
                 "--static-ip-confirmed.", file=sys.stderr)
        return 1
    ad = DhanAdapter(cfg, None, a.base, now)
    problems, notes = [], []
    if a.margins:
        cash = ad.cash_of(kite._load_saved(a.margins, "the saved funds result"))
        notes.append("Dhan shows %s available." % (kite.inr(cash, True) if cash is not None else "no readable cash figure"))
        if cash is None:
            problems.append("the funds result has no cash figure I can read")
        elif cash <= 0:
            problems.append("Dhan shows no available cash")
    else:
        problems.append("pass --margins (the funds result): the available cash must be read")
    if a.positions:
        pos = kite._load_saved(a.positions, "the saved positions result")
        raw = (pos.get("net") if isinstance(pos, dict) and "net" in pos else pos) or []
        open_pos = {}
        for p in (ad.norm_position(q) for q in raw if isinstance(q, dict)):
            if p["quantity"]:
                open_pos[p["tradingsymbol"]] = p["quantity"]
        _, led = ad.load_live()
        ours = {t["symbol"]: t["qty"] for t in led["trades"] if t["status"] in ("open", "pending_fill")}
        for sym, q in open_pos.items():
            if ours.get(sym) != q:
                problems.append("Dhan shows %d %s open and this record has %s: unexpected position" % (q, sym, ours.get(sym, 0)))
        notes.append("%d open position%s at Dhan." % (len(open_pos), "" if len(open_pos) == 1 else "s"))
    else:
        problems.append("pass --positions (the positions result): unexpected open positions must be ruled out")
    try:
        load_ids(cfg)
    except DhanError as e:
        problems.append(str(e))
    if problems:
        kite.say("dhan.py: NOT verified:", file=sys.stderr)
        for pr in problems:
            kite.say("  - " + pr, file=sys.stderr)
        return 1
    kite.write_json(a.out, {"verified": True, "date": today, "broker": "dhan",
                            "checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "order_tools_need_approval_confirmed_by_user": True, "static_ip_confirmed_by_user": True,
                            "note": "no account details are stored"})
    kite.say("Dhan verified for %s. %s" % (today, " ".join(notes)))
    kite.say("Dhan's login lasts one day: connect again tomorrow before you trade.")
    return 0


def cmd_prices(a):
    now = kite.parse_now(a.now)
    cfg = _cfg(a)
    ltp = kite._load_saved(a.ltp, "the saved last-price result")
    ad = DhanAdapter(cfg, None, a.base, now)
    prices, detail = {}, {}
    for sym in load_ids(cfg):
        px = ad.price_of(ltp, sym)
        if px is not None:
            prices[sym] = px
            detail[sym] = {"time": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "dhan ltp"}
    if not prices:
        raise DhanError("no prices for mapped symbols in that result")
    kite.write_json(a.out, {"as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "dhan", "prices": prices,
                            "detail": detail})
    kite.say("Saved %d Dhan price%s to %s: %s." % (len(prices), "" if len(prices) == 1 else "s", a.out,
                                                   ", ".join("%s %.2f" % kv for kv in sorted(prices.items()))))
    return 0


def cmd_status(a):
    cfg = _cfg(a)
    now = kite.parse_now(a.now)
    ok, why = enabled(cfg, kite.read_json(os.path.join(a.base, "state", "dhan.json"), "dhan state", required=False),
                      kite.to_ist(now).date().isoformat())
    kite.say("Dhan adapter: %s" % ("ON (live mode, enabled, verified today)" if ok else "OFF. " + why))
    _, led = DhanAdapter(cfg, None, a.base, now).load_live()
    if led and led.get("trades"):
        kite.say("Live ledger: balance %s, %d trades." % (kite.inr(led["balance"], True), len(led["trades"])))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="The Dhan adapter (trader-broker). Off unless live, broker dhan and enabled.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--base", default="trader", help="the trader/ folder")
        p.add_argument("--now")

    p = sub.add_parser("securities", help="build the symbol -> securityId map from Dhan's instrument CSV")
    common(p)
    p.add_argument("--csv", required=True)
    p.add_argument("--out", default=IDS_FILE)
    p = sub.add_parser("check")
    common(p)
    p.add_argument("--profile", required=True)
    p.add_argument("--margins")
    p.add_argument("--positions")
    p.add_argument("--tools-need-approval", action="store_true",
                   help="you confirm every Dhan order tool is set to Needs approval in Claude")
    p.add_argument("--static-ip-confirmed", action="store_true",
                   help="you confirm Dhan's static-IP requirement is met for your route")
    p.add_argument("--out", default="trader/state/dhan.json")
    p = sub.add_parser("prepare")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--orders", default="trader/orders.json")
    p.add_argument("--open-orders", required=True, help="the saved get_orders result")
    p.add_argument("--ltp", help="the saved last-price result (required for an entry)")
    p.add_argument("--margins", help="the saved funds result (required for an entry)")
    p = sub.add_parser("record")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--orders", default="trader/orders.json")
    p.add_argument("--order-id")
    p.add_argument("--failed", action="store_true")
    p.add_argument("--denied", action="store_true")
    p.add_argument("--note")
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
    kite.NAME, kite.PROG, kite.ADAPTER_CLS = "Dhan", "dhan.py", DhanAdapter
    try:
        fn = {"securities": cmd_securities, "check": cmd_check, "prepare": kite.cmd_prepare, "record": kite.cmd_record,
              "sync": kite.cmd_sync, "reconcile": kite.cmd_reconcile, "set-charges": kite.cmd_set_charges,
              "prices": cmd_prices, "status": cmd_status}[a.cmd]
        return fn(a)
    except kite.KiteDisabled as e:
        kite.say("dhan.py: OFF. %s" % e, file=sys.stderr)
        return 2
    except kite.KiteError as e:
        kite.say("dhan.py: %s" % e, file=sys.stderr)
        return 1
    finally:
        kite.NAME, kite.PROG, kite.ADAPTER_CLS = saved


if __name__ == "__main__":
    sys.exit(main())
