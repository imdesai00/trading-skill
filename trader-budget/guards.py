#!/usr/bin/env python3
"""Shared guard rules for the India day-trader pack. Standard library only, no network, no clock of its own.

The same file lives in trader-risk, trader-budget, trader-scanner and trader-control (a test keeps the copies
identical). Everything here is a pure function of the config, the data files under trader/state/ and the
numbers it is given, so trader-budget sizes with the same rules trader-risk enforces.

  limits         every limit is stored as a PERCENT of capital in config["limits_pct"]; the rupee values
                 (max_loss_per_trade_inr ...) are derived from it. If a rupee value was edited by hand and
                 no longer matches, the percent wins and a note says so.
  protection     data files you refresh each morning (trader/state/):
                   surveillance.json  {"date": "2026-10-30", "asm": ["ABC"], "gsm": ["XYZ"]}
                   mis.json           {"date": "2026-10-30", "symbols": ["RELIANCE", ...]}
                   bands.json         {"date": "2026-10-30", "bands": {"ABC": 20, "RELIANCE": null}}   price band %, null = none
                   market.json        {"date": "2026-10-30", "vix": 14.2, "nifty_gap_pct": 0.3}
                   results_today.json {"date": "2026-10-30", "symbols": {"TCS": "Q2 results"}}    (written by trader-news)
                   events.json        {"2026-11-06": "RBI policy"}                                  (yours)
                 Where the files come from is in VERIFY.md section 14. A missing or out-of-date file is reported
                 as a data gap: a WARNING in paper mode, a BLOCK in live mode (config "protection_data_required").
  half size      results day for the stock, RBI policy day, Union Budget day, index expiry day, India VIX above
                 20, a NIFTY gap above 1 %: the per-trade loss limit and the position cap are halved (once, not
                 per reason). VIX above 25: no entries. The first 2 live weeks run at 50 % of normal as well.
  brakes         2 losing exits in a row today: no new entries today. 15 minutes after a losing exit: no entry.
"""

import datetime
import json
import os

PCT_KEYS = (("max_loss_per_trade_pct", "max_loss_per_trade_inr"), ("max_daily_loss_pct", "max_daily_loss_inr"),
            ("max_weekly_loss_pct", "max_weekly_loss_inr"), ("max_drawdown_pause_pct", "max_drawdown_pause_inr"),
            ("max_position_value_pct", "max_position_value_inr"))
DEFAULT_PCT = {"max_loss_per_trade_pct": 0.5, "max_daily_loss_pct": 1.5, "max_weekly_loss_pct": 4.0,
               "max_drawdown_pause_pct": 8.0, "max_position_value_pct": 75.0}
TUESDAY = 1
GUARD_DEFAULTS = {"vix_half": 20.0, "vix_stop": 25.0, "gap_half_pct": 1.0, "circuit_margin_pct": 2.0,
                  "max_data_age_min": 3, "losing_streak_stop": 2, "cooldown_min": 15, "live_start_risk_pct": 50,
                  "live_start_weeks": 2, "index_expiry_weekday": TUESDAY, "budget_day": "02-01",
                  "half_size_factor": 0.5}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def read_state(state_dir, name):
    p = os.path.join(state_dir, name)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- limits as percent of capital

def derive_inr(capital, pcts):
    return {inr_key: round(float(capital) * float(pcts[pct_key]) / 100.0, 2)
            for pct_key, inr_key in PCT_KEYS if pct_key in pcts}


def effective_limits(cfg):
    """-> (config copy with the rupee limits filled from limits_pct x capital, notes)."""
    c = dict(cfg or {})
    notes = []
    pcts = c.get("limits_pct")
    capital = _f(c.get("capital_inr"))
    if not isinstance(pcts, dict) or not capital:
        return c, notes
    for key, val in derive_inr(capital, pcts).items():
        have = _f(c.get(key))
        if have is not None and abs(have - val) > 0.005:
            notes.append("%s is %s in the file but limits_pct x capital makes it %s: using %s. Change the percent "
                         "(control.py set), or the file's number is ignored." % (key, have, val, val))
        c[key] = val
    return c, notes


def limits_table(capital, pcts):
    """Plain rows for 'old vs new' displays: [(label, percent, rupees)]."""
    labels = {"max_loss_per_trade_inr": "max loss per trade", "max_daily_loss_inr": "max daily loss",
              "max_weekly_loss_inr": "max weekly loss", "max_drawdown_pause_inr": "drawdown pause",
              "max_position_value_inr": "max position value"}
    inr = derive_inr(capital, pcts)
    return [(labels[k], pcts[p], inr[k]) for p, k in PCT_KEYS if p in pcts]


def g(cfg, key):
    v = (cfg or {}).get(key)
    return GUARD_DEFAULTS[key] if v is None else v


# ---------------------------------------------------------------- protection data

def load_protection(state_dir):
    return {name: read_state(state_dir, name) for name in
            ("surveillance.json", "mis.json", "bands.json", "market.json", "results_today.json", "events.json")}


def strict(cfg, mode):
    """Are missing protection files a BLOCK? Live: yes unless you set protection_data_required false."""
    v = (cfg or {}).get("protection_data_required")
    return (mode == "live") if v is None else bool(v)


def _fresh(doc, today):
    return isinstance(doc, dict) and str(doc.get("date")) == today


def data_gaps(prot, today):
    """Which protection files are missing or not from today."""
    gaps = []
    for name, what in (("surveillance.json", "the ASM/GSM lists"), ("mis.json", "the MIS-eligible list"),
                       ("bands.json", "the price bands (circuit limits)"), ("market.json", "India VIX and the NIFTY gap")):
        doc = prot.get(name)
        if doc is None:
            gaps.append("%s: no trader/state/%s" % (what, name))
        elif not _fresh(doc, today):
            gaps.append("%s: trader/state/%s is from %s, not today" % (what, name, doc.get("date") if isinstance(doc, dict) else "?"))
    return gaps


def symbol_blocks(sym, ref, prev_close, prot, today, cfg):
    """Hard reasons this stock cannot be bought: ASM/GSM, not MIS-eligible, near a circuit."""
    out = []
    s = (prot.get("surveillance.json") or {}) if _fresh(prot.get("surveillance.json"), today) else {}
    for lst, name in (("asm", "ASM (Additional Surveillance Measure)"), ("gsm", "GSM (Graded Surveillance Measure)")):
        if sym in {str(x).upper() for x in s.get(lst, [])}:
            out.append("surveillance: %s is on the %s list" % (sym, name))
    m = prot.get("mis.json") if _fresh(prot.get("mis.json"), today) else None
    if m is not None and sym not in {str(x).upper() for x in m.get("symbols", [])}:
        out.append("mis: %s is not on today's MIS-eligible list: the broker would refuse the intraday order" % sym)
    b = prot.get("bands.json") if _fresh(prot.get("bands.json"), today) else None
    band = _f((b or {}).get("bands", {}).get(sym)) if b else None
    prev = _f(prev_close)
    if band and prev and ref:
        upper, lower = prev * (1 + band / 100.0), prev * (1 - band / 100.0)
        margin = float(g(cfg, "circuit_margin_pct"))
        if (upper - ref) / upper * 100.0 <= margin:
            out.append("circuit: %.2f is within %s%% of the upper circuit %.2f (band %s%% on yesterday's %.2f)"
                       % (ref, "%g" % margin, upper, "%g" % band, prev))
        elif (ref - lower) / lower * 100.0 <= margin:
            out.append("circuit: %.2f is within %s%% of the lower circuit %.2f (band %s%% on yesterday's %.2f)"
                       % (ref, "%g" % margin, lower, "%g" % band, prev))
    return out


# ---------------------------------------------------------------- half size, VIX, events

def events_today(today, sym, cfg, prot):
    """-> (market_wide [names], this_stock [names])."""
    d = datetime.date.fromisoformat(today)
    wide = []
    ev = prot.get("events.json") or {}
    wide += [str(v) for k, v in ev.items() if k == today]
    wide += [str(v) for k, v in ((cfg or {}).get("event_days") or {}).items() if k == today]
    if today[5:] == str(g(cfg, "budget_day")):
        wide.append("Union Budget day (1 February by default; check the date)")
    if d.weekday() == int(g(cfg, "index_expiry_weekday")):
        wide.append("index expiry day (NIFTY weekly options expire on Tuesdays since September 2025; unverified)")
    mine = []
    res = prot.get("results_today.json")
    if _fresh(res, today):
        t = (res.get("symbols") or {}).get(sym)
        if t is not None:
            mine.append("%s reports results today (%s)" % (sym, t))
    return wide, mine


def volatility(prot, today, cfg):
    """-> (factor_reasons, stop_reasons)."""
    half, stop = [], []
    m = prot.get("market.json")
    if not _fresh(m, today):
        return half, stop
    vix, gap = _f(m.get("vix")), _f(m.get("nifty_gap_pct"))
    if vix is not None:
        if vix > float(g(cfg, "vix_stop")):
            stop.append("volatility: India VIX is %.1f, above %g: no entries" % (vix, float(g(cfg, "vix_stop"))))
        elif vix > float(g(cfg, "vix_half")):
            half.append("India VIX is %.1f, above %g" % (vix, float(g(cfg, "vix_half"))))
    if gap is not None and abs(gap) > float(g(cfg, "gap_half_pct")):
        half.append("NIFTY opened %+.1f%% from yesterday's close" % gap)
    return half, stop


def live_start_reason(trades, today, cfg, mode):
    """The first 2 live weeks run at live_start_risk_pct of normal. trades: the LIVE ledger's trades."""
    if mode != "live":
        return None
    days = [str(t.get("date")) for t in (trades or []) if t.get("status") != "failed" and t.get("date")]
    first = min(days) if days else today
    weeks = float(g(cfg, "live_start_weeks"))
    if (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(first)).days < weeks * 7:
        return "the first %g live weeks run at %g%% of normal risk (live started %s)" % (
            weeks, float(g(cfg, "live_start_risk_pct")), first)
    return None


def size_factor(sym, today, cfg, prot, mode, live_trades=None):
    """-> (factor, [plain reasons], [stop reasons]). 0.5 once for half-size reasons, times the live-start factor."""
    reasons, stops = [], []
    wide, mine = events_today(today, sym, cfg, prot)
    reasons += wide + mine
    half, stop = volatility(prot, today, cfg)
    reasons += half
    stops += stop
    factor = float(g(cfg, "half_size_factor")) if reasons else 1.0
    ls = live_start_reason(live_trades, today, cfg, mode)
    if ls:
        reasons.append(ls)
        factor *= float(g(cfg, "live_start_risk_pct")) / 100.0
    return factor, reasons, stops


# ---------------------------------------------------------------- brakes

def exit_stamp(t):
    """A closed trade's exit as a naive India datetime, or None."""
    day, hm = t.get("exit_date") or t.get("date"), t.get("exit_time")
    try:
        return datetime.datetime.strptime("%s %s" % (day, hm), "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None


def brakes(trades, today, now_ist, cfg):
    """Reasons no new entry is allowed after today's losing exits. now_ist is naive India time."""
    out = []
    closed = [t for t in (trades or []) if t.get("status") == "closed" and str(t.get("exit_date") or t.get("date")) == today]
    closed.sort(key=lambda t: (exit_stamp(t) or datetime.datetime.min, t.get("trade_id", "")))
    streak = 0
    for t in closed:
        streak = streak + 1 if (_f(t.get("net_pnl")) or 0) < 0 else 0
    n = int(g(cfg, "losing_streak_stop"))
    if n and streak >= n:
        out.append("streak: %d losing trades in a row today: no new entries today" % streak)
    if closed and (_f(closed[-1].get("net_pnl")) or 0) < 0:
        stamp = exit_stamp(closed[-1])
        cd = int(g(cfg, "cooldown_min"))
        if stamp is not None and cd and now_ist < stamp + datetime.timedelta(minutes=cd):
            left = int((stamp + datetime.timedelta(minutes=cd) - now_ist).total_seconds() // 60) + 1
            out.append("cooldown: %s lost at %s; wait %d more minute%s before another entry" % (
                closed[-1].get("symbol"), closed[-1].get("exit_time"), left, "" if left == 1 else "s"))
    return out
