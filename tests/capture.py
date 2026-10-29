#!/usr/bin/env python3
"""Capture REAL Yahoo .NS charts as offline fixtures. Run it on your own machine.

The build sandbox could not reach Yahoo (VERIFY.md, top), so the shipped tests
run on synthetic stocks. This saves real data in the same layout, so the tools
(and the README's worked example) can replay a real NSE day offline:

    python3 tests/capture.py RELIANCE                 # after 15:30 on the day you want to keep
    python3 tests/capture.py RELIANCE TCS INFY --out tests/fixtures/nse-real

It writes, per symbol, gzipped exactly as Yahoo sent it:
    <out>/5m-60d/<SYMBOL>.json.gz   60 days of 5-minute bars  (time machine, scanner orb)
    <out>/1m-5d/<SYMBOL>.json.gz    5 days of 1-minute bars   (paper fills, reconcile)
    <out>/daily/<SYMBOL>.json.gz    3 months of daily bars    (scanner daily)
and <out>/CAPTURED.txt with the time and the URLs, so the data's date is stated.

Yahoo's data is free and unofficial: for testing and paper trading, not for
redistribution. One request at a time, half a second apart.
"""

import argparse
import datetime
import gzip
import os
import sys
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; india-day-trader-skill/1.0)"
KINDS = (("5m-60d", "60d", "5m"), ("1m-5d", "5d", "1m"), ("daily", "3mo", "1d"))


def url_for(sym, rng, interval):
    s = sym.upper() if sym.upper().endswith((".NS", ".BO")) else sym.upper() + ".NS"
    return "https://query2.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s" % (
        urllib.parse.quote(s), rng, interval)


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def capture(symbols, out, get=fetch, sleep=time.sleep, now=None):
    """Write the files; returns the list of (path, url)."""
    done = []
    for i, sym in enumerate(symbols):
        for kind, rng, interval in KINDS:
            if done:
                sleep(0.5)
            url = url_for(sym, rng, interval)
            body = get(url)
            path = os.path.join(out, kind, "%s.json.gz" % sym.upper())
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with gzip.open(path, "wb") as fh:
                fh.write(body)
            done.append((path, url))
    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    with open(os.path.join(out, "CAPTURED.txt"), "a", encoding="utf-8") as fh:
        for path, url in done:
            fh.write("%s  %s  %s\n" % (stamp, os.path.relpath(path, out), url))
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description="Capture real Yahoo .NS charts as offline fixtures.")
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "nse-real"))
    a = ap.parse_args(argv)
    try:
        done = capture(a.symbols, a.out)
    except Exception as e:  # network errors are the user's to see, in one line
        print("capture.py: %s" % e, file=sys.stderr)
        return 1
    for path, url in done:
        print("saved %s" % path)
    print("Real Yahoo data, free and unofficial. The date of the capture is in %s."
          % os.path.join(a.out, "CAPTURED.txt"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
