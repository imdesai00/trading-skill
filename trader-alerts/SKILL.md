---
name: trader-alerts
description: >-
  Price Alerts and the NSE clock. Rings when a price, a stop, a target or a
  time hits. Turns the trade plan into three alerts in one go (the stop, the
  target and the flat-by time, 15:00 by default), plus above / below levels,
  percent moves from the previous close, any India time of day, and the NSE
  opening bell, each ringing once so you are not spammed. Knows NSE hours
  (09:15 to 15:30, closing-auction stocks to 15:15), the 2026 holidays and
  special sessions like Muhurat, works out India time itself, fetches free
  Yahoo quotes for .NS symbols one at a time, and writes trader/prices.json
  for sizing and the risk check. Use when the user says "alert me when",
  "tell me if RELIANCE hits", "watch my stop", "watch my trade", "remind me
  to close by 15:00", "is the market open", "when does NSE open", "get me a
  price", or right after /trader-exit writes a plan.
argument-hint: "[SYMBOL] [price]"
---

# trader-alerts

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill. In live mode only the stop rests at the
broker; the target and the flat-by exit are new orders that need trader-risk
and the user's yes, and this skill is what tells them it is time.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                          # NSE open or closed, next open
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, flat-by from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --stop 1412.00
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --target 1446.00
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:00 --label "flat by" --symbol RELIANCE
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol TCS --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                          # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE --write         # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write       # portfolio + plan symbols
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset reliance-plan-stop
```

`--now "2026-10-30 15:00"` pins the clock (India time); `--offline DIR` reads
saved `yahoo-<SYMBOL>.json` quote files instead of the network.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from trader-exit, for `add --from-plan` |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes, merged in, for trader-budget and trader-risk |
| `trader/portfolio.json` | the symbols held, for `quote --holdings` |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the India clock passed `at` ("15:00") on a trading day | once per trading day |
| `opening_bell` | NSE is open | once per trading day |

A rule with `"date"` is watched on that India date only; `--from-plan` rules
carry the plan's date, so yesterday's stop never rings today.

## Quotes

- Yahoo's chart JSON for `SYMBOL.NS` (`query2`, then `query1` on a 429 or a
  5xx), one request per symbol, half a second apart. Free and unofficial.
  Yahoo's help page lists NSE as real time; check it against your broker on
  your machine (VERIFY.md section 9). Each price keeps its quote time, and
  trader-risk only uses one under 15 minutes old.
- If Yahoo is down, read the price from Kite (live mode, `get_ltp`, read only)
  and write it into `trader/prices.json` as `{"as_of": "...Z", "prices": {"RELIANCE": 1420.5}}`.

## The NSE clock

Normal market 09:15 to 15:30 India time (UTC+05:30, no daylight saving).
Stocks in NSE's closing auction (the F&O stocks, so most of NIFTY 100) stop
continuous trading at 15:15. Closed on weekends and on the 2026 trading
holidays (NSE circular NSE/CMTR/71775 plus public calendars, VERIFY.md
section 3); the ones left this year are Nov 10, Nov 24 and Dec 25. Special
sessions such as Muhurat trading (Sunday 2026-11-08) are treated as closed:
this pack does not trade them. For 2027 the clock says the holiday list is
unknown until you add it.

## Steps

1. Right after trader-exit writes the plan, run `add --from-plan`. Confirm the
   three alerts back to the user: the stop, the target and the flat-by time.
2. Run `check` while the trade is open: every few minutes from a scheduled
   task or `/loop`. A check reads the latest quote; a price can pass a level
   between two checks.
3. Tell the user what rang in one line each. In paper mode the end-of-day
   reconcile decides what really happened from the bars. In live mode a stop
   alert means the stop order has probably filled (check positions, read
   only); a target or flat-by alert means: build the exit with trader-broker
   flatten, then trader-risk, then the user's yes. Never act for them.

## Example (synthetic test data)

SYNTHA, Friday 2026-10-30: stop 1,408.00, target 1,440.00, flat by 15:00.
A check at 10:10 with SYNTHA at 1,407.90:

```
NSE OPEN (2026-10-30 10:10 India time, normal market).
ALERT SYNTHA is ₹1,407.90: at or below your stop ₹1,408.00 (plan stop: opening-range midpoint).
```

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a free quote as the broker's price.
- Confirm every new rule back to the user: symbol, type, level, day.

## What it never does

It never places, cancels or changes an order, and never predicts where a
price is going.

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
