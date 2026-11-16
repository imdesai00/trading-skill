#!/usr/bin/env python3
"""The Zerodha Kite adapter for trader-broker. Standard library only. DISABLED unless you turn it on.

It does nothing at all unless trader/config.json says BOTH "mode": "live" AND
"kite_enabled": true (both set by you, never by a tool) and a Kite check from
today says the connection works. It holds no key and no token and opens no
network connection: the orders go through the Kite MCP tools, called by Claude,
whose "Needs approval" card is the third gate. What this file adds is everything
around that call, in code instead of in good intentions:

    python3 kite.py check --profile FILE        # after get_profile: writes trader/state/kite.json (today)
    python3 kite.py prepare --index 1 --open-orders FILE   # gates, then the exact tool call to make
    python3 kite.py record --index 1 --order-id ID         # after Allow once: log it, once
    python3 kite.py stop-ticket --trade LT-20261030-01     # the stop-loss sell, after the entry filled
    python3 kite.py sync --orders F --trades F [--positions F]   # fills, stop, exits, charges, balance
    python3 kite.py status

The replacement for a bracket order (VERIFY.md section 2): Zerodha offers no
bracket order for intraday and, as far as could be checked, no cover order or
GTT for MIS. So a live trade is THREE separate orders, each with its own
trader-risk PASS, its own typed yes and its own Allow once card:
    1. the entry (BUY, MIS)
    2. after it fills, the stop-loss (SELL, SL-M, MIS) resting at Kite
    3. the exit, when the target alert rings or at 15:00: first cancel the stop
       (its own yes), then a market SELL (its own yes)
A resting target order is NOT used: a resting target and a resting stop could
both fill and leave you short.

Every gate, in code:
  enabled    mode live + kite_enabled true + a Kite check from today
  risk       trader-risk passed THIS order today, in live mode
  yes        your typed yes for this order is on record and nothing has been done
             with it yet (one yes, one order: placing, failing or denying uses it up)
  payload    the call is built from the approved order's own fields, and its hash
             must equal the hash recorded when you said yes
  duplicate  an order with the same tag already at Kite stops it (a retry must
             never double a position); the tag comes from the order's fingerprint

The Kite field names come from Zerodha's official Python client and the tool
names from the kite-mcp-server README; neither has been run against your account
(VERIFY.md, RECHECK-AT-KITE).
"""

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import charges as chg  # noqa: E402  (a copy of trader-budget/charges.py; a test keeps the copies identical)
import db  # noqa: E402  (a copy of trader-db/db.py; a test keeps the copies identical)

NEUTRAL_FIELDS = ("symbol", "exchange", "side", "qty", "product", "entry_type", "limit_price", "stop", "target",
                  "action", "order_ref")
TOOLS = {"profile": "get_profile", "orders": "get_orders", "positions": "get_positions", "trades": "get_trades",
         "place": "place_order", "cancel": "cancel_order", "ltp": "get_ltp", "margins": "get_margins",
         "holdings": "get_holdings"}
PRICE_MOVE_PCT = 0.3     # a live price this far from the planned entry means: new risk check, new yes
DEFAULT_STOP_TYPE = "SL-M"
DEFAULT_SLIPPAGE = 0.05


class KiteError(Exception):
    pass


class KiteDisabled(KiteError):
    pass


ClockError = KiteError

# ---------------------------------------------------------------- NSE clock (same block in every script)
# tests/test_repo.py checks that every copy of this block is identical.
# India Standard Time is UTC+05:30 all year (no daylight saving, no tz files).
# Holidays: NSE circular NSE/CMTR/71775 (2025-12-12) and public calendars, read
# 2026-10-29 (VERIFY.md section 3). The 2027 list is not published yet.
IST = datetime.timedelta(hours=5, minutes=30)
NSE_HOLIDAYS = {
    "2026-01-15": "Maharashtra municipal elections (in some lists only)",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
NSE_SPECIAL = {"2026-11-08": "Muhurat trading, Diwali"}  # short special sessions: never traded here
CALENDAR_YEARS = (2026,)
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
CAS_CLOSE = datetime.time(15, 15)  # continuous trading ends here for closing-auction (F&O) stocks


def to_ist(utc):
    """naive UTC -> naive India time."""
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    """naive India time -> naive UTC."""
    return local.replace(tzinfo=None) - IST


def ts_to_ist(ts):
    """Unix seconds -> naive India time. Never uses the computer's own time zone."""
    return datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(ts)) + IST


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is India time."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        raise ClockError('--now wants a time like "2026-10-30 10:15" (India time) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def trading_day(d):
    """(is_trading_day, reason) for an NSE date: weekends, holidays, special sessions, unknown years."""
    iso = d.isoformat()
    if iso in NSE_SPECIAL:
        return False, "special session (%s): not traded by this pack" % NSE_SPECIAL[iso]
    if d.weekday() >= 5:
        return False, "weekend"
    if d.year not in CALENDAR_YEARS:
        return False, ("the NSE holiday list for %d is not in this tool yet: add it from nseindia.com "
                       "(VERIFY.md section 3)" % d.year)
    if iso in NSE_HOLIDAYS:
        return False, "NSE holiday: " + NSE_HOLIDAYS[iso]
    return True, ""


def session(d):
    """(open, close) in India time on a trading day, else None."""
    return (OPEN, CLOSE) if trading_day(d)[0] else None


def inr(x, paise=None):
    """Rupees with Indian digit grouping: 100000 -> "₹1,00,000", 2913.4 -> "₹2,913.40".
    paise=True always shows paise, False never does, None shows them when not zero."""
    p = int(round(abs(float(x)) * 100))
    whole, frac = divmod(p, 100)
    if paise is False and frac:
        whole, frac = int(round(abs(float(x)))), 0
    s = str(whole)
    if len(s) > 3:
        head, groups = s[:-3], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join(([head] if head else []) + groups) + "," + s[-3:]
    if paise is True or (paise is None and frac):
        s += ".%02d" % frac
    return ("-" if float(x) < 0 and p else "") + "₹" + s


def tick_size(price):
    """NSE cash-market tick for a price (circular of 2025-03-13, VERIFY.md section 3).
    Kite's instrument list has each symbol's own tick: pass that instead once connected."""
    p = float(price)
    if p < 250:
        return 0.01
    for upper, tick in ((1000, 0.05), (5000, 0.10), (10000, 0.50), (20000, 1.00)):
        if p <= upper:
            return tick
    return 5.00


def to_tick(price, tick, way="nearest"):
    """A price on the tick grid, in whole paise: way "down", "up" or "nearest" (half up)."""
    t = int(round(float(tick) * 100))
    p = float(price) * 100
    if way == "down":
        n = math.floor(p / t + 1e-9)
    elif way == "up":
        n = math.ceil(p / t - 1e-9)
    else:
        n = math.floor(p / t + 0.5 + 1e-9)
    return round(n * t / 100.0, 2)
# ---------------------------------------------------------------- end NSE clock


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise KiteError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise KiteError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def r2(x):
    return round(float(x) + 1e-9, 2)


def fingerprint(order):
    """Same function as trader-risk, ticket.py and paper.py."""
    core = {k: order[k] for k in NEUTRAL_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def tag_for(order):
    """Kite's order tag (letters and digits, at most 20): the order's fingerprint, so a retry is recognisable."""
    return "IDT" + fingerprint(order)[:14]


# ---------------------------------------------------------------- the gates

def enabled(cfg, kite_state, today):
    """(ok, why). Live mode, kite_enabled and a Kite check from today. Nothing else comes before this."""
    if str((cfg or {}).get("mode") or "paper").lower() != "live":
        return False, "the Kite adapter is off: \"mode\" is not \"live\" (paper mode never contacts a broker)"
    if not (cfg or {}).get("kite_enabled"):
        return False, "the Kite adapter is off: \"kite_enabled\" is not true (only you set it)"
    ks = kite_state or {}
    if not ks.get("verified") or str(ks.get("date")) != today:
        return False, ("Kite is not verified today: log in to Kite yourself, call get_profile, then "
                       "kite.py check --profile FILE (an access token lasts one day)")
    return True, ""


def build_call(order, cfg=None):
    """The ONE Kite tool call for an approved order, built only from the order's own (fingerprinted) fields
    and the config. Returns {"tool", "arguments", "tag"}."""
    cfg = cfg or {}
    sym = str(order.get("symbol", "")).strip().upper()
    side = str(order.get("side", "")).lower()
    qty = int(_f(order.get("qty")) or 0)
    if str(order.get("exchange", "NSE")).upper() != "NSE" or str(order.get("product", "MIS")).upper() != "MIS":
        raise KiteError("only NSE and MIS orders can be built")
    if order.get("action") == "cancel":
        if not order.get("order_ref"):
            raise KiteError("a cancel needs order_ref")
        return {"tool": TOOLS["cancel"], "arguments": {"variety": "regular", "order_id": str(order["order_ref"])},
                "tag": None}
    if not sym or qty <= 0 or side not in ("buy", "sell"):
        raise KiteError("the order needs a symbol, a quantity and a side")
    a = {"variety": "regular", "exchange": "NSE", "tradingsymbol": sym, "transaction_type": side.upper(),
         "quantity": qty, "product": "MIS", "validity": "DAY", "tag": tag_for(order)}
    etype = str(order.get("entry_type", "market")).lower()
    if etype == "limit":
        lp = _f(order.get("limit_price"))
        if not lp:
            raise KiteError("a limit order needs limit_price")
        a.update(order_type="LIMIT", price=lp)
    elif etype == "stop":
        trig = _f(order.get("stop"))
        if side != "sell" or not trig:
            raise KiteError("a stop order is a sell with a trigger price")
        if str(cfg.get("stop_order_type", DEFAULT_STOP_TYPE)).upper() == "SL":
            buf = float(cfg.get("stop_limit_buffer_pct") or 0.5) / 100.0
            tick = _f(order.get("tick")) or tick_size(trig)
            a.update(order_type="SL", trigger_price=trig, price=to_tick(trig * (1 - buf), tick, "down"))
        else:
            a.update(order_type="SL-M", trigger_price=trig, market_protection=-1)
    elif etype == "market":
        a.update(order_type="MARKET", market_protection=-1)
    else:
        raise KiteError("entry_type must be market, limit or stop")
    return {"tool": TOOLS["place"], "arguments": a, "tag": a["tag"]}


def payload_hash(call):
    return hashlib.sha256(json.dumps(call, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the ledger

def new_live_ledger(cfg):
    cap = float((cfg or {}).get("capital_inr") or 100000)
    return {"mode": "live", "capital": cap, "balance": cap, "trades": [], "skipped": [],
            "note": "Live trades at Zerodha Kite. Real money. Fills and charges are as Kite reported them; the "
                    "contract note is the final word."}


def load_ledger(path, cfg):
    led = read_json(path, "live ledger", required=False) or new_live_ledger(cfg)
    led.setdefault("trades", [])
    led.setdefault("skipped", [])
    return led


def next_id(led, day):
    stem = "LT-%s-" % day.replace("-", "")
    n = max([int(t["trade_id"][len(stem):]) for t in led["trades"] if str(t.get("trade_id", "")).startswith(stem)]
            + [0])
    return "%s%02d" % (stem, n + 1)


def latest(state, fp, today):
    found = None
    for rec in (state or {}).get("orders", []):
        if rec.get("fingerprint") == fp and str(rec.get("date") or str(rec.get("at", ""))[:10]) == today:
            found = rec
    return found


# ---------------------------------------------------------------- the adapter

def last_price(ltp, sym):
    """The last traded price for `sym` from a get_ltp result: {"NSE:SYM": {"last_price": x}} or {"SYM": x}."""
    doc = ltp.get("data", ltp) if isinstance(ltp, dict) else {}
    for key in ("NSE:%s" % sym, sym):
        v = doc.get(key)
        if isinstance(v, dict):
            v = v.get("last_price", v.get("ltp"))
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def available_cash(margins):
    """Spendable equity cash from a get_margins result (equity.available.live_balance, else cash, else net)."""
    doc = margins.get("data", margins) if isinstance(margins, dict) else {}
    eq = doc.get("equity", doc) if isinstance(doc, dict) else {}
    av = eq.get("available", {}) if isinstance(eq, dict) else {}
    for src, key in ((av, "live_balance"), (av, "cash"), (eq, "net")):
        try:
            if src.get(key) is not None:
                return float(src[key])
        except (AttributeError, TypeError, ValueError):
            pass
    return None


class KiteAdapter(object):
    """All the live-order logic. `call(tool, arguments)` performs one Kite MCP tool call and returns its result;
    in real use Claude is the caller (see the CLI), in the tests it is a fake Kite."""

    # ---- broker hooks: dhan.py overrides these; Kite's behaviour is the default
    NAME = "Kite"
    STATE_FILE = "kite.json"
    STOP_WITH_ENTRY = False          # True when the stop travels inside the entry order (Dhan's super order)
    MATCH_SELLS_BY_SYMBOL = False    # True when exit fills carry order ids the ledger does not know

    def enabled_fn(self, cfg, state, today):
        return enabled(cfg, state, today)

    def build(self, order):
        return build_call(order, self.cfg)

    def tag(self, order):
        return tag_for(order)

    def tag_of_call(self, call):
        return call.get("tag")

    def tag_of_order(self, o):
        return o.get("tag")

    def tool(self, key):
        return TOOLS[key]

    def ltp_args(self, order):
        return {"instruments": ["NSE:%s" % str(order.get("symbol", "")).upper()]}

    def price_of(self, ltp, sym):
        return last_price(ltp, sym)

    def cash_of(self, margins):
        return available_cash(margins)

    def err(self, msg):
        return KiteError(str(msg).replace("Kite", self.NAME))

    def dis(self, msg):
        return KiteDisabled(str(msg).replace("Kite", self.NAME))

    def order_id_from(self, res):
        return res.get("order_id") if isinstance(res, dict) else res

    def norm_order(self, o):
        return o

    def norm_trade(self, x):
        return x

    def norm_position(self, p):
        return p

    def exit_reason(self, t, sells, vwap, last_t, stop_id):
        from_stop = all(str(x.get("order_id")) == str(stop_id) for x in sells)
        hint = t.get("exit_reason_hint")
        return "stop" if from_stop else (hint if hint in ("target", "flat-by", "manual") else
                                         "target" if t.get("target") and vwap >= t["target"] - 0.005 else
                                         "flat-by" if _hhmm(last_t) and _hhmm(last_t) >= t["flat_by"] else "manual")

    def known_trade(self, x, led, known_orders):
        return str(x.get("order_id")) in known_orders

    def closed_trade_at_broker(self, t, kite_trades):
        ids = {str(t.get(k)) for k in ("stop_order_id", "exit_order_id") if t.get(k)}
        return any(str(x.get("order_id")) in ids for x in kite_trades)

    def __init__(self, cfg, call=None, base="trader", now=None):
        self.cfg = cfg or {}
        self.call = call
        self.base = base
        self.now = now or parse_now(None)
        self.today = to_ist(self.now).date().isoformat()
        self.p = lambda *x: os.path.join(base, *x)

    # -- gates
    def _state(self, name):
        return read_json(self.p("state", name), name, required=False)

    def require_enabled(self):
        ok, why = self.enabled_fn(self.cfg, self._state(self.STATE_FILE), self.today)
        if not ok:
            raise self.dis(why)

    def db_path(self):
        return os.path.join(self.base, "trader.db")

    def store(self):
        cfg = self.cfg
        return db.LedgerStore(self.p("state", "live.json"), self.db_path(), "live", cfg,
                              lambda c: new_live_ledger(c), lambda q: read_json(q, "live ledger", required=False),
                              write_json, "kite")

    def load_live(self):
        st = self.store()
        led = st.load()
        led.setdefault("trades", [])
        led.setdefault("skipped", [])
        return st, led

    def event(self, order, status, broker_id=None, detail=None):
        """Append an order_events row when the database exists."""
        if not db.exists(self.db_path()):
            return
        oid = db.order_for_fingerprint(self.db_path(), fingerprint(order), self.today, "live")
        if oid:
            db.record_order_event(self.db_path(), oid, status, broker_id, detail, self.now)

    @staticmethod
    def is_entry(order):
        return str(order.get("side")) == "buy" and not order.get("action") and order.get("entry_type") != "stop"

    def check_gates(self, order):
        """Everything that must be true before this order may be placed. Returns the approval record."""
        self.require_enabled()
        if self.is_entry(order):
            blocks = db.control_blocks(self.p("config.json"), "live", self.today, self.db_path())
            if blocks:
                raise self.err("; ".join(blocks))
        fp = fingerprint(order)
        risk = self._state("risk.json")
        if not risk or risk.get("date") != self.today or str(risk.get("mode")) != "live":
            raise self.err("trader-risk has not checked this order today in live mode: run check.py again")
        rv = next((r for r in risk.get("results", []) if r.get("fingerprint") == fp), None)
        if not rv or rv.get("verdict") != "PASS":
            raise self.err("trader-risk did not pass this exact order today: %s"
                            % ("; ".join(rv.get("reasons", [])) if rv else "it is not in the last check"))
        rec = latest(self._state("broker.json"), fp, self.today)
        if not rec or rec.get("status") != "approved":
            raise self.err("no unused yes on record for this order: %s. One yes covers one order, once."
                            % ("it was already %s" % rec.get("status") if rec else "you have not said yes"))
        if rec.get("mode") != "live":
            raise self.err("that yes was given in %s mode, not live" % rec.get("mode"))
        call = self.build(order)
        if rec.get("payload_hash") != payload_hash(call):
            raise self.err("the Kite call built now differs from the one you approved (config changed?): ask "
                            "for a new ticket and a new yes")
        return rec, call, rv

    def refuse_duplicates(self, call, open_orders):
        tag = self.tag_of_call(call)
        if tag and any(str(self.tag_of_order(self.norm_order(o))) == tag for o in (open_orders or [])):
            raise self.err("an order with this order's tag is already at Kite: it was placed once. Check "
                            "get_orders instead of placing it again")

    def void_yes(self, order, why):
        """Use the yes up without placing anything: the price moved, so the user must be asked again."""
        state = self._state("broker.json") or {"orders": []}
        rec = latest(state, fingerprint(order), self.today)
        if rec and rec.get("status") == "approved":
            new = dict(rec, status="voided", note=why,
                       at="%s+05:30" % to_ist(self.now).replace(microsecond=0).isoformat())
            state["orders"].append(new)
            write_json(self.p("state", "broker.json"), state)
        self.event(order, "cancelled", None, why)

    def check_live_price(self, order, ltp):
        """Use Kite's live price, not Yahoo's. More than PRICE_MOVE_PCT from the planned entry: new check, new yes."""
        sym = str(order.get("symbol", "")).upper()
        px = self.price_of(ltp, sym) if ltp is not None else None
        if px is None:
            raise self.err("no live price for %s: call get_ltp and pass it (prepare --ltp FILE). Entries are "
                            "never placed on Yahoo's price alone." % sym)
        ref = _f(order.get("limit_price")) or _f((order.get("plan") or {}).get("entry"))
        if not ref:
            raise self.err("the order has no planned entry to compare the live price with")
        limit = float(self.cfg.get("live_price_move_pct", PRICE_MOVE_PCT))
        moved = (px - ref) / ref * 100.0
        if abs(moved) > limit + 1e-9:
            why = "Kite's last price %.2f is %+.2f%% from the planned entry %.2f (limit %.2f%%)" % (px, moved, ref, limit)
            self.void_yes(order, why)
            raise self.err("%s. Nothing was placed and this yes is used up: save the price (kite.py prices --ltp "
                            "FILE), run trader-risk check.py again, and ask for a new yes." % why)
        return px

    def check_margin(self, order, margins, px):
        cash = self.cash_of(margins) if margins is not None else None
        if cash is None:
            raise self.err("no margin figures: call get_margins and pass them (prepare --margins FILE). The "
                            "available cash must cover the whole order: leverage is off.")
        need = int(order["qty"]) * (px or 0)
        if need > cash + 0.005:
            raise self.err("Kite shows %s available and this order needs %s (leverage is off): nothing placed"
                            % (inr(cash, True), inr(need, True)))
        return cash

    # -- the three steps
    def prepare(self, order, open_orders, ltp=None, margins=None):
        rec, call, rv = self.check_gates(order)
        self.refuse_duplicates(call, open_orders)
        if self.is_entry(order):
            px = self.check_live_price(order, ltp)
            self.check_margin(order, margins, px)
        return call

    def record(self, order, order_id=None, status="placed", note=None, trade_ref=None):
        """Log what happened after the Allow once card. placed / denied / failed all use the yes up."""
        self.require_enabled()
        fp = fingerprint(order)
        state = self._state("broker.json") or {"orders": []}
        rec = latest(state, fp, self.today)
        if not rec or rec.get("status") != "approved":
            raise self.err("no unused yes to record a result against")
        new = {"at": "%s+05:30" % to_ist(self.now).replace(microsecond=0).isoformat(), "date": self.today,
               "fingerprint": fp, "symbol": rec["symbol"], "side": rec["side"], "plain": rec["plain"],
               "status": status, "mode": "live", "kite_tag": self.tag(order) if not order.get("action") else None}
        for k in ("index", "qty", "kind", "max_loss", "stop"):
            if rec.get(k) is not None:
                new[k] = rec[k]
        if order_id:
            new["broker_order_id"] = str(order_id)
        if note:
            new["note"] = note
        state["orders"].append(new)
        write_json(self.p("state", "broker.json"), state)
        self.event(order, status, str(order_id) if order_id else None, note)
        if status == "placed":
            self._book(order, str(order_id), rec, trade_ref)
        return new

    def execute(self, order):
        """prepare, make the Kite call, record. For tests and for unattended-safe flows: the caller still needs
        the yes on record. Never called from a scheduled task."""
        self.require_enabled()          # off means off: not even a read-only Kite call
        if self.call is None:
            raise self.err("no Kite caller: in Claude, use prepare then record")
        open_orders = self.call(self.tool("orders"), {})
        ltp = margins = None
        if self.is_entry(order):
            ltp = self.call(self.tool("ltp"), self.ltp_args(order))
            margins = self.call(self.tool("margins"), {})
        call = self.prepare(order, open_orders, ltp, margins)
        try:
            res = self.call(call["tool"], call["arguments"])
        except Exception as e:  # a failed order is not retried without a new check and a new yes
            self.record(order, None, "failed", "Kite refused: %s" % e)
            raise self.err("Kite refused the order: %s. The yes is used up: new check, new yes." % e)
        oid = self.order_id_from(res)
        if not oid:
            self.record(order, None, "failed", "no order id came back")
            raise self.err("Kite returned no order id")
        self.record(order, oid, "placed")
        return str(oid)

    # -- the live ledger
    def _book(self, order, order_id, rec, trade_ref):
        st, led = self.load_live()
        sym = str(order.get("symbol", "")).upper()
        if order.get("action") == "cancel":
            t = self._trade(led, sym)
            if t:
                t["stop_order_id"] = None
                t["notes"] = _add(t.get("notes"), "stop cancelled (%s)" % order.get("order_ref"))
        elif str(order.get("side")) == "buy":
            plan = order.get("plan") or {}
            led["trades"].append({
                "trade_id": next_id(led, self.today), "date": self.today, "symbol": sym, "setup": plan.get("setup", ""),
                "side": "buy", "qty": int(order["qty"]), "planned_entry": _f(plan.get("entry")), "entry_fill": None,
                "entry_time": None, "stop": _f(order.get("stop")), "target": _f(order.get("target")),
                "flat_by": plan.get("flat_by") or self.cfg.get("flat_by") or "15:00", "exit_price": None,
                "exit_fill": None, "exit_time": None, "exit_reason": None, "exit_date": None, "gross_pnl": None,
                "charges": None, "net_pnl": None, "r_multiple": None, "max_loss": _f(plan.get("max_loss")),
                "risk_at_stop": _f(plan.get("max_loss")), "tm_odds": None, "risk_verdict": "PASS",
                "news_tags": [], "approval": rec.get("reply"), "approved_at": rec.get("at"), "mode": "live",
                "status": "pending_fill", "balance_after": None, "notes": "", "fingerprint": fingerprint(order),
                "entry_order_id": order_id, "stop_order_id": order_id if self.STOP_WITH_ENTRY else None,
                "exit_order_id": None,
                "why": rec.get("why"), "charges_kind": None,
                "order_id": db.order_for_fingerprint(self.db_path(), fingerprint(order), self.today, "live")
                if db.exists(self.db_path()) else None, "broker_order_id": order_id})
        elif order.get("entry_type") == "stop":
            t = self._trade(led, sym)
            if t:
                t["stop_order_id"] = order_id
        else:
            t = self._trade(led, sym)
            if t:
                t["exit_order_id"] = order_id
                t["exit_reason_hint"] = (order.get("plan") or {}).get("reason") or trade_ref
        st.save(led, self.now)

    @staticmethod
    def _trade(led, sym):
        return next((t for t in led["trades"] if t["symbol"] == sym and t["status"] in ("pending_fill", "open")), None)

    def sync(self, orders, trades, positions=None):
        """Bring the live ledger up to date from Kite's get_orders / get_trades / get_positions results.
        Returns plain messages; WARNING lines are the ones to act on now."""
        self.require_enabled()
        lstore, led = self.load_live()
        rates = chg.rates_from(self.cfg)
        orders = [self.norm_order(o) for o in orders or []]
        trades = [self.norm_trade(x) for x in trades or []]
        if positions is not None:
            raw = (positions.get("net") if isinstance(positions, dict) else positions) or []
            positions = {"net": [self.norm_position(p_) for p_ in raw]}
        by_id = {str(o.get("order_id")): o for o in orders or []}
        msgs = []
        for t in led["trades"]:
            if t["status"] not in ("pending_fill", "open"):
                continue
            eo = by_id.get(str(t.get("entry_order_id")))
            if t["status"] == "pending_fill":
                if eo is None:
                    msgs.append("%s: the entry order %s is not in Kite's order list yet" % (t["trade_id"],
                                                                                           t["entry_order_id"]))
                    continue
                st = str(eo.get("status", "")).upper()
                if st == "COMPLETE":
                    t["entry_fill"] = r2(eo.get("average_price"))
                    t["qty"] = int(eo.get("filled_quantity") or eo.get("quantity") or t["qty"])
                    t["entry_time"] = _hhmm(eo.get("order_timestamp") or eo.get("exchange_timestamp"))
                    t["status"] = "open"
                    self._ev(t, "filled", eo.get("order_id"), "filled at %.2f" % t["entry_fill"])
                    msgs.append("%s: entry filled, %d shares at %.2f" % (t["trade_id"], t["qty"], t["entry_fill"]))
                elif st in ("REJECTED", "CANCELLED"):
                    t["status"] = "failed"
                    self._ev(t, "rejected" if st == "REJECTED" else "cancelled", eo.get("order_id"), eo.get("status_message"))
                    t["notes"] = _add(t.get("notes"), "entry %s: %s" % (st.lower(), eo.get("status_message") or ""))
                    msgs.append("%s: the entry was %s (%s). No position." % (t["trade_id"], st.lower(),
                                                                          eo.get("status_message") or "no reason"))
                    continue
                else:
                    msgs.append("%s: entry order is %s" % (t["trade_id"], st or "unknown"))
                    continue
            # open: protection and exit
            so = by_id.get(str(t.get("stop_order_id"))) if t.get("stop_order_id") else None
            if not t.get("stop_order_id"):
                msgs.append("WARNING %s: %d shares of %s are open with NO stop-loss at Kite. Make the stop ticket "
                            "now: kite.py stop-ticket --trade %s" % (t["trade_id"], t["qty"], t["symbol"], t["trade_id"]))
            elif so is None:
                msgs.append("WARNING %s: the stop order %s is not in Kite's order list" % (t["trade_id"],
                                                                                           t["stop_order_id"]))
            elif str(so.get("status", "")).upper() in ("REJECTED", "CANCELLED"):
                msgs.append("WARNING %s: the stop-loss order is %s (%s). The position is UNPROTECTED." % (
                    t["trade_id"], str(so["status"]).lower(), so.get("status_message") or "no reason"))
            ids = {x for x in (t.get("stop_order_id"), t.get("exit_order_id")) if x}
            sells = [x for x in trades or [] if str(x.get("tradingsymbol", "")).upper() == t["symbol"]
                     and str(x.get("transaction_type", "")).upper() == "SELL"
                     and (self.MATCH_SELLS_BY_SYMBOL or str(x.get("order_id")) in ids)]
            sold = sum(int(x.get("quantity") or x.get("filled") or 0) for x in sells)
            if sold and sold < t["qty"]:
                msgs.append("%s: partly sold (%d of %d). Left open; check get_positions." % (t["trade_id"], sold,
                                                                                            t["qty"]))
            if sold >= t["qty"] and sells:
                vwap = sum(float(x["average_price"]) * int(x.get("quantity") or x.get("filled") or 0)
                           for x in sells) / sold
                last_t = max((x.get("fill_timestamp") or x.get("exchange_timestamp") or "") for x in sells)
                reason = self.exit_reason(t, sells, vwap, last_t, t.get("stop_order_id"))
                fill = r2(vwap)
                c = chg.charges(t["qty"] * t["entry_fill"], t["qty"] * fill, rates)
                gross = r2(t["qty"] * (fill - t["entry_fill"]))
                net = r2(gross - c["total"])
                t.update(exit_price=fill, exit_fill=fill, exit_time=_hhmm(last_t), exit_reason=reason,
                         exit_date=self.today, gross_pnl=gross, charges=c["total"], net_pnl=net, status="closed",
                         r_multiple=round(net / t["risk_at_stop"], 2) if t.get("risk_at_stop") else None)
                led["balance"] = r2(float(led["balance"]) + net)
                t["balance_after"] = led["balance"]
                t["charges_kind"] = "estimated"
                t["notes"] = _add(t.get("notes"), "charges are an estimate from the rate table; the contract note is "
                                                  "the final word (kite.py set-charges)")
                msgs.append("%s closed (%s): in %.2f, out %.2f, gross %s, charges %s, net %s. Balance %s." % (
                    t["trade_id"], reason, t["entry_fill"], fill, inr(gross, True), inr(c["total"], True),
                    inr(net, True), inr(led["balance"], True)))
        if positions is not None:
            net = (positions.get("net") if isinstance(positions, dict) else positions) or []
            held = {str(p.get("tradingsymbol", "")).upper(): int(p.get("quantity") or 0) for p in net
                    if str(p.get("product", "MIS")).upper() == "MIS" and int(p.get("quantity") or 0)}
            ours = {t["symbol"]: t["qty"] for t in led["trades"] if t["status"] == "open"}
            for sym, q in held.items():
                if ours.get(sym) != q:
                    msgs.append("WARNING Kite shows %d %s in MIS but this record says %s: check get_positions "
                                "before anything else" % (q, sym, ours.get(sym, 0)))
            for sym, q in ours.items():
                if sym not in held:
                    msgs.append("WARNING this record has %s open but Kite shows no MIS position in it" % sym)
        lstore.save(led, self.now)
        for m_ in msgs:
            if m_.startswith("WARNING") and db.exists(self.db_path()):
                kind = "no stop" if "NO stop" in m_ else "stop problem" if "stop" in m_ else "position mismatch"
                db.add_discrepancy(self.db_path(), "live", kind, None, m_, self.now)
        return msgs

    def _ev(self, trade, status, broker_id, detail):
        if trade.get("order_id") and db.exists(self.db_path()):
            db.record_order_event(self.db_path(), trade["order_id"], status, str(broker_id) if broker_id else None,
                                  detail, self.now)

    def reconcile(self, orders, trades, positions, margins=None):
        """The after-15:30 comparison. Kite always wins: nothing is silently changed; every difference becomes a
        message and a row in the discrepancies table. Returns (messages, differences)."""
        msgs = self.sync(orders, trades, positions)
        diffs = []
        st, led = self.load_live()
        trades = [self.norm_trade(x) for x in trades or []]
        kite_trades = [x for x in trades if str(x.get("product", "MIS")).upper() == "MIS"]
        known_orders = {str(t.get(k)) for t in led["trades"] for k in ("entry_order_id", "stop_order_id", "exit_order_id")
                        if t.get(k)}
        for x in kite_trades:
            if not self.known_trade(x, led, known_orders):
                diffs.append(("trade not in the ledger", str(x.get("tradingsymbol")),
                              "Kite has a %s of %s %s (order %s) that this record never placed" % (
                                  x.get("transaction_type"), x.get("quantity"), x.get("tradingsymbol"), x.get("order_id"))))
        for t in led["trades"]:
            if t["status"] == "closed":
                if not self.closed_trade_at_broker(t, kite_trades):
                    diffs.append(("closed trade not at Kite", t["symbol"],
                                  "%s is closed here but Kite shows no sell for its stop or exit order" % t["trade_id"]))
        if margins is not None:
            cash = self.cash_of(margins)
            if cash is not None:
                if db.exists(self.db_path()):
                    db.set_meta(self.db_path(), "kite_account", {
                        "at": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"), "balance": cash,
                        "positions": [{"symbol": str(p.get("tradingsymbol")), "qty": int(p.get("quantity") or 0)}
                                      for p in [self.norm_position(q) for q in
                                                ((positions.get("net") if isinstance(positions, dict) else positions) or [])]
                                      if int(p.get("quantity") or 0)]})
                if abs(cash - float(led["balance"])) > 1.0:
                    diffs.append(("balance", None, "Kite shows %s available; this record says %s. Kite is the truth; the "
                                  "difference can be charges, a deposit or a fill price (use the contract note)." % (
                                      inr(cash, True), inr(led["balance"], True))))
        for kind, sym, detail in diffs:
            msgs.append("DISCREPANCY (%s): %s. Not fixed silently: it is logged for you." % (kind, detail))
            if db.exists(self.db_path()):
                db.add_discrepancy(self.db_path(), "live", kind, sym, detail, self.now)
        return msgs, diffs

    def set_actual_charges(self, trade_id, amount):
        """Replace the estimated charges of a closed live trade with the contract note's figure."""
        st, led = self.load_live()
        t = next((x for x in led["trades"] if x["trade_id"] == trade_id), None)
        if not t or t["status"] != "closed":
            raise self.err("no closed live trade %s" % trade_id)
        amount = r2(amount)
        delta = r2(float(t["charges"]) - amount)
        t["charges"] = amount
        t["net_pnl"] = r2(float(t["gross_pnl"]) - amount)
        t["charges_kind"] = "actual"
        if t.get("risk_at_stop"):
            t["r_multiple"] = round(t["net_pnl"] / t["risk_at_stop"], 2)
        led["balance"] = r2(float(led["balance"]) + delta)
        t["balance_after"] = r2(float(t["balance_after"] or led["balance"]) + delta)
        t["notes"] = _add(t.get("notes"), "charges replaced by the contract note's figure")
        st.save(led, self.now)
        return t


def _add(a, b):
    return "%s; %s" % (a, b) if a else b


def _hhmm(stamp):
    m = re.search(r"(\d{2}):(\d{2})", str(stamp or ""))
    return "%s:%s" % (m.group(1), m.group(2)) if m else None


def stop_order_for(trade, cfg):
    """The neutral stop-loss ticket for an open live trade (after the entry filled)."""
    if trade.get("status") != "open" or not trade.get("entry_fill"):
        raise KiteError("%s has not filled yet: the stop is placed only after the entry fills" % trade["trade_id"])
    if trade.get("stop_order_id"):
        raise KiteError("%s already has a stop at Kite (%s)" % (trade["trade_id"], trade["stop_order_id"]))
    return {"symbol": trade["symbol"], "exchange": "NSE", "side": "sell", "qty": int(trade["qty"]), "product": "MIS",
            "entry_type": "stop", "stop": trade["stop"],
            "why": "protective stop-loss for %s %d shares, entry filled at %.2f" % (trade["trade_id"], trade["qty"],
                                                                                   trade["entry_fill"]),
            "plan": {"setup": trade.get("setup"), "flat_by": trade.get("flat_by")}}


# ---------------------------------------------------------------- the command line

def _cfg(a):
    return read_json(a.config, "config") or {}


NAME = "Kite"          # dhan.py sets these before it reuses the commands below
PROG = "kite.py"
ADAPTER_CLS = KiteAdapter


def say(msg="", **kw):
    """print, with the broker's name and the script's name swapped in (the messages are written for Kite)."""
    print(str(msg).replace("Kite", NAME).replace("kite.py", PROG), **kw)


def _load_saved(path, what):
    doc = read_json(path, what) if path else None
    return doc.get("data", doc) if isinstance(doc, dict) and "data" in doc else doc


def cmd_check(a):
    """The read-only connection check. No order is possible until this passes today."""
    cfg = _cfg(a)
    now = parse_now(a.now)
    today = to_ist(now).date().isoformat()
    for k, want, why in (("mode", "live", "\"mode\" must be \"live\""), ("kite_enabled", True,
                                                                         "\"kite_enabled\" must be true")):
        if (str(cfg.get(k)).lower() if k == "mode" else cfg.get(k)) != want:
            say("kite.py: %s (set by you, in trader/config.json) before the adapter does anything." % why,
                  file=sys.stderr)
            return 1
    prof = read_json(a.profile, "get_profile result (save what the tool returned)")
    data = prof.get("data", prof) if isinstance(prof, dict) else {}
    if not (data.get("user_id") or data.get("user_name")):
        say("kite.py: that does not look like a Kite profile (no user_id): log in again and call get_profile.",
              file=sys.stderr)
        return 1
    if not a.tools_need_approval:
        say("kite.py: not verified. Every Kite tool that places, modifies, cancels or exits orders (place_order, "
              "modify_order, cancel_order, the GTT tools) must be set to \"Needs approval\" in Claude (Customize, "
              "Connectors, the Kite server). A script cannot see that setting: check it yourself, then run this "
              "again with --tools-need-approval.", file=sys.stderr)
        return 1
    ad = ADAPTER_CLS(cfg, None, a.base, now)
    problems, notes = [], []
    if a.margins:
        cash = available_cash(_load_saved(a.margins, "get_margins result"))
        notes.append("Kite shows %s available." % (inr(cash, True) if cash is not None else "no readable cash figure"))
        if cash is None:
            problems.append("the margins result has no cash figure I can read")
        elif cash <= 0:
            problems.append("Kite shows no available cash")
    else:
        problems.append("pass --margins (the get_margins result): the available cash must be read")
    if a.positions:
        pos = _load_saved(a.positions, "get_positions result")
        net = (pos.get("net") if isinstance(pos, dict) else pos) or []
        open_pos = {str(p.get("tradingsymbol")): int(p.get("quantity") or 0) for p in net if int(p.get("quantity") or 0)}
        _, led = ad.load_live()
        ours = {t["symbol"]: t["qty"] for t in led["trades"] if t["status"] in ("open", "pending_fill")}
        for sym, q in open_pos.items():
            if ours.get(sym) != q:
                problems.append("Kite shows %d %s open and this record has %s: unexpected position" % (q, sym, ours.get(sym, 0)))
        notes.append("%d open position%s at Kite." % (len(open_pos), "" if len(open_pos) == 1 else "s"))
    else:
        problems.append("pass --positions (the get_positions result): unexpected open positions must be ruled out")
    if problems:
        say("kite.py: NOT verified:", file=sys.stderr)
        for pr in problems:
            say("  - " + pr, file=sys.stderr)
        return 1
    write_json(os.path.join(os.path.dirname(a.out) or ".", os.path.basename(a.out)),
               {"verified": True, "date": today, "broker": "zerodha", "checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "order_tools_need_approval_confirmed_by_user": True,
                "note": "a profile call worked today; no user details are stored"})
    say("Kite verified for %s. %s" % (today, " ".join(notes)))
    say("Kite's login expires every day: log in again tomorrow before you trade.")
    return 0


def _order(a):
    doc = read_json(a.orders, "orders file")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if a.index is None or not 1 <= a.index <= len(orders):
        raise KiteError("say which order: --index 1 to %d" % len(orders))
    return orders[a.index - 1]


def _adapter(a):
    return ADAPTER_CLS(_cfg(a), None, a.base, parse_now(a.now))


def cmd_prepare(a):
    ad = _adapter(a)
    order = _order(a)
    open_orders = read_json(a.open_orders, "get_orders result (call get_orders first and save it)")
    open_orders = open_orders.get("data", open_orders) if isinstance(open_orders, dict) else open_orders
    ltp = _load_saved(a.ltp, "get_ltp result") if a.ltp else None
    margins = _load_saved(a.margins, "get_margins result") if a.margins else None
    call = ad.prepare(order, open_orders, ltp, margins)
    say("All gates pass for order %d. Make exactly this one Kite tool call (the Allow once card will ask):" % a.index)
    say(json.dumps(call, indent=2))
    say("Then: kite.py record --index %d --order-id <the id Kite returned>   (or --failed / --denied)" % a.index)
    return 0


def cmd_record(a):
    ad = _adapter(a)
    order = _order(a)
    status = "failed" if a.failed else "denied" if a.denied else "placed"
    if status == "placed" and not a.order_id:
        raise KiteError("--order-id is needed for a placed order (or say --failed / --denied)")
    rec = ad.record(order, a.order_id, status, a.note)
    say("Recorded order %d as %s%s. The yes is used up: another order needs a new check and a new yes." % (
        a.index, status, (" (Kite order %s)" % a.order_id) if a.order_id else ""))
    return 0


def cmd_stop_ticket(a):
    cfg = _cfg(a)
    ad = ADAPTER_CLS(cfg, None, a.base, parse_now(a.now))
    ad.require_enabled()
    _, led = ad.load_live()
    t = next((x for x in led["trades"] if x["trade_id"] == a.trade), None)
    if not t:
        raise KiteError("no live trade %s" % a.trade)
    order = stop_order_for(t, cfg)
    doc = {"created_by": "trader-broker", "created_at": ad.now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "proposed",
           "mode": "live", "note": "Stop-loss ticket. Nothing is placed until trader-risk passes it and you say yes.",
           "source": "kite.py stop-ticket %s" % a.trade, "orders": [order]}
    write_json(a.orders, doc)
    say("Wrote the stop-loss ticket for %s to %s: SELL %d %s, SL-M trigger %.2f." % (
        a.trade, a.orders, order["qty"], order["symbol"], order["stop"]))
    say("Next: trader-risk check.py, ticket.py show, your yes, kite.py prepare, the Kite call, kite.py record.")
    return 0


def cmd_sync(a):
    ad = ADAPTER_CLS(_cfg(a), None, a.base, parse_now(a.now))

    def load(path):
        doc = read_json(path, "saved Kite result") if path else None
        return doc.get("data", doc) if isinstance(doc, dict) and "data" in doc else doc
    msgs = ad.sync(load(a.orders_file), load(a.trades_file), load(a.positions_file))
    for m in msgs or ["Nothing changed."]:
        say(m)
    return 1 if any(m.startswith("WARNING") for m in msgs) else 0


def cmd_prices(a):
    """Save Kite's last prices as trader/prices.json so trader-risk measures an entry against Kite, not Yahoo."""
    now = parse_now(a.now)
    ltp = _load_saved(a.ltp, "get_ltp result")
    doc = ltp if isinstance(ltp, dict) else {}
    prices, detail = {}, {}
    for key in doc:
        sym = key.split(":", 1)[-1].upper()
        px = last_price(ltp, sym)
        if px is not None:
            prices[sym] = px
            detail[sym] = {"time": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "kite get_ltp"}
    if not prices:
        raise KiteError("no prices in that get_ltp result")
    write_json(a.out, {"as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "kite", "prices": prices, "detail": detail})
    say("Saved %d Kite price%s to %s: %s." % (len(prices), "" if len(prices) == 1 else "s", a.out,
                                               ", ".join("%s %.2f" % kv for kv in sorted(prices.items()))))
    return 0


def cmd_reconcile(a):
    ad = ADAPTER_CLS(_cfg(a), None, a.base, parse_now(a.now))
    msgs, diffs = ad.reconcile(_load_saved(a.orders_file, "get_orders result"), _load_saved(a.trades_file, "get_trades result"),
                               _load_saved(a.positions_file, "get_positions result"),
                               _load_saved(a.margins, "get_margins result") if a.margins else None)
    for m in msgs or ["Nothing changed."]:
        say(m)
    say("Live reconcile: %s." % ("Kite and the record agree" if not diffs and not any(m.startswith("WARNING") for m in msgs)
                                   else "DIFFERENCES FOUND. Kite is the truth. Look at each one before the next trade."))
    return 1 if diffs or any(m.startswith("WARNING") for m in msgs) else 0


def cmd_set_charges(a):
    ad = ADAPTER_CLS(_cfg(a), None, a.base, parse_now(a.now))
    ad.require_enabled()
    t = ad.set_actual_charges(a.trade, a.amount)
    say("%s: charges are now %s (from the contract note), net %s." % (t["trade_id"], inr(t["charges"], True),
                                                                    inr(t["net_pnl"], True)))
    return 0


def cmd_status(a):
    cfg = _cfg(a)
    now = parse_now(a.now)
    ok, why = enabled(cfg, read_json(os.path.join(a.base, "state", "kite.json"), "kite state", required=False),
                      to_ist(now).date().isoformat())
    say("Kite adapter: %s" % ("ON (live mode, enabled, verified today)" if ok else "OFF. " + why))
    _, led = ADAPTER_CLS(cfg, None, a.base, now).load_live()
    if led and led.get("trades"):
        say("Live ledger: balance %s, %d trades." % (inr(led["balance"], True), len(led["trades"])))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="The Kite adapter (trader-broker). Off unless live and enabled.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--base", default="trader", help="the trader/ folder")
        p.add_argument("--now")

    p = sub.add_parser("check")
    common(p)
    p.add_argument("--profile", required=True)
    p.add_argument("--margins", help="the saved get_margins result")
    p.add_argument("--positions", help="the saved get_positions result")
    p.add_argument("--tools-need-approval", action="store_true",
                   help="you confirm every Kite order tool is set to Needs approval in Claude")
    p.add_argument("--out", default="trader/state/kite.json")
    p = sub.add_parser("prepare")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--orders", default="trader/orders.json")
    p.add_argument("--open-orders", required=True, help="the saved get_orders result")
    p.add_argument("--ltp", help="the saved get_ltp result (required for an entry)")
    p.add_argument("--margins", help="the saved get_margins result (required for an entry)")
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
    p = sub.add_parser("prices")
    common(p)
    p.add_argument("--ltp", required=True)
    p.add_argument("--out", default="trader/prices.json")
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
    p = sub.add_parser("status")
    common(p)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"check": cmd_check, "prepare": cmd_prepare, "record": cmd_record, "stop-ticket": cmd_stop_ticket,
                "sync": cmd_sync, "status": cmd_status, "prices": cmd_prices, "reconcile": cmd_reconcile,
                "set-charges": cmd_set_charges}[a.cmd](a)
    except KiteDisabled as e:
        say("kite.py: OFF. %s" % e, file=sys.stderr)
        return 2
    except KiteError as e:
        say("kite.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
