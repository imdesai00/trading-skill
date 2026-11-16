#!/usr/bin/env python3
"""Daily and weekly reports for the paper and live record (trader-report). Standard library only, no network.

The record lives in trader/trader.db (see trader-db). This tool reads it and writes plain reports: it prints them, saves
them as Markdown files in trader/reports/, and keeps a copy in the database's daily_summary and weekly_reports tables.
Paper and live are always reported separately.

    python3 report.py daily                          # today, paper
    python3 report.py daily --date 2026-10-30 --mode live
    python3 report.py weekly                         # the week of today
    python3 report.py weekly --week-of 2026-10-30

A daily report: trades, wins and losses, net after charges, charges, balance, limits hit, rules broken, and for each
trade your "why" and "how I felt". A weekly report: trades, win rate, average R, expectancy per trade after charges,
charges paid, best and worst trade, results by setup and against the time machine's odds, the skipped signals compared with
the trades taken, limits hit, and the balance against your starting capital. Plain words, no hype.

With no database it reads trader/state/paper.json (or live.json) instead, and says so.
"""

import argparse
import datetime
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db  # noqa: E402  (a copy of trader-db/db.py; a test keeps the copies identical)

LIMIT_DEFAULTS = {"capital_inr": 100000, "max_loss_per_trade_inr": 500, "max_daily_loss_inr": 1500,
                  "max_weekly_loss_inr": 4000, "max_drawdown_pause_inr": 8000, "max_trades_per_day": 3}
HYP_REASONS = ("target", "stop", "flat-by")


class ReportError(Exception):
    pass


ClockError = ReportError

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
            raise ReportError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise ReportError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def load_cfg(path):
    cfg = dict(LIMIT_DEFAULTS)
    cfg.update(read_json(path, "config", required=False) or {})
    return cfg


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def r2(x):
    return round(float(x) + 1e-9, 2)


# ---------------------------------------------------------------- the numbers

def summarize(trades):
    """Plain statistics of closed trades."""
    n = len(trades)
    nets = [t["net_pnl"] for t in trades]
    rs = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    wins = [t for t in trades if t["net_pnl"] > 0]
    out = {"n": n, "wins": len(wins), "losses": n - len(wins), "net": r2(sum(nets)),
           "charges": r2(sum(t["charges"] for t in trades)), "gross": r2(sum(t["gross_pnl"] for t in trades)),
           "win_rate": round(100.0 * len(wins) / n, 1) if n else None,
           "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
           "expectancy": r2(sum(nets) / n) if n else None,
           "best": max(trades, key=lambda t: t["net_pnl"]) if n else None,
           "worst": min(trades, key=lambda t: t["net_pnl"]) if n else None}
    return out


def closed(led):
    return sorted([t for t in led["trades"] if t.get("status") == "closed"],
                  key=lambda t: (str(t.get("exit_date") or t["date"]), t["trade_id"]))


def balance_through(led, day, cfg):
    """The account balance after the last trade closed on or before `day`."""
    bal = float(led.get("capital") or cfg["capital_inr"])
    for t in closed(led):
        if str(t.get("exit_date") or t["date"]) <= day:
            bal = t["balance_after"] if t.get("balance_after") is not None else bal + t["net_pnl"]
    return r2(bal)


def week_days(day):
    d = datetime.date.fromisoformat(day)
    mon = d - datetime.timedelta(days=d.weekday())
    return mon, [(mon + datetime.timedelta(days=i)).isoformat() for i in range(5)]


def day_stats(led, cfg, day):
    capital = float(led.get("capital") or cfg["capital_inr"])
    cl = [t for t in closed(led) if str(t.get("exit_date") or t["date"]) == day]
    entries = [t for t in led["trades"] if t["date"] == day]
    s = summarize(cl)
    bal = balance_through(led, day, cfg)
    mon, days = week_days(day)
    wk = [t for t in closed(led) if mon.isoformat() <= str(t.get("exit_date") or t["date"]) <= day]
    day_loss = -sum(t["net_pnl"] for t in cl if t["net_pnl"] < 0)
    wk_loss = -sum(t["net_pnl"] for t in wk if t["net_pnl"] < 0)
    limits = []
    if cfg["max_daily_loss_inr"] and day_loss >= cfg["max_daily_loss_inr"] - 0.005:
        limits.append("daily loss limit %s reached (lost %s)" % (inr(cfg["max_daily_loss_inr"]), inr(day_loss, True)))
    if cfg["max_weekly_loss_inr"] and wk_loss >= cfg["max_weekly_loss_inr"] - 0.005:
        limits.append("weekly loss limit %s reached (lost %s this week)" % (inr(cfg["max_weekly_loss_inr"]),
                                                                           inr(wk_loss, True)))
    if cfg["max_drawdown_pause_inr"] and bal <= capital - cfg["max_drawdown_pause_inr"] + 0.005:
        limits.append("drawdown pause: %s below %s" % (inr(capital - bal, True), inr(capital)))
    if cfg["max_trades_per_day"] and len(entries) >= cfg["max_trades_per_day"]:
        limits.append("%d trades today (the day's limit)" % len(entries))
    rules = []
    cap = cfg["max_loss_per_trade_inr"]
    for t in cl:
        if cap and t["net_pnl"] < -cap - 0.005:
            rules.append("%s lost %s, %s over the %s per-trade limit" % (
                t["trade_id"], inr(-t["net_pnl"], True), inr(-t["net_pnl"] - cap, True), inr(cap)))
    return {"date": day, "trades": len(entries), "closed": len(cl), "wins": s["wins"], "losses": s["losses"],
            "net": s["net"], "charges": s["charges"], "win_rate": s["win_rate"], "avg_r": s["avg_r"],
            "balance": bal, "drawdown": r2(max(0.0, capital - bal)), "limits": limits, "rules": rules,
            "capital": capital}


def daily_text(st):
    if st["trades"] == 0:
        head = "%s: no trades." % st["date"]
    else:
        head = "%s: %d trade%s (%d win%s, %d loss%s), net %s after %s of charges." % (
            st["date"], st["trades"], "" if st["trades"] == 1 else "s", st["wins"], "" if st["wins"] == 1 else "s",
            st["losses"], "" if st["losses"] == 1 else "es", inr(st["net"], True), inr(st["charges"], True))
    lines = [head, "Balance %s (%s %s %s)." % (
        inr(st["balance"], True), "down" if st["balance"] < st["capital"] else "up",
        inr(abs(st["balance"] - st["capital"]), True), "from " + inr(st["capital"]))]
    lines.append("Limits hit: %s." % ("; ".join(st["limits"]) if st["limits"] else "none"))
    lines.append("Rules broken: %s." % ("; ".join(st["rules"]) if st["rules"] else "none"))
    return lines


def odds_bucket(t):
    o = t.get("tm_odds") or {}
    p = o.get("target")
    if p is None:
        return "no time-machine odds recorded"
    return "target-first under 20 %" if p < 0.20 else "target-first 20 to 35 %" if p <= 0.35 else \
        "target-first over 35 %"


def week_stats(led, cfg, day):
    mon, days = week_days(day)
    cl = [t for t in closed(led) if str(t.get("exit_date") or t["date"]) in days]
    s = summarize(cl)
    by_setup, by_odds = {}, {}
    for t in cl:
        by_setup.setdefault(t.get("setup") or "(none)", []).append(t)
        by_odds.setdefault(odds_bucket(t), []).append(t)
    sk = [x for x in led["skipped"] if x["date"] in days]
    replayed = [x for x in sk if x.get("hypothetical_net_pnl") is not None]
    limits, rules = [], []
    for d in days:
        st = day_stats(led, cfg, d)
        limits += ["%s: %s" % (d, x) for x in st["limits"]]
        rules += ["%s: %s" % (d, x) for x in st["rules"]]
    bal = balance_through(led, days[-1], cfg)
    capital = float(led.get("capital") or cfg["capital_inr"])
    return {"monday": mon.isoformat(), "friday": days[-1], "iso": "%d-W%02d" % mon.isocalendar()[:2], "days": days,
            "summary": s, "by_setup": {k: summarize(v) for k, v in by_setup.items()},
            "by_odds": {k: summarize(v) for k, v in by_odds.items()}, "skipped": len(sk),
            "skipped_replayed": len(replayed), "skipped_net": r2(sum(x["hypothetical_net_pnl"] for x in replayed)),
            "limits": limits, "rules": rules, "balance": bal, "capital": capital,
            "vs_start": r2(bal - capital), "all_closed": len(closed(led))}


def _line_sum(label, s):
    return "%s: %d trade%s, net %s, win rate %s" % (
        label, s["n"], "" if s["n"] == 1 else "s", inr(s["net"], True),
        ("%.0f %%" % s["win_rate"]) if s["win_rate"] is not None else "n/a")


def weekly_markdown(w):
    s = w["summary"]
    L = ["## What happened", ""]
    if s["n"] == 0:
        L += ["- No trades closed this week.", ""]
    else:
        L += ["- Trades: %d (%d win%s, %d loss%s). Win rate %.0f %%." % (
                  s["n"], s["wins"], "" if s["wins"] == 1 else "s", s["losses"], "" if s["losses"] == 1 else "es",
                  s["win_rate"]),
              "- Net result after charges: %s. Average R: %s." % (inr(s["net"], True), s["avg_r"]),
              "- Net expectancy per trade after charges: %s." % inr(s["expectancy"], True),
              "- Charges paid: %s. Without charges the gross result was %s, so charges took %s of it." % (
                  inr(s["charges"], True), inr(s["gross"], True),
                  ("%.0f %%" % (100.0 * s["charges"] / abs(s["gross"]))) if s["gross"] else "all"),
              ("- The only trade: %s %s, %s." % (s["best"]["trade_id"], s["best"]["symbol"],
                                                  inr(s["best"]["net_pnl"], True))) if s["n"] == 1 else
              "- Best trade: %s %s, %s. Worst trade: %s %s, %s." % (
                  s["best"]["trade_id"], s["best"]["symbol"], inr(s["best"]["net_pnl"], True),
                  s["worst"]["trade_id"], s["worst"]["symbol"], inr(s["worst"]["net_pnl"], True)), ""]
    L += ["## By setup type", ""]
    L += ["- " + _line_sum(k, v) for k, v in sorted(w["by_setup"].items())] or ["- (none)"]
    L += ["", "## Results against the time machine's odds", ""]
    L += ["- " + _line_sum(k, v) for k, v in sorted(w["by_odds"].items())] or ["- (none)"]
    L += ["", "## Signals you skipped", ""]
    if w["skipped"] == 0:
        L += ["- None logged this week."]
    else:
        if w["skipped_replayed"]:
            L += ["- %d skipped; %d replayed. The replayed ones would have netted %s after charges, against %s from "
                  "the trades you took." % (w["skipped"], w["skipped_replayed"], inr(w["skipped_net"], True),
                                            inr(s["net"], True))]
        else:
            L += ["- %d skipped, none replayed yet: run paper.py reconcile on the day to see what they would have "
                  "done." % w["skipped"]]
        L += ["- A few trades prove nothing either way. Look at this over the whole four weeks."]
    L += ["", "## Limits and rules", ""]
    L += ["- Limits hit: " + ("; ".join(w["limits"]) if w["limits"] else "none") + "."]
    L += ["- Rules broken: " + ("; ".join(w["rules"]) if w["rules"] else "none") + "."]
    L += ["", "## Account", "",
          "- Balance %s, %s %s against the %s you started with." % (
              inr(w["balance"], True), "down" if w["vs_start"] < 0 else "up", inr(abs(w["vs_start"]), True),
              inr(w["capital"])),
          "- Trades closed so far in the whole record: %d." % w["all_closed"], "",
          "## Read this plainly", "",
          "- A week is a handful of trades. It cannot tell skill from luck, and paper fills are kinder than real ones.",
          "- Not financial advice."]
    return "\n".join(L)


def weekly_title(w):
    return "%s (Mon %s to Fri %s)" % (w["iso"], w["monday"], w["friday"])



# ---------------------------------------------------------------- commands

def _db(a):
    return getattr(a, "sqlite", None) or db.default_path(a.config)


def _ledger(a):
    mode = a.mode
    cfg = load_cfg(a.config)
    path = a.ledger or "trader/state/%s.json" % mode
    store = db.LedgerStore(path, _db(a), mode, cfg,
                           lambda c: {"mode": mode, "capital": float(c["capital_inr"]), "balance": float(c["capital_inr"]),
                                      "trades": [], "skipped": []},
                           lambda p_: read_json(p_, "ledger", required=False), write_json, "paper" if mode == "paper" else "kite")
    led = store.load()
    for w in store.warnings:
        print("WARNING: " + w, file=sys.stderr)
    if not store.uses_db and not os.path.exists(path):
        raise ReportError("no record yet: there is no database at %s and no ledger at %s (run size.py init, and trade first)"
                          % (_db(a), path))
    led.setdefault("trades", [])
    led.setdefault("skipped", [])
    return led


def _notes(a):
    out = {}
    if db.exists(_db(a)):
        for n in db.notes_for(_db(a)):
            out.setdefault(n["trade_id"], {})[n["kind"]] = n["text"]
    return out


def _save(a, name, text):
    folder = os.path.join(os.path.dirname(os.path.abspath(a.config)), "reports")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")
    return path


def trade_lines(led, day, notes):
    out = []
    for t in sorted((x for x in led["trades"] if str(x.get("date")) == day), key=lambda x: x["trade_id"]):
        if t.get("status") == "closed":
            line = "  %s %s %d: %s, net %s (%+.2fR), charges %s%s" % (
                t["trade_id"], t["symbol"], t["qty"], t.get("exit_reason") or "closed", inr(t["net_pnl"], True),
                t.get("r_multiple") or 0, inr(t["charges"], True),
                " (actual)" if t.get("charges_kind") == "actual" else " (estimated)")
        else:
            line = "  %s %s %d: %s" % (t["trade_id"], t["symbol"], t["qty"], t.get("status"))
        out.append(line)
        n = notes.get(t["trade_id"], {})
        why = t.get("why") or n.get("why")
        if why:
            out.append("      why I took it: " + why)
        if n.get("feel"):
            out.append("      how I felt: " + n["feel"])
    return out


def cmd_daily(a):
    led, cfg = _ledger(a), load_cfg(a.config)
    now = parse_now(a.now)
    day = a.date or to_ist(now).date().isoformat()
    st = day_stats(led, cfg, day)
    text = ["Daily report, %s mode, %s" % (a.mode.upper(), day)] + daily_text(st) + trade_lines(led, day, _notes(a))
    skipped = [x for x in led["skipped"] if str(x.get("date")) == day]
    if skipped:
        text.append("Skipped signals: %d" % len(skipped))
        for x in skipped:
            hyp = x.get("hypothetical_net_pnl")
            text.append("  %s %s: %s; %s" % (x["signal_id"], x["symbol"], x.get("why_skipped") or "skipped",
                                             ("would have netted " + inr(hyp, True)) if hyp is not None else "not replayed yet"))
    body = "\n".join(text)
    print(body)
    path = _save(a, "daily-%s-%s.md" % (a.mode, day), body)
    if db.exists(_db(a)):
        with db.Tx(_db(a)) as conn:
            conn.execute("INSERT OR REPLACE INTO daily_summary (date, mode, doc) VALUES (?,?,?)",
                         (day, a.mode, json.dumps(dict(st, text=body), ensure_ascii=False)))
    print("\n(saved: %s)" % path)
    return 0


def cmd_weekly(a):
    led, cfg = _ledger(a), load_cfg(a.config)
    now = parse_now(a.now)
    day = a.week_of or to_ist(now).date().isoformat()
    w = week_stats(led, cfg, day)
    md = "# Weekly report: %s (%s only)\n\n%s" % (weekly_title(w), a.mode.upper(), weekly_markdown(w))
    print(md)
    path = _save(a, "weekly-%s-%s.md" % (a.mode, w["iso"]), md)
    if db.exists(_db(a)):
        with db.Tx(_db(a)) as conn:
            conn.execute("INSERT OR REPLACE INTO weekly_reports (week, mode, doc) VALUES (?,?,?)",
                         (w["iso"], a.mode, json.dumps({"title": weekly_title(w), "markdown": md}, ensure_ascii=False)))
    print("\n(saved: %s)" % path)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Daily and weekly reports (trader-report). Reads the record, changes nothing.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--sqlite", help="the database file (default: trader/trader.db, next to the config)")
        p.add_argument("--mode", choices=["paper", "live"], default="paper")
        p.add_argument("--ledger", help="used when there is no database (default trader/state/<mode>.json)")
        p.add_argument("--now", help="pretend it is this India time, e.g. \"2026-10-30 15:45\"")

    p = sub.add_parser("daily", help="print, save and keep one day's report")
    common(p)
    p.add_argument("--date")
    p = sub.add_parser("weekly", help="print, save and keep a week's report")
    common(p)
    p.add_argument("--week-of", help="any date in the week (default today)")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"daily": cmd_daily, "weekly": cmd_weekly}[a.cmd](a)
    except ReportError as e:
        print("report.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
