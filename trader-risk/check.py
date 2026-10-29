#!/usr/bin/env python3
"""Pre-trade risk check for trader-risk (NSE intraday). Standard library only, no network.

Reads the proposed orders (trader/orders.json) and checks every one against
your limits (trader/config.json), the account ledger of the current mode
(paper: trader/state/paper.json, live: trader/state/live.json), fresh prices
(trader/prices.json) and, in live mode, the Kite connection check
(trader/state/kite.json). Each order gets PASS or BLOCK with plain reasons.
Orders are checked in file order as if each PASS had filled.

    python3 check.py                                 # the trader/ files, writes trader/state/risk.json
    python3 check.py --now "2026-10-30 09:51"        # pin the clock (India time)
    python3 check.py --json
    python3 check.py --no-write                      # check without recording the verdicts

ENTRY or EXIT. An entry opens a position (a buy; a sell of shares you do not
hold would be a short). An exit only sells what you hold. Getting out must
always be possible: an exit is checked for format and short selling only;
every other rule turns into a warning for it.

What blocks any order:
  format     symbol, side buy/sell, a whole-share qty, exchange NSE, product MIS,
             entry_type market or limit (a limit needs a limit_price)
  short      selling more than you hold ("allow_short" is false)
  mode       "mode": "live" without "kite_enabled": true, or without a Kite check
             from today (trader/state/kite.json, written by trader-broker kite.py)

What blocks an entry (on top):
  pause      the account is ₹8,000 or more below capital (max_drawdown_pause_inr):
             all trading stops until you review it
  daily      today's realised loss + the risk still open + this trade's loss at
             the stop would pass max_daily_loss_inr (₹1,500), or already has
  weekly     the same for the week from Monday (max_weekly_loss_inr, ₹4,000):
             no new entries until next Monday
  trades     max_trades_per_day (3) entries already today
  open       max_open_positions (1) already open
  stop       no stop, or a stop not below the entry
  loss       the loss at the stop, slippage and charges included, over
             max_loss_per_trade_inr (₹500)
  reward     reward:risk after charges under min_reward_risk (1.5)
  charges    charges over max_charges_pct_of_target (25 %) of the gross profit at the target
  position   order value over max_position_value_inr (₹75,000)
  cash       the buy would dip into the cash buffer, or need margin (leverage is off)
  window     before no_entries_before (09:30) or after no_entries_after (14:45)
             India time, or NSE is closed (weekend, holiday, special session,
             a year without a holiday list)
  blocked    a symbol on your "blocked" list
  duplicate  the same symbol and side twice in this batch

The entry it measures from: the limit price for a limit order, else the HIGHER
of the plan's entry and a fresh price (under 15 minutes old).

Exit code 0 when every order passes, 1 when any order is blocked. The verdicts
go to trader/state/risk.json with a fingerprint of each exact order, so
trader-broker only shows an order as ready if THIS exact order passed today.
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

DEFAULTS = {
    "mode": "paper", "kite_enabled": False, "capital_inr": 100000, "max_loss_per_trade_inr": 500,
    "max_daily_loss_inr": 1500, "max_weekly_loss_inr": 4000, "max_drawdown_pause_inr": 8000,
    "max_trades_per_day": 3, "max_open_positions": 1, "max_position_value_inr": 75000, "cash_buffer_pct": 10,
    "min_reward_risk": 1.5, "max_charges_pct_of_target": 25, "allow_margin": False, "allow_short": False,
    "require_stop": True, "no_entries_before": "09:30", "no_entries_after": "14:45", "flat_by": "15:00",
    "paper_slippage_pct": 0.05, "limits_include_slippage": True, "blocked": [],
}
NEUTRAL_FIELDS = ("symbol", "exchange", "side", "qty", "product", "entry_type", "limit_price", "stop", "target")
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
EPS = 1e-9
FRESH_MINUTES = 15
REF_WORDS = {"plan": "the plan's entry", "price": "the latest price", "limit": "the limit price"}


class RiskError(Exception):
    pass


ClockError = RiskError

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
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def hhmm(value, key):
    """"09:30" -> time(9, 30); None or "" -> None (that bound is off)."""
    if value in (None, "", False):
        return None
    m = HHMM.match(str(value).strip())
    if not m:
        raise RiskError("%s must be an India time like \"09:30\", got %r" % (key, value))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def window_check(now_ist, c):
    """Reasons an entry cannot be placed now (India time)."""
    before = hhmm(c.get("no_entries_before"), "no_entries_before")
    after = hhmm(c.get("no_entries_after"), "no_entries_after")
    d, t = now_ist.date(), now_ist.time().replace(second=0, microsecond=0)
    ok, why = trading_day(d)
    stamp = now_ist.strftime("%a %Y-%m-%d %H:%M")
    if not ok:
        return ["window: NSE is closed today (%s, %s India time)" % (why, stamp)]
    if t < OPEN or now_ist.time() >= CLOSE:
        return ["window: NSE is closed (%s India time; the session is 09:15 to 15:30)" % stamp]
    if before is not None and t < before:
        return ["window: no new entries before %s (it is %s)" % (before.strftime("%H:%M"), t.strftime("%H:%M"))]
    if after is not None and t > after:
        return ["window: no new entries after %s (it is %s)" % (after.strftime("%H:%M"), t.strftime("%H:%M"))]
    return []


def monday_of(d):
    return d - datetime.timedelta(days=d.weekday())


# ---------------------------------------------------------------- files and helpers

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RiskError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise RiskError("%s is not valid JSON: %s" % (path, e))


def fingerprint(order):
    """Same function in trader-broker/ticket.py: the exact order that was checked."""
    core = {k: order[k] for k in NEUTRAL_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _num(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x).rstrip("0").rstrip(".")


def ledger_view(ledger, today, capital):
    """What the account ledger says: balance, open positions, today's and this week's numbers."""
    led = ledger or {}
    trades = [t for t in led.get("trades", []) if isinstance(t, dict)]
    balance = _f(led.get("balance"))
    if balance is None:
        balance = capital + sum(_f(t.get("net_pnl")) or 0 for t in trades if t.get("status") == "closed")
    d = datetime.date.fromisoformat(today)
    week0 = monday_of(d).isoformat()
    opened_today = [t for t in trades if str(t.get("date")) == today]
    open_now = [t for t in trades if t.get("status") == "open"]
    closed = [t for t in trades if t.get("status") == "closed"]
    real_today = sum(_f(t.get("net_pnl")) or 0 for t in closed if str(t.get("exit_date") or t.get("date")) == today)
    real_week = sum(_f(t.get("net_pnl")) or 0 for t in closed
                    if week0 <= str(t.get("exit_date") or t.get("date")) <= today)
    open_risk = sum(_f(t.get("max_loss")) or 0 for t in open_now)
    held = {}
    for t in open_now:
        s = str(t.get("symbol", "")).upper()
        held[s] = held.get(s, 0) + int(_f(t.get("qty")) or 0)
    return {"balance": round(balance, 2), "open": open_now, "held": held, "entries_today": len(opened_today),
            "realised_today": round(real_today, 2), "realised_week": round(real_week, 2),
            "open_risk": round(open_risk, 2), "week_from": week0}


def kite_ready(c, kite_state, today):
    """(ok, why): live mode needs kite_enabled and a Kite connection check from today."""
    if not c.get("kite_enabled"):
        return False, ("\"mode\" is \"live\" but \"kite_enabled\" is not true: live orders stay off until you set "
                       "both yourself and connect Kite (trader-broker SKILL.md)")
    ks = kite_state or {}
    if not ks.get("verified") or str(ks.get("date")) != today:
        return False, ("Kite is not verified today: run trader-broker's kite.py check after your daily Kite "
                       "login (an access token lasts one day)")
    return True, ""


# ---------------------------------------------------------------- the check

def check(orders, cfg, ledger=None, prices=None, kite_state=None, today=None, now=None, price_times=None):
    """Pure function. Returns {"results": [...], "pass": n, "block": n, "warnings": [...], ...}.

    now          naive UTC (default: the real clock); its India date is "today"
    price_times  {SYM: naive UTC time of that price}; None = every price counts as fresh"""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    rates = chg.rates_from(c)
    slip = float(c.get("paper_slippage_pct") or 0) if c.get("limits_include_slippage", True) else 0.0
    prices = {str(k).upper(): float(v) for k, v in (prices or {}).items() if _f(v)}
    now_utc = now or parse_now(None)
    now_ist = to_ist(now_utc)
    today = today or now_ist.date().isoformat()
    capital = float(c.get("capital_inr") or 0)
    mode = str(c.get("mode") or "paper").lower()
    view = ledger_view(ledger, today, capital)
    top_warn = []
    blocked = {str(b).upper() for b in c.get("blocked") or []}
    win_reasons = window_check(now_ist, c)
    mode_reasons = []
    if mode not in ("paper", "live"):
        mode_reasons.append("mode: \"mode\" must be \"paper\" or \"live\", got %r" % c.get("mode"))
    elif mode == "live":
        ok, why = kite_ready(c, kite_state, today)
        if not ok:
            mode_reasons.append("mode: " + why)
        else:
            top_warn.append("LIVE mode: these orders use real money")
    pause_at = capital - float(c.get("max_drawdown_pause_inr") or 0)
    paused = bool(c.get("max_drawdown_pause_inr")) and view["balance"] <= pause_at + EPS
    if paused:
        top_warn.append("the account is at %s, %s below the %s you started with: the drawdown pause is on. Stop "
                        "and review your trades before you continue." % (inr(view["balance"], True),
                                                                        inr(capital - view["balance"], True),
                                                                        inr(capital)))
    daily = _f(c.get("max_daily_loss_inr"))
    weekly = _f(c.get("max_weekly_loss_inr"))
    used_day = max(0.0, -view["realised_today"]) + view["open_risk"]
    used_week = max(0.0, -view["realised_week"]) + view["open_risk"]
    cash = view["balance"] - sum((_f(t.get("entry_fill")) or _f(t.get("planned_entry")) or _f(t.get("entry")) or 0)
                                 * int(_f(t.get("qty")) or 0)
                                 for t in view["open"])
    buffer_inr = capital * float(c.get("cash_buffer_pct") or 0) / 100.0
    held = dict(view["held"])
    seen = set()
    results = []
    batch_entries, batch_loss, batch_open = 0, 0.0, 0

    def fresh(sym):
        if sym not in prices:
            return None
        if price_times is None:
            return prices[sym]
        t = price_times.get(sym)
        if t is None:
            return None
        age = (now_utc - t).total_seconds()
        return prices[sym] if -60 <= age <= FRESH_MINUTES * 60 else None

    for i, o in enumerate(orders, 1):
        reasons, warns, info, later = [], [], [], []
        sym = str(o.get("symbol", "")).strip().upper()
        side = str(o.get("side", "")).strip().lower()
        etype = str(o.get("entry_type", "market")).strip().lower()
        qty = _f(o.get("qty"))
        plan = o.get("plan") if isinstance(o.get("plan"), dict) else {}

        # format
        if not sym or not SYMBOL.match(sym):
            reasons.append("format: %r is not an NSE symbol" % o.get("symbol"))
        if side not in ("buy", "sell"):
            reasons.append("format: side must be buy or sell, got %r" % o.get("side"))
        if qty is None or qty <= 0 or abs(qty - round(qty)) > EPS:
            reasons.append("format: qty must be a whole number of shares, got %r" % o.get("qty"))
            qty = None
        if str(o.get("exchange", "NSE")).upper() != "NSE":
            reasons.append("format: this pack trades NSE only, got exchange %r" % o.get("exchange"))
        if str(o.get("product", "MIS")).upper() != "MIS":
            reasons.append("format: product must be MIS (intraday); delivery, F&O and margin products are off")
        if etype not in ("market", "limit"):
            reasons.append("format: entry_type must be market or limit, got %r" % etype)
        if etype == "limit" and not _f(o.get("limit_price")):
            reasons.append("format: a limit order needs a limit_price")
        reasons.extend(mode_reasons)

        # entry or exit
        have = held.get(sym, 0)
        exiting = side == "sell" and qty is not None and have > 0 and qty <= have + EPS
        kind = "exit" if exiting else "entry"
        if side == "sell" and not exiting and not c.get("allow_short"):
            reasons.append("short: selling %s %s but you hold %d; short selling is off" % (
                _num(qty or 0), sym, have))

        # the reference price
        plan_entry = _f(plan.get("entry"))
        live = fresh(sym)
        ref, ref_from = None, None
        if etype == "limit":
            ref, ref_from = _f(o.get("limit_price")), "limit"
        else:
            cands = [(p, w) for p, w in ((plan_entry, "plan"), (live, "price")) if p]
            if cands:
                ref, ref_from = max(cands, key=lambda x: x[0])
                if live and ref_from == "price" and plan_entry and live > plan_entry + 0.005:
                    info.append("%s last %.2f is above the plan's entry %.2f: the risk is measured from %.2f"
                                % (sym, live, plan_entry, live))
        if plan_entry and live is None and sym in prices and price_times is not None and etype == "market":
            warns.append("the %s price in trader/prices.json is older than %d minutes: not used" % (sym, FRESH_MINUTES))
        value = (qty or 0) * ref if ref else None

        max_loss = rr = charge_pct = None
        if kind == "entry" and side == "buy":
            if paused:
                later.append("pause: the account is %s below capital (limit %s). All paper trading stops until "
                             "you review it." % (inr(capital - view["balance"], True),
                                                 inr(c["max_drawdown_pause_inr"])))
            if sym in blocked:
                later.append("blocked: %s is on your blocked list" % sym)
            stop, target = _f(o.get("stop")), _f(o.get("target"))
            if not stop:
                if c.get("require_stop", True):
                    later.append("stop: no stop. Every entry needs one (trader-exit sets it)")
            elif ref is None:
                later.append("price: no entry price to measure the risk from (the order has no plan entry and "
                             "no fresh price)")
            elif stop >= ref - 0.005:
                later.append("stop: the stop %.2f must be below the entry %.2f" % (stop, ref))
            elif qty:
                bf, sf = ref * (1 + slip / 100.0), stop * (1 - slip / 100.0)
                cs = chg.charges(qty * bf, qty * sf, rates)
                max_loss = round(qty * (bf - sf) + cs["total"], 2)
                cap = _f(c.get("max_loss_per_trade_inr"))
                if cap and max_loss > cap + EPS:
                    later.append("loss: %d shares lose %s if the stop %.2f fills (risk %.2f a share + %s charges%s), "
                                 "over your %s max loss per trade" % (
                                     qty, inr(max_loss, True), stop, ref - stop, inr(cs["total"], True),
                                     " + slippage" if slip else "", inr(cap)))
                if not target:
                    later.append("reward: no target to measure reward:risk against")
                elif target <= ref + 0.005:
                    later.append("reward: the target %.2f must be above the entry %.2f" % (target, ref))
                else:
                    tf = target * (1 - slip / 100.0)
                    ct = chg.charges(qty * bf, qty * tf, rates)
                    gain = qty * (tf - bf) - ct["total"]
                    rr = round(gain / max_loss, 2) if max_loss > 0 else None
                    mrr = _f(c.get("min_reward_risk"))
                    if mrr and (rr is None or rr < mrr - EPS):
                        later.append("reward: reward:risk after charges is %.2f (net %s at the target %.2f vs %s at "
                                     "the stop), under your minimum %s" % (rr or 0, inr(gain, True), target,
                                                                          inr(max_loss, True), _num(mrr)))
                    gross = qty * (target - ref)
                    plain = chg.charges(qty * ref, qty * target, rates)["total"]
                    charge_pct = round(100.0 * plain / gross, 1)
                    mc = _f(c.get("max_charges_pct_of_target"))
                    if mc and charge_pct > mc + EPS:
                        later.append("charges: %s of charges is %.1f%% of the %s gross profit at the target, over "
                                     "your %s%% limit" % (inr(plain, True), charge_pct, inr(gross, True), _num(mc)))
            if value is not None:
                cap_v = _f(c.get("max_position_value_inr"))
                if cap_v and value > cap_v + 0.005:
                    later.append("position: %s of %s is over your %s position cap" % (inr(value, True), sym,
                                                                                      inr(cap_v)))
                if value > cash + 0.005 and not c.get("allow_margin"):
                    later.append("cash: needs %s but the account has %s free; that would be margin (borrowed "
                                 "money), and leverage is off" % (inr(value, True), inr(max(cash, 0), True)))
                elif cash - value < buffer_inr - 0.005:
                    later.append("cash: leaves %s, under your %s%% cash buffer of %s" % (
                        inr(max(cash - value, 0), True), _num(c["cash_buffer_pct"]), inr(buffer_inr)))
            if daily and max_loss is not None:
                used = used_day + batch_loss
                if used >= daily - EPS:
                    later.append("daily: today's realised loss and open risk already reach your %s daily limit. "
                                 "No new entries today." % inr(daily))
                elif used + max_loss > daily + EPS:
                    later.append("daily: %s used today (realised loss %s + open risk %s) plus %s for this one = %s, "
                                 "over your %s daily limit" % (
                                     inr(used, True), inr(max(0, -view["realised_today"]), True),
                                     inr(view["open_risk"] + batch_loss, True), inr(max_loss, True),
                                     inr(used + max_loss, True), inr(daily)))
            if weekly and max_loss is not None:
                used = used_week + batch_loss
                if used >= weekly - EPS:
                    later.append("weekly: this week's realised loss and open risk already reach your %s weekly "
                                 "limit. No new entries until next Monday." % inr(weekly))
                elif used + max_loss > weekly + EPS:
                    later.append("weekly: %s used this week plus %s for this one = %s, over your %s weekly limit "
                                 "(it resets on Monday)" % (inr(used, True), inr(max_loss, True),
                                                            inr(used + max_loss, True), inr(weekly)))
            cap_t = int(c.get("max_trades_per_day") or 0)
            if cap_t and view["entries_today"] + batch_entries >= cap_t:
                later.append("trades: %d entries already today, your limit is %d a day" % (
                    view["entries_today"] + batch_entries, cap_t))
            cap_o = int(c.get("max_open_positions") or 0)
            if cap_o and len(view["open"]) + batch_open >= cap_o:
                later.append("open: %d position%s already open (%s), your limit is %d" % (
                    len(view["open"]) + batch_open, "" if len(view["open"]) + batch_open == 1 else "s",
                    ", ".join(sorted(view["held"])) or "this batch", cap_o))
            later.extend(win_reasons)
            if stop and ref and max_loss is not None:
                info.append("entry %.2f (%s), stop %.2f, target %s: %d shares, %s order value"
                            % (ref, REF_WORDS.get(ref_from, ref_from), stop, ("%.2f" % target) if target else "none",
                               qty, inr(value, True)))
                info.append("loss at the stop %s incl. charges%s (limit %s); reward:risk after charges %s (min %s)"
                            % (inr(max_loss, True), " and slippage" if slip else "",
                               inr(c["max_loss_per_trade_inr"]), ("%.2f" % rr) if rr is not None else "n/a",
                               _num(c["min_reward_risk"])))
            info.append("today before this: %d of %s trades, %s of %s daily; this week %s of %s"
                        % (view["entries_today"] + batch_entries, c.get("max_trades_per_day") or "unlimited",
                           inr(used_day + batch_loss, True), inr(daily) if daily else "no limit",
                           inr(used_week + batch_loss, True), inr(weekly) if weekly else "no limit"))

        key = (sym, side)
        if key in seen:
            later.append("duplicate: a second %s %s in this batch" % (side, sym))
        seen.add(key)

        if kind == "entry":
            reasons.extend(later)
        else:
            warns.extend("exit, not blocked: " + x for x in later)
            if win_reasons:
                warns.extend("exit, not blocked: " + x for x in win_reasons)
        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS" and kind == "entry" and side == "buy":
            batch_entries += 1
            batch_open += 1
            batch_loss += max_loss or 0.0
            cash -= value or 0.0
            held[sym] = held.get(sym, 0) + int(qty or 0)
        elif verdict == "PASS" and kind == "exit":
            held[sym] = have - int(qty or 0)
        results.append({
            "index": i, "symbol": sym, "side": side, "kind": kind, "qty": int(qty) if qty else None,
            "value_inr": round(value, 2) if value is not None else None,
            "entry_ref": round(ref, 2) if ref else None, "entry_ref_from": ref_from,
            "stop": _f(o.get("stop")), "target": _f(o.get("target")), "max_loss": max_loss,
            "reward_risk_net": rr, "charges_pct_of_target": charge_pct, "fingerprint": fingerprint(o),
            "verdict": verdict, "reasons": reasons, "warnings": warns, "info": info,
        })

    return {
        "date": today, "now_india": now_ist.strftime("%Y-%m-%d %H:%M"), "mode": mode,
        "results": results,
        "pass": sum(1 for r in results if r["verdict"] == "PASS"),
        "block": sum(1 for r in results if r["verdict"] == "BLOCK"),
        "warnings": top_warn,
        "account": {"balance": view["balance"], "capital": capital, "open_positions": len(view["open"]),
                    "entries_today": view["entries_today"], "realised_today": view["realised_today"],
                    "realised_week": view["realised_week"], "open_risk": view["open_risk"],
                    "week_from": view["week_from"], "paused": paused},
    }


def report(res):
    lines = []
    for w in res["warnings"]:
        lines.append("WARNING: " + w)
    if res["warnings"]:
        lines.append("")
    for r in res["results"]:
        lines.append("%-5s #%d %s %s %s shares, %s" % (r["verdict"], r["index"], r["side"], r["symbol"],
                                                      r["qty"] if r["qty"] is not None else "?", r["kind"]))
        for x in r.get("info", []):
            lines.append("          " + x)
        for x in r["reasons"]:
            lines.append("        - " + x)
        for x in r["warnings"]:
            lines.append("        ~ " + x)
    a = res["account"]
    lines.append("")
    lines.append("%d passed, %d blocked at %s India time, %s mode. Account %s (capital %s)."
                 % (res["pass"], res["block"], res["now_india"], res["mode"].upper(), inr(a["balance"], True),
                    inr(a["capital"])))
    if res["block"]:
        lines.append("A blocked order goes nowhere. Fix the order (fewer shares, a better plan) or skip the trade. "
                     "Never loosen a limit to make it pass.")
    else:
        lines.append("Every order passed. A PASS is a rules check, not a recommendation: trader-broker still asks "
                     "you yes or no for each one.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


def load_prices(pdoc):
    """prices.json -> ({SYM: price}, {SYM: price time as naive UTC or None})."""
    if not isinstance(pdoc, dict):
        return {}, {}
    prices = pdoc.get("prices", pdoc)
    if not isinstance(prices, dict):
        return {}, {}
    out, times = {}, {}
    as_of = parse_utc(pdoc.get("as_of"))
    detail = pdoc.get("detail") or {}
    for s, p in prices.items():
        if _f(p) is None:
            continue
        s = str(s).upper()
        out[s] = float(p)
        d = detail.get(s) or {}
        times[s] = parse_utc(d.get("time")) or parse_utc(d.get("fetched")) or as_of
    return out, times


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check proposed orders against your limits (trader-risk).")
    ap.add_argument("--orders", default="trader/orders.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--ledger", help="account ledger (default trader/state/<mode>.json)")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--kite-state", default="trader/state/kite.json", help="the Kite connection check (live mode)")
    ap.add_argument("--now", help="pretend it is this India time, e.g. \"2026-10-30 09:51\"")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today in India)")
    ap.add_argument("--out", default="trader/state/risk.json", help="where the verdicts are recorded")
    ap.add_argument("--no-write", action="store_true", help="do not record the verdicts")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        now = parse_now(a.now)
        if a.today and not re.match(r"^\d{4}-\d{2}-\d{2}$", a.today):
            raise RiskError("--today wants a date like 2026-10-30")
        doc = read_json(a.orders, "orders file (run trader-budget first)")
        orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
        if not isinstance(orders, list) or not orders:
            raise RiskError("no orders in %s" % a.orders)
        cfg = read_json(a.config, "config", required=False) or {}
        mode = str(cfg.get("mode") or "paper").lower()
        ledger = read_json(a.ledger or "trader/state/%s.json" % ("live" if mode == "live" else "paper"),
                           "ledger", required=False)
        pdoc = read_json(a.prices, "prices", required=False) or {}
        prices, times = load_prices(pdoc)
        kite_state = read_json(a.kite_state, "kite state", required=False)
        res = check(orders, cfg, ledger, prices, kite_state, a.today, now, times)
        if not a.no_write:
            d = os.path.dirname(a.out)
            if d:
                os.makedirs(d, exist_ok=True)
            rec = dict(res)
            rec["checked_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            rec["orders_file"] = a.orders
            tmp = a.out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, a.out)
    except RiskError as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2, ensure_ascii=False) if a.json else report(res))
    return 1 if res["block"] else 0


if __name__ == "__main__":
    sys.exit(main())
