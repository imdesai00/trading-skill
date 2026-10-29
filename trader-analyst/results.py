#!/usr/bin/env python3
"""Quarterly results reader for trader-analyst (NSE). Standard library only, no network.

You paste the results table (text copied from the company's filing on NSE or
BSE, or from its press release) into a file; this reads the numbers and gives
the year-on-year and quarter-on-quarter changes, the margins and the flags.

    python3 results.py read results.txt --symbol RELIANCE --basis consolidated
    python3 results.py read - < results.txt                  # from standard input
    python3 results.py read results.txt --columns cur,pq,yoy # say what the columns are
    python3 results.py read results.txt --json

It reads numbers; it does not fetch anything and does not check them against the
filing. It cannot read a PDF: copy the text out of the PDF (or the table) first.
Rows it looks for: revenue from operations (or net sales), other income, total
income, total expenses, exceptional items, profit before tax, tax, net profit,
basic EPS. Units (crore, lakh, million) are read from the text; ratios do not
depend on them. Column order is taken from date headers when there are some,
else assumed: current quarter, previous quarter, same quarter last year. The
report says which, so check it against the header yourself.

Nothing here is a forecast, a rating or advice.
"""

import argparse
import datetime
import json
import re
import sys


class ResultsError(Exception):
    pass


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                      "nov", "dec"], 1)}
NUM = re.compile(r"^\(?-?[\d,]*\d(?:\.\d+)?\)?$")
DASH = {"-", "\u2013", "\u2014", "nil", "Nil", "NIL"}
UNITS = [(r"\bcrores?\b|\bcr\b|\bcrs\b", "crore", 1.0), (r"\blakhs?\b|\blacs?\b", "lakh", 0.01),
         (r"\bmillions?\b|\bmn\b", "million", 0.1), (r"\bbillions?\b|\bbn\b", "billion", 100.0)]
ROWS = [   # first matching line wins; order matters (profit before tax is checked before net profit)
    ("total_income", r"total income"),
    ("revenue", r"revenue from operations|net sales|income from operations|sales/income from operations"),
    ("other_income", r"other income"),
    ("total_expenses", r"total expenses"),
    ("exceptional", r"exceptional"),
    ("pbt", r"profit.{0,25}before.{0,40}tax|\bPBT\b"),
    ("tax", r"tax expenses?|total tax|\btax\b"),
    ("net_profit", r"net profit|profit after tax|\bPAT\b|(?:profit|loss).{0,25}for the (?:period|quarter|year)|"
                   r"net (?:profit|loss)"),
    ("eps", r"basic"),
]
LABELS = {"total_income": "Total income", "revenue": "Revenue from operations", "other_income": "Other income",
          "total_expenses": "Total expenses", "exceptional": "Exceptional items", "pbt": "Profit before tax",
          "tax": "Tax", "net_profit": "Net profit", "eps": "Basic EPS (₹)"}


def num(tok):
    """'1,234.56' -> 1234.56; '(12.30)' -> -12.3; '-' -> 0.0; None when it is not a number.
    A bracketed whole number such as '(1)' is a note reference in the label, not a loss."""
    if tok in DASH:
        return 0.0
    if not NUM.match(tok):
        return None
    neg = tok.startswith("(") or tok.startswith("-")
    core = tok.strip("()-").replace(",", "")
    if tok.startswith("(") and "." not in core and "," not in tok:
        return None
    return -float(core) if neg else float(core)


def trailing_numbers(line):
    toks = line.split()
    out = []
    while toks:
        v = num(toks[-1])
        if v is None:
            break
        out.append(v)
        toks.pop()
    out.reverse()
    return " ".join(toks), out


def parse_date(text):
    t = text.strip().strip(",").lower()
    m = re.match(r"^(\d{1,2})[-./ ](\d{1,2})[-./ ](\d{2,4})$", t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?[-./ ]([a-z]{3})[a-z]*[-./ ,]*(\d{2,4})$", t)
        if m:
            d, mo, y = int(m.group(1)), MONTHS.get(m.group(2)), int(m.group(3))
        else:
            m = re.match(r"^([a-z]{3})[a-z]*[ ]+(\d{1,2})(?:st|nd|rd|th)?,?[ ]+(\d{4})$", t)
            if not m:
                return None
            mo, d, y = MONTHS.get(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    try:
        return datetime.date(y, mo, d)
    except (TypeError, ValueError):
        return None


DATE_RX = re.compile(r"\d{1,2}[-./]\d{1,2}[-./]\d{2,4}|\d{1,2}(?:st|nd|rd|th)?[-./ ][A-Za-z]{3,9}[-./ ,]+\d{2,4}|"
                     r"[A-Za-z]{3,9} \d{1,2}(?:st|nd|rd|th)?, \d{4}")


def header_dates(text):
    """The first line with three or more dates -> [date, ...] in column order."""
    for line in text.splitlines():
        found = [parse_date(m.group(0)) for m in DATE_RX.finditer(line)]
        found = [d for d in found if d]
        if len(found) >= 3:
            return found
    return []


def unit_of(text):
    for rx, name, to_crore in UNITS:
        if re.search(rx, text, re.I):
            return name, to_crore
    return None, 1.0


def column_map(dates, n_cols, forced):
    """-> ({'cur': i, 'pq': j, 'yoy': k}, how it was decided)."""
    if forced:
        names = [x.strip() for x in forced.split(",")]
        if any(x not in ("cur", "pq", "yoy", "skip") for x in names):
            raise ResultsError('--columns wants a list from cur, pq, yoy, skip, e.g. "cur,pq,yoy,skip,skip"')
        return {n: i for i, n in enumerate(names) if n != "skip"}, "as you said with --columns"
    if len(dates) >= 3:
        pairs = list(enumerate(dates[:n_cols]))
        cur_i, cur = max(pairs, key=lambda p: p[1])
        m = {"cur": cur_i}
        for i, d in pairs:
            gap = (cur - d).days
            if 60 <= gap <= 125 and "pq" not in m:
                m["pq"] = i
            elif 330 <= gap <= 400 and "yoy" not in m:
                m["yoy"] = i
        if len(m) == 3:
            return m, "from the dates in the header"
    return {"cur": 0, "pq": 1, "yoy": 2}, ("ASSUMED: current quarter, previous quarter, same quarter last year. "
                                           "Check this against the header, or pass --columns")


def read_table(text):
    rows = {}
    for line in text.splitlines():
        label, nums = trailing_numbers(line.strip())
        if len(nums) < 2 or not label:
            continue
        for key, rx in ROWS:
            if key in rows:
                continue
            if re.search(rx, label, re.I) and not (key in ("net_profit",) and re.search(r"before", label, re.I)) \
                    and not (key == "tax" and re.search(r"before|after|profit", label, re.I)) \
                    and not (key == "revenue" and re.search(r"total income", label, re.I)):
                rows[key] = {"label": label.strip(), "values": nums}
                break
    return rows


def pct(new, old):
    if new is None or old is None or old <= 0 or new < 0:
        return None
    return round(100.0 * (new - old) / old, 1)


def analyse(text, forced=None, basis="unknown"):
    rows = read_table(text)
    if not rows or ("revenue" not in rows and "total_income" not in rows) or "net_profit" not in rows:
        raise ResultsError("could not find the revenue and net profit rows. Paste the quarterly results table "
                           "as text, one row per line with the numbers after the label. A PDF has to be copied "
                           "out as text first.")
    n_cols = max(len(r["values"]) for r in rows.values())
    cmap, how = column_map(header_dates(text), n_cols, forced)
    unit, to_crore = unit_of(text)

    def get(key, col):
        r = rows.get(key)
        if r is None or col not in cmap or cmap[col] >= len(r["values"]):
            return None
        return r["values"][cmap[col]]

    out = {"basis": basis, "unit": unit, "columns_decided": how, "rows": {k: v["label"] for k, v in rows.items()},
           "periods": {}, "flags": [], "notes": []}
    top = "revenue" if "revenue" in rows else "total_income"
    out["top_line"] = top
    for col in ("cur", "pq", "yoy"):
        p = {k: get(k, col) for k in rows}
        rev, npf, pbt = p.get(top), p.get("net_profit"), p.get("pbt")
        p["net_margin_pct"] = round(100.0 * npf / rev, 1) if rev and npf is not None else None
        p["pbt_margin_pct"] = round(100.0 * pbt / rev, 1) if rev and pbt is not None else None
        out["periods"][col] = p
    cur, pq, yoy = (out["periods"][c] for c in ("cur", "pq", "yoy"))
    ch = {}
    for name, base in (("yoy", yoy), ("qoq", pq)):
        ch[name] = {"revenue_pct": pct(cur.get(top), base.get(top)), "net_profit_pct": pct(cur.get("net_profit"),
                                                                                           base.get("net_profit")),
                    "net_margin_pp": (round(cur["net_margin_pct"] - base["net_margin_pct"], 1)
                                      if cur["net_margin_pct"] is not None and base["net_margin_pct"] is not None
                                      else None)}
    out["changes"] = ch
    f = out["flags"]
    npc, npy = cur.get("net_profit"), yoy.get("net_profit")
    if npc is not None and npc < 0:
        f.append("LOSS this quarter (net profit %s)." % fmt(npc))
    if npc is not None and npy is not None:
        if npy < 0 <= npc:
            f.append("Turnaround: profit this quarter against a loss a year ago. Growth percentages do not "
                     "apply; read what changed.")
        elif npy >= 0 > npc:
            f.append("Swing to loss: a profit a year ago, a loss now.")
    if ch["yoy"]["revenue_pct"] is not None and ch["yoy"]["revenue_pct"] < 0:
        f.append("Revenue DOWN %.1f%% on a year ago." % -ch["yoy"]["revenue_pct"])
    if ch["yoy"]["net_profit_pct"] is not None and ch["yoy"]["net_profit_pct"] < 0:
        f.append("Net profit DOWN %.1f%% on a year ago." % -ch["yoy"]["net_profit_pct"])
    if ch["qoq"]["net_profit_pct"] is not None and ch["qoq"]["net_profit_pct"] <= -20:
        f.append("Net profit down %.1f%% on the previous quarter." % -ch["qoq"]["net_profit_pct"])
    if ch["yoy"]["net_margin_pp"] is not None and ch["yoy"]["net_margin_pp"] <= -2:
        f.append("Net margin down %.1f points on a year ago." % -ch["yoy"]["net_margin_pp"])
    pbt_c = cur.get("pbt")
    if pbt_c:
        ex, oi, tx = cur.get("exceptional"), cur.get("other_income"), cur.get("tax")
        if ex and abs(ex) / abs(pbt_c) >= 0.10:
            f.append("Exceptional items are %.0f%% of profit before tax: the headline profit may not repeat."
                     % (100.0 * abs(ex) / abs(pbt_c)))
        if oi and pbt_c > 0 and oi / pbt_c >= 0.25:
            f.append("Other income is %.0f%% of profit before tax: the profit leans on non-operating income."
                     % (100.0 * oi / pbt_c))
        if tx is not None and pbt_c > 0 and not 0.10 <= tx / pbt_c <= 0.35:
            f.append("Tax is %.0f%% of profit before tax, unusual: check for a one-off tax item." % (100.0 * tx / pbt_c))
        if tx is not None and npc is not None and abs((pbt_c - tx) - npc) > max(0.02 * abs(npc), 0.05):
            out["notes"].append("Net profit does not equal profit before tax minus tax (%s against %s). That can be "
                                "minority interest or associates, or the columns are wrong: check the table."
                                % (fmt(npc), fmt(pbt_c - tx)))
    if "yoy" not in cmap or yoy.get(top) is None:
        out["notes"].append("No same-quarter-last-year column found: year-on-year figures are blank.")
    if unit is None:
        out["notes"].append("No unit (crore / lakh / million) found in the text: percentages are fine, amounts are "
                            "as pasted.")
    if basis == "unknown":
        out["notes"].append("Consolidated or standalone is not stated. They can differ a lot: say which table this is.")
    out["to_crore"] = to_crore
    return out


def fmt(x):
    return "%s" % "{:,.2f}".format(x) if x is not None else "n/a"


def render(a, symbol=None):
    L = ["Quarterly results%s (%s basis), read from the text you pasted. Nothing was fetched or checked against "
         "the filing." % (" for " + symbol if symbol else "", a["basis"]),
         "Columns: %s." % a["columns_decided"],
         "Unit: %s." % (a["unit"] or "not found"), ""]
    head = "%-26s %14s %14s %14s" % ("", "this quarter", "prev quarter", "year ago")
    L.append(head)
    for key in ("revenue", "total_income", "other_income", "total_expenses", "exceptional", "pbt", "tax",
                "net_profit", "eps"):
        if key not in a["rows"]:
            continue
        L.append("%-26s %14s %14s %14s" % (LABELS[key], *(fmt(a["periods"][c].get(key)) for c in ("cur", "pq", "yoy"))))
    for key, label in (("pbt_margin_pct", "PBT margin %"), ("net_margin_pct", "Net margin %")):
        vals = [a["periods"][c].get(key) for c in ("cur", "pq", "yoy")]
        if any(v is not None for v in vals):
            L.append("%-26s %14s %14s %14s" % (label, *("n/a" if v is None else "%.1f" % v for v in vals)))
    L.append("")

    def p(x):
        return "n/m" if x is None else "%+.1f%%" % x
    ch = a["changes"]
    L.append("Revenue: %s on a year ago, %s on the previous quarter." % (p(ch["yoy"]["revenue_pct"]),
                                                                      p(ch["qoq"]["revenue_pct"])))
    L.append("Net profit: %s on a year ago, %s on the previous quarter. (n/m: the earlier figure was zero or a loss.)"
             % (p(ch["yoy"]["net_profit_pct"]), p(ch["qoq"]["net_profit_pct"])))
    L.append("")
    L += ["FLAG: " + x for x in a["flags"]] or ["No flags from the rules above. That is not an all-clear."]
    L += ["NOTE: " + x for x in a["notes"]]
    L.append("")
    L.append("These are last quarter's numbers, already public. They say nothing about tomorrow's price. "
             "Not financial advice.")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Quarterly results reader (trader-analyst).")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("read", help="read a pasted results table")
    p.add_argument("file", help="text file, or - for standard input")
    p.add_argument("--symbol")
    p.add_argument("--basis", choices=["consolidated", "standalone", "unknown"], default="unknown")
    p.add_argument("--columns", help='what the numeric columns are, e.g. "cur,pq,yoy,skip,skip"')
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd != "read":
        ap.print_help()
        return 1
    try:
        if a.file == "-":
            text = sys.stdin.read()
        else:
            with open(a.file, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        res = analyse(text, a.columns, a.basis)
    except (OSError, ResultsError) as e:
        print("results.py: %s" % e, file=sys.stderr)
        return 1
    if a.json:
        res["symbol"] = a.symbol
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(res, a.symbol))
    return 0


if __name__ == "__main__":
    sys.exit(main())
