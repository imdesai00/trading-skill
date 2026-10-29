#!/usr/bin/env python3
"""Insider-trade tracker for trader-insider (NSE). Standard library only.

Insiders of a listed company (promoters, directors, key managerial staff,
designated employees) must disclose their trades in its shares under SEBI's
insider-trading (PIT) rules, and NSE publishes the disclosures. This tool reads
them for your symbols, alerts once per filing, and says how late each one is.

    python3 insider.py import nse_insider_export.csv     # a CSV you exported from the NSE site
    python3 insider.py check                             # new filings on your watchlist / holdings
    python3 insider.py check --symbols RELIANCE TCS --days 60 --all
    python3 insider.py feed                              # the NSE insider-trading RSS (best effort)
    python3 insider.py status

Where the data comes from:
  - import: a CSV you download yourself from NSE's corporate-filings page for
    insider trading (nseindia.com, Corporate Filings > Insider Trading). Columns
    are found by name, not position. If a column it needs is missing, it says
    which, and stops. This is the reliable route.
  - feed: https://nsearchives.nseindia.com/content/RSS/Insider_Trading.xml
    (VERIFY.md section 10: the URL is from a third-party list and could not be
    fetched here). The feed gives notices (a title and a link), not the numbers.
    If it fails on your machine the tool says so; import still works.
  - nothing else. NSE's website APIs need browser cookies and are not used.

Your symbols, when --symbols is not given: trader/watchlist.json, trader/plan.json
and trader/portfolio.json, the same as trader-news.

What a disclosure is, and is not:
  - It is PUBLIC and LATE. It arrives days after the trade (the rules allow about
    two trading days to the company and then to the exchange: VERIFY.md section 10,
    not checked against the SEBI text). Every row shows the trade date, the
    date it was published and the gap, so you see how stale it is.
  - It is not a signal. A purchase on the open market is the more deliberate
    act. A sale can be for tax, a loan, an ESOP or personal money. Neither says
    what the price does next. Pledges, gifts, ESOP allotments and off-market
    deals are shown as what they are, not as buying or selling.
  - Reading it fast is not inside information. There is none here.

State: trader/state/insider.json keeps the rows (120 days) and which filings you
have already been told about, so each filing alerts once.
"""

import argparse
import csv
import datetime
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


class InsiderError(Exception):
    pass


ClockError = InsiderError

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

FEED = "https://nsearchives.nseindia.com/content/RSS/Insider_Trading.xml"
UA = "Mozilla/5.0 (compatible; india-day-trader-skill/1.0)"
KEEP_DAYS = 120
GAP_NOTE = "the rules allow about 2 trading days to disclose; a longer gap is worth noticing"
# a column is found when ALL its words are in the header (lower case); first match wins
COLUMNS = {
    "symbol": [["symbol"]],
    "company": [["company"]],
    "person": [["name of the person"], ["name of person"], ["acquirer"], ["name"]],
    "category": [["category of person"], ["category"]],
    "security": [["type of security", "acquired"], ["type of security", "disposed"], ["type of security"]],
    "qty": [["no. of securit", "acquired"], ["no. of securit", "disposed"], ["no. of securit", "displaced"],
            ["no of securit", "acquired"], ["no. of securit"]],
    "value": [["value of security", "acquired"], ["value of security", "disposed"], ["value of security", "displaced"],
              ["value of security"]],
    "txn": [["transaction type"], ["acquisition/disposal"], ["buy/sell"], ["acquisition", "disposal"]],
    "mode": [["mode of acquisition"], ["mode"]],
    "txn_date": [["date of allotment", "from"], ["date of allotment/acquisition"], ["date of transaction"],
                 ["transaction date"]],
    "intimation": [["intimation"], ["initimation"]],
    "broadcast": [["broadcast"]],
}
REQUIRED = ("symbol", "person", "txn")


# ---------------------------------------------------------------- reading the CSV

def find_columns(header):
    low = [h.strip().lower() for h in header]
    found = {}
    for key, options in COLUMNS.items():
        for words in options:
            idx = next((i for i, h in enumerate(low) if all(w in h for w in words) and i not in found.values()), None)
            if idx is not None:
                found[key] = idx
                break
    return found


def parse_when(text):
    """'30-Oct-2026 10:15', '30-Oct-2026', '2026-10-30 10:15', '30/10/2026' -> naive India datetime or None."""
    t = (text or "").strip()
    if not t or t in ("-", "--"):
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y"):
        try:
            return datetime.datetime.strptime(t, fmt)
        except ValueError:
            pass
    return None


def to_number(text):
    t = re.sub(r"[₹,\s]", "", str(text or ""))
    if t in ("", "-", "--", "NIL", "Nil"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def direction(txn, mode):
    """-> (kind, open_market). kind: buy, sell, pledge, other."""
    t = "%s %s" % (txn or "", mode or "")
    low = t.lower()
    if re.search(r"pledge|invoc|revok|revoc|lien", low):
        return "pledge", False
    open_market = bool(re.search(r"market purchase|market sale", low)) and "off" not in low
    if re.search(r"\bsell\b|dispos|displac|\bsale\b", (txn or "").lower()):
        return "sell", open_market
    if re.search(r"\bbuy\b|acqui|purchase", (txn or "").lower()):
        return "buy", open_market
    return "other", open_market


def row_key(r):
    raw = "|".join(str(r.get(k)) for k in ("symbol", "person", "txn_date", "txn", "qty", "value", "mode"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def read_csv(text):
    """CSV text -> (rows, columns_found). Raises InsiderError naming any missing required column."""
    rdr = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    rdr = [r for r in rdr if any(c.strip() for c in r)]
    if len(rdr) < 2:
        raise InsiderError("the CSV has no rows")
    cols = find_columns(rdr[0])
    missing = [k for k in REQUIRED if k not in cols]
    if missing:
        raise InsiderError("the CSV is missing a column for: %s. Its columns are: %s"
                           % (", ".join(missing), "; ".join(h.strip() for h in rdr[0])))
    rows = []
    for r in rdr[1:]:
        def cell(k):
            return r[cols[k]].strip() if k in cols and cols[k] < len(r) else ""
        kind, open_market = direction(cell("txn"), cell("mode"))
        tdate, bdate, idate = parse_when(cell("txn_date")), parse_when(cell("broadcast")), parse_when(cell("intimation"))
        row = {"symbol": cell("symbol").upper(), "company": cell("company"), "person": cell("person"),
               "category": cell("category"), "security": cell("security"), "qty": to_number(cell("qty")),
               "value": to_number(cell("value")), "txn": cell("txn"), "mode": cell("mode"), "kind": kind,
               "open_market": open_market,
               "txn_date": tdate.date().isoformat() if tdate else None,
               "published": (bdate or idate).isoformat(sep=" ") if (bdate or idate) else None,
               "published_is": "broadcast" if bdate else ("intimation to company" if idate else None)}
        if not row["symbol"]:
            continue
        row["key"] = row_key(row)
        rows.append(row)
    return rows, {k: rdr[0][i].strip() for k, i in cols.items()}


# ---------------------------------------------------------------- the gap

def trading_days_between(a, b):
    """Trading days after date a up to and including date b (0 when b <= a)."""
    n, d = 0, a
    while d < b:
        d += datetime.timedelta(days=1)
        if trading_day(d)[0]:
            n += 1
    return n


def gap_of(row, now_utc):
    """{'to_publish_days': trading days from trade to publication or None, 'age_days': days since the trade}."""
    out = {"to_publish_days": None, "age_days": None, "published_gap_note": None}
    if not row.get("txn_date"):
        return out
    t = datetime.date.fromisoformat(row["txn_date"])
    out["age_days"] = (to_ist(now_utc).date() - t).days
    if row.get("published"):
        p = datetime.datetime.fromisoformat(row["published"]).date()
        if t.year in CALENDAR_YEARS and p.year in CALENDAR_YEARS:
            out["to_publish_days"] = trading_days_between(t, p)
        else:
            out["to_publish_days"] = (p - t).days
            out["published_gap_note"] = "calendar days (the holiday list does not cover that year)"
    return out


# ---------------------------------------------------------------- state and symbols

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def save_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def watch_symbols(a):
    """{SYMBOL: 'watchlist' | 'plan' | 'held'}."""
    out = {}
    for h in (load_json(a.watchlist) or {}).get("hits", []):
        if h.get("symbol"):
            out[h["symbol"].upper()] = "watchlist"
    pl = load_json(a.plan) or {}
    if pl.get("symbol"):
        out[pl["symbol"].upper()] = "plan"
    for p in (load_json(a.portfolio) or {}).get("positions", []):
        s = p.get("symbol") or p.get("ticker")
        if s:
            out[s.upper()] = "held"
    return out


def prune(state, now_utc):
    cutoff = (to_ist(now_utc).date() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    state["rows"] = {k: r for k, r in state.get("rows", {}).items() if (r.get("txn_date") or "9999") >= cutoff}
    state["seen"] = {k: v for k, v in state.get("seen", {}).items() if k in state["rows"] or k.startswith("feed:")}


# ---------------------------------------------------------------- commands

def inr_value(x):
    return "value not given" if x is None else inr(x, False)


def describe(r, why, now_utc):
    g = gap_of(r, now_utc)
    kind = {"buy": "BOUGHT", "sell": "SOLD", "pledge": "PLEDGE/RELEASE", "other": "OTHER"}[r["kind"]]
    mode = r["mode"] or "mode not given"
    who = "%s%s" % (r["person"], (" (%s)" % r["category"]) if r["category"] else "")
    qty = ("{:,.0f} shares".format(r["qty"])) if r["qty"] is not None else "quantity not given"
    lines = ["%-12s %s %s: %s, %s. Mode: %s%s." % (r["symbol"], who, kind, qty, inr_value(r["value"]), mode,
                                                  "" if r["open_market"] or r["kind"] in ("pledge",)
                                                  else " (NOT an open-market trade)" if r["kind"] in ("buy", "sell")
                                                  else "")]
    when = "traded %s" % (r["txn_date"] or "date not given")
    if r["published"]:
        when += ", published %s (%s)" % (r["published"][:16], r["published_is"])
    if g["to_publish_days"] is not None:
        when += ", %d %s to publish" % (g["to_publish_days"], "day" if g["to_publish_days"] == 1 else "days") + \
            (" [%s]" % g["published_gap_note"] if g["published_gap_note"] else "")
        if g["to_publish_days"] > 2 and not g["published_gap_note"]:
            when += " (late: " + GAP_NOTE + ")"
    if g["age_days"] is not None:
        when += "; you are reading it %d day%s after the trade" % (g["age_days"], "" if g["age_days"] == 1 else "s")
    lines.append("             " + when + ("; " + why if why else "") + ".")
    return lines


def cmd_import(a):
    try:
        with open(a.file, encoding="utf-8", errors="replace") as fh:
            rows, cols = read_csv(fh.read())
    except OSError as e:
        raise InsiderError("cannot read %s: %s" % (a.file, e))
    now = parse_now(a.now)
    state = load_json(a.state) or {"rows": {}, "seen": {}}
    state.setdefault("rows", {})
    state.setdefault("seen", {})
    new = sum(1 for r in rows if r["key"] not in state["rows"])
    for r in rows:
        state["rows"].setdefault(r["key"], r)
    prune(state, now)
    save_json(a.state, state)
    print("Read %d row%s from %s (%d new, %d already known)." % (len(rows), "" if len(rows) == 1 else "s", a.file,
                                                                new, len(rows) - new))
    print("Columns used: " + "; ".join("%s = \"%s\"" % (k, v) for k, v in sorted(cols.items())))
    absent = [k for k in COLUMNS if k not in cols]
    if absent:
        print("Not found (shown as 'not given'): " + ", ".join(absent))
    print("Next: python3 insider.py check")
    return 0


def cmd_check(a):
    now = parse_now(a.now)
    state = load_json(a.state) or {"rows": {}, "seen": {}}
    rows = list(state.get("rows", {}).values())
    if not rows:
        print("No insider rows yet. Export the CSV from NSE and run: insider.py import FILE.csv", file=sys.stderr)
        return 2
    watch = {s.upper(): "named" for s in a.symbols} if a.symbols else watch_symbols(a)
    if not watch:
        print("insider: no symbols. Pass --symbols, or run /trader-scanner first.", file=sys.stderr)
        return 2
    cutoff = (to_ist(now).date() - datetime.timedelta(days=a.days)).isoformat()
    mine = [r for r in rows if r["symbol"] in watch and (r.get("txn_date") or "0000") >= cutoff]
    mine.sort(key=lambda r: (r.get("published") or r.get("txn_date") or ""), reverse=True)
    seen = state.setdefault("seen", {})
    shown = [r for r in mine if a.all or r["key"] not in seen]
    lines = []
    for r in shown:
        lines += describe(r, "on your %s" % watch[r["symbol"]] if watch[r["symbol"]] != "named" else "", now)
    if not shown:
        lines.append("Nothing new for %s in the last %d days." % (", ".join(sorted(watch)), a.days))
    # net open-market activity per symbol over the window (all matching rows, new or old)
    net = {}
    for r in mine:
        if r["kind"] in ("buy", "sell") and r["open_market"] and r.get("value") is not None:
            net[r["symbol"]] = net.get(r["symbol"], 0.0) + (r["value"] if r["kind"] == "buy" else -r["value"])
    if net:
        lines.append("")
        lines.append("Net open-market value in the last %d days (buys minus sells, as published):" % a.days)
        for s in sorted(net):
            lines.append("  %-12s %s%s" % (s, "bought " if net[s] >= 0 else "sold ", inr(abs(net[s]), False)))
    lines.append("")
    lines.append("Disclosures are public and late, and an insider's reason is not in them. This is not a signal "
                 "and not advice.")
    if a.json:
        print(json.dumps({"checked": now.isoformat(), "new": [dict(r, **gap_of(r, now)) for r in shown],
                          "net_open_market_inr": net}, indent=2, ensure_ascii=False))
    else:
        print("\n".join(lines))
    if not a.dry_run:
        for r in shown:
            seen.setdefault(r["key"], now.isoformat())
        prune(state, now)
        save_json(a.state, state)
    return 0 if shown else 1


def fetch_feed(offline, errors):
    if offline:
        path = os.path.join(offline, "insider-feed.xml")
        if not os.path.isfile(path):
            raise InsiderError("offline: no file %s" % path)
        with open(path, "rb") as fh:
            return fh.read()
    req = urllib.request.Request(FEED, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise InsiderError("HTTP %s for %s" % (e.code, FEED))
    except urllib.error.URLError as e:
        raise InsiderError("could not reach %s: %s" % (FEED, e.reason))


def cmd_feed(a):
    now = parse_now(a.now)
    try:
        data = fetch_feed(a.offline, [])
        root = ET.fromstring(data)
    except (InsiderError, ET.ParseError) as e:
        print("insider.py: the RSS feed did not work (%s). Use: insider.py import FILE.csv" % e, file=sys.stderr)
        return 2
    watch = {s.upper(): "named" for s in a.symbols} if a.symbols else watch_symbols(a)
    state = load_json(a.state) or {"rows": {}, "seen": {}}
    seen = state.setdefault("seen", {})
    out = []
    for it in root.iter("item"):
        title = re.sub(r"\s+", " ", (it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        key = "feed:" + hashlib.sha1((it.findtext("guid") or link or title).encode("utf-8")).hexdigest()[:16]
        mine = [s for s in watch if re.search(r"(?<![A-Za-z0-9&-])%s(?![A-Za-z0-9&-])" % re.escape(s), title)]
        if watch and not mine and not a.market:
            continue
        if key in seen and not a.all:
            continue
        out.append((key, title, link, mine, (it.findtext("pubDate") or "").strip()))
    for key, title, link, mine, pub in out:
        print("%s  %s  %s%s" % (pub or "time unknown", title, link, ("  [your: %s]" % ",".join(mine)) if mine else ""))
    if not out:
        print("No new notices%s." % ("" if a.market else " for your symbols"))
    else:
        print("\nA notice is a headline for a filing, not the numbers: open the link, or import the CSV for the "
              "details. Public and late; not a signal; not advice.")
    if not a.dry_run:
        for key, *_ in out:
            seen[key] = now.isoformat()
        save_json(a.state, state)
    return 0 if out else 1


def cmd_status(a):
    state = load_json(a.state) or {}
    rows = state.get("rows", {})
    print("%d insider rows kept, %d filings already alerted." % (len(rows), len(state.get("seen", {}))))
    if rows:
        dates = sorted(r["txn_date"] for r in rows.values() if r.get("txn_date"))
        if dates:
            print("Trades from %s to %s." % (dates[0], dates[-1]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Insider-trade tracker (trader-insider). Public, late, not a signal.")
    sub = ap.add_subparsers(dest="cmd")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state", default="trader/state/insider.json")
    common.add_argument("--portfolio", default="trader/portfolio.json")
    common.add_argument("--watchlist", default="trader/watchlist.json")
    common.add_argument("--plan", default="trader/plan.json")
    common.add_argument("--now", help="a time like \"2026-10-30 08:30\" (India time), for reproducible runs")
    p = sub.add_parser("import", parents=[common], help="read a CSV you exported from NSE")
    p.add_argument("file")
    p = sub.add_parser("check", parents=[common], help="new filings on your symbols")
    p.add_argument("--symbols", nargs="*")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--all", action="store_true", help="show filings already alerted too")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("feed", parents=[common], help="the NSE insider-trading RSS, best effort")
    p.add_argument("--symbols", nargs="*")
    p.add_argument("--market", action="store_true", help="every notice, not only your symbols")
    p.add_argument("--all", action="store_true")
    p.add_argument("--offline", metavar="DIR")
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("status", parents=[common])
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"import": cmd_import, "check": cmd_check, "feed": cmd_feed, "status": cmd_status}[a.cmd](a)
    except InsiderError as e:
        print("insider.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
