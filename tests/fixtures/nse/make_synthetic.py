#!/usr/bin/env python3
"""Builds the SYNTHETIC NSE fixtures in this folder. Standard library only.

These are NOT market data. The build sandbox could not reach Yahoo or NSE
(VERIFY.md, top), so the tests run on made-up stocks with obviously fake
symbols (SYNTHA, SYNTHB, ...) in the shape of Yahoo's v8 chart JSON for an
.NS symbol. Every chart says "synthetic": true in its meta. Real fixtures come
from tests/capture.py, run on a machine that can reach Yahoo.

    python3 tests/fixtures/nse/make_synthetic.py      # rewrites the files, same bytes every time

What is planted (on Friday 2026-10-30 unless said):
  SYNTHA  ~₹1,400, tick ₹0.10. Opening range 09:15-09:30: high 1414.80, low
          1401.20. The 09:45 bar closes at 1416.00, the first close above the
          range high: an opening-range breakout at 09:50. 60 sessions of
          5-minute bars (the time machine's pool) and 1-minute bars for the day.
          Daily closes: a 20-day closing-high breakout on 2026-10-29.
  SYNTHB  ~₹240, tick ₹0.01. No close above its opening range all morning.
          Daily closes: RSI under 30 (oversold).
  SYNTHC  ~₹42. Breaks out at 09:50 (the 09:45 bar), but is under the ₹50 floor.
  SYNTHD  ~₹3,200. Breaks out with the 10:30 bar (closes 10:35). Daily: a bull flag.
  SYNTHE  ~₹600 and thin (a few thousand shares a bar): under the volume floor.
"""

import datetime
import gzip
import io
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
IST = datetime.timedelta(hours=5, minutes=30)
DAY = datetime.date(2026, 10, 30)
HOLIDAYS = {"2026-08-15", "2026-09-14", "2026-10-02", "2026-10-20"}
NOTE = "SYNTHETIC test data in the shape of Yahoo's v8 chart JSON. Not real prices."
SLOTS5 = [(9 + (15 + 5 * k) // 60, (15 + 5 * k) % 60) for k in range(75)]   # 09:15 .. 15:25
SLOTS1 = [(9 + (15 + k) // 60, (15 + k) % 60) for k in range(375)]          # 09:15 .. 15:29

# the planted morning for SYNTHA (o, h, l, c, v), 09:15 to 09:45
SYNTHA_MORNING = [
    (1402.00, 1412.50, 1401.20, 1410.00, 900000),
    (1410.00, 1414.80, 1406.00, 1407.50, 500000),
    (1407.50, 1409.00, 1403.60, 1405.20, 420000),
    (1405.20, 1411.00, 1404.00, 1409.90, 300000),
    (1409.90, 1413.90, 1408.20, 1412.60, 310000),
    (1412.60, 1414.50, 1410.10, 1413.80, 280000),
    (1413.80, 1416.20, 1412.90, 1416.00, 450000),
]


def trading_days(end, n):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5 and d.isoformat() not in HOLIDAYS:
            out.append(d)
        d -= datetime.timedelta(days=1)
    return out[::-1]


def ts(day, hh, mm):
    local = datetime.datetime.combine(day, datetime.time(hh, mm))
    return int(((local - IST) - datetime.datetime(1970, 1, 1)).total_seconds())


def r2(x):
    return round(x + 1e-9, 2)


def on_tick(x, tick):
    return r2(round(x / tick) * tick)


def walk_bars(rng, start, n, vol, base_vol, tick):
    """n bars of a random walk: [(o, h, l, c, v)] on the tick grid."""
    out, p = [], start
    for _ in range(n):
        o = on_tick(p, tick)
        c = on_tick(o * (1 + rng.gauss(0, vol)), tick)
        h = max(o, c, on_tick(max(o, c) * (1 + abs(rng.gauss(0, vol / 2))), tick))
        lo = min(o, c, on_tick(min(o, c) * (1 - abs(rng.gauss(0, vol / 2))), tick))
        out.append((o, h, lo, c, int(base_vol * (0.5 + rng.random()))))
        p = c
    return out


def chart(symbol, stamps, bars, meta_extra=None, granularity="5m"):
    o, h, l, c, v = ([b[i] for b in bars] for i in range(5))
    meta = {"currency": "INR", "symbol": symbol + ".NS", "exchangeName": "NSI",
            "exchangeTimezoneName": "Asia/Kolkata", "gmtoffset": 19800, "synthetic": True, "note": NOTE,
            "regularMarketPrice": c[-1], "regularMarketTime": stamps[-1] + 60, "dataGranularity": granularity}
    meta.update(meta_extra or {})
    return {"chart": {"result": [{"meta": meta, "timestamp": stamps,
                                  "indicators": {"quote": [{"open": o, "high": h, "low": l, "close": c,
                                                            "volume": v}]}}], "error": None}}


def write(name, doc):
    raw = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode("utf-8")
    path = os.path.join(HERE, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if name.endswith(".gz"):
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            gz.write(raw)
        data = buf.getvalue()
    else:
        data = raw + b"\n"
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def plant_breakout(rng, day_bars, slot, vol, base_vol, tick):
    """Closes stay under the opening-range high until `slot`, which closes 0.4 % above it."""
    oh = max(b[1] for b in day_bars[:3])
    for k in range(3, slot):
        o, h, l, c, v = day_bars[k]
        o, c = min(o, r2(oh - tick)), min(c, r2(oh - tick))
        day_bars[k] = (o, max(o, c, min(h, oh)), min(l, o, c), c, v)
    o, h, l, c, v = day_bars[slot]
    o = min(o, r2(oh - tick))
    c = on_tick(oh * 1.004, tick)
    day_bars[slot] = (o, max(h, c), min(l, o), c, v * 3)
    day_bars[slot + 1:] = walk_bars(rng, c, 75 - slot - 1, vol, base_vol, tick)
    return day_bars


def cap_under_range(day_bars, tick):
    """No close above the opening-range high all day."""
    oh = max(b[1] for b in day_bars[:3])
    out = day_bars[:3]
    for o, h, l, c, v in day_bars[3:]:
        o, c = min(o, r2(oh - tick)), min(c, r2(oh - tick))
        out.append((o, max(o, c, min(h, oh)), min(l, o, c), c, v))
    return out


def intraday(rng, days, start, vol, base_vol, tick, morning=None, breakout=None, no_breakout=False):
    """60 sessions of 5-minute bars ending DAY: (stamps, bars, DAY's bars)."""
    stamps, bars, p, today = [], [], start, None
    for d in days:
        p = on_tick(p * (1 + rng.gauss(0, 0.004)), tick)
        if d == DAY and morning:
            day_bars = list(morning) + walk_bars(rng, morning[-1][3], 75 - len(morning), vol, base_vol, tick)
        else:
            day_bars = walk_bars(rng, p, 75, vol, base_vol, tick)
            if d == DAY and breakout is not None:
                day_bars = plant_breakout(rng, day_bars, breakout, vol, base_vol, tick)
            elif d == DAY and no_breakout:
                day_bars = cap_under_range(day_bars, tick)
        for (hh, mm), b in zip(SLOTS5, day_bars):
            stamps.append(ts(d, hh, mm))
            bars.append(b)
        if d == DAY:
            today = day_bars
        p = day_bars[-1][3]
    return stamps, bars, today


def one_minute(rng, five, tick):
    """1-minute bars for DAY that roll up exactly to the given 5-minute bars."""
    stamps, bars = [], []
    for k, (o, h, l, c, v) in enumerate(five):
        path = [o] + [on_tick(rng.uniform(l, h), tick) for _ in range(4)] + [c]
        mins = [[path[i], max(path[i], path[i + 1]), min(path[i], path[i + 1]), path[i + 1], v // 5]
                for i in range(5)]
        mins[rng.randrange(5)][1] = h
        mins[rng.randrange(5)][2] = l
        for i in range(5):
            m = mins[i]
            m[1], m[2] = max(m[1], m[0], m[3]), min(m[2], m[0], m[3])
            hh, mm = SLOTS1[k * 5 + i]
            stamps.append(ts(DAY, hh, mm))
            bars.append(tuple(m))
    return stamps, bars


def daily(symbol, rng, days, start, kind, base_vol):
    """Daily bars through 2026-10-29 with a planted setup on the last close."""
    closes, p = [], start
    for _ in days:
        p *= 1 + rng.gauss(0, 0.012)
        closes.append(p)
    n = len(closes)
    if kind == "breakout":
        closes[-1] = max(closes[-21:-1]) * 1.012
    elif kind == "oversold":
        for i in range(n - 15, n):
            closes[i] = closes[i - 1] * (1 - 0.012 - rng.random() * 0.004)
    elif kind == "flag":
        base = closes[n - 16]
        for i in range(n - 15, n - 5):
            closes[i] = base * (1 + 0.016 * (i - (n - 16)))
        top = closes[n - 6]
        for i in range(n - 5, n):
            closes[i] = top * (1 + rng.uniform(-0.015, 0.01))
    bars = []
    for c in closes:
        c = r2(c)
        o = r2(c * (1 + rng.gauss(0, 0.004)))
        bars.append((o, r2(max(o, c) * 1.006), r2(min(o, c) * 0.994), c,
                     int(base_vol * (0.6 + rng.random() * 0.8))))
    return chart(symbol, [ts(d, 9, 15) for d in days], bars, {"regularMarketVolume": bars[-1][4]}, "1d")


SPECS = [
    # symbol, seed, start, 5m vol, 5m base volume, tick, daily kind, daily base volume, extra
    ("SYNTHA", 101, 1395.0, 0.0018, 120000, 0.10, "breakout", 6000000, {"morning": SYNTHA_MORNING}),
    ("SYNTHB", 102, 240.0, 0.0015, 300000, 0.01, "oversold", 9000000, {"no_breakout": True}),
    ("SYNTHC", 103, 42.0, 0.0025, 500000, 0.01, "breakout", 12000000, {"breakout": 6}),
    ("SYNTHD", 104, 3200.0, 0.0016, 30000, 0.10, "flag", 1500000, {"breakout": 15}),
    ("SYNTHE", 105, 600.0, 0.0020, 600, 0.05, "breakout", 40000, {"breakout": 6}),
]


def build():
    days60 = trading_days(DAY, 60)
    days90 = trading_days(DAY - datetime.timedelta(days=1), 90)
    written = []
    for sym, seed, start, vol, bvol, tick, kind, dvol, extra in SPECS:
        rng = random.Random(seed)
        stamps, bars, today = intraday(rng, days60, start, vol, bvol, tick, **extra)
        written.append(write("5m-60d/%s.json.gz" % sym, chart(sym, stamps, bars)))
        n = sum(1 for t in stamps if t >= ts(DAY, 0, 0))
        written.append(write("5m-1d/%s.json" % sym, chart(sym, stamps[-n:], bars[-n:])))
        if sym == "SYNTHA":
            s1, b1 = one_minute(random.Random(seed + 1000), today, tick)
            written.append(write("1m-1d/%s.json.gz" % sym, chart(sym, s1, b1, granularity="1m")))
        written.append(write("daily/%s.json" % sym, daily(sym, random.Random(seed + 2000), days90,
                                                         start * 0.97, kind, dvol)))
    return written


if __name__ == "__main__":
    for p in build():
        print(os.path.relpath(p, HERE))
