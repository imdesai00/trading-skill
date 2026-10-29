#!/usr/bin/env python3
"""The paper broker for trader-broker (NSE intraday). Standard library only.

No broker is contacted, ever. After your recorded yes it books a paper trade,
fills it from real (free, delayed) 1-minute bars, applies Zerodha's charges and
keeps the paper account in trader/state/paper.json, the ledger trader-risk
reads for the daily, weekly and drawdown limits.

    python3 paper.py open --index 1                 # after ticket.py approve: book the trade
    python3 paper.py reconcile                      # replay today's bars: stop, target or flat-by
    python3 paper.py reconcile --date 2026-10-30 --now "2026-10-30 15:45"
    python3 paper.py skip --index 1                 # a no, or a trader-risk BLOCK: log the skipped signal
    python3 paper.py skip --from-plan --why "budget refused: reward:risk 1.20 after charges"
    python3 paper.py status                         # balance, open trades, today
    python3 paper.py trades --json                  # the whole log

The rules (your request of 2026-10-29):
  Entry fill   the OPEN of the first 1-minute bar that starts at or after your
               yes, plus paper_slippage_pct (0.05 %).
  Exits        bar by bar from the entry bar on, the stop is checked before the
               target. A bar that touches both counts as the STOP.
               stop     fills at the stop, or at the bar's open if the bar opens
                        below the stop (a gap through it), minus slippage
               target   fills at the target, minus slippage (never better: in live
                        mode the target is a new order after an alert, so a paper
                        fill at the target is already generous)
               flat-by  still open at flat_by (15:00): the close of the last bar
                        that ends by then, minus slippage
  Charges      the full Zerodha intraday charges on the buy and the sell
               (trader-budget/charges.py, rates from trader/config.json).
  P&L          gross = qty x (exit fill - entry fill); net = gross - charges;
               R = net / the planned loss at the stop (charges included).
  Skipped      a signal you said no to, or that trader-risk blocked, is replayed
               the same way from the time it was skipped: what would have happened.

Bars: Yahoo's 1-minute chart for SYMBOL.NS (Yahoo keeps about a week of
1-minute bars, so reconcile the same day). --interval 5m replays 5-minute bars
when 1-minute bars are gone, and the trade is noted as replayed on 5-minute
bars. --offline PATH reads saved chart files. A reconcile before the flat-by
time only uses bars that have closed and leaves a trade open if nothing hit.
"""

import argparse
import datetime
import gzip
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import charges as chg  # noqa: E402  (a copy of trader-budget/charges.py; a test keeps the copies identical)

UA = "Mozilla/5.0 (compatible; india-day-trader-skill/1.0)"
HOSTS = ("query2", "query1")
BACKOFF = 5.0
INTERVALS = {"1m": 60, "5m": 300}
NEUTRAL_FIELDS = ("symbol", "exchange", "side", "qty", "product", "entry_type", "limit_price", "stop", "target")
DEFAULTS = {"capital_inr": 100000, "paper_slippage_pct": 0.05, "flat_by": "15:00"}
EPOCH = datetime.datetime(1970, 1, 1)


class PaperError(Exception):
    pass


ClockError = PaperError

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
            raise PaperError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise PaperError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def fingerprint(order):
    """Same function as trader-risk and ticket.py: identifies the exact order."""
    import hashlib
    core = {k: order[k] for k in NEUTRAL_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def new_ledger(cfg):
    capital = float(cfg.get("capital_inr") or DEFAULTS["capital_inr"])
    return {"mode": "paper", "capital": capital, "balance": capital, "trades": [], "skipped": [],
            "note": "Paper trading only: no broker was contacted. Notion is the permanent record."}


def load_ledger(path, cfg):
    led = read_json(path, "paper ledger", required=False)
    if led is None:
        return new_ledger(cfg)
    led.setdefault("trades", [])
    led.setdefault("skipped", [])
    return led


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def r2(x):
    return round(float(x) + 1e-9, 2)


def parse_ist(text):
    """"2026-10-30T09:51:20+05:30" (ticket.py's stamp) -> naive India time."""
    t = str(text or "").strip()
    try:
        dt = datetime.datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        raise PaperError("not a time: %r" % text)
    if dt.tzinfo is not None:
        dt = to_ist((dt - dt.utcoffset()).replace(tzinfo=None))
    return dt


def hhmm_on(day, text):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text or ""))
    if not m:
        raise PaperError("flat_by must be a time like 15:00, got %r" % text)
    return datetime.datetime.combine(day, datetime.time(int(m.group(1)), int(m.group(2))))


# ---------------------------------------------------------------- bars

def _open_url(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _sleep(seconds):
    time.sleep(seconds)


def yahoo_get(path):
    last = "no answer"
    for host in HOSTS:
        url = "https://%s.finance.yahoo.com%s" % (host, path)
        for attempt in (0, 1):
            try:
                return _open_url(url)
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                e.close()
                if e.code == 429 and attempt == 0:
                    _sleep(BACKOFF)
                    continue
                if e.code == 429 or e.code >= 500:
                    break
                raise PaperError("Yahoo answered HTTP %d for %s" % (e.code, url.split("?")[0]))
            except (urllib.error.URLError, OSError) as e:
                last = str(getattr(e, "reason", e))
                break
    raise PaperError("Yahoo did not answer (%s): reconcile again in a minute" % last)


def read_offline(path, sym):
    if os.path.isdir(path):
        for name in (sym + ".json", sym + ".json.gz"):
            p = os.path.join(path, name)
            if os.path.exists(p):
                path = p
                break
        else:
            raise PaperError("no %s.json(.gz) in %s" % (sym, path))
    if not os.path.exists(path):
        raise PaperError("no offline chart %s" % path)
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return fh.read()


def parse_bars(text, sym, day, step):
    """Yahoo v8 chart -> [{start, o, h, l, c}] of `day` on the interval grid, India time, oldest first."""
    try:
        res = json.loads(text)["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise PaperError("Yahoo returned something unexpected for %s" % sym)
    if not res:
        raise PaperError("Yahoo has no data for %s" % sym)
    r = res[0]
    meta = r.get("meta") or {}
    ysym = str(meta.get("symbol") or sym).upper()
    if ysym.endswith((".NS", ".BO")):
        ysym = ysym[:-3]
    if ysym != sym.upper():
        raise PaperError("the chart holds %s, not %s" % (ysym, sym))
    gran = meta.get("dataGranularity")
    if gran and INTERVALS.get(gran) != step:
        raise PaperError("the chart has %s bars, not the interval asked for" % gran)
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    out = {}
    for i, t in enumerate(r.get("timestamp") or []):
        vals = [(q.get(k) or [None] * (i + 1))[i] if i < len(q.get(k) or []) else None
                for k in ("open", "high", "low", "close")]
        if t is None or None in vals or int(t) % step:
            continue
        start = ts_to_ist(t)
        if start.date() != day or not (OPEN <= start.time() < CLOSE):
            continue
        out[start] = {"start": start, "o": float(vals[0]), "h": float(vals[1]), "l": float(vals[2]),
                      "c": float(vals[3])}
    return [out[k] for k in sorted(out)], bool(meta.get("synthetic"))


def get_bars(sym, day, interval="1m", offline=None):
    step = INTERVALS[interval]
    if offline:
        text = read_offline(offline, sym)
    else:
        ysym = sym.upper() + ".NS"
        text = yahoo_get("/v8/finance/chart/%s?range=5d&interval=%s" % (urllib.parse.quote(ysym), interval))
    return parse_bars(text, sym, day, step)


# ---------------------------------------------------------------- the simulation

def first_bar_at(bars, when):
    """Index of the first bar that starts at or after `when` (India time), or None."""
    for i, b in enumerate(bars):
        if b["start"] >= when:
            return i
    return None


def replay(bars, k, entry_fill, stop, target, flat_by, slip_pct, step, cutoff=None):
    """Walk bars k.. (the entry bar first). Returns the exit dict, or None while still open at cutoff.

    cutoff  India time now: only bars that have closed by then count (None = all)."""
    s = slip_pct / 100.0
    span = datetime.timedelta(seconds=step)
    last_ok = None
    for i in range(k, len(bars)):
        b = bars[i]
        end = b["start"] + span
        if end > flat_by:
            break
        if cutoff is not None and end > cutoff:
            return None
        last_ok = b
        if b["l"] <= stop:
            raw = min(stop, b["o"]) if i > k else stop       # the entry bar opened at the fill, above the stop
            why = "stop" if raw == stop else "stop (gap through it)"
            return {"reason": "stop", "detail": why, "price": r2(raw), "fill": r2(raw * (1 - s)),
                    "time": b["start"].strftime("%H:%M"), "both": b["h"] >= target}
        if b["h"] >= target:
            return {"reason": "target", "detail": "target", "price": r2(target), "fill": r2(target * (1 - s)),
                    "time": b["start"].strftime("%H:%M"), "both": False}
    if cutoff is not None and cutoff < flat_by:
        return None
    if last_ok is None:
        raise PaperError("no closed bar between the entry and the flat-by time to exit on")
    return {"reason": "flat-by", "detail": "flat by %s" % flat_by.strftime("%H:%M"), "price": r2(last_ok["c"]),
            "fill": r2(last_ok["c"] * (1 - s)), "time": (last_ok["start"] + span).strftime("%H:%M"), "both": False}


def outcome(qty, entry_fill, exit_fill, risk_at_stop, rates):
    c = chg.charges(qty * entry_fill, qty * exit_fill, rates)
    gross = r2(qty * (exit_fill - entry_fill))
    net = r2(gross - c["total"])
    r = round(net / risk_at_stop, 2) if risk_at_stop else None
    return {"gross_pnl": gross, "charges": c["total"], "charges_detail": c, "net_pnl": net, "r_multiple": r}


# ---------------------------------------------------------------- open, skip, reconcile

def next_trade_id(led, day):
    stem = "PT-%s-" % day.replace("-", "")
    n = max([int(t["trade_id"][len(stem):]) for t in led["trades"] if str(t.get("trade_id", "")).startswith(stem)]
            + [0])
    return "%s%02d" % (stem, n + 1)


def approval_for(state, order, today):
    fp = fingerprint(order)
    found = None
    for rec in (state or {}).get("orders", []):
        if rec.get("fingerprint") == fp and str(rec.get("date")) == today:
            found = rec
    return found


def risk_result(risk, order, today):
    if not risk or risk.get("date") != today:
        return None
    fp = fingerprint(order)
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r
    return None


def tm_odds(tm_doc, sym, day):
    """The time machine's odds for this symbol and day, or None."""
    if not isinstance(tm_doc, dict):
        return None
    p = tm_doc.get("plan") or {}
    if str(p.get("symbol", "")).upper() != sym or p.get("date") != day:
        return None
    m = tm_doc.get("mc") or {}
    return {"target": m.get("p_target"), "stop": m.get("p_stop"), "neither": m.get("p_neither"),
            "verdict": tm_doc.get("verdict")}


def open_trade(led, order, approval, cfg, risk_res=None, tm_doc=None, news_tags=None, now_utc=None):
    """Book an approved BUY as a paper trade (status open, filled at reconcile or here)."""
    sym = str(order.get("symbol", "")).upper()
    day = str(approval.get("date"))
    plan = order.get("plan") or {}
    if any(t.get("fingerprint") == approval.get("fingerprint") and t.get("date") == day for t in led["trades"]):
        raise PaperError("this order is already booked as a paper trade today")
    tid = next_trade_id(led, day)
    t = {"trade_id": tid, "date": day, "symbol": sym, "setup": plan.get("setup") or "", "side": "buy",
         "qty": int(order["qty"]), "planned_entry": _f(plan.get("entry")), "entry_fill": None, "entry_time": None,
         "stop": _f(order.get("stop")), "target": _f(order.get("target")),
         "flat_by": plan.get("flat_by") or cfg.get("flat_by") or DEFAULTS["flat_by"],
         "exit_price": None, "exit_fill": None, "exit_time": None, "exit_reason": None, "exit_date": None,
         "gross_pnl": None, "charges": None, "net_pnl": None, "r_multiple": None,
         "max_loss": _f(plan.get("max_loss")), "risk_at_stop": _f(plan.get("max_loss")),
         "tm_odds": tm_odds(tm_doc, sym, day), "risk_verdict": (risk_res or {}).get("verdict", "PASS"),
         "news_tags": list(news_tags or []), "approval": approval.get("reply"), "approved_at": approval.get("at"),
         "mode": "paper", "status": "open", "balance_after": None, "notes": "",
         "fingerprint": approval.get("fingerprint"),
         "booked_at": (now_utc or parse_now(None)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    led["trades"].append(t)
    return t


def fill_entry(t, bars, slip_pct, step):
    """Fill an open trade at the open of the first bar at or after the yes. Returns the bar index or None."""
    approved = parse_ist(t["approved_at"])
    k = first_bar_at(bars, approved)
    if k is None:
        return None
    b = bars[k]
    t["entry_bar_open"] = r2(b["o"])
    t["entry_fill"] = r2(b["o"] * (1 + slip_pct / 100.0))
    t["entry_time"] = b["start"].strftime("%H:%M")
    if t["entry_fill"] <= t["stop"]:
        t["notes"] = (t["notes"] + " " if t["notes"] else "") + "the price was already under the stop at the fill"
    return k


def close_trade(t, ex, led, rates, interval):
    o = outcome(t["qty"], t["entry_fill"], ex["fill"], t["risk_at_stop"], rates)
    t.update(exit_price=ex["price"], exit_fill=ex["fill"], exit_time=ex["time"], exit_reason=ex["reason"],
             exit_date=t["date"], status="closed", **{k: o[k] for k in ("gross_pnl", "charges", "net_pnl",
                                                                       "r_multiple")})
    notes = []
    if ex["detail"] != ex["reason"]:
        notes.append(ex["detail"])
    if ex.get("both"):
        notes.append("stop and target in the same bar: counted as the stop")
    if interval != "1m":
        notes.append("replayed on %s bars (1-minute bars were not available)" % interval)
    if t.get("risk_at_stop") and o["net_pnl"] < -t["risk_at_stop"] - 0.005:
        t["over_max_loss"] = r2(-o["net_pnl"] - t["risk_at_stop"])
        why = []
        if t.get("planned_entry") and t["entry_fill"] > t["planned_entry"] * (1 + 0.0005) + 0.005:
            why.append("the fill was %.2f above the planned entry" % (t["entry_fill"] - t["planned_entry"]))
        if ex["detail"] != "stop" and ex["reason"] == "stop":
            why.append("the price gapped through the stop")
        notes.append("lost %s more than the planned %s at the stop%s" % (
            inr(t["over_max_loss"], True), inr(t["risk_at_stop"], True), (": " + ", ".join(why)) if why else ""))
    if notes:
        t["notes"] = "; ".join(([t["notes"]] if t["notes"] else []) + notes)
    led["balance"] = r2(float(led["balance"]) + o["net_pnl"])
    t["balance_after"] = led["balance"]


def reconcile(led, cfg, day, bars_for, now_ist=None, interval="1m"):
    """Fill and close today's open trades, and replay today's skipped signals. bars_for(sym) -> bars."""
    rates = chg.rates_from(cfg)
    slip = float(cfg.get("paper_slippage_pct", DEFAULTS["paper_slippage_pct"]))
    step = INTERVALS[interval]
    report = []
    for t in sorted((x for x in led["trades"] if x.get("date") == day and x.get("status") == "open"),
                    key=lambda x: x["trade_id"]):
        bars = bars_for(t["symbol"])
        flat = hhmm_on(datetime.date.fromisoformat(day), t["flat_by"])
        k = fill_entry(t, bars, slip, step) if t.get("entry_fill") is None else \
            first_bar_at(bars, datetime.datetime.combine(datetime.date.fromisoformat(day),
                                                         datetime.time(*map(int, t["entry_time"].split(":")))))
        if k is None:
            report.append((t, "waiting for the first bar after your yes"))
            continue
        manual = parse_ist(t["manual_exit_at"]) if t.get("manual_exit_at") else None
        if manual is not None and manual < flat:
            flat = manual
        ex = replay(bars, k, t["entry_fill"], t["stop"], t["target"], flat, slip, step, now_ist)
        if ex is not None and manual is not None and flat == manual and ex["reason"] == "flat-by":
            j = first_bar_at(bars, manual)
            if j is None or (now_ist is not None and bars[j]["start"] + datetime.timedelta(seconds=step) > now_ist):
                ex = None
            else:
                ex = {"reason": "manual", "detail": "closed by your yes (exit order)", "price": r2(bars[j]["o"]),
                      "fill": r2(bars[j]["o"] * (1 - slip / 100.0)), "time": bars[j]["start"].strftime("%H:%M"),
                      "both": False}
        if ex is None:
            report.append((t, "still open: nothing hit yet"))
            continue
        close_trade(t, ex, led, rates, interval)
        report.append((t, "closed"))
    for s in (x for x in led["skipped"] if x.get("date") == day and x.get("hypothetical_net_pnl") is None):
        try:
            bars = bars_for(s["symbol"])
        except PaperError as e:
            s["notes"] = "not replayed: %s" % e
            continue
        flat = hhmm_on(datetime.date.fromisoformat(day), s.get("flat_by") or cfg.get("flat_by") or "15:00")
        k = first_bar_at(bars, parse_ist(s["skipped_at"]))
        if k is None or not s.get("qty") or not s.get("stop") or not s.get("target"):
            continue
        fill = r2(bars[k]["o"] * (1 + slip / 100.0))
        ex = replay(bars, k, fill, s["stop"], s["target"], flat, slip, step, now_ist)
        if ex is None:
            continue
        o = outcome(int(s["qty"]), fill, ex["fill"], s.get("risk_at_stop"), rates)
        s.update(hypothetical_entry_fill=fill, hypothetical_exit=ex["fill"], hypothetical_exit_reason=ex["reason"],
                 hypothetical_exit_time=ex["time"], hypothetical_net_pnl=o["net_pnl"],
                 hypothetical_r=o["r_multiple"])
    return report


def skip_signal(led, src, why, when_ist, cfg, now_utc=None):
    """Log a signal you did not take. src: an order (from orders.json) or a sizing (from a plan)."""
    plan = src.get("plan") or {}
    day = when_ist.date().isoformat()
    fp = fingerprint(src) if src.get("side") else None
    if fp and any(x.get("fingerprint") == fp and x.get("date") == day for x in led["skipped"]):
        raise PaperError("this exact order is already logged as a skipped signal today")
    s = {"signal_id": "SK-%s-%02d" % (day.replace("-", ""),
                                      1 + sum(1 for x in led["skipped"] if x.get("date") == day)),
         "date": day, "symbol": str(src.get("symbol", "")).upper(), "setup": plan.get("setup") or src.get("setup") or "",
         "side": "buy", "qty": int(src.get("qty") or 0), "planned_entry": _f(plan.get("entry") or src.get("entry")),
         "stop": _f(src.get("stop")), "target": _f(src.get("target")),
         "risk_at_stop": _f(plan.get("max_loss") or src.get("max_loss")),
         "flat_by": plan.get("flat_by") or src.get("flat_by") or cfg.get("flat_by"),
         "why_skipped": why, "skipped_at": when_ist.strftime("%Y-%m-%dT%H:%M:%S+05:30"), "fingerprint": fp,
         "hypothetical_net_pnl": None, "mode": "paper",
         "logged_at": (now_utc or parse_now(None)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    led["skipped"].append(s)
    return s


# ---------------------------------------------------------------- words

def trade_line(t):
    if t["status"] != "closed":
        return "%s %s %d %s: open, entry %s, stop %.2f, target %.2f" % (
            t["trade_id"], "BUY", t["qty"], t["symbol"],
            ("%.2f at %s" % (t["entry_fill"], t["entry_time"])) if t.get("entry_fill") else "not filled yet",
            t["stop"], t["target"])
    return ("%s BUY %d %s: in %.2f at %s, out %.2f at %s (%s). Gross %s, charges %s, net %s, %+.2fR. Balance %s."
            % (t["trade_id"], t["qty"], t["symbol"], t["entry_fill"], t["entry_time"], t["exit_fill"],
               t["exit_time"], t["exit_reason"], inr(t["gross_pnl"], True), inr(t["charges"], True),
               inr(t["net_pnl"], True), t["r_multiple"] or 0, inr(t["balance_after"], True)))


# ---------------------------------------------------------------- commands

def _clock(a):
    now = parse_now(getattr(a, "now", None))
    return now, to_ist(now)


def _cfg(a):
    cfg = dict(DEFAULTS)
    cfg.update(read_json(a.config, "config", required=False) or {})
    if str(cfg.get("mode", "paper")).lower() != "paper":
        raise PaperError("the config says \"mode\": \"%s\": the paper broker only runs in paper mode" % cfg.get("mode"))
    return cfg


def _orders(a):
    doc = read_json(a.orders, "orders file")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise PaperError("no orders in %s" % a.orders)
    if a.index is None or not 1 <= a.index <= len(orders):
        raise PaperError("say which order: --index 1 to %d" % len(orders))
    return orders[a.index - 1]


def cmd_open(a):
    cfg = _cfg(a)
    now, now_ist = _clock(a)
    today = now_ist.date().isoformat()
    order = _orders(a)
    appr = approval_for(read_json(a.state, "broker log", required=False), order, today)
    if not appr or appr.get("status") != "approved":
        raise PaperError("order %d has no yes recorded today: ticket.py approve first. No yes, no trade." % a.index)
    if appr.get("mode") != "paper":
        raise PaperError("that yes was given in %s mode, not paper" % appr.get("mode"))
    led = load_ledger(a.ledger, cfg)
    sym = str(order.get("symbol", "")).upper()
    if str(order.get("side")) == "sell":
        t = next((x for x in led["trades"] if x["status"] == "open" and x["symbol"] == sym), None)
        if not t:
            raise PaperError("no open paper trade in %s to sell" % sym)
        t["exit_request"] = {"at": appr["at"], "reply": appr.get("reply")}
        t["manual_exit_at"] = appr["at"]
        write_json(a.ledger, led)
        print("Recorded your yes to close %s %s. reconcile fills it at the next 1-minute bar's open." % (t["trade_id"],
                                                                                                    sym))
        return 0
    risk = read_json(a.risk, "risk check", required=False)
    rr = risk_result(risk, order, today)
    if not rr or rr.get("verdict") != "PASS":
        raise PaperError("trader-risk did not pass this exact order today: no paper trade")
    tm_doc = read_json(a.timemachine, "time machine", required=False)
    t = open_trade(led, order, appr, cfg, rr, tm_doc, a.news_tag, now)
    write_json(a.ledger, led)
    print("Booked paper trade %s: BUY %d %s, stop %.2f, target %.2f, flat by %s. No broker was contacted."
          % (t["trade_id"], t["qty"], sym, t["stop"], t["target"], t["flat_by"]))
    print("It fills at the open of the first 1-minute bar at or after your yes (%s), plus %.2f%% slippage. "
          "Run paper.py reconcile after the bars are in (by 15:30 at the latest)."
          % (appr["at"][11:16], float(cfg["paper_slippage_pct"])))
    return 0


def cmd_skip(a):
    cfg = _cfg(a)
    now, now_ist = _clock(a)
    today = now_ist.date().isoformat()
    led = load_ledger(a.ledger, cfg)
    if a.from_plan:
        s = read_json(a.sizing, "last sizing (run trader-budget size.py first)")
        if s.get("date") != today:
            raise PaperError("the last sizing is for %s, not today: run trader-budget size.py again" % s.get("date"))
        src = {"symbol": s["symbol"], "qty": s["qty"], "stop": s["stop"], "target": s["target"], "entry": s["entry"],
               "max_loss": s["max_loss"], "setup": s["setup"], "flat_by": s["flat_by"]}
        why = a.why or ("budget refused: " + (s["refusal"] or "no reason given"))
    else:
        order = _orders(a)
        src = order
        why = a.why
        if not why:
            appr = approval_for(read_json(a.state, "broker log", required=False), order, today)
            rr = risk_result(read_json(a.risk, "risk check", required=False), order, today)
            if appr and appr.get("status") == "declined":
                why = "you said no: \"%s\"" % appr.get("reply", "no")
            elif rr and rr.get("verdict") == "BLOCK":
                why = "trader-risk blocked it: " + "; ".join(rr.get("reasons", []))
            else:
                raise PaperError("order %d was neither declined nor blocked today: give --why" % a.index)
    s = skip_signal(led, src, why, now_ist, cfg, now)
    write_json(a.ledger, led)
    print("Logged skipped signal %s: %s %s (%s). reconcile replays what would have happened."
          % (s["signal_id"], s["symbol"], s["qty"], why))
    return 0


def cmd_reconcile(a):
    cfg = _cfg(a)
    now, now_ist = _clock(a)
    day = a.date or now_ist.date().isoformat()
    led = load_ledger(a.ledger, cfg)
    cache, synthetic = {}, []

    def bars_for(sym):
        if sym not in cache:
            bars, syn = get_bars(sym, datetime.date.fromisoformat(day), a.interval, a.offline)
            if not bars:
                raise PaperError("no %s bars for %s on %s" % (a.interval, sym, day))
            cache[sym] = bars
            if syn:
                synthetic.append(sym)
        return cache[sym]

    cutoff = now_ist if now_ist.date().isoformat() == day else None
    report = reconcile(led, cfg, day, bars_for, cutoff, a.interval)
    write_json(a.ledger, led)
    if synthetic:
        print("SYNTHETIC test data, not market prices.")
    if not report:
        print("No open paper trades on %s." % day)
    for t, what in report:
        print(trade_line(t) if what == "closed" else "%s %s: %s" % (t["trade_id"], t["symbol"], what))
        if what == "closed" and t.get("notes"):
            print("  note: " + t["notes"])
    for s in (x for x in led["skipped"] if x.get("date") == day and x.get("hypothetical_net_pnl") is not None):
        print("Skipped %s %s (%s): it would have exited by %s at %s, net %s." % (
            s["signal_id"], s["symbol"], s["why_skipped"], s["hypothetical_exit_reason"], s["hypothetical_exit_time"],
            inr(s["hypothetical_net_pnl"], True)))
    print("Paper account: %s (started at %s). Paper fills are kinder than real ones." % (
        inr(led["balance"], True), inr(led["capital"])))
    return 0


def cmd_status(a):
    cfg = _cfg(a)
    now, now_ist = _clock(a)
    today = now_ist.date().isoformat()
    led = load_ledger(a.ledger, cfg)
    closed = [t for t in led["trades"] if t["status"] == "closed"]
    today_net = sum(t["net_pnl"] for t in closed if t.get("exit_date") == today)
    if a.json:
        print(json.dumps({"balance": led["balance"], "capital": led["capital"], "today_net": r2(today_net),
                          "open": [t for t in led["trades"] if t["status"] == "open"],
                          "trades": len(led["trades"]), "skipped": len(led["skipped"])}, indent=2, ensure_ascii=False))
        return 0
    nt, ns = len(led["trades"]), len(led["skipped"])
    print("Paper account %s (capital %s, %s %s). %d trade%s, %d skipped signal%s." % (
        inr(led["balance"], True), inr(led["capital"]), "up" if led["balance"] >= led["capital"] else "down",
        inr(abs(led["balance"] - led["capital"]), True), nt, "" if nt == 1 else "s", ns, "" if ns == 1 else "s"))
    print("Today (%s): net %s." % (today, inr(today_net, True)))
    for t in led["trades"]:
        if t["status"] == "open":
            print("  open: " + trade_line(t))
    return 0


def cmd_trades(a):
    cfg = _cfg(a)
    led = load_ledger(a.ledger, cfg)
    if a.json:
        print(json.dumps(led, indent=2, ensure_ascii=False))
        return 0
    for t in led["trades"]:
        print(trade_line(t))
    for s in led["skipped"]:
        print("%s %s skipped (%s): %s" % (s["signal_id"], s["symbol"], s["why_skipped"],
                                          "would have netted %s" % inr(s["hypothetical_net_pnl"], True)
                                          if s.get("hypothetical_net_pnl") is not None else "not replayed yet"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="The paper broker (trader-broker). Never contacts a broker.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--ledger", default="trader/state/paper.json")
        p.add_argument("--now", help="pretend it is this India time, e.g. \"2026-10-30 15:45\"")

    p = sub.add_parser("open", help="book an approved order as a paper trade")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--orders", default="trader/orders.json")
    p.add_argument("--state", default="trader/state/broker.json")
    p.add_argument("--risk", default="trader/state/risk.json")
    p.add_argument("--timemachine", default="trader/timemachine.json")
    p.add_argument("--news-tag", action="append", default=[], help="a news tag for the record (repeatable)")
    p = sub.add_parser("skip", help="log a signal you did not take")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--orders", default="trader/orders.json")
    p.add_argument("--state", default="trader/state/broker.json")
    p.add_argument("--risk", default="trader/state/risk.json")
    p.add_argument("--from-plan", action="store_true", help="a signal trader-budget refused (no order was written)")
    p.add_argument("--sizing", default="trader/state/sizing.json", help="trader-budget's last sizing")
    p.add_argument("--why")
    p = sub.add_parser("reconcile", help="replay the day's bars: fills, exits, skipped signals")
    common(p)
    p.add_argument("--date", help="YYYY-MM-DD (default: today in India)")
    p.add_argument("--interval", choices=["1m", "5m"], default="1m")
    p.add_argument("--offline", metavar="PATH", help="a saved chart file or a folder of <SYMBOL>.json(.gz)")
    p = sub.add_parser("status", help="balance and open trades")
    common(p)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("trades", help="the whole paper log")
    common(p)
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"open": cmd_open, "skip": cmd_skip, "reconcile": cmd_reconcile, "status": cmd_status,
                "trades": cmd_trades}[a.cmd](a)
    except PaperError as e:
        print("paper.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
