#!/usr/bin/env python3
"""Chart Scanner for trader-scanner (NSE): daily setups and opening-range breakouts. Standard library only.

    python3 scan.py universe                          # NSE equity list x NIFTY 100 -> trader/universe.txt
    python3 scan.py universe --index nifty50          # or nifty500, or all (every EQ-series stock)
    python3 scan.py daily                             # before 09:15: daily setups -> trader/watchlist.json
    python3 scan.py daily --symbols RELIANCE,TCS,INFY # or --watchlist FILE, --limit 50
    python3 scan.py orb                               # after 09:35: opening-range breakouts so far
    python3 scan.py orb --at 10:15                    # cut the clock at 10:15 India time
    python3 scan.py orb --offline tests/fixtures/nse/5m-60d --now "2026-10-30 10:15"

universe  Reads NSE's list of listed equities (EQUITY_L.csv) and keeps series EQ,
          then keeps only the stocks in the chosen index (NIFTY 100 by default,
          from niftyindices.com's constituent CSV). Neither URL could be checked
          from the build sandbox (VERIFY.md section 10): if a download fails,
          save the CSV in your browser and pass --equity-file / --index-file.

daily     Pre-market scan of daily bars (Yahoo v8 chart, 3 months of 1-day bars,
          one request per symbol). Liquidity: last close >= --min-price (₹50)
          and the 20-session average volume >= --min-avg-volume (5,00,000 shares).
          Setups, from closes:
            breakout_20d     the last close is above every close of the 20 before it
            bull_flag        up more than 10 % over the 10 sessions before the
                             last 5, then the last 5 closes inside a 5 % range
            reclaim_ma20     closed under the 20-day average, then closed above it
            oversold         RSI 14 (Wilder) under 30
            oversold_bounce  RSI 14 was under 30 yesterday and today closed up
          Today's daily bar is dropped until 15:30.

orb       Opening-range breakouts on 5-minute bars (Yahoo v8 chart, range 5d,
          one request per symbol). The range is the first --or-minutes (15) from
          09:15: the 09:15, 09:20 and 09:25 bars. A hit is the first COMPLETED
          bar after the range that CLOSES above the range high, at that bar's
          close time. A chart is usable only when every 5-minute bar from 09:15
          to the cutoff is there (a missing bar is a halt or no trades). The
          average volume of the earlier sessions in the 5 days is the liquidity
          filter. After no_entries_after (14:45) a breakout is expired.

Symbols are NSE trading symbols (RELIANCE, M&M); Yahoo's is the same plus ".NS".
Bars: null closes are dropped, a bar off the 5-minute grid (the live tick) is
dropped, and a bar counts only once it is complete (start + 5 minutes <= the
clock). Times are India time; --now pins the clock ("2026-10-30 10:15").

Yahoo's endpoints are free and unofficial. query2 first, query1 on a 429 or
5xx, one retry after a pause on a 429. A polite pause between requests.
Partial results are reported as partial. A scan of NIFTY 100 is about 100
requests (about a minute); NIFTY 500 about 500.

--offline PATH reads files instead of the network: universe wants a folder
with EQUITY_L.csv and the index CSV; daily and orb take a folder of
<SYMBOL>.json(.gz) v8 chart files, or one file that is a v8 chart or a bundle
{"RELIANCE": <v8 json>, ...}.

A scan is a list of charts that match a rule, not a list of stocks to buy.
Exit code 0 when the scan found something, 1 when it did not or could not run.
"""

import argparse
import csv
import datetime
import gzip
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; india-day-trader-skill/1.0)"
YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"          # UNVERIFIED, VERIFY.md s.10
INDEX_URLS = {                                                                         # UNVERIFIED, VERIFY.md s.10
    "nifty50": "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "nifty100": "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv",
    "nifty500": "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
}
PAUSE = 0.3         # seconds between requests: be polite to free endpoints
BACKOFF = 5.0       # seconds to wait before the one retry after a 429
SLEEP = time.sleep  # tests swap this out
BAR_MIN = 5
MIN_DAILY_BARS = 30
AVG_SESSIONS = 20
DEFAULT_UNIVERSE = "trader/universe.txt"
MIN_PRICE = 50.0
MIN_AVG_VOLUME = 500000
NOTE = "A scan is a list of charts that match a rule, not a list of stocks to buy."

SETUPS = [
    ("breakout_20d", "20-day closing-high breakout"),
    ("bull_flag", "bull flag"),
    ("reclaim_ma20", "reclaimed the 20-day average"),
    ("oversold", "oversold (RSI under 30)"),
    ("oversold_bounce", "oversold bounce"),
]
LABEL = dict(SETUPS)


class ScanError(Exception):
    pass


ClockError = ScanError

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


def parse_hhmm(text, flag="--at"):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", text or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ScanError("%s wants an India time like 10:15" % flag)
    return datetime.time(int(m.group(1)), int(m.group(2)))


def clock_label(t):
    return t.strftime("%H:%M")


def day_label(d):
    return "%s %s" % (d.strftime("%a"), d.isoformat())


# ---------------------------------------------------------------- network

def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def yahoo_get(path):
    """GET a Yahoo Finance path. query2 first, query1 on a 429 or 5xx, and one
    more round after BACKOFF seconds if a 429 was seen. 4xx other than 429 is final."""
    last = "no answer"
    for attempt in (0, 1):
        saw_429 = False
        for host in YAHOO_HOSTS:
            try:
                return http_get(host + path)
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                try:
                    body = e.read().decode("utf-8", "replace") if 400 <= e.code < 500 and e.code != 429 else ""
                except Exception:
                    body = ""
                finally:
                    e.close()
                if e.code == 429:
                    saw_429 = True
                    continue
                if e.code >= 500:
                    continue
                if body.strip().startswith("{"):
                    return body  # Yahoo's JSON error ("No data found"), parsed by the caller
                raise ScanError("Yahoo answered %s for %s" % (last, path.split("?")[0]))
            except (urllib.error.URLError, OSError) as e:
                last = str(getattr(e, "reason", e))
                continue
        if attempt == 0 and saw_429:
            SLEEP(BACKOFF)
            continue
        break
    raise ScanError("Yahoo answered %s for %s" % (last, path.split("?")[0]))


def to_yahoo(sym):
    s = sym.strip().upper()
    return s if s.endswith((".NS", ".BO")) else s + ".NS"


def from_yahoo(ysym):
    s = ysym.strip().upper()
    return s[:-3] if s.endswith((".NS", ".BO")) else s


def chart_path(sym, rng, interval):
    return "/v8/finance/chart/%s?range=%s&interval=%s" % (urllib.parse.quote(to_yahoo(sym)), rng, interval)


# ---------------------------------------------------------------- parsing

def series_from_result(res):
    """A Yahoo v8 chart result -> plain lists."""
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    t = res.get("timestamp") or []
    n = len(t)

    def col(k):
        v = q.get(k)
        return list(v) + [None] * (n - len(v)) if isinstance(v, list) else None
    return {"t": list(t), "c": col("close") or [None] * n, "o": col("open"), "h": col("high"),
            "l": col("low"), "v": col("volume"), "meta": res.get("meta") or {}}


def parse_chart(text_or_doc, sym=""):
    """v8 chart JSON -> series. Raises ScanError for Yahoo's 'no data' answers."""
    try:
        doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
        chart = doc["chart"]
    except (ValueError, KeyError, TypeError):
        raise ScanError("Yahoo returned something unexpected for %s" % sym)
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise ScanError("no data for %s: %s" % (sym, err.get("description", "unknown symbol")))
    return series_from_result(res[0])


def _read_maybe_gz(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _stem(name):
    for ext in (".json.gz", ".json"):
        if name.endswith(ext):
            return name[:-len(ext)]
    return name


def _absorb(doc, name_hint, data, errors):
    """One parsed offline JSON doc -> data / errors."""
    if isinstance(doc, dict) and "chart" in doc:
        sym = name_hint
        if not sym:
            try:
                sym = (doc["chart"].get("result") or [{}])[0]["meta"]["symbol"]
            except (AttributeError, KeyError, TypeError, IndexError):
                raise ScanError("an offline v8 chart without a symbol: name the file <SYMBOL>.json")
        sym = from_yahoo(sym)
        try:
            data[sym] = parse_chart(doc, sym)
        except ScanError as e:
            errors[sym] = str(e)
    elif isinstance(doc, dict):
        for k, v in doc.items():
            _absorb(v, k, data, errors)
    else:
        raise ScanError("offline file %s is not a v8 chart or a bundle" % name_hint)


def load_offline(path):
    """A folder of <SYMBOL>.json(.gz) files or one file -> ({SYM: series}, {SYM: error})."""
    data, errors = {}, {}
    if os.path.isdir(path):
        names = sorted(f for f in os.listdir(path) if f.endswith((".json", ".json.gz")))
        if not names:
            raise ScanError("no .json files in %s" % path)
        for f in names:
            try:
                doc = json.loads(_read_maybe_gz(os.path.join(path, f)))
            except ValueError as e:
                raise ScanError("%s is not valid JSON: %s" % (f, e))
            _absorb(doc, _stem(f) if isinstance(doc, dict) and "chart" in doc else "", data, errors)
    elif os.path.isfile(path):
        try:
            doc = json.loads(_read_maybe_gz(path))
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        _absorb(doc, "", data, errors)
    else:
        raise ScanError("no offline file or folder at %s" % path)
    return data, errors


def is_synthetic(data):
    return any((s.get("meta") or {}).get("synthetic") for s in data.values())


# ---------------------------------------------------------------- universe

def _rows(text):
    """A CSV -> list of {lowercased, trimmed header: value}."""
    rows = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        rows.append({(k or "").strip().lower(): (v or "").strip() for k, v in r.items()})
    return rows


def _col(row, *names):
    for n in names:
        if n in row:
            return row[n]
    return ""


def parse_equity_list(text):
    """NSE EQUITY_L.csv -> {SYMBOL: name} for series EQ. Columns are found by name, not position."""
    rows = _rows(text)
    if not rows or "symbol" not in rows[0] or "series" not in rows[0]:
        raise ScanError("not NSE's equity list: it needs SYMBOL and SERIES columns")
    return {r["symbol"].upper(): _col(r, "name of company", "company name") for r in rows
            if r.get("series", "").upper() == "EQ" and r.get("symbol")}


def parse_index_list(text):
    """A niftyindices.com constituent CSV -> [SYMBOL] (series EQ, or no series column)."""
    rows = _rows(text)
    if not rows or "symbol" not in rows[0]:
        raise ScanError("not an index constituent list: it needs a Symbol column")
    return [r["symbol"].upper() for r in rows if r.get("symbol") and r.get("series", "EQ").upper() == "EQ"]


def build_universe(equity_text, index_text=None):
    """-> (kept [(SYM, name)], counts). index_text None = every EQ-series stock."""
    eq = parse_equity_list(equity_text)
    counts = {"eq_series": len(eq)}
    if index_text is None:
        return sorted(eq.items()), counts
    idx = parse_index_list(index_text)
    counts["index"] = len(idx)
    missing = [s for s in idx if s not in eq]
    counts["index_not_in_eq_list"] = len(missing)
    return sorted((s, eq[s]) for s in set(idx) if s in eq), counts


# ---------------------------------------------------------------- daily setups

def rsi(closes, n=14):
    """Wilder's RSI on closes."""
    if len(closes) < n + 1:
        return None
    g = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    lo = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def daily_bars(series, now_utc):
    """[(date, close, volume)] of COMPLETE daily bars: null closes dropped, today's bar dropped
    until 15:30, anything dated after the clock dropped."""
    now = to_ist(now_utc)
    today = now.date()
    s = session(today)
    today_done = s is None or now.time() >= s[1]
    out = {}
    vs = series.get("v") or [None] * len(series["t"])
    for ts, c, v in zip(series["t"], series["c"], vs):
        if c is None:
            continue
        d = ts_to_ist(ts).date()
        if d > today or (d == today and not today_done):
            continue
        out[d] = (float(c), v or 0)
    return [(d, c, v) for d, (c, v) in sorted(out.items())]


def find_setups(closes):
    """closes (oldest first, >= 30) -> (setup keys, numbers)."""
    c = closes
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r = rsi(c)
    r_prev = rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags, {"rsi": r, "rsi_prev": r_prev, "high20": hi20, "ma20": ma20,
                  "run_pct": run * 100, "range5_pct": rng5 * 100}


def scan_daily(data, symbols, now_utc, min_price, min_avg_volume):
    """Pure. data = {SYM: series}. Returns (hits, counts, last_date)."""
    hits, dates = [], {}
    counts = {"requested": len(symbols), "fetched": 0, "illiquid": 0, "short": 0, "scanned": 0}
    for sym in symbols:
        s = data.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = daily_bars(s, now_utc)
        if len(bars) < MIN_DAILY_BARS:
            counts["short"] += 1
            continue
        price = bars[-1][1]
        vols = [v for _, _, v in bars[-AVG_SESSIONS:]]
        avg_vol = sum(vols) / float(len(vols))
        if price < min_price or avg_vol < min_avg_volume:
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        d_last = bars[-1][0]
        dates[d_last] = dates.get(d_last, 0) + 1
        closes = [c for _, c, _ in bars]
        tags, nums = find_setups(closes)
        if tags:
            hits.append({"symbol": sym, "setups": tags, "price": round(price, 2),
                         "close_date": d_last.isoformat(), "rsi": round(nums["rsi"], 1),
                         "avg_volume": int(avg_vol)})
    hits.sort(key=lambda h: (-(h["price"] * h["avg_volume"]), h["symbol"]))  # most rupees traded first
    last_date = max(dates, key=lambda d: (dates[d], d)) if dates else None
    counts["hits"] = len(hits)
    return hits, counts, last_date


# ---------------------------------------------------------------- opening-range breakout

def slot(day, k):
    return datetime.datetime.combine(day, OPEN) + datetime.timedelta(minutes=BAR_MIN * k)


def session_bars(series, day, cutoff):
    """5-minute bars of `day` that are complete at `cutoff` (naive India time),
    on the grid, with a close. Sorted, one per slot."""
    out = {}
    hs, ls, vs = series.get("h"), series.get("l"), series.get("v")
    for i, ts in enumerate(series["t"]):
        c = series["c"][i]
        if c is None:
            continue
        it = ts_to_ist(ts)
        if it.date() != day or it.second or (it.minute - OPEN.minute) % BAR_MIN:
            continue  # another day, or the live tick off the grid
        if it.time() < OPEN or it.time() >= CLOSE:
            continue
        if it + datetime.timedelta(minutes=BAR_MIN) > cutoff:
            continue  # still in progress at the cutoff
        out[it] = {"start": it, "c": float(c),
                   "h": None if not hs or hs[i] is None else float(hs[i]),
                   "l": None if not ls or ls[i] is None else float(ls[i]),
                   "v": (vs[i] or 0) if vs else 0}
    return [out[k] for k in sorted(out)]


def avg_session_volume(series, day):
    """Average whole-day volume of the complete sessions before `day` in the series, or None."""
    per = {}
    vs = series.get("v") or []
    for i, ts in enumerate(series["t"]):
        d = ts_to_ist(ts).date()
        if d < day and i < len(vs) and vs[i] is not None:
            per[d] = per.get(d, 0) + vs[i]
    return (sum(per.values()) / float(len(per))) if per else None


def usable(bars, day, cutoff, n_range):
    """Every slot from 09:15 to the cutoff is there, and at least the range."""
    want = int((cutoff - slot(day, 0)).total_seconds() // (BAR_MIN * 60))
    want = min(want, int((datetime.datetime.combine(day, CLOSE) - slot(day, 0)).total_seconds()
                         // (BAR_MIN * 60)))
    if want < n_range or len(bars) != want:
        return False
    return all(b["start"] == slot(day, k) for k, b in enumerate(bars))


def orb_hit(bars, n_range):
    """The first completed bar after the range to CLOSE above the range high."""
    rng = bars[:n_range]
    if any(b["h"] is None or b["l"] is None for b in rng):
        return None
    oh = max(b["h"] for b in rng)
    ol = min(b["l"] for b in rng)
    for k in range(n_range, len(bars)):
        b = bars[k]
        if b["c"] > oh:
            closes_at = b["start"] + datetime.timedelta(minutes=BAR_MIN)
            return {"price": round(b["c"], 2), "at": clock_label(closes_at),
                    "bar_start": b["start"].strftime("%H:%M"), "bar_index": k,
                    "or_high": round(oh, 2), "or_low": round(ol, 2),
                    "pct_over": round((b["c"] / oh - 1) * 100, 2), "_t": closes_at}
    return None


def orb_window(now_utc, at, or_minutes, until, explicit_day=None):
    """-> (day, cutoff naive India time) or raise ScanError with the plain message."""
    now = to_ist(now_utc)
    day = explicit_day or now.date()
    if or_minutes < BAR_MIN or or_minutes % BAR_MIN or or_minutes > 120:
        raise ScanError("--or-minutes wants a multiple of 5 from 5 to 120 (15 is the classic)")
    ok, why = trading_day(day)
    if not ok:
        raise ScanError("No session on %s (%s): the market is closed. Run orb on a trading day "
                        "after the opening range, or replay a past day with --offline." % (day_label(day), why))
    range_end = slot(day, or_minutes // BAR_MIN)
    first_close = range_end + datetime.timedelta(minutes=BAR_MIN)
    close_dt = datetime.datetime.combine(day, CLOSE)
    if at is not None:
        cutoff = datetime.datetime.combine(day, at)
        if explicit_day is None and cutoff > now + datetime.timedelta(seconds=59):
            raise ScanError("--at %s is later than now (%s India time)." % (at.strftime("%H:%M"), clock_label(now)))
        if cutoff > close_dt:
            raise ScanError("--at %s is after the 15:30 close on %s." % (at.strftime("%H:%M"), day_label(day)))
    else:
        cutoff = now.replace(second=0, microsecond=0)
        if cutoff >= close_dt:
            raise ScanError("NSE closed at 15:30 on %s. Replay the session with --at 10:15." % day_label(day))
    if cutoff < first_close:
        raise ScanError("It is %s: the %d-minute opening range runs 09:15 to %s and the first bar after it "
                        "closes at %s. Run orb again after %s."
                        % (clock_label(cutoff), or_minutes, clock_label(range_end), clock_label(first_close),
                           clock_label(first_close)))
    if until is not None and cutoff.time() > until:
        raise ScanError("It is %s, after %s, your last time for new entries (no_entries_after): an "
                        "opening-range breakout found now is expired. Run orb tomorrow after %s, or replay "
                        "today with --at 10:15." % (clock_label(cutoff), until.strftime("%H:%M"),
                                                      clock_label(first_close)))
    return day, cutoff


def scan_orb(data, symbols, day, cutoff, or_minutes, min_price=MIN_PRICE, min_avg_volume=MIN_AVG_VOLUME):
    """data = {SYM: series with highs and lows}. Returns (hits, counts)."""
    n_range = or_minutes // BAR_MIN
    counts = {"requested": len(symbols), "fetched": 0, "unusable": 0, "illiquid": 0, "scanned": 0}
    hits = []
    for sym in symbols:
        s = data.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = session_bars(s, day, cutoff)
        if not usable(bars, day, cutoff, n_range):
            counts["unusable"] += 1
            continue
        avg = avg_session_volume(s, day)
        if bars[-1]["c"] < min_price or (min_avg_volume and (avg is None or avg < min_avg_volume)):
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        h = orb_hit(bars, n_range)
        if h:
            hits.append(dict({"symbol": sym, "setups": ["orb"],
                              "avg_volume": int(avg) if avg is not None else None}, **h))
    hits.sort(key=lambda x: (x["_t"], x["symbol"]))
    for h in hits:
        del h["_t"]
    counts["hits"] = len(hits)
    return hits, counts


# ---------------------------------------------------------------- fetching and files

def progress(a, msg):
    if not getattr(a, "json", False) and not getattr(a, "quiet", False):
        print(msg, file=sys.stderr)


def fetch_charts(symbols, rng, interval, pause, a=None):
    """One v8 chart per symbol. -> ({SYM: series}, {SYM: error})."""
    data, failed = {}, {}
    for k, s in enumerate(symbols):
        if k and pause:
            SLEEP(pause)
        try:
            data[s] = parse_chart(yahoo_get(chart_path(s, rng, interval)), s)
        except (ScanError, ValueError) as e:
            failed[s] = str(e)
        if a is not None and len(symbols) > 50 and (k + 1) % 50 == 0:
            progress(a, "  %d of %d charts" % (k + 1, len(symbols)))
    return data, failed


def read_symbols(a, offline_syms=None):
    if a.symbols:
        syms = [s for s in re.split(r"[,\s]+", a.symbols.upper()) if s]
    elif a.watchlist:
        syms = symbols_from_file(a.watchlist)
    elif offline_syms is not None and not a.universe:
        syms = sorted(offline_syms)  # offline: every chart in the file, unless a list was given
    else:
        path = a.universe or DEFAULT_UNIVERSE
        if not os.path.exists(path):
            raise ScanError("no %s yet: run `scan.py universe` first, or pass --symbols" % path)
        syms = symbols_from_file(path)
    out = []
    for s in syms:
        s = from_yahoo(s)
        if s not in out:
            out.append(s)
    if a.limit:
        out = out[:a.limit]
    if not out:
        raise ScanError("no symbols to scan")
    return out


def symbols_from_file(path):
    if not os.path.exists(path):
        raise ScanError("no file at %s" % path)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.lstrip().startswith(("{", "[")):
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        items = doc.get("hits", []) if isinstance(doc, dict) else doc
        return [str(x.get("symbol") if isinstance(x, dict) else x).upper() for x in items if x]
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln.split("|")[0].strip().upper())
    return out


def read_config(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except ValueError as e:
        raise ScanError("%s is not valid JSON: %s" % (path, e))
    return cfg if isinstance(cfg, dict) else {}


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _grp(n):
    """Indian digit grouping for counts: 500000 -> 5,00,000."""
    return inr(n, False)[1:]


def _read_source(path, url, what):
    if path:
        if not os.path.exists(path):
            raise ScanError("no %s at %s" % (what, path))
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    try:
        return http_get(url, timeout=60)
    except urllib.error.HTTPError as e:
        raise ScanError("%s answered HTTP %d for the %s. Save it from your browser and pass the file "
                        "instead (VERIFY.md section 10)." % (url.split("/")[2], e.code, what))
    except (urllib.error.URLError, OSError) as e:
        raise ScanError("could not reach %s for the %s (%s). Save it from your browser and pass the file "
                        "instead." % (url.split("/")[2], what, getattr(e, "reason", e)))


# ---------------------------------------------------------------- commands

def cmd_universe(a):
    eq_file = a.equity_file or (os.path.join(a.offline, "EQUITY_L.csv") if a.offline else None)
    eq_text = _read_source(eq_file, EQUITY_URL, "NSE equity list")
    idx_text = None
    if a.index != "all":
        idx_file = a.index_file or (os.path.join(a.offline, "ind_%slist.csv" % a.index) if a.offline else None)
        idx_text = _read_source(idx_file, INDEX_URLS[a.index], "%s constituent list" % a.index.upper())
    kept, counts = build_universe(eq_text, idx_text)
    if not a.no_write:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            for sym, name in kept:
                fh.write("%s|%s\n" % (sym, name))
    if a.json:
        print(json.dumps({"count": len(kept), "index": a.index, "counts": counts,
                          "out": None if a.no_write else a.out}, indent=2))
    else:
        print("NSE equity list: %s stocks in series EQ." % _grp(counts["eq_series"]))
        if a.index != "all":
            print("%s: %d constituents, %d kept%s." % (
                a.index.upper(), counts["index"], len(kept),
                (" (%d not in the EQ list)" % counts["index_not_in_eq_list"]) if counts["index_not_in_eq_list"]
                else ""))
        if not a.no_write:
            print("Wrote %s (one SYMBOL|Name a line)." % a.out)
    return 0 if kept else 1


def cmd_daily(a):
    t0 = time.time()
    now = parse_now(a.now)
    offline_syms = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
    symbols = read_symbols(a, offline_syms)
    if not a.offline:
        progress(a, "Fetching 3 months of daily bars for %d symbols (one request each)..." % len(symbols))
        data, failed = fetch_charts(symbols, "3mo", "1d", a.pause, a)
    hits, counts, last_date = scan_daily(data, symbols, now, a.min_price, a.min_avg_volume)
    nfailed = len(symbols) - counts["fetched"]
    doc = {"created_by": "trader-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "daily",
           "as_of": ("%s close" % last_date.isoformat()) if last_date else None, "universe": len(symbols),
           "scanned": counts["scanned"], "failed": nfailed, "source": "yahoo",
           "filters": {"min_price": a.min_price, "min_avg_volume": a.min_avg_volume}, "note": NOTE, "hits": hits}
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/"))
        if is_synthetic(data):
            doc["synthetic"] = True
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2,
                         ensure_ascii=False))
        return 0 if hits else 1
    local = to_ist(now)
    if doc.get("synthetic"):
        print("SYNTHETIC test data, not market prices.")
    print("Daily scan, %s India time, closes through %s (Yahoo daily bars)." % (
        local.strftime("%a %Y-%m-%d %H:%M"), last_date.isoformat() if last_date else "nothing"))
    print("Fetched %d of %d charts%s. %d passed the liquidity filter (%s+ and %s+ shares a day on "
          "average)%s; %d scanned." % (counts["fetched"], len(symbols),
                                      (" (%d failed or had no data)" % nfailed) if nfailed else "",
                                      counts["fetched"] - counts["illiquid"] - counts["short"],
                                      inr(a.min_price), _grp(a.min_avg_volume),
                                      (", %d had under %d days" % (counts["short"], MIN_DAILY_BARS))
                                      if counts["short"] else "", counts["scanned"]))
    if hits:
        per = [(k, sum(1 for h in hits if k in h["setups"])) for k, _ in SETUPS]
        print("%d chart%s matched a setup: %s." % (len(hits), "" if len(hits) == 1 else "s",
                                                  ", ".join("%d %s" % (n, LABEL[k]) for k, n in per if n)))
        print("Most traded first (price x average volume):")
    else:
        print("No setups.")
    for h in hits[:a.show]:
        print("  %-12s %12s  RSI %5.1f  %s" % (h["symbol"], inr(h["price"], True), h["rsi"],
                                               ", ".join(LABEL[t] for t in h["setups"])))
    if len(hits) > a.show:
        print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    if not a.no_write:
        print("Wrote %s (mode daily, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /trader-news SYMBOL.")
    return 0 if hits else 1


def cmd_orb(a):
    t0 = time.time()
    now = parse_now(a.now)
    at = parse_hhmm(a.at) if a.at else None
    cfg = read_config(a.config)
    until = parse_hhmm(a.until or cfg.get("no_entries_after") or "14:45", "--until")
    explicit_day = None
    offline_syms = None
    data = failed = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
        if not a.now:
            days = [ts_to_ist(s["t"][-1]).date() for s in data.values() if s["t"]]
            if days:
                explicit_day = max(days)
                if at is None:
                    raise ScanError('offline replay: give --at HH:MM or --now "YYYY-MM-DD HH:MM"')
    day, cutoff = orb_window(now, at, a.or_minutes, until, explicit_day)
    symbols = read_symbols(a, offline_syms)
    if not a.offline:
        today = to_ist(parse_now(None)).date()
        if (today - day).days > 4:
            raise ScanError("this scan reads 5 days of 5-minute bars: replay %s with --offline" % day.isoformat())
        progress(a, "Fetching 5-minute bars for %d symbols (one request each)..." % len(symbols))
        data, failed = fetch_charts(symbols, "5d", "%dm" % BAR_MIN, a.pause, a)
    hits, counts = scan_orb(data, symbols, day, cutoff, a.or_minutes, a.min_price, a.min_avg_volume)
    nfailed = len([s for s in symbols if s in (failed or {})])
    doc = {"created_by": "trader-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "orb",
           "as_of": "%s %s IST" % (day.isoformat(), cutoff.strftime("%H:%M")), "universe": len(symbols),
           "scanned": counts["scanned"], "failed": nfailed, "source": "yahoo", "or_minutes": a.or_minutes,
           "interval": "%dm" % BAR_MIN, "filters": {"min_price": a.min_price, "min_avg_volume": a.min_avg_volume},
           "note": NOTE, "hits": hits}
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/"))
        if is_synthetic(data):
            doc["synthetic"] = True
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2,
                         ensure_ascii=False))
        return 0 if hits else 1
    if doc.get("synthetic"):
        print("SYNTHETIC test data, not market prices.")
    print("Opening-range breakout scan, %s %s India time: %d-minute range (09:15 to %s), 5-minute bars, Yahoo."
          % (day_label(day), clock_label(cutoff), a.or_minutes, clock_label(slot(day, a.or_minutes // BAR_MIN))))
    print("Fetched %d of %d charts%s; %d usable, %d under the price / volume floor, %d scanned."
          % (counts["fetched"], len(symbols), (" (%d failed or had no data)" % nfailed) if nfailed else "",
             counts["fetched"] - counts["unusable"], counts["illiquid"], counts["scanned"]))
    if hits:
        print("%d of %d charts closed above their opening-range high by %s:"
              % (len(hits), counts["scanned"], clock_label(cutoff)))
        for h in hits[:a.show]:
            print("  %5s  %-12s %12s  range %s to %s  %+.2f%% over" % (
                h["at"], h["symbol"], inr(h["price"], True), _p(h["or_low"]), _p(h["or_high"]), h["pct_over"]))
        if len(hits) > a.show:
            print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    else:
        print("No chart closed above its opening-range high by %s." % clock_label(cutoff))
    if not a.no_write:
        print("Wrote %s (mode orb, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /trader-news SYMBOL and /trader-entry SYMBOL.")
    return 0 if hits else 1


def _p(x):
    return "%.2f" % x


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chart Scanner (trader-scanner): NSE daily setups and "
                                             "opening-range breakouts.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("universe", help="NSE equity list x an index -> trader/universe.txt")
    p.add_argument("--index", default="nifty100", choices=["nifty50", "nifty100", "nifty500", "all"])
    p.add_argument("--equity-file", help="a saved EQUITY_L.csv instead of downloading it")
    p.add_argument("--index-file", help="a saved index constituent CSV instead of downloading it")
    p.add_argument("--offline", metavar="DIR", help="a folder with EQUITY_L.csv and ind_<index>list.csv")
    p.add_argument("--out", default=DEFAULT_UNIVERSE)
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--json", action="store_true")

    def common(p):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--symbols", help="comma-separated NSE symbols instead of the universe")
        g.add_argument("--watchlist", metavar="FILE", help="a watchlist.json (hits) or a text file of symbols")
        p.add_argument("--universe", help="symbol list, one SYMBOL|Name a line (default %s)" % DEFAULT_UNIVERSE)
        p.add_argument("--limit", type=int, help="scan only the first N symbols")
        p.add_argument("--min-price", type=float, default=MIN_PRICE, help="rupees (default 50)")
        p.add_argument("--min-avg-volume", type=int, default=MIN_AVG_VOLUME,
                       help="average shares a day (default 500000)")
        p.add_argument("--offline", metavar="PATH", help="fixture folder or file instead of the network")
        p.add_argument("--now", help='pretend it is this time ("2026-10-30 10:15" India time, or ...Z)')
        p.add_argument("--out", default="trader/watchlist.json")
        p.add_argument("--no-write", action="store_true")
        p.add_argument("--pause", type=float, default=PAUSE, help="seconds between requests (%.1f)" % PAUSE)
        p.add_argument("--show", type=int, default=50, help="hits to print (all are written)")
        p.add_argument("--quiet", action="store_true")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("daily", help="pre-market setups from daily bars")
    common(p)
    p = sub.add_parser("orb", help="opening-range breakouts so far today")
    common(p)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--at", help="cut the clock at this India time, HH:MM")
    p.add_argument("--until", help="no new entries after HH:MM (default: config no_entries_after, 14:45)")
    p.add_argument("--config", default="trader/config.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"universe": cmd_universe, "daily": cmd_daily, "orb": cmd_orb}[a.cmd](a)
    except ScanError as e:
        print("scan.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("scan.py: stopped", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
