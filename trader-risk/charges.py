#!/usr/bin/env python3
"""Zerodha intraday (MIS) equity charges on NSE. Standard library only, no network.

One calculator for the whole pack: trader-budget sizes with it, trader-risk
checks with it, the paper broker books every trade with it. The rates live in
trader/config.json under "charges" so you can correct them in one place; the
defaults below are what VERIFY.md section 5 found on 2026-10-29. Re-check them
at https://zerodha.com/charges before you rely on them.

    python3 charges.py --buy 73632 --sell 74464          # one round trip, by value in rupees
    python3 charges.py --qty 52 --entry 1416 --exit 1432 # the same by shares and prices
    python3 charges.py --qty 52 --entry 1416 --exit 1432 --json

Per round trip (one buy order, one sell order):
  brokerage   the lower of ₹20 or 0.03% of each executed order
  STT         0.025% of the sell value
  exchange    NSE transaction charge 0.00297% of buy + sell value (UNVERIFIED rate: three
              different values seen; VERIFY.md section 5)
  SEBI        ₹10 per crore of buy + sell value
  stamp duty  0.003% of the buy value
  GST         18% of brokerage + exchange + SEBI charges
Every component is kept to the paisa; the contract note may round some of
them (for example STT to the rupee). Treat the total as an estimate.
"""

import argparse
import json
import sys

DEFAULT_RATES = {
    "checked": "2026-10-29",
    "source": "zerodha.com/charges (search snippets), VERIFY.md section 5",
    "brokerage_pct": 0.03,
    "brokerage_cap_inr": 20.0,
    "stt_sell_pct": 0.025,
    "exchange_pct": 0.00297,
    "sebi_per_crore_inr": 10.0,
    "stamp_buy_pct": 0.003,
    "gst_pct": 18.0,
}
CRORE = 10000000.0


def rates_from(cfg):
    """The charge rates: the defaults, overridden by trader/config.json "charges"."""
    r = dict(DEFAULT_RATES)
    r.update((cfg or {}).get("charges") or {})
    return r


def _paise(x):
    return round(float(x) + 1e-9, 2)


def brokerage(order_value, rates):
    if order_value <= 0:
        return 0.0
    return _paise(min(rates["brokerage_cap_inr"], order_value * rates["brokerage_pct"] / 100.0))


def charges(buy_value, sell_value, rates=None):
    """Charges in rupees for one intraday round trip (a side with value 0 has no order).
    Returns {"brokerage", "stt", "exchange", "sebi", "stamp", "gst", "total"}."""
    r = rates or DEFAULT_RATES
    buy, sell = float(buy_value or 0), float(sell_value or 0)
    if buy < 0 or sell < 0:
        raise ValueError("order values cannot be negative")
    br = _paise(brokerage(buy, r) + brokerage(sell, r))
    stt = _paise(sell * r["stt_sell_pct"] / 100.0)
    exch = _paise((buy + sell) * r["exchange_pct"] / 100.0)
    sebi = _paise((buy + sell) * r["sebi_per_crore_inr"] / CRORE)
    stamp = _paise(buy * r["stamp_buy_pct"] / 100.0)
    gst = _paise((br + exch + sebi) * r["gst_pct"] / 100.0)
    out = {"brokerage": br, "stt": stt, "exchange": exch, "sebi": sebi, "stamp": stamp, "gst": gst}
    out["total"] = _paise(sum(out.values()))
    return out


def round_trip(qty, entry, exit_price, rates=None):
    """Charges for buying qty at entry and selling them at exit_price."""
    return charges(qty * float(entry), qty * float(exit_price), rates)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Zerodha intraday equity charges (estimate).")
    ap.add_argument("--buy", type=float, help="buy value in rupees")
    ap.add_argument("--sell", type=float, help="sell value in rupees")
    ap.add_argument("--qty", type=int)
    ap.add_argument("--entry", type=float)
    ap.add_argument("--exit", type=float)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.qty is not None:
        if a.entry is None or a.exit is None:
            print("charges.py: --qty needs --entry and --exit", file=sys.stderr)
            return 1
        buy, sell = a.qty * a.entry, a.qty * a.exit
    elif a.buy is not None or a.sell is not None:
        buy, sell = a.buy or 0.0, a.sell or 0.0
    else:
        ap.print_help()
        return 1
    c = charges(buy, sell)
    if a.json:
        print(json.dumps(dict(c, buy_value=round(buy, 2), sell_value=round(sell, 2), rates=DEFAULT_RATES), indent=2,
                         ensure_ascii=False))
        return 0
    print("Buy ₹%.2f, sell ₹%.2f (Zerodha intraday equity, NSE, rates checked %s):" % (buy, sell,
                                                                                      DEFAULT_RATES["checked"]))
    for k, label in (("brokerage", "brokerage"), ("stt", "STT"), ("exchange", "NSE transaction"),
                     ("sebi", "SEBI fee"), ("stamp", "stamp duty"), ("gst", "GST")):
        print("  %-16s ₹%8.2f" % (label, c[k]))
    print("  %-16s ₹%8.2f" % ("total", c["total"]))
    print("An estimate: the contract note is the final word. Re-check the rates at zerodha.com/charges.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
