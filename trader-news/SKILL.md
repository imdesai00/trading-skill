---
name: trader-news
description: >-
  News Monitor for NSE stocks. Tracks news on every stock you watch: Yahoo
  Finance headlines per symbol (.NS), NSE corporate announcements, and
  Moneycontrol section feeds, matched to your symbols by symbol or company
  name. Shows only what is new since the last check, newest first, in India
  time (IST), tags each item (results, board meeting, analyst, deal, capital,
  people, legal, risk, surveillance, regulatory, macro), shows how far each
  stock is from yesterday's close, and warns loudly about any company
  reporting results today. Weak name matches are shown as POSSIBLE, never as
  facts. Use when the user says "any news on RELIANCE", "why is this moving",
  "what's the news on my watchlist", "morning briefing", "pre-market news",
  "check news before the open", "are results due today", "did anything break",
  or after /trader-scanner finds setups.
argument-hint: "[symbols]"
---

# trader-news

A chart can look perfect and still be about to gap on a results announcement, a
block deal or a surveillance (ASM/GSM) listing. Before a setup becomes a trade, this skill reads
the free public feeds that carry those first, for every stock on the watchlist, and tells you only
what is new. It also prints how far each stock has moved today.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives). If your Claude does not
set that variable, use the path of the folder this SKILL.md is in. Run every command from your working
folder (the one that holds `trader/`), not from inside the skill folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                              # watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/news.py" --symbols RELIANCE TCS --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --names trader/state/EQUITY_L.csv --json
```

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--symbols`, else `trader/watchlist.json` | the scanner's hits (trader-scanner) |
| `trader/plan.json`, `trader/portfolio.json` | the trade you are planning, what you hold |
| Yahoo Finance headline RSS | one request per symbol, `RELIANCE.NS` |
| NSE announcements RSS | market-wide: board meetings, results, dividends, directors |
| Moneycontrol RSS sections | market-wide; `latestnews`, `business`, `marketreports`, `results`, `buzzingstocks` |
| Yahoo v8 chart | the last price against yesterday's close (unofficial, can lag) |
| `trader/config.json` | optional `"news_sources": {"yahoo": true, "nse": true, "moneycontrol": true}` and `"moneycontrol_sections": [...]` to switch feeds off or change sections |
| `trader/state/news.json` | what has been shown (kept 30 days) |

Groww has no public news feed, so it is not read (VERIFY.md section 10). The NSE and Moneycontrol feed
URLs are from secondary sources and could not be fetched when this was built: if a feed fails on your
machine the tool prints a `note:` line and the other feeds still run. Tell the user which one failed.

## The rules

- **Headlines are leads, not facts.** Before telling the user something happened, open the story or
  the announcement and read it. Quote the source and link it. Never summarise a paywalled story you
  could not read: say so.
- **RESULTS TODAY is a stop sign for new entries.** A company reporting results today can gap either way.
  Say so first, and tell the user this pack does not trade into results. It is a warning, not a block:
  `/trader-risk` does not read it, so the user has to act on it.
- **POSSIBLE is not a match.** Market-wide feeds are matched to symbols by the symbol written as a word
  (names of 4 letters or more), `(SYMBOL)`, `NSE: SYMBOL`, or the company's name. An item that only shares
  the first distinctive word of a company's name is listed as POSSIBLE: read it before believing it. A
  short symbol such as ITC only matches in brackets or by its full company name.
- **Names.** Give `--names` the `EQUITY_L.csv` the scanner downloaded, or the watchlist's names are used.
  Without names only symbols match, and the tool says so.
- **MOVING** marks a stock 3 % or more from yesterday's close (`--move-pct`). A move with no headline is
  worth saying out loud too.
- **No predictions.** Report what was published and how far the price is from yesterday's close.

## What it never does

It never places an order, never changes a limit, never asks for a key (no feed here needs one), and
never reads a web page that is not a public feed.

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
