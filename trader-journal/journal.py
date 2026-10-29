#!/usr/bin/env python3
"""The Notion journal for the paper-trading record (NSE intraday). Standard library only, no network.

Notion is the permanent source of truth; the files under trader/ are a working
copy that must be rebuildable from it. This script NEVER calls Notion and never
sees a Notion token. It does the two jobs around the Notion connector that
Claude uses:

  outbox   it writes what should be in Notion to trader/state/notion_outbox.json
           (one row per Paper Trade, Skipped Signal, Daily Summary and Weekly
           Report). Claude then writes each pending row through the Notion
           connector and marks it sent. A row is matched on its key (the Trade
           ID, the Signal ID, the date, the week), so it is created once and
           UPDATED after that, never duplicated. Anything unsent stays queued.
  rebuild  it turns rows read back from Notion into a fresh local ledger.

    python3 journal.py schema                    # the 4 databases to create (names, columns, Notion DDL)
    python3 journal.py setup-save --page-id ID --db paper_trades=DS_ID --db skipped_signals=DS_ID ...
    python3 journal.py queue                     # queue every trade, skipped signal and day summary
    python3 journal.py daily --date 2026-10-30   # queue and print one day's summary
    python3 journal.py weekly --week-of 2026-10-30   # queue and print that week's report
    python3 journal.py pending --json            # what Claude must write to Notion now
    python3 journal.py mark-sent --key KEY --page-id ID
    python3 journal.py status
    python3 journal.py rebuild --from trader/state/notion_export.json   # restore the ledger from Notion

Which Notion page is the parent is asked of you at setup time; nothing is
written to Notion before you name it. The Notion property names follow your
spec (Trade ID, Gross P&L ₹, Charges ₹, ...); the exact shapes the connector
accepts are listed in VERIFY.md section 8 and were read from its tool
definitions, not yet exercised against your workspace.
"""

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import sys

OUTBOX_VERSION = 1
DB_ORDER = ("paper_trades", "skipped_signals", "daily_summary", "weekly_reports")
DB_TITLES = {"paper_trades": "Paper Trades", "skipped_signals": "Skipped Signals",
             "daily_summary": "Daily Summary", "weekly_reports": "Weekly Reports"}
PAGE_TITLE = "India Day Trader – Paper Trading"
LIMIT_DEFAULTS = {"capital_inr": 100000, "max_loss_per_trade_inr": 500, "max_daily_loss_inr": 1500,
                  "max_weekly_loss_inr": 4000, "max_drawdown_pause_inr": 8000, "max_trades_per_day": 3}
HYP_REASONS = ("target", "stop", "flat-by")


class JournalError(Exception):
    pass


ClockError = JournalError

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


# ---------------------------------------------------------------- the databases

def drawdown_name(capital):
    return "Drawdown from " + inr(capital)


def columns(db, capital=100000):
    """[(property name, notion DDL type, value kind)] in order. The first is the title."""
    sel = lambda *o: "SELECT(%s)" % ", ".join("'%s':%s" % x for x in o)  # noqa: E731
    if db == "paper_trades":
        return [
            ("Trade ID", "TITLE"), ("Date", "DATE"), ("Symbol", "RICH_TEXT"), ("Setup type", "RICH_TEXT"),
            ("Side", sel(("buy", "green"), ("sell", "red"))), ("Qty", "NUMBER"), ("Planned entry", "NUMBER"),
            ("Entry fill", "NUMBER"), ("Stop", "NUMBER"), ("Target", "NUMBER"), ("Exit price", "NUMBER"),
            ("Exit time", "RICH_TEXT"),
            ("Exit reason", sel(("target", "green"), ("stop", "red"), ("flat-by", "yellow"), ("manual", "gray"))),
            ("Gross P&L ₹", "NUMBER"), ("Charges ₹", "NUMBER"), ("Net P&L ₹", "NUMBER"), ("R multiple", "NUMBER"),
            ("Risk at stop ₹", "NUMBER"), ("Time-machine odds (target/stop/open %)", "RICH_TEXT"),
            ("Risk verdict", sel(("PASS", "green"), ("BLOCK", "red"))), ("News tags", "RICH_TEXT"),
            ("My approval (exact words)", "RICH_TEXT"), ("Mode", sel(("paper", "blue"), ("live", "red"))),
            ("Status", sel(("open", "yellow"), ("closed", "gray"))), ("Account balance after ₹", "NUMBER"),
            ("Notes", "RICH_TEXT"), ("Entry time", "RICH_TEXT"), ("Flat by", "RICH_TEXT")]
    if db == "skipped_signals":
        return [
            ("Signal ID", "TITLE"), ("Date", "DATE"), ("Symbol", "RICH_TEXT"), ("Setup type", "RICH_TEXT"),
            ("Side", sel(("buy", "green"), ("sell", "red"))), ("Qty", "NUMBER"), ("Planned entry", "NUMBER"),
            ("Stop", "NUMBER"), ("Target", "NUMBER"), ("Risk at stop ₹", "NUMBER"), ("Why skipped", "RICH_TEXT"),
            ("Hypothetical exit", sel(("target", "green"), ("stop", "red"), ("flat-by", "yellow"))),
            ("Hypothetical net P&L ₹", "NUMBER"), ("Hypothetical R", "NUMBER"), ("Skipped at", "RICH_TEXT"),
            ("Mode", sel(("paper", "blue"), ("live", "red"))), ("Notes", "RICH_TEXT")]
    if db == "daily_summary":
        return [
            ("Day", "TITLE"), ("Date", "DATE"), ("Trades", "NUMBER"), ("Wins", "NUMBER"), ("Losses", "NUMBER"),
            ("Net P&L ₹", "NUMBER"), ("Charges ₹", "NUMBER"), ("Win rate", "NUMBER"), ("Avg R", "NUMBER"),
            ("Balance ₹", "NUMBER"), (drawdown_name(capital), "NUMBER"), ("Limits hit", "RICH_TEXT"),
            ("Rules broken", "RICH_TEXT")]
    if db == "weekly_reports":
        return [
            ("Week", "TITLE"), ("Week start", "DATE"), ("Week end", "DATE"), ("Trades", "NUMBER"),
            ("Win rate", "NUMBER"), ("Avg R", "NUMBER"), ("Expectancy per trade ₹", "NUMBER"),
            ("Charges paid ₹", "NUMBER"), ("Net P&L ₹", "NUMBER"), ("Balance ₹", "NUMBER"),
            ("Balance vs start ₹", "NUMBER")]
    raise JournalError("unknown database %r" % db)


def ddl(db, capital=100000):
    parts = []
    for name, typ in columns(db, capital):
        if "'" in name or '"' in name:
            raise JournalError("a column name must not hold a quote: %r" % name)
        parts.append('"%s" %s' % (name, typ))
    return "CREATE TABLE (%s)" % ", ".join(parts)


DATE_COLUMNS = {"paper_trades": ("Date",), "skipped_signals": ("Date",), "daily_summary": ("Date",),
                "weekly_reports": ("Week start", "Week end")}


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise JournalError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise JournalError("%s is not valid JSON: %s" % (path, e))


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


# ---------------------------------------------------------------- notion rows

def _date_props(name, day):
    return {"date:%s:start" % name: day, "date:%s:is_datetime" % name: 0}


def _put(props, name, value):
    if value is not None and value != "":
        props[name] = value


def odds_text(t):
    o = t.get("tm_odds") or {}
    if o.get("target") is None:
        return None
    return " / ".join("%.1f" % (100.0 * (o.get(k) or 0)) for k in ("target", "stop", "neither"))


def trade_props(t):
    p = {"Trade ID": t["trade_id"]}
    p.update(_date_props("Date", t["date"]))
    for name, key in (("Symbol", "symbol"), ("Setup type", "setup"), ("Side", "side"), ("Qty", "qty"),
                      ("Planned entry", "planned_entry"), ("Entry fill", "entry_fill"), ("Stop", "stop"),
                      ("Target", "target"), ("Exit price", "exit_fill"), ("Exit time", "exit_time"),
                      ("Exit reason", "exit_reason"), ("Gross P&L ₹", "gross_pnl"), ("Charges ₹", "charges"),
                      ("Net P&L ₹", "net_pnl"), ("R multiple", "r_multiple"), ("Risk at stop ₹", "risk_at_stop"),
                      ("Risk verdict", "risk_verdict"), ("My approval (exact words)", "approval"),
                      ("Mode", "mode"), ("Status", "status"), ("Account balance after ₹", "balance_after"),
                      ("Notes", "notes"), ("Entry time", "entry_time"), ("Flat by", "flat_by")):
        _put(p, name, t.get(key))
    _put(p, "Time-machine odds (target/stop/open %)", odds_text(t))
    _put(p, "News tags", ", ".join(t.get("news_tags") or []))
    return p


def skipped_props(s):
    p = {"Signal ID": s["signal_id"]}
    p.update(_date_props("Date", s["date"]))
    for name, key in (("Symbol", "symbol"), ("Setup type", "setup"), ("Side", "side"), ("Qty", "qty"),
                      ("Planned entry", "planned_entry"), ("Stop", "stop"), ("Target", "target"),
                      ("Risk at stop ₹", "risk_at_stop"), ("Why skipped", "why_skipped"),
                      ("Hypothetical exit", "hypothetical_exit_reason"),
                      ("Hypothetical net P&L ₹", "hypothetical_net_pnl"), ("Hypothetical R", "hypothetical_r"),
                      ("Skipped at", "skipped_at"), ("Mode", "mode"), ("Notes", "notes")):
        _put(p, name, s.get(key))
    return p


def daily_props(st):
    p = {"Day": st["date"]}
    p.update(_date_props("Date", st["date"]))
    p.update({"Trades": st["trades"], "Wins": st["wins"], "Losses": st["losses"], "Net P&L ₹": st["net"],
              "Charges ₹": st["charges"], "Balance ₹": st["balance"], drawdown_name(st["capital"]): st["drawdown"],
              "Limits hit": "; ".join(st["limits"]) or "None", "Rules broken": "; ".join(st["rules"]) or "None"})
    _put(p, "Win rate", st["win_rate"])
    _put(p, "Avg R", st["avg_r"])
    return p


def weekly_props(w):
    s = w["summary"]
    p = {"Week": weekly_title(w)}
    p.update(_date_props("Week start", w["monday"]))
    p.update(_date_props("Week end", w["friday"]))
    p.update({"Trades": s["n"], "Charges paid ₹": s["charges"], "Net P&L ₹": s["net"], "Balance ₹": w["balance"],
              "Balance vs start ₹": w["vs_start"]})
    _put(p, "Win rate", s["win_rate"])
    _put(p, "Avg R", s["avg_r"])
    _put(p, "Expectancy per trade ₹", s["expectancy"])
    return p


# ---------------------------------------------------------------- the outbox

def load_outbox(path):
    doc = read_json(path, "outbox", required=False) or {"version": OUTBOX_VERSION, "rows": []}
    return doc


def digest(props, content):
    blob = json.dumps({"p": props, "c": content}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def upsert(box, db, ident, title, props, content=None, now=None):
    """Queue or refresh one row. Returns "new", "changed" or "same"."""
    key = "%s:%s" % (db, ident)
    h = digest(props, content)
    stamp = (now or parse_now(None)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for row in box["rows"]:
        if row["key"] == key:
            if row["hash"] == h:
                return "same"
            row.update(properties=props, content=content, hash=h, queued_at=stamp)
            return "changed"
    box["rows"].append({"key": key, "db": db, "title": title, "properties": props, "content": content, "hash": h,
                        "queued_at": stamp, "notion_page_id": None, "sent_hash": None, "sent_at": None})
    return "new"


def is_pending(row):
    return row.get("sent_hash") != row["hash"]


def calls_for(row, cfg):
    """The Notion connector calls that bring this row up to date (Claude makes them)."""
    notion = cfg.get("notion") or {}
    ds = (notion.get("databases") or {}).get(row["db"])
    if not ds:
        raise JournalError("no Notion database id for %s in %s: run the Notion setup first" % (row["db"],
                                                                                          "trader/config.json"))
    if row.get("notion_page_id"):
        calls = [{"tool": "notion-update-page", "arguments": {"page_id": row["notion_page_id"],
                                                              "command": "update_properties",
                                                              "properties": row["properties"]}}]
        if row.get("content") and row.get("sent_hash"):
            calls.append({"tool": "notion-update-page", "arguments": {"page_id": row["notion_page_id"],
                                                                      "command": "replace_content",
                                                                      "new_str": row["content"]}})
        return calls
    page = {"properties": row["properties"]}
    if row.get("content"):
        page["content"] = row["content"]
    return [{"tool": "notion-query-data-sources",
             "why": "first look for an existing row with this title so nothing is duplicated; if it exists, "
                    "update it instead of creating one and mark-sent with its page id",
             "arguments": {"data": {"mode": "rows", "data_source_url": "collection://" + ds, "limit": 5,
                                    "filter": {"type": "group", "operator": "and", "filters": [
                                        {"type": "property", "property": columns(row["db"])[0][0],
                                         "propertyType": "title", "operator": "string_is",
                                         "value": {"type": "exact", "value": row["title"]}}]}}}},
            {"tool": "notion-create-pages", "arguments": {"parent": {"type": "data_source_id", "data_source_id": ds},
                                                          "pages": [page]}}]


# ---------------------------------------------------------------- queueing

def queue_all(led, cfg, box, days=(), now=None):
    counts = {"new": 0, "changed": 0, "same": 0}
    for t in led["trades"]:
        counts[upsert(box, "paper_trades", t["trade_id"], t["trade_id"], trade_props(t), None, now)] += 1
    for s in led["skipped"]:
        counts[upsert(box, "skipped_signals", s["signal_id"], s["signal_id"], skipped_props(s), None, now)] += 1
    seen = set(days)
    for t in led["trades"]:
        seen.add(t["date"])
        if t.get("exit_date"):
            seen.add(str(t["exit_date"]))
    for s in led["skipped"]:
        seen.add(s["date"])
    for d in sorted(seen):
        st = day_stats(led, cfg, d)
        counts[upsert(box, "daily_summary", d, d, daily_props(st), None, now)] += 1
    return counts


# ---------------------------------------------------------------- rebuild from Notion rows

def _get(row, name):
    if name in row:
        return row[name]
    for k in ("date:%s:start" % name, name.lower()):
        if k in row:
            return row[k]
    return None


def _num(v):
    if v in (None, ""):
        return None
    if isinstance(v, dict):
        v = v.get("number", v.get("value"))
    try:
        return float(str(v).replace(",", "").replace("₹", ""))
    except ValueError:
        return None


def _txt(v):
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("name") or v.get("text") or v.get("start")
    if isinstance(v, list):
        v = ", ".join(str(x.get("name", x) if isinstance(x, dict) else x) for x in v)
    v = str(v).strip()
    return v or None


def _day(v):
    t = _txt(v)
    return t[:10] if t else None


def trade_from_row(row):
    tid = _txt(_get(row, "Trade ID"))
    if not tid:
        raise JournalError("a Paper Trades row has no Trade ID")
    odds = _txt(_get(row, "Time-machine odds (target/stop/open %)"))
    tm = None
    if odds:
        nums = [_num(x) for x in re.split(r"\s*/\s*", odds)]
        if len(nums) == 3 and None not in nums:
            tm = {"target": nums[0] / 100.0, "stop": nums[1] / 100.0, "neither": nums[2] / 100.0, "verdict": None}
    qty = _num(_get(row, "Qty"))
    status = _txt(_get(row, "Status")) or "open"
    day = _day(_get(row, "Date"))
    tags = _txt(_get(row, "News tags"))
    t = {"trade_id": tid, "date": day, "symbol": (_txt(_get(row, "Symbol")) or "").upper(),
         "setup": _txt(_get(row, "Setup type")) or "", "side": _txt(_get(row, "Side")) or "buy",
         "qty": int(qty) if qty else 0, "planned_entry": _num(_get(row, "Planned entry")),
         "entry_fill": _num(_get(row, "Entry fill")), "entry_time": _txt(_get(row, "Entry time")),
         "stop": _num(_get(row, "Stop")), "target": _num(_get(row, "Target")),
         "flat_by": _txt(_get(row, "Flat by")) or "15:00", "exit_price": None,
         "exit_fill": _num(_get(row, "Exit price")), "exit_time": _txt(_get(row, "Exit time")),
         "exit_reason": _txt(_get(row, "Exit reason")), "exit_date": day if status == "closed" else None,
         "gross_pnl": _num(_get(row, "Gross P&L ₹")), "charges": _num(_get(row, "Charges ₹")),
         "net_pnl": _num(_get(row, "Net P&L ₹")), "r_multiple": _num(_get(row, "R multiple")),
         "max_loss": _num(_get(row, "Risk at stop ₹")), "risk_at_stop": _num(_get(row, "Risk at stop ₹")),
         "tm_odds": tm, "risk_verdict": _txt(_get(row, "Risk verdict")) or "PASS",
         "news_tags": [x.strip() for x in tags.split(",")] if tags else [],
         "approval": _txt(_get(row, "My approval (exact words)")), "approved_at": None,
         "mode": _txt(_get(row, "Mode")) or "paper", "status": status,
         "balance_after": _num(_get(row, "Account balance after ₹")), "notes": _txt(_get(row, "Notes")) or "",
         "fingerprint": None, "rebuilt_from_notion": True}
    if status == "closed" and None in (t["net_pnl"], t["gross_pnl"], t["charges"]):
        raise JournalError("%s is closed in Notion but is missing its P&L fields" % tid)
    return t


def skipped_from_row(row):
    sid = _txt(_get(row, "Signal ID"))
    if not sid:
        raise JournalError("a Skipped Signals row has no Signal ID")
    qty = _num(_get(row, "Qty"))
    return {"signal_id": sid, "date": _day(_get(row, "Date")), "symbol": (_txt(_get(row, "Symbol")) or "").upper(),
            "setup": _txt(_get(row, "Setup type")) or "", "side": _txt(_get(row, "Side")) or "buy",
            "qty": int(qty) if qty else 0, "planned_entry": _num(_get(row, "Planned entry")),
            "stop": _num(_get(row, "Stop")), "target": _num(_get(row, "Target")),
            "risk_at_stop": _num(_get(row, "Risk at stop ₹")), "flat_by": None,
            "why_skipped": _txt(_get(row, "Why skipped")) or "", "skipped_at": _txt(_get(row, "Skipped at")),
            "hypothetical_exit_reason": _txt(_get(row, "Hypothetical exit")),
            "hypothetical_net_pnl": _num(_get(row, "Hypothetical net P&L ₹")),
            "hypothetical_r": _num(_get(row, "Hypothetical R")), "fingerprint": None,
            "mode": _txt(_get(row, "Mode")) or "paper", "notes": _txt(_get(row, "Notes")) or ""}


def rebuild_ledger(export, cfg, mode="paper"):
    """Rows read back from Notion -> a ledger like trader/state/paper.json."""
    capital = float(cfg["capital_inr"])
    trades = [trade_from_row(r) for r in export.get("paper_trades", [])]
    trades = [t for t in trades if t["mode"] == mode]
    skipped = [skipped_from_row(r) for r in export.get("skipped_signals", [])]
    skipped = [s for s in skipped if s["mode"] == mode]
    if len({t["trade_id"] for t in trades}) != len(trades):
        raise JournalError("the Notion export has the same Trade ID twice: fix the duplicate in Notion first")
    trades.sort(key=lambda t: t["trade_id"])
    cl = closed({"trades": trades})
    bal = capital
    for t in cl:
        bal = t["balance_after"] if t["balance_after"] is not None else r2(bal + t["net_pnl"])
    return {"mode": mode, "capital": capital, "balance": r2(bal), "trades": trades, "skipped": skipped,
            "rebuilt_from_notion": True,
            "note": "Rebuilt from Notion. Open trades come back without their fingerprint, so reconcile them by "
                    "hand if they are still open."}


# ---------------------------------------------------------------- commands

def _paths(a):
    mode = getattr(a, "mode", "paper")
    return (a.ledger or "trader/state/%s.json" % ("live" if mode == "live" else "paper"))


def _ledger(a):
    led = read_json(_paths(a), "ledger (the paper broker writes it)")
    led.setdefault("trades", [])
    led.setdefault("skipped", [])
    return led


def cmd_schema(a):
    cfg = load_cfg(a.config)
    cap = float(cfg["capital_inr"])
    plan = {"page": {"tool": "notion-create-pages", "title": PAGE_TITLE,
                     "arguments": {"parent": {"type": "page_id", "page_id": "<the page you name>"},
                                   "pages": [{"properties": {"title": PAGE_TITLE},
                                              "content": "Paper-trading record for the NSE intraday pack. Notion "
                                                         "is the permanent record; the files on your computer are "
                                                         "a working copy."}]}},
            "databases": [{"key": k, "title": DB_TITLES[k], "tool": "notion-create-database",
                           "columns": [n for n, _ in columns(k, cap)],
                           "arguments": {"parent": {"type": "page_id", "page_id": "<the page created above>"},
                                         "title": DB_TITLES[k], "schema": ddl(k, cap)}} for k in DB_ORDER]}
    if a.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0
    print("Notion setup, in this order (after you name the parent page):")
    print("1. Create the page \"%s\" under your parent page." % PAGE_TITLE)
    for i, k in enumerate(DB_ORDER, 2):
        print("%d. Create the database \"%s\" inside it, columns:" % (i, DB_TITLES[k]))
        print("     " + "; ".join(n for n, _ in columns(k, cap)))
    print("6. Save the ids: journal.py setup-save --page-id <page> --db paper_trades=<data source id> ...")
    return 0


def cmd_setup_save(a):
    path = a.config
    cfg = read_json(path, "config (run trader-budget size.py init first)")
    notion = cfg.setdefault("notion", {})
    if a.page_id:
        notion["page_id"] = a.page_id
    if a.parent_page_id:
        notion["parent_page_id"] = a.parent_page_id
    dbs = notion.setdefault("databases", {})
    for spec in a.db or []:
        if "=" not in spec:
            raise JournalError("--db wants key=id, like paper_trades=1234abcd")
        k, v = spec.split("=", 1)
        if k not in DB_ORDER:
            raise JournalError("unknown database %r (use %s)" % (k, ", ".join(DB_ORDER)))
        dbs[k] = v.strip().replace("collection://", "")
    write_json(path, cfg)
    missing = [k for k in DB_ORDER if k not in dbs]
    print("Saved the Notion ids in %s (only the \"notion\" section; your limits are untouched)." % path)
    print("Still missing: %s." % ", ".join(missing) if missing else "All four databases are saved.")
    return 0


def cmd_queue(a):
    led, cfg = _ledger(a), load_cfg(a.config)
    box = load_outbox(a.outbox)
    now = parse_now(a.now)
    days = [a.date] if getattr(a, "date", None) else [to_ist(now).date().isoformat()]
    counts = queue_all(led, cfg, box, days, now)
    write_json(a.outbox, box)
    pend = sum(1 for r in box["rows"] if is_pending(r))
    print("Queued: %d new, %d changed, %d unchanged. %d row%s waiting to be written to Notion." % (
        counts["new"], counts["changed"], counts["same"], pend, "" if pend == 1 else "s"))
    return 0


def cmd_daily(a):
    led, cfg = _ledger(a), load_cfg(a.config)
    now = parse_now(a.now)
    day = a.date or to_ist(now).date().isoformat()
    st = day_stats(led, cfg, day)
    box = load_outbox(a.outbox)
    res = upsert(box, "daily_summary", day, day, daily_props(st), None, now)
    write_json(a.outbox, box)
    print("\n".join(daily_text(st)))
    print("(queued for Notion: %s)" % res)
    return 0


def cmd_weekly(a):
    led, cfg = _ledger(a), load_cfg(a.config)
    now = parse_now(a.now)
    day = a.week_of or to_ist(now).date().isoformat()
    w = week_stats(led, cfg, day)
    md = weekly_markdown(w)
    box = load_outbox(a.outbox)
    res = upsert(box, "weekly_reports", w["iso"], weekly_title(w), weekly_props(w), md, now)
    write_json(a.outbox, box)
    print("# Weekly report: " + weekly_title(w))
    print(md)
    print("\n(queued for Notion: %s)" % res)
    return 0


def cmd_pending(a):
    cfg = load_cfg(a.config)
    box = load_outbox(a.outbox)
    rows = [r for r in box["rows"] if is_pending(r) and (not a.db or r["db"] == a.db)]
    if a.json:
        out = [{"key": r["key"], "db": r["db"], "title": r["title"], "hash": r["hash"],
                "action": "update" if r.get("notion_page_id") else "create", "notion_page_id": r.get("notion_page_id"),
                "calls": calls_for(r, cfg)} for r in rows]
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if rows else 1
    if not rows:
        print("Nothing waiting: Notion is up to date with the last queue.")
        return 1
    for r in rows:
        print("%-7s %s  (%s)" % ("update" if r.get("notion_page_id") else "create", r["key"], DB_TITLES[r["db"]]))
    print("%d pending. Claude writes each one with the Notion connector, then: journal.py mark-sent --key KEY "
          "--page-id ID" % len(rows))
    return 0


def cmd_mark_sent(a):
    box = load_outbox(a.outbox)
    for r in box["rows"]:
        if r["key"] == a.key:
            if a.hash and a.hash != r["hash"]:
                print("Not marked: the row changed since you listed it (hash %s, now %s). It stays queued."
                      % (a.hash, r["hash"]))
                return 1
            if a.page_id:
                r["notion_page_id"] = a.page_id
            if not r.get("notion_page_id"):
                raise JournalError("%s has no Notion page id yet: pass --page-id from the create call" % a.key)
            r["sent_hash"] = r["hash"]
            r["sent_at"] = (parse_now(a.now)).strftime("%Y-%m-%dT%H:%M:%SZ")
            write_json(a.outbox, box)
            print("Marked %s as sent (page %s)." % (a.key, r["notion_page_id"]))
            return 0
    raise JournalError("no row %s in the outbox" % a.key)


def cmd_status(a):
    box = load_outbox(a.outbox)
    cfg = load_cfg(a.config)
    dbs = (cfg.get("notion") or {}).get("databases") or {}
    print("Notion setup: %s." % ("done (4 databases saved)" if all(k in dbs for k in DB_ORDER)
                                 else "NOT done (%d of 4 databases saved)" % sum(1 for k in DB_ORDER if k in dbs)))
    for k in DB_ORDER:
        rows = [r for r in box["rows"] if r["db"] == k]
        print("  %-16s %3d rows, %3d pending" % (DB_TITLES[k], len(rows), sum(1 for r in rows if is_pending(r))))
    return 0


def cmd_rebuild(a):
    cfg = load_cfg(a.config)
    export = read_json(a.src, "Notion export (Claude saves the rows it read from Notion here)")
    mode = a.mode
    out = a.ledger or "trader/state/%s.json" % ("live" if mode == "live" else "paper")
    if os.path.exists(out) and not a.force:
        raise JournalError("%s already exists. Rebuild is for when local files are lost; pass --force to replace "
                           "it with what Notion holds (the old file is kept as %s.bak)." % (out, out))
    led = rebuild_ledger(export, cfg, mode)
    if os.path.exists(out):
        os.replace(out, out + ".bak")
    write_json(out, led)
    nopen = sum(1 for t in led["trades"] if t["status"] == "open")
    print("Rebuilt %s from Notion: %d trades (%d open), %d skipped signals, balance %s." % (
        out, len(led["trades"]), nopen, len(led["skipped"]), inr(led["balance"], True)))
    if nopen:
        print("%d trade%s still open in Notion: check whether %s really open, then reconcile." % (
            nopen, "" if nopen == 1 else "s", "it is" if nopen == 1 else "they are"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="The Notion journal (trader-journal). Never calls Notion.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p, ledger=True):
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--outbox", default="trader/state/notion_outbox.json")
        p.add_argument("--now", help="pretend it is this India time, e.g. \"2026-10-30 15:45\"")
        if ledger:
            p.add_argument("--mode", choices=["paper", "live"], default="paper")
            p.add_argument("--ledger", help="the account ledger (default trader/state/<mode>.json)")

    p = sub.add_parser("schema", help="the 4 databases to create in Notion")
    common(p, False)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("setup-save", help="save the Notion ids in trader/config.json")
    common(p, False)
    p.add_argument("--page-id")
    p.add_argument("--parent-page-id")
    p.add_argument("--db", action="append", metavar="KEY=ID")
    p = sub.add_parser("queue", help="queue trades, skipped signals and day summaries")
    common(p)
    p.add_argument("--date", help="also make sure this day has a summary")
    p = sub.add_parser("daily", help="queue and print one day's summary")
    common(p)
    p.add_argument("--date")
    p = sub.add_parser("weekly", help="queue and print a week's report")
    common(p)
    p.add_argument("--week-of", help="any date in the week (default today)")
    p = sub.add_parser("pending", help="what still has to be written to Notion")
    common(p, False)
    p.add_argument("--db", choices=DB_ORDER)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("mark-sent", help="record that a row is in Notion")
    common(p, False)
    p.add_argument("--key", required=True)
    p.add_argument("--page-id")
    p.add_argument("--hash", help="the hash from pending --json; refuses if the row changed since")
    p = sub.add_parser("status", help="setup and outbox counts")
    common(p, False)
    p = sub.add_parser("rebuild", help="restore the ledger from rows read back from Notion")
    common(p)
    p.add_argument("--from", dest="src", required=True)
    p.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"schema": cmd_schema, "setup-save": cmd_setup_save, "queue": cmd_queue, "daily": cmd_daily,
                "weekly": cmd_weekly, "pending": cmd_pending, "mark-sent": cmd_mark_sent, "status": cmd_status,
                "rebuild": cmd_rebuild}[a.cmd](a)
    except JournalError as e:
        print("journal.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
