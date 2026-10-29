#!/usr/bin/env python3
"""Position sizing for trader-budget (NSE intraday, MIS). Standard library only, no network.

Sizes ONE day trade from trader/plan.json (written by trader-exit) to your
limits and writes one proposed order to trader/orders.json: broker-neutral
fields plus the Kite Connect payload (VERIFY.md section 2). It never places
anything: trader-risk checks the order and trader-broker asks you yes or no.

    python3 size.py init                             # write trader/config.json with your limits (once)
    python3 size.py                                  # trader/plan.json + trader/config.json
    python3 size.py --write                          # write trader/orders.json
    python3 size.py --limit                          # a limit order at the plan's entry
    python3 size.py --limit 1416.00 --write          # a limit order at 1416.00
    python3 size.py --now "2026-10-30 09:50" --json  # pin the clock (India time)

How many shares. Whole shares only. The size is the smallest of:

    max loss       the loss if the stop fills, INCLUDING the paper slippage on
                   both sides and every charge, stays within max_loss_per_trade_inr
                   (₹500): shares x (entry fill - stop fill) + charges <= ₹500,
                   where entry fill = entry + 0.05 % and stop fill = stop - 0.05 %
                   ("limits_include_slippage": false counts charges only, as the
                   paper broker will still fill with slippage)
    position cap   shares x entry <= max_position_value_inr (₹75,000)
    cash           shares x entry <= cash minus the cash buffer (10 % of capital);
                   cash is the paper balance in trader/state/paper.json when there
                   is one, else capital_inr

It says which limit decided the size. Then, at that size, it refuses (writes
no order) when:
    reward:risk after charges is under min_reward_risk (1.5): the net profit at
        the target (after slippage and charges) / the net loss at the stop
    charges eat more than max_charges_pct_of_target (25 %) of the gross profit
        at the target
    zero shares fit, the stop is not below the entry, the target is not above
    it, the plan is for another day, the symbol is blocked, or the plan is a short

The entry it sizes at: a limit order's limit price; for a market order the
plan's entry, or the price in trader/prices.json if that is fresh (under 15
minutes old) and higher, because a price that ran up since the plan means a
bigger loss at the same stop.
"""

import argparse
import datetime
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import charges as chg  # noqa: E402  (same folder: one calculator for the pack)
import db  # noqa: E402  (a copy of trader-db/db.py; a test keeps the copies identical)
import guards  # noqa: E402  (shared guard rules; trader-risk, -scanner and -control carry identical copies)

# Your limits (the request of 2026-10-29). trader/config.json overrides them; the tools never write them.
DEFAULTS = {
    "mode": "paper",
    "kite_enabled": False,
    "capital_inr": 100000,
    "max_loss_per_trade_inr": 500,
    "max_daily_loss_inr": 1500,
    "max_weekly_loss_inr": 4000,
    "max_drawdown_pause_inr": 8000,
    "max_trades_per_day": 3,
    "max_open_positions": 1,
    "max_position_value_inr": 75000,
    "cash_buffer_pct": 10,
    "min_reward_risk": 1.5,
    "max_charges_pct_of_target": 25,
    "allow_margin": False,
    "allow_short": False,
    "require_stop": True,
    "no_entries_before": "09:30",
    "no_entries_after": "14:45",
    "flat_by": "15:00",
    "paper_slippage_pct": 0.05,
    "limits_include_slippage": True,
    "paper_period_weeks": 4,
    "min_trades_for_review": 40,
    "exchange": "NSE",
    "product": "MIS",
    "stop_order_type": "SL-M",
    "stop_limit_buffer_pct": 0.5,
    "universe_index": "nifty100",
    "min_price_inr": 50,
    "min_avg_volume": 500000,
    "blocked": [],
    "limits_pct": dict(guards.DEFAULT_PCT),
    "live_start_risk_pct": 50,
    "live_start_weeks": 2,
    "vix_half": 20,
    "vix_stop": 25,
    "gap_half_pct": 1.0,
    "circuit_margin_pct": 2.0,
    "max_data_age_min": 3,
    "losing_streak_stop": 2,
    "cooldown_min": 15,
    "protection_data_required": None,
    "event_days": {},
}
EPS = 1e-9
FRESH_MINUTES = 15
SETUP_WORDS = {"orb": "opening-range breakout", "level": "close above a level"}
NEUTRAL_FIELDS = ("symbol", "exchange", "side", "qty", "product", "entry_type", "limit_price", "stop", "target",
                  "action", "order_ref")


class SizeError(Exception):
    pass


ClockError = SizeError

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


def parse_utc(text):
    """"2026-10-30T04:20:13Z" -> naive UTC, or None."""
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def utc_iso(now_utc=None):
    now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    return now_utc.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- inputs

def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SizeError("no %s at %s" % (what, path))
    except json.JSONDecodeError as e:
        raise SizeError("%s is not valid JSON: %s" % (path, e))


def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        cfg.update(read_json(path, "config"))
    return cfg


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def price_time(doc, sym):
    """When this symbol's price is from: the quote's own time, else when it was fetched, else as_of."""
    det = ((doc or {}).get("detail") or {}).get(sym) or {}
    for key in ("time", "fetched"):
        t = parse_utc(det.get(key))
        if t:
            return t
    return parse_utc((doc or {}).get("as_of"))


def fresh_price(doc, sym, now_utc, minutes=FRESH_MINUTES):
    """(price, fetched_utc, fresh) for sym from a prices.json doc, or (None, None, False)."""
    if not isinstance(doc, dict):
        return None, None, False
    p = _f((doc.get("prices") or {}).get(sym))
    if p is None or p <= 0:
        return None, None, False
    t = price_time(doc, sym)
    if t is None:
        return p, None, False
    age = (now_utc - t).total_seconds()
    return p, t, -60 <= age <= minutes * 60


# ---------------------------------------------------------------- the math

def r2(x):
    return round(float(x) + EPS, 2)


def fills(entry, stop, target, slip_pct):
    """Paper fill prices: you pay the slippage going in and coming out."""
    s = float(slip_pct) / 100.0
    return entry * (1 + s), stop * (1 - s), target * (1 - s)


def loss_at_stop(n, entry, stop, slip_pct, rates):
    """(rupees lost if the stop fills, the charges in it) for n shares, slippage and charges included."""
    bf, sf, _ = fills(entry, stop, stop, slip_pct)
    c = chg.charges(n * bf, n * sf, rates)
    return r2(n * (bf - sf) + c["total"]), c


def profit_at_target(n, entry, target, slip_pct, rates):
    """(net rupees made if the target fills, the charges in it) for n shares."""
    bf, _, tf = fills(entry, target, target, slip_pct)
    c = chg.charges(n * bf, n * tf, rates)
    return r2(n * (tf - bf) - c["total"]), c


def shares_for_loss(cap, entry, stop, slip_pct, rates):
    """The most whole shares whose loss at the stop, with charges, is within cap."""
    bf, sf, _ = fills(entry, stop, stop, slip_pct)
    per = bf - sf
    if per <= 0 or cap <= 0:
        return 0
    n = int(cap // per)
    while n > 0 and loss_at_stop(n, entry, stop, slip_pct, rates)[0] > cap + EPS:
        n -= 1
    return n


def size_trade(plan, cfg, limit=None, price=None, cash=None, today=None, prot=None, live_trades=None):
    """Pure function: one plan + your rules -> the sizing (a dict). qty may be 0.

    limit  the limit price for a limit order (None = market)
    price  a FRESH latest price; a market order is sized at the higher of it and the plan's entry
    cash   the paper (or live) cash balance, if known; else capital_inr
    today  India date (YYYY-MM-DD); a plan for another day is refused
    prot   protection data from guards.load_protection (None = those rules are off)
    live_trades  the live ledger's trades, for the half-risk start of live trading"""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    c, limit_notes = guards.effective_limits(c)
    rates = chg.rates_from(c)
    if not isinstance(plan, dict):
        raise SizeError("the plan is not a JSON object")
    sym = str(plan.get("symbol") or "").strip().upper()
    if not sym:
        raise SizeError("the plan has no symbol (run trader-exit)")
    side = str(plan.get("side") or "buy").strip().lower()
    if side != "buy":
        raise SizeError("this pack sizes long (buy) intraday trades only; shorts are off. The plan says %r" % side)
    if today and plan.get("date") and str(plan["date"]) != today:
        raise SizeError("the plan is for %s and today is %s in India: make a new plan with trader-exit"
                        % (plan["date"], today))
    entry_plan, stop, target = _f(plan.get("entry")), _f(plan.get("stop")), _f(plan.get("target"))
    for name, v in (("entry", entry_plan), ("stop", stop), ("target", target)):
        if v is None or v <= 0:
            raise SizeError("the plan needs a positive %s (run trader-exit)" % name)
    if sym in {str(b).upper() for b in (c.get("blocked") or [])}:
        raise SizeError("%s is on your blocked list" % sym)
    tick = _f(plan.get("tick")) or tick_size(entry_plan)
    if limit is not None:
        ref, ref_from = to_tick(limit, tick, "nearest"), "limit"
    elif price is not None and r2(price) > r2(entry_plan):
        ref, ref_from = to_tick(price, tick, "up"), "price"
    else:
        ref, ref_from = r2(entry_plan), "plan"
    if stop >= ref:
        raise SizeError("the stop %.2f is not below the entry %.2f; a long trade needs its stop underneath"
                        % (stop, ref))
    if target <= ref:
        raise SizeError("the target %.2f is not above the entry %.2f: the move has already happened. "
                        "Skip it or make a new plan with trader-exit" % (target, ref))
    slip_fill = float(c.get("paper_slippage_pct") or 0)
    slip = slip_fill if c.get("limits_include_slippage", True) else 0.0
    capital = float(c.get("capital_inr") or 0)
    if capital <= 0:
        raise SizeError("no capital: set \"capital_inr\" in trader/config.json")
    buf_pct = float(c.get("cash_buffer_pct") or 0)
    if not 0 <= buf_pct < 100:
        raise SizeError("cash_buffer_pct must be between 0 and 100")
    buffer_inr = r2(capital * buf_pct / 100.0)
    have = capital if cash is None else float(cash)
    available = max(r2(have - buffer_inr), 0.0)

    limits = []
    factor, half_reasons, stop_reasons = 1.0, [], []
    blocks = []
    if prot is not None:
        factor, half_reasons, stop_reasons = guards.size_factor(sym, today or str(plan.get("date")), c, prot,
                                                                str(c.get("mode") or "paper"), live_trades)
        blocks = stop_reasons + guards.symbol_blocks(sym, ref, None, prot, today or str(plan.get("date")), c)
    max_loss = _f(c.get("max_loss_per_trade_inr"))
    base_max_loss = max_loss
    if max_loss:
        max_loss = r2(max_loss * factor)
        limits.append({"name": "max loss", "inr": max_loss,
                       "shares": shares_for_loss(max_loss, ref, stop, slip, rates),
                       "how": "%s at the stop, %scharges included" % (inr(max_loss), "slippage and " if slip else "")})
    cap_val = _f(c.get("max_position_value_inr"))
    if cap_val:
        limits.append({"name": "position cap", "inr": cap_val, "shares": int(cap_val // ref + EPS),
                       "how": "%s / %.2f" % (inr(cap_val), ref)})
    limits.append({"name": "cash", "inr": available, "shares": int(available // ref + EPS),
                   "how": "%s %s minus the %s cash buffer = %s / %.2f"
                          % ("cash" if cash is not None else "capital", inr(have), inr(buffer_inr),
                             inr(available), ref)})
    qty = max(0, min(l["shares"] for l in limits))
    bound = [l["name"] for l in limits if l["shares"] == qty][0]
    for l in limits:
        l["binds"] = l["name"] == bound

    risk_ps = r2(ref - stop)
    loss, loss_c = loss_at_stop(qty, ref, stop, slip, rates) if qty else (0.0, chg.charges(0, 0, rates))
    gain, gain_c = profit_at_target(qty, ref, target, slip, rates) if qty else (0.0, chg.charges(0, 0, rates))
    gross_target = r2(qty * (target - ref))
    plain_c = chg.charges(qty * ref, qty * target, rates)["total"] if qty else 0.0
    rr_gross = r2((target - ref) / (ref - stop))
    rr_net = r2(gain / loss) if qty and loss > 0 else None
    charge_share = r2(100.0 * plain_c / gross_target) if qty and gross_target > 0 else None
    refusal = None
    if blocks:
        refusal = "; ".join(blocks) + ". No order."
    elif qty == 0:
        if bound == "max loss":
            one, _ = loss_at_stop(1, ref, stop, slip, rates)
            refusal = ("one share loses %s at the stop (entry %.2f minus stop %.2f, plus slippage and charges), more "
                       "than your %s max loss per trade. No order. Skip it, or plan a closer stop with trader-exit."
                       % (inr(one, True), ref, stop, inr(max_loss)))
        else:
            refusal = "one share of %s costs %s, more than your %s limit. No order." % (sym, inr(ref, True), bound)
    elif _f(c.get("min_reward_risk")) and (rr_net is None or rr_net < float(c["min_reward_risk"]) - EPS):
        refusal = ("reward:risk after charges is %.2f (net %s at the target vs %s at the stop), under your minimum "
                   "%s. No order. Skip it, or make a new plan with a farther target." % (
                       rr_net or 0, inr(gain, True), inr(loss, True), _num(c["min_reward_risk"])))
    elif _f(c.get("max_charges_pct_of_target")) and charge_share is not None and \
            charge_share > float(c["max_charges_pct_of_target"]) + EPS:
        refusal = ("charges of %s would eat %.1f%% of the %s gross profit at the target, over your %s%% limit. "
                   "No order: the move is too small to pay for itself." % (
                       inr(plain_c, True), charge_share, inr(gross_target, True), _num(c["max_charges_pct_of_target"])))
    return {
        "symbol": sym, "side": "buy", "setup": plan.get("setup", ""), "date": plan.get("date"),
        "entry_time": plan.get("entry_time", ""), "entry_plan": r2(entry_plan), "entry": ref, "entry_from": ref_from,
        "stop": r2(stop), "target": r2(target), "tick": tick, "risk_per_share": risk_ps,
        "reward_risk_gross": rr_gross, "reward_risk_net": rr_net,
        "stop_label": plan.get("stop_label", ""), "target_label": plan.get("target_label", ""),
        "capital": capital, "cash": have, "cash_buffer": buffer_inr, "cash_available": available,
        "limits": limits, "qty": qty, "bound_by": bound, "order_value": r2(qty * ref),
        "max_loss": loss, "charges_at_stop": loss_c["total"], "net_at_target": gain,
        "charges_at_target": gain_c["total"], "gross_at_target": gross_target, "charges_pct_of_target": charge_share,
        "slippage_pct": slip, "max_loss_per_trade_inr": max_loss, "flat_by": plan.get("flat_by") or c.get("flat_by"),
        "order_type": "limit" if limit is not None else "market", "refusal": refusal,
        "charges_checked": rates.get("checked"), "half_size": {"factor": factor, "reasons": half_reasons},
        "normal_max_loss_inr": base_max_loss, "config_notes": limit_notes,
    }


def _num(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x).rstrip("0").rstrip(".")


# ---------------------------------------------------------------- the order

def kite_payload(s, cfg):
    """The Kite Connect order parameters (pykiteconnect place_order names, VERIFY.md section 2).
    The entry goes first; the stop is placed only after the entry fills; the target is NOT a
    resting order (a resting target and a resting stop could both fill and leave you short)."""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    base = {"variety": "regular", "exchange": c.get("exchange", "NSE"), "tradingsymbol": s["symbol"],
            "quantity": s["qty"], "product": c.get("product", "MIS"), "validity": "DAY"}
    entry = dict(base, transaction_type="BUY")
    if s["order_type"] == "limit":
        entry.update(order_type="LIMIT", price=s["entry"])
    else:
        entry.update(order_type="MARKET", market_protection=-1)
    stop = dict(base, transaction_type="SELL", trigger_price=s["stop"])
    if str(c.get("stop_order_type", "SL-M")).upper() == "SL":
        lim = to_tick(s["stop"] * (1 - float(c.get("stop_limit_buffer_pct") or 0.5) / 100.0), s["tick"], "down")
        stop.update(order_type="SL", price=lim)
    else:
        stop.update(order_type="SL-M", market_protection=-1)
    return {"entry": entry, "stop_after_fill": stop,
            "target": "not a resting order: when trader-alerts rings at %.2f, cancel the stop, then a new SELL "
                      "(each after trader-risk and your yes)" % s["target"],
            "verify": "Kite field names from pykiteconnect; re-check against the Kite MCP tool before live use "
                      "(VERIFY.md, RECHECK-AT-KITE)"}


def order_from(s, cfg):
    """The one proposed order: broker-neutral fields, the Kite payload, why and the plan."""
    if s["refusal"] or s["qty"] <= 0:
        return None
    c = dict(DEFAULTS)
    c.update(cfg or {})
    why = "%s on %s: %d shares, decided by your %s (%s)" % (
        SETUP_WORDS.get(s["setup"], s["setup"] or "trade"), s["symbol"], s["qty"], s["bound_by"],
        [l["how"] for l in s["limits"] if l["binds"]][0])
    return {
        "symbol": s["symbol"], "exchange": c.get("exchange", "NSE"), "side": "buy", "qty": s["qty"],
        "product": c.get("product", "MIS"), "entry_type": s["order_type"],
        "limit_price": s["entry"] if s["order_type"] == "limit" else None,
        "stop": s["stop"], "target": s["target"],
        "kite": kite_payload(s, cfg),
        "why": why,
        "plan": {"entry": s["entry"], "entry_from": s["entry_from"], "stop": s["stop"], "target": s["target"],
                 "tick": s["tick"], "risk_per_share": s["risk_per_share"], "max_loss": s["max_loss"],
                 "charges_at_stop": s["charges_at_stop"], "net_at_target": s["net_at_target"],
                 "charges_at_target": s["charges_at_target"], "reward_risk_net": s["reward_risk_net"],
                 "reward_risk_gross": s["reward_risk_gross"], "charges_pct_of_target": s["charges_pct_of_target"],
                 "slippage_pct": s["slippage_pct"], "setup": s["setup"], "date": s["date"],
                 "entry_time": s["entry_time"], "flat_by": s["flat_by"], "bound_by": s["bound_by"],
                 "order_value": s["order_value"]},
    }


def plan_report(s, order, notes=()):
    lines = []
    for n in notes:
        lines.append("Note: " + n)
    lines.append("%s %s on %s, entry %.2f (%s), stop %.2f, target %.2f: risk %.2f a share."
                 % (SETUP_WORDS.get(s["setup"], s["setup"] or "Trade").capitalize(), s["symbol"], s["date"],
                    s["entry"], {"plan": "the plan's entry", "price": "the latest price, above the plan",
                                 "limit": "your limit"}[s["entry_from"]], s["stop"], s["target"],
                    s["risk_per_share"]))
    for n in s.get("config_notes", []):
        lines.append("Note: " + n)
    if s.get("half_size", {}).get("factor", 1.0) < 1.0:
        lines.append("HALF SIZE today (x%g): %s. Your max loss per trade is %s, not %s." % (
            s["half_size"]["factor"], "; ".join(s["half_size"]["reasons"]), inr(s["max_loss_per_trade_inr"]),
            inr(s["normal_max_loss_inr"])))
    lines.append("The limits (whole shares, the smallest wins):")
    for l in s["limits"]:
        lines.append("  %-13s %4d shares   %s%s" % (l["name"], l["shares"], l["how"], "   <- decides it" if l["binds"]
                                                    else ""))
    if s["qty"]:
        lines.append("%d shares = %s. If the stop fills at %.2f: about %s lost, of which %s is charges (%s)."
                     % (s["qty"], inr(s["order_value"], True), s["stop"], inr(s["max_loss"], True),
                        inr(s["charges_at_stop"], True), ("slippage %.2f%% a side included" % s["slippage_pct"])
                        if s["slippage_pct"] else "no slippage counted: limits_include_slippage is false"))
        lines.append("If the target fills at %.2f: about %s made after %s of charges. Reward:risk %.2f before "
                     "charges, %.2f after. Charges are %.1f%% of the gross profit at the target."
                     % (s["target"], inr(s["net_at_target"], True), inr(s["charges_at_target"], True),
                        s["reward_risk_gross"], s["reward_risk_net"] or 0, s["charges_pct_of_target"] or 0))
    if s["refusal"]:
        lines.append("REFUSED: " + s["refusal"])
        return lines
    lines.append("A stop is an order, not a promise: in a fast market it can fill below the stop, and the loss "
                 "can be bigger than this.")
    lines.append("Proposed order (nothing has been placed):")
    lines.append("  BUY %d %s on NSE, %s, %s, product MIS (intraday)" % (
        order["qty"], order["symbol"], "market" if order["entry_type"] == "market" else
        "limit %.2f" % order["limit_price"], "good for today"))
    lines.append("  then, after the buy fills: a stop-loss SELL %d at trigger %.2f (%s)" % (
        order["qty"], order["stop"], order["kite"]["stop_after_fill"]["order_type"]))
    lines.append("  target %.2f: watched by trader-alerts, closed by a new order after your yes" % order["target"])
    lines.append("Next: /trader-risk checks it against every limit.")
    return lines


def write_orders(path, orders, cfg, source="", now_utc=None):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    doc = {"created_by": "trader-budget", "created_at": utc_iso(now_utc), "status": "proposed",
           "mode": (cfg or {}).get("mode", "paper"),
           "note": "Proposed only. Nothing is placed until trader-risk passes it and you say yes in the chat.",
           "source": source, "orders": orders}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def paper_cash(path):
    """The paper account's cash from trader/state/paper.json (written by the paper broker), or None."""
    if not path or not os.path.exists(path):
        return None
    doc = read_json(path, "paper account")
    return _f((doc or {}).get("balance"))


# ---------------------------------------------------------------- commands

def cmd_init(a):
    if os.path.exists(a.config) and not a.force:
        print("%s already exists: nothing changed. Edit it yourself, or pass --force to start over from the "
              "defaults." % a.config)
        return 1
    d = os.path.dirname(a.config)
    if d:
        os.makedirs(d, exist_ok=True)
    doc = dict(DEFAULTS)
    doc["limits_pct"] = dict(guards.DEFAULT_PCT)
    doc.update(guards.derive_inr(doc["capital_inr"], doc["limits_pct"]))
    doc["charges"] = dict(chg.DEFAULT_RATES)
    doc["notion"] = {"parent_page_id": None, "page_id": None, "databases": {}}
    with open(a.config, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("Wrote %s with your limits: capital %s, max loss %s a trade, %s a day, %s a week, pause at %s below "
          "capital, mode paper." % (a.config, inr(doc["capital_inr"]), inr(doc["max_loss_per_trade_inr"]),
                                    inr(doc["max_daily_loss_inr"]), inr(doc["max_weekly_loss_inr"]),
                                    inr(doc["max_drawdown_pause_inr"])))
    dbp = db.default_path(a.config)
    if not db.exists(dbp):
        db.init(dbp)
        db.set_account(dbp, "paper", capital=doc["capital_inr"], balance=doc["capital_inr"], baseline=doc["capital_inr"])
        db.log_config(dbp, doc, "initial", "written by size.py init")
        print("Created the database %s (db.py check, db.py backup, db.py export are in /trader-db)." % dbp)
    print("The limits are stored as percent of capital (limits_pct); the rupee values follow from them.")
    print("Only you change these (or /trader-control, with your confirmation). The other tools never do.")
    return 0


def run_plan(a, cfg):
    now = parse_now(a.now)
    today = to_ist(now).date().isoformat()
    plan = read_json(a.plan, "plan (run trader-exit first)")
    notes = []
    price = None
    sym = str(plan.get("symbol") or "").upper()
    if a.limit is None and a.prices and os.path.exists(a.prices):
        doc = read_json(a.prices, "prices")
        p, t, fresh = fresh_price(doc, sym, now)
        if p is not None and fresh:
            price = p
        elif p is not None:
            notes.append("the %s price in %s is older than %d minutes: not used" % (sym, a.prices, FRESH_MINUTES))
    limit = None
    if a.limit is not None:
        limit = _f(plan.get("entry")) if a.limit == "plan" else _f(a.limit)
        if limit is None or limit <= 0:
            raise SizeError("--limit wants a price, like --limit 1416.00")
    cash = a.cash if a.cash is not None else paper_cash(a.paper)
    state_dir = os.path.join(os.path.dirname(os.path.abspath(a.config)), "state")
    prot = guards.load_protection(state_dir)
    live_trades = None
    if str(cfg.get("mode") or "paper") == "live":
        live = read_json(os.path.join(state_dir, "live.json"), "live ledger") if os.path.exists(
            os.path.join(state_dir, "live.json")) else None
        live_trades = (live or {}).get("trades", [])
    gaps = guards.data_gaps(prot, today)
    if gaps:
        notes.append("protection data incomplete, so these checks did not run here (trader-risk reports them too): "
                     + "; ".join(gaps))
    s = size_trade(plan, cfg, limit, price, cash, today, prot, live_trades)
    order = order_from(s, cfg)
    if not a.no_state:       # for the paper broker's `skip --from-plan`, even when the plan was refused
        os.makedirs(os.path.dirname(a.sizing) or ".", exist_ok=True)
        with open(a.sizing, "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    if a.json:
        print(json.dumps({"sizing": s, "order": order, "notes": notes}, indent=2, ensure_ascii=False))
    else:
        print("\n".join(plan_report(s, order, notes)))
    if order and a.write:
        write_orders(a.write, [order], cfg, "trader-budget size.py from %s" % a.plan, now)
        if not a.json:
            print("Wrote 1 proposed order to %s." % a.write)
    return 0 if order else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["init"]:
        ap = argparse.ArgumentParser(description="Write trader/config.json with your limits (trader-budget).")
        ap.add_argument("--config", default="trader/config.json")
        ap.add_argument("--force", action="store_true", help="overwrite an existing config")
        return cmd_init(ap.parse_args(argv[1:]))
    ap = argparse.ArgumentParser(description="Size one NSE intraday trade (trader-budget). Never places orders.")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--paper", default="trader/state/paper.json", help="the paper account, for the cash balance")
    ap.add_argument("--cash", type=float, help="cash to size against (default: the paper balance, else capital)")
    ap.add_argument("--limit", nargs="?", const="plan", help="a limit order (at the plan's entry, or this price)")
    ap.add_argument("--now", help='pretend it is this India time, e.g. "2026-10-30 09:50"')
    ap.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH")
    ap.add_argument("--sizing", default="trader/state/sizing.json", help="where the last sizing is saved")
    ap.add_argument("--no-state", action="store_true", help="do not save the last sizing")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        return run_plan(a, load_config(a.config))
    except SizeError as e:
        print("size.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
