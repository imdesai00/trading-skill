#!/usr/bin/env python3
"""News monitor for trader-news (NSE). Standard library only.

Reads free public headline feeds for the stocks you watch or hold and prints
only what is new since the last check, newest first, in India time, with how far
each stock has moved today and a warning on any company reporting results today.

    python3 news.py                                  # watchlist + plan + portfolio symbols
    python3 news.py --symbols RELIANCE TCS INFY
    python3 news.py --symbols RELIANCE --all --hours 72   # everything in the last 3 days, seen or not
    python3 news.py --no-moves                       # skip the price line per symbol
    python3 news.py --offline tests/fixtures/trader-news --now "2026-10-30 08:30"

Your symbols, when --symbols is not given: every hit in trader/watchlist.json
(trader-scanner), the symbol in trader/plan.json (trader-exit) and the positions
in trader/portfolio.json.

Sources (each can be switched off in trader/config.json under "news_sources";
a source that fails is reported and the rest still run):
  - Yahoo Finance headline RSS, one request per symbol:
    https://feeds.finance.yahoo.com/rss/2.0/headline?s=RELIANCE.NS&region=IN&lang=en-IN
  - NSE corporate announcements RSS (market-wide, one request):
    https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml
  - Moneycontrol RSS section feeds (market-wide): https://www.moneycontrol.com/rss/<section>.xml
    for latestnews, business, marketreports, results, buzzingstocks (change them with
    "moneycontrol_sections"). Personal reading only; check their terms (VERIFY.md section 10).
  - The move: Yahoo's v8 chart endpoint, the last price against yesterday's close.
Groww has no public news feed and is not read.

Market-wide feeds are matched to your symbols by the symbol written as a
word, "(SYMBOL)" or "NSE: SYMBOL", or by the company's name (from the watchlist,
or from --names EQUITY_L.csv). An item that only shares a distinctive first word of the
name with your company is shown as a POSSIBLE match: read it before believing it.

Times print in India time (IST). State: trader/state/news.json remembers what
you have seen (30 days), so an item shows once. Offline: --offline DIR reads
yahoo-<SYMBOL>.xml, nse-announcements.xml, moneycontrol-<section>.xml,
chart-<SYMBOL>.json and EQUITY_L.csv from DIR. The tests use it, with synthetic files.

Headlines are leads, not facts: open the story before acting, and treat nothing
here as advice.
"""

import argparse
import csv
import datetime
import email.utils
import gzip
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

YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s.NS&region=IN&lang=en-IN"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s.NS?range=1d&interval=5m"
NSE_ANN = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"
MONEYCONTROL = "https://www.moneycontrol.com/rss/%s.xml"
MC_SECTIONS = ["latestnews", "business", "marketreports", "results", "buzzingstocks"]
BROWSER_UA = "Mozilla/5.0 (compatible; india-day-trader-skill/1.0)"
KEEP_DAYS = 30
MOVE_PCT = 3.0   # a stock this far from yesterday's close is flagged as moving

# words that start many company names: not enough on their own to match a story
GENERIC = {"india", "indian", "bank", "limited", "industries", "industry", "international", "global",
           "national", "power", "steel", "motors", "finance", "financial", "capital", "tata", "bharat",
           "reliance", "hindustan", "state", "united", "general", "new", "first", "grand", "sun",
           "adani", "bajaj", "mahindra", "birla", "aditya", "jindal", "godrej", "larsen", "oil", "gas",
           "pharma", "cement", "textiles", "energy", "infra", "infrastructure", "technologies",
           "services", "enterprises", "holdings", "group", "company", "corporation", "housing"}
KEYWORDS = [
    ("results", r"\bresults?\b|\bearnings\b|\bQ[1-4]\b|\bquarterly\b|\bprofit\b|\bPAT\b|\bnet loss\b|\brevenue\b"),
    ("board meeting", r"\bboard meeting\b|\bmeeting of the board\b|\bboard to consider\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|target price|\bbrokerage\b|\boutperform\b|\bunderperform\b|"
                r"\bbuy rating\b|\bsell rating\b|\binitiates coverage\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bdemerger\b|\btakeover\b|\bstake sale\b|"
             r"\bblock deal\b|\bbulk deal\b|\bopen offer\b"),
    ("capital", r"\bdividend\b|\bbuyback\b|\bbuy-back\b|\bbonus\b|\bstock split\b|\brights issue\b|\bQIP\b|\bIPO\b|"
                r"\bfund ?raise\b"),
    ("people", r"\bCEO\b|\bCFO\b|\bmanaging director\b|\bresign|\bsteps down\b|\bappoint"),
    ("legal", r"\bSEBI\b.*\b(?:order|probe|penalty|notice|bars)\b|\bshow cause\b|\bNCLT\b|\bcourt\b|\bED\b raid|"
              r"\bprobe\b|\bfraud\b|\blawsuit\b"),
    ("risk", r"\bdefault\b|\binsolvency\b|\bdelist|\bpledge\b|\bdowngrades? by (?:CRISIL|ICRA|CARE)\b|\brecall\b|"
             r"\bhack|\bbreach\b"),
    ("surveillance", r"\bASM\b|\bGSM\b|\bprice band\b|\bupper circuit\b|\blower circuit\b|\btrading halt\b|"
                     r"\bsuspend(?:ed)?\b"),
    ("regulatory", r"\bRBI\b|\bUSFDA\b|\bFDA\b|\bCCI\b|\bapproval\b|\bapproves\b"),
    ("macro", r"\brepo rate\b|\bRBI policy\b|\binflation\b|\bCPI\b|\bFII\b|\bFPI\b|\bcrude\b|\brupee\b|"
              r"\bNifty\b|\bSensex\b|\btariffs?\b"),
]
RESULTS_TAGS = {"results", "board meeting"}


class NewsError(Exception):
    pass


ClockError = NewsError

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

# ---------------------------------------------------------------- inputs

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{SYMBOL: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = h.get("symbol")
        if sym:
            out[sym.upper()] = h.get("name")
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(pl["symbol"].upper(), pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = p.get("symbol") or p.get("ticker")
        if sym:
            out[sym.upper()] = p.get("name") or out.get(sym.upper())
    return out


def read_names(text):
    """NSE EQUITY_L.csv text -> {SYMBOL: company name}. Columns are found by name."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {}
    head = [c.strip().lower() for c in rows[0]]
    try:
        si = head.index("symbol")
        ni = next(i for i, c in enumerate(head) if "name" in c)
    except (ValueError, StopIteration):
        raise NewsError("the names file has no SYMBOL / NAME OF COMPANY columns")
    return {r[si].strip().upper(): r[ni].strip() for r in rows[1:] if len(r) > max(si, ni) and r[si].strip()}


def config_sources(path="trader/config.json"):
    cfg = load_json(path) or {}
    src = {"yahoo": True, "nse": True, "moneycontrol": True}
    src.update({k: bool(v) for k, v in (cfg.get("news_sources") or {}).items() if k in src})
    secs = cfg.get("moneycontrol_sections") or MC_SECTIONS
    secs = [s for s in secs if re.match(r"^[a-z0-9_]+$", str(s))]
    return src, secs or MC_SECTIONS


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.3):
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def get(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (url, exc.reason))


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_time(value):
    """RFC 822 (RSS) or ISO 8601 -> naive UTC datetime, or None. A time with no zone is India time."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        return d - IST
    return (d - d.utcoffset()).replace(tzinfo=None)


def parse_rss(data, source, symbol=None, feed=""):
    """Any RSS 2.0 feed -> items. `symbol` is set for Yahoo's one-symbol feeds."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("%s feed is not valid XML: %s" % (source, exc))
    out = []
    for it in root.iter("item"):
        title = re.sub(r"\s+", " ", _text(it, "title"))
        link = _text(it, "link")
        guid = _text(it, "guid") or link or title
        out.append({"id": "%s:%s" % (source, guid), "source": source, "feed": feed, "symbol": symbol,
                    "title": title, "summary": re.sub(r"<[^>]+>", " ", _text(it, "description")).strip(),
                    "link": link, "time": parse_time(_text(it, "pubDate"))})
    return out


def parse_chart(data, symbol):
    """Yahoo v8 chart JSON -> {"symbol", "price", "prev_close", "pct", "time"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % symbol)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % symbol)
    when = meta.get("regularMarketTime")
    return {"symbol": symbol, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2),
            "time": (datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(when))) if when else None}


def collect_moves(fetcher, watch, errors=None):
    errors = [] if errors is None else errors
    out = []
    for s in sorted(watch):
        name = "chart-%s.json" % s
        try:
            try:
                data = fetcher.get(CHART % ("query2", s), name)
            except NewsError as exc:
                if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
                    raise
                data = fetcher.get(CHART % ("query1", s), name)
            out.append(parse_chart(data, s))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (s, exc))
    return out


# ---------------------------------------------------------------- matching and tags

def _short_name(name):
    n = re.sub(r"\b(?:limited|ltd\.?|corporation|corp\.?|company|co\.)\s*$", "", (name or "").strip(), flags=re.I)
    return re.sub(r"\s+", " ", n).strip()


def exact_pattern(symbol, name):
    s = re.escape(symbol)
    alts = [r"\(%s\)" % s, r"\bNSE\s*:\s*%s(?![A-Z0-9&-])" % s, r"\$%s(?![A-Z0-9&-])" % s]
    if len(symbol) >= 4:   # a short symbol as a bare word is too often an ordinary word
        alts.append(r"(?<![A-Za-z0-9&-])%s(?![A-Za-z0-9&-])" % s)
    short, full = _short_name(name), (name or "").strip()
    forms = [short] if len(short) >= 5 else []
    if len(full) >= 5 and full != short:   # a short name such as ITC is only safe in its full form, "ITC Limited"
        forms.append(full)
    for form in forms:
        alts.append(r"(?i:\b%s\b)" % re.escape(form).replace(r"\ ", r"[\s-]"))
    return re.compile("|".join(alts))


def first_word(name):
    """The first distinctive word of the name, or None when it is too common to mean anything."""
    for w in re.findall(r"[A-Za-z]{5,}", _short_name(name)):
        return None if w.lower() in GENERIC else w
    return None


def weak_patterns(watch):
    """{SYMBOL: regex of the first word of its name}. A word that starts two of your companies' names is dropped:
    it cannot tell them apart."""
    words = {s: first_word(n) for s, n in watch.items() if n}
    count = {}
    for w in words.values():
        if w:
            count[w.lower()] = count.get(w.lower(), 0) + 1
    return {s: re.compile(r"(?i:\b%s\b)" % re.escape(w)) for s, w in words.items() if w and count[w.lower()] == 1}


def tag(items, watch):
    """Which of your symbols each item touches (exactly or only possibly) and what kind of news it is."""
    exact = {s: exact_pattern(s, n) for s, n in watch.items()}
    weak = weak_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = {it["symbol"]} if it.get("symbol") in watch else set()
        for s, rx in exact.items():
            if rx.search(hay):
                touches.add(s)
        possible = set()
        for s, rx in weak.items():
            if s not in touches and rx.search(hay):
                possible.add(s)
        it["touches"] = sorted(touches)
        it["possible"] = sorted(possible)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
    return [it for it in items if it["touches"] or it["possible"]]


def dedupe(items):
    """One item per story: same id, same link, or the same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for field in ("touches", "possible"):
                for t in it.get(field, []):
                    if t not in hit[field]:
                        hit[field].append(t)
                hit[field].sort()
            hit["possible"] = [p for p in hit["possible"] if p not in hit["touches"]]
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


def results_warnings(items, now_utc):
    """Symbols with a results / board-meeting item dated today (India date): gaps are common."""
    today = to_ist(now_utc).date()
    out = {}
    for it in items:
        if not (RESULTS_TAGS & set(it["tags"])) or not it["touches"]:
            continue
        if it["time"] is None or to_ist(it["time"]).date() != today:
            continue
        for s in it["touches"]:
            out.setdefault(s, it["title"])
    return out


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, sources, sections, errors=None):
    errors = [] if errors is None else errors
    items = []
    if sources.get("yahoo"):
        for s in sorted(watch):
            try:
                items += parse_rss(fetcher.get(YAHOO % s, "yahoo-%s.xml" % s), "Yahoo Finance", s)
            except NewsError as exc:
                errors.append("Yahoo headlines for %s: %s" % (s, exc))
    if sources.get("nse"):
        try:
            items += parse_rss(fetcher.get(NSE_ANN, "nse-announcements.xml"), "NSE announcements")
        except NewsError as exc:
            errors.append("NSE announcements: %s (VERIFY.md section 10: feed URL unverified)" % exc)
    if sources.get("moneycontrol"):
        for sec in sections:
            try:
                items += parse_rss(fetcher.get(MONEYCONTROL % sec, "moneycontrol-%s.xml" % sec),
                                   "Moneycontrol", None, sec)
            except NewsError as exc:
                errors.append("Moneycontrol %s: %s" % (sec, exc))
    return items


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    cutoff = now - datetime.timedelta(hours=hours)
    ahead = now + datetime.timedelta(minutes=10)   # a pinned --now: nothing from after it
    out = []
    for it in items:
        if it["time"] is not None and (it["time"] < cutoff or it["time"] > ahead):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = (" at %s IST" % to_ist(m["time"]).strftime("%H:%M")) if m.get("time") else ""
        out.append("%-12s %s %+.2f%% vs yesterday's close %s%s  %s" % (
            m["symbol"], inr(m["price"], True), m["pct"], inr(m["prev_close"], True), when, flag))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT, warnings=None):
    out = []
    for s, title in sorted((warnings or {}).items()):
        out.append("RESULTS TODAY: %s (%s). Gaps are common on results day: this pack does not trade into "
                   "them. Check the time and the numbers before any entry." % (s, title))
    out += render_moves(moves or [], move_pct)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        who = ",".join(it["touches"])
        if it["possible"]:
            who = (who + " " if who else "") + "POSSIBLE:" + ",".join(it["possible"])
        out.append("  %s  %-16s %-18s %s" % (when, who, it["source"], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        if it["possible"] and not it["touches"]:
            extra.append("name match only: read it before believing it")
        extra.append(it["link"])
        out.append("      " + "  ".join(x for x in extra if x))
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Headlines are leads, not facts; open the story before acting. "
               "Not financial advice." % to_ist(now).strftime("%Y-%m-%d %H:%M IST"))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        out.append(d)
    return out


def save_results_today(state_path, warnings, now_utc):
    """trader/state/results_today.json: who reports results today. trader-risk and trader-budget read it (half size)."""
    day = to_ist(now_utc).date().isoformat()
    path = os.path.join(os.path.dirname(os.path.abspath(state_path)), "results_today.json")
    doc = {"date": day, "symbols": {}}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
            if old.get("date") == day:
                doc["symbols"] = dict(old.get("symbols") or {})
        except ValueError:
            pass
    doc["symbols"].update(warnings)
    if not doc["symbols"] and not os.path.isfile(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def log_items(state_path, items, now_utc):
    """Record the items in trader/trader.db (the news_items table) when the database exists. A failure is shown."""
    d = os.path.dirname(os.path.abspath(state_path))
    if os.path.basename(d) == "state":
        d = os.path.dirname(d)
    path = os.path.join(d, "trader.db")
    if not items or not os.path.isfile(path):
        return
    try:
        import sqlite3
        conn = sqlite3.connect(path, timeout=30)
        try:
            for it in items:
                conn.execute("INSERT OR IGNORE INTO news_items (item_hash, at, source, symbol, title, tags, link) "
                             "VALUES (?,?,?,?,?,?,?)",
                             (fingerprint(it), (it["time"] or now_utc).strftime("%Y-%m-%dT%H:%M:%SZ"), it["source"],
                              ",".join(it["touches"] or it["possible"]), it["title"], ",".join(it["tags"]), it["link"]))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print("WARNING: could not log the news to %s: %s" % (path, e), file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--symbols", nargs="*", help="NSE symbols; default: watchlist + plan + portfolio")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--names", metavar="EQUITY_L.csv", help="company names, for matching market-wide feeds")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per symbol")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="a time like \"2026-10-30 08:30\" (India time), for reproducible runs")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    known = watch_from_files(args.portfolio, args.watchlist, args.plan)
    if args.symbols:
        watch = {s.upper(): known.get(s.upper()) for s in args.symbols}
    else:
        watch = known
    if not watch:
        print("news: no symbols. Pass --symbols, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    fetcher = Fetcher(offline=args.offline)
    errors = []
    names_text = None
    if args.names:
        with open(args.names, encoding="utf-8", errors="replace") as fh:
            names_text = fh.read()
    elif args.offline and os.path.isfile(os.path.join(args.offline, "EQUITY_L.csv")):
        with open(os.path.join(args.offline, "EQUITY_L.csv"), encoding="utf-8", errors="replace") as fh:
            names_text = fh.read()
    if names_text:
        try:
            names = read_names(names_text)
            for s in watch:
                if not watch[s]:
                    watch[s] = names.get(s)
        except NewsError as exc:
            errors.append(str(exc))
    if any(not n for n in watch.values()) and not names_text:
        errors.append("no company names: market-wide feeds are matched by symbol only. Pass --names "
                      "EQUITY_L.csv (the scanner downloads it) to match by name too")
    now = parse_now(args.now)
    state = load_json(args.state) or {}
    sources, sections = config_sources(args.config)
    items = collect(fetcher, watch, sources, sections, errors)
    items = dedupe(tag(items, watch))
    warnings = results_warnings(items, now)
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": now.isoformat(), "results_today": warnings, "moves": mv,
                          "items": _json_ready(shown), "notes": errors}, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct, warnings))
    if not args.dry_run:
        save_results_today(args.state, warnings, now)
        log_items(args.state, items, now)
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
