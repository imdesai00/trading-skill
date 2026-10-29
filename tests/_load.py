"""Load a script from a skill folder (the folder names have hyphens), and shared test helpers."""

import datetime
import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")
NSE = os.path.join(FIX, "nse")          # SYNTHETIC NSE fixtures: tests/fixtures/nse/make_synthetic.py
IST = datetime.timedelta(hours=5, minutes=30)


def load(folder, name):
    path = os.path.join(ROOT, folder, name + ".py")
    spec = importlib.util.spec_from_file_location("trader_" + name.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ist(text):
    """"2026-10-30 09:50" (India time) -> naive UTC, the way the tools keep time."""
    return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M") - IST


def quote_doc(symbol, price, prev_close=None, when="2026-10-30 10:00"):
    """A minimal Yahoo v8 quote in the shape trader-alerts reads (SYNTHETIC)."""
    t = int((ist(when) - datetime.datetime(1970, 1, 1)).total_seconds())
    return {"chart": {"result": [{"meta": {"symbol": symbol + ".NS", "currency": "INR", "regularMarketPrice": price,
                                           "regularMarketTime": t, "previousClose": prev_close, "synthetic": True},
                                  "timestamp": [t - 300], "indicators": {"quote": [{"open": [price], "high": [price],
                                                                                    "low": [price], "close": [price],
                                                                                    "volume": [100]}]}}],
                      "error": None}}


def write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)
