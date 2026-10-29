---
name: trader-scanner
description: >-
  Chart Scanner for the Indian market (NSE). Builds the stock list from NSE's
  official equity list (series EQ) limited to NIFTY 100 by default (or NIFTY
  50, NIFTY 500 or every EQ stock), runs a pre-market scan of daily bars
  (20-day closing-high breakout, bull flag, reclaimed the 20-day average,
  oversold, oversold bounce) and an intraday 15-minute opening-range breakout
  scan from 09:15 on 5-minute bars, skips stocks under ₹50 or with thin
  average volume, then writes trader/watchlist.json for trader-news and
  trader-entry. Free Yahoo data (.NS symbols), bar by bar. Use when the user
  says "scan the market", "find me setups", "what is breaking out", "opening
  range breakouts", "NIFTY 100 scan", "pre-market scan", "build my
  watchlist", "what should I look at today", or at the start of a trading
  day before /trader-news.
argument-hint: "[universe | daily | orb] [--at HH:MM]"
---

# trader-scanner

The Chart Scanner is the first step of a day trade. It reads free charts and
lists the ones that match a rule. That is all it does. A scan is a list of
charts that match a rule, not a list of stocks to buy, and a setup on a chart
is not a prediction. SEBI's own study found most individual intraday traders
in the cash segment lost money (VERIFY.md section 7); the scanner does not
change that. It only saves you from flipping through a hundred charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives). If your Claude does not
set that variable, use the path of the folder this SKILL.md is in. Run every command from your working
folder (the one that holds `trader/`), not from inside the skill folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                       # NSE EQ list x NIFTY 100 -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --index nifty50       # or nifty500, or all
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                          # before 09:15 -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,TCS,INFY
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                            # after 09:35 -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15                 # cut the clock at 10:15 India time
```

Flags: `--min-price 50` (rupees), `--min-avg-volume 500000` (shares a day,
20-session average for daily, the earlier days of the 5-day chart for orb),
`--or-minutes 15`, `--now "2026-10-30 10:15"` (India time), `--offline PATH`
to read saved Yahoo files, `--no-write`, `--show N`, `--json`. Exit code 0
when the scan found something, 1 when it found nothing or could not run (the
reason is one plain line).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line, read by `daily` and `orb` |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol` |
| `trader/config.json` | read by `orb`: `no_entries_after` (default 14:45) |

## The universe

`universe` reads NSE's list of listed equities (`EQUITY_L.csv`), keeps series
**EQ** (normal rolling settlement; BE, BZ and the rest are out), and keeps the
stocks in the chosen index from niftyindices.com's constituent CSV (NIFTY 100
by default). Neither download could be checked from the build sandbox
(VERIFY.md section 10). If one fails on your machine, save the CSV from your
browser and pass `--equity-file` / `--index-file`. The tool finds columns by
name, so a reordered file still works.

## The daily scan (before the open)

One Yahoo v8 chart per symbol (`SYMBOL.NS`, 3 months of daily bars): about
100 requests for NIFTY 100, half a minute or so with the polite pause. Run it
before 09:15 or after 15:30; during the session today's bar is left out.

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

Liquidity: the last close at least ₹50 and the 20-session average volume at
least 5,00,000 shares. Low-priced and thin stocks slip badly on a market order
and a stop. Hits are listed most traded first (price times average volume).

## The opening-range breakout scan (during the session)

The opening range is the first `--or-minutes` (15) of the session: the 09:15,
09:20 and 09:25 five-minute bars. A hit is the first completed 5-minute bar to
CLOSE above the range high, at that bar's close time (the 09:45 bar closes at
09:50). One v8 chart per symbol (range 5d), so the average volume of the
earlier days is the liquidity check. A chart is usable only when every bar
from 09:15 to the cutoff is there; a missing bar means a halt or no trades.
Before 09:35, on a weekend, a holiday or Muhurat day, or after
`no_entries_after` (14:45) it prints one plain line and writes nothing.

## Data and limits

- Yahoo's chart endpoint is free and unofficial. Its help page lists NSE as
  real time; that is not verified from your machine yet (VERIFY.md section 9).
  Check a price against your broker before you rely on it. Bars are 5-minute
  bars, never tick by tick.
- Partial results are said as partial: "Fetched 96 of 100 charts (4 failed)".
- The NSE calendar covers 2026. For 2027 the tools say the holiday list is
  unknown until you add it (VERIFY.md section 3).

## Steps

1. Once a week or so: `scan.py universe`.
2. Before the open: `scan.py daily`. Tell the user the counts (fetched, liquid,
   scanned, hits) and the top few by setup.
3. After 09:35 (10:00 to 10:15 is common): `scan.py orb`. Tell the user how many
   usable charts broke their range and when, and that a later hit is closer to
   the price now.
4. The user picks one chart. Hand it on: `/trader-news SYMBOL`, then
   `/trader-entry SYMBOL`. Never pick for them.

## Example (synthetic test data)

The tests use made-up stocks (SYNTHA to SYNTHE, `tests/fixtures/nse`) because
the build sandbox could not reach Yahoo. Friday 2026-10-30, scan at 10:15:

```
SYNTHETIC test data, not market prices.
Opening-range breakout scan, Fri 2026-10-30 10:15 India time: 15-minute range (09:15 to 09:30), 5-minute bars, Yahoo.
Fetched 5 of 5 charts; 5 usable, 2 under the price / volume floor, 3 scanned.
1 of 3 charts closed above their opening-range high by 10:15:
  09:50  SYNTHA          ₹1,416.00  range 1401.20 to 1414.80  +0.08% over
```

SYNTHC (₹42) and SYNTHE (thin) also broke out and were skipped by the floors.
README.md has the worked example on a real NSE stock once real data is captured.

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data official, and never promises that a setup works.

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
