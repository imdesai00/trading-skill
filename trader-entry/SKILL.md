---
name: trader-entry
description: >-
  Entry Timer for NSE intraday. Watches a setup bar by bar and tells you the
  moment to get in. Reads free Yahoo 1-minute or 5-minute bars for an .NS
  stock, builds the opening range (the first 15 minutes from 09:15 by
  default) and calls the entry on the first completed bar that CLOSES above
  the range high, or above a level you give it (like a 20-day high from the
  scanner), with an optional volume check. Says waiting and how far,
  triggered with the time and the price, expired after 14:45, or not open
  yet, in India time, and writes trader/entry.json for /trader-exit. Long
  side only. It never places an order. Use when the user says "tell me when
  to get in", "watch RELIANCE", "has it broken out", "did it break the
  opening range", "entry signal", "time my entry", "check my watchlist", or
  after /trader-scanner and /trader-news, before /trader-exit.
argument-hint: "[SYMBOL] [--setup orb|level] [--interval 5m|1m]"
---

# trader-entry

The Entry Timer turns a setup into one moment: the bar that confirms it. It
reads bars, 1-minute or 5-minute, and waits for each one to finish. A price
that pokes above the high in the middle of a bar is not an entry. A bar that
CLOSES above it is. Then it says the time and the price, writes them down for
the Exit Planner, and stops. It never buys anything. Most intraday traders
lose money, and a clean signal does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives). If your Claude does not
set that variable, use the path of the folder this SKILL.md is in. Run every command from your working
folder (the one that holds `trader/`), not from inside the skill folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE                  # 15-minute opening range, 5-minute bars
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --write          # also save trader/entry.json
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --setup level --level 1431.20
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --interval 1m --min-volume-x 1.5
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch RELIANCE --until 14:45    # one line per closed bar, stops on the trigger
python3 "${CLAUDE_SKILL_DIR}/entry.py" watchlist                       # every hit in trader/watchlist.json
```

Other flags: `--or-minutes N` (default 15), `--json`, `--offline FILE` (a
saved Yahoo chart JSON, `.json` or `.json.gz`, or a folder of
`<SYMBOL>.json`), `--now "2026-10-30 10:15"` (India time) to replay a day.
`watch` with `--now` or `--offline` replays the day bar by bar without waiting.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/watchlist.json` | the scanner's hits (`hits[].symbol`), for `watchlist` |
| `trader/config.json` | `no_entries_before` (09:30) and `no_entries_after` (14:45), India time. Optional |
| Yahoo chart API | today's bars for `SYMBOL.NS`, `range=1d&interval=5m` (or `1m`): free and unofficial |
| `trader/entry.json` | with `check --write`, or when `watch` triggers: symbol, setup, status, time, bar, price, level, opening range |

## The rule

- **Opening range (orb).** The high and low of the first 15 minutes: the
  09:15, 09:20 and 09:25 five-minute bars, or fifteen 1-minute bars. The level
  to beat is the range high.
- **Level.** A price you give, like a 20-day high from `/trader-scanner`.
- **The trigger** is the first completed bar after the range that CLOSES
  above the level. A wick above is not a trigger. A close exactly at the
  level is not above it.
- **The time** is that bar's close time (the 09:45 bar closes at 09:50). The
  price is its close.
- **Only closed bars count.** A bar is closed when the clock passes its start
  plus 5 (or 1) minutes and Yahoo has caught up (it waits a minute at most).
- **Volume, if you want it.** `--min-volume-x 1.5` also needs the trigger
  bar's volume at 1.5 times the range bars' average. The 09:15 bar carries the
  pre-open auction's volume, so 1.0 is already strict.
- **Time limits.** No entries before 09:30 or after 14:45 (your config).
- **What it says.** Waiting (and how far the last close is from the level),
  triggered, expired, or no data. Before 09:15: not open yet. Before 09:30:
  the range is still forming. Weekends, NSE holidays and Muhurat: closed.

## Steps

1. Get the symbol from the user, or run `entry.py watchlist` after
   `/trader-scanner`. Check `/trader-news` for it first (results today?).
2. Run `entry.py check SYMBOL`. Tell the user the status in one line.
3. To wait for it, Claude can run `entry.py watch SYMBOL --until 14:45` in the
   background, or `entry.py check SYMBOL --write` on a `/loop` every 5
   minutes. Keep the computer awake: a sleeping laptop pauses it, and it
   catches up, late, when it wakes.
4. When it triggers, say the time and the price, and that nothing was placed.
   If the trigger is more than a bar old, say that too: a late entry is a
   different trade.
5. Hand off to `/trader-exit` to set the stop and the target BEFORE anything
   is bought. A background watch never goes further than this: nobody is
   there to say yes.

## Example (synthetic test data)

SYNTHA (a made-up stock in `tests/fixtures/nse`), Friday 2026-10-30, 5-minute
bars. The opening range is 1,401.20 to 1,414.80. At 09:48 the 09:45 bar is
still open, so it waits:

```
WAITING  last close ₹1,413.80 (09:40 bar), ₹1.00 under the range high ₹1,414.80 (-0.07%).
The 09:45 bar is still forming (₹1,416.00 so far, closes 09:50); it does not count until it closes.
```

At 09:50 the bar has closed:

```
TRIGGERED 09:50 at ₹1,416.00: the 09:45 bar (bar 6) closed above the range high ₹1,414.80 (+0.08%).
This is the moment to get in, if you take the trade. Nothing was placed.
```

## Rules

- Bar by bar, on free Yahoo bars. They are unofficial; say so when a minute matters.
- A signal is information, not an order, and not a promise.
- Long side only: shorts are off in your rules.
- One trigger per setup per day: the first close above.
- Never call an entry early because a bar looks like it will close above.

## What it never does

It never places an order, never sizes a position, never sets the stop (that
is `/trader-exit`), never moves the level after the fact, and never counts a
bar that has not closed.

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
