---
name: trader-insider
description: >-
  Insider Tracker for NSE companies. Reads the insider-trading disclosures
  (SEBI PIT rules) that promoters, directors and key staff of listed companies
  must file, from a CSV the user exports from the NSE site (reliable) or the
  NSE insider-trading RSS (best effort). Alerts once per filing for stocks on
  the watchlist, plan or portfolio, and shows for each: who, their category,
  bought or sold, shares and rupee value, the mode (open-market purchase vs
  ESOP, pledge, off-market), the trade date, the publication date and how many
  trading days it took to publish, plus net open-market buying or selling over
  the window. Honest that disclosures are public, late and not a signal. Use
  when the user says "insider trades", "promoter buying", "did the promoter
  sell", "any insider activity on my stocks", "SEBI PIT disclosures", or
  "check insider filings".
argument-hint: "[import FILE.csv | check | feed]"
---

# trader-insider

Promoters, directors, key managerial staff and designated employees of a listed company must disclose
their trades in its shares, and NSE publishes the disclosures. This skill reads them for your symbols
and tells you which are new, how late they are, and what kind of trade each was.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives). If your Claude does not
set that variable, use the path of the folder this SKILL.md is in. Run every command from your working
folder (the one that holds `trader/`), not from inside the skill folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" import nse_insider_export.csv     # a CSV the user exported
python3 "${CLAUDE_SKILL_DIR}/insider.py" check                             # new filings on watchlist / plan / holdings
python3 "${CLAUDE_SKILL_DIR}/insider.py" check --symbols RELIANCE --days 60 --all
python3 "${CLAUDE_SKILL_DIR}/insider.py" feed                              # NSE RSS notices, best effort
python3 "${CLAUDE_SKILL_DIR}/insider.py" status
```

## How to use it

1. **Data.** NSE's website APIs need browser cookies and are not used. Ask the user to download the insider
   trading CSV themselves from the NSE site (Corporate Filings > Insider Trading), save it in their working
   folder, then `insider.py import FILE`. Columns are found by name. If one is missing the tool names it and
   prints the columns it did see: show that to the user, do not guess a mapping.
2. `insider.py check` alerts once per filing (new ones only; `--all` repeats). Read each line to the user,
   including the publication gap and how many days old the trade is.
3. `insider.py feed` reads NSE's insider RSS (the URL is unverified, VERIFY.md section 10). It gives a notice and a
   link, not numbers. If it fails, say so and use the CSV route.

## The rules

- **Public and late.** The rows show the trade date, the publication date and the gap. A gap above about
  two trading days is flagged as late. The user is always reading it days after the trade.
- **Not a signal.** An open-market purchase is the more deliberate act. A sale can be for tax, a loan, an
  ESOP or personal money. ESOP allotments, pledges, gifts and off-market deals are shown as what they are
  and are not counted as buying or selling in the net figure. Never say a stock will rise or fall because of one.
- **Not inside information.** Reading a public filing is not trading on a tip, but it gives no edge either.
- It flags stocks on the watchlist, plan or portfolio as "on your watchlist / plan / held". It does not
  change any limit and is not read by `/trader-risk`.

## What it never does

It never places an order, never scrapes NSE's website, never asks for a key, never changes a limit.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading is the default (`"mode": "paper"`): no broker is contacted, ever. Live mode needs
  `"mode": "live"` AND `"kite_enabled": true`, both set by you, plus a Kite connection verified today.
  The tools never change those settings.
- Every order, paper or live, needs a trader-risk PASS on that exact order and your typed "yes" for
  that one order. A live order also needs Claude's own "Allow once" card, with every Kite order tool set
  to "Needs approval". Never "yes to all", never two orders on one yes, never an order from a scheduled
  or background task.
- NSE cash equity, intraday (MIS), longs only. No F&O, no margin or leverage, no short selling.
- Kite keys and access tokens live in environment variables or config you edit yourself, never in the chat.
