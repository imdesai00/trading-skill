---
name: trader-risk
description: >-
  Risk Manager for NSE intraday. Checks every order against your limits and
  stops anything over them, in paper and live mode alike: the loss at the
  stop with charges within ₹500, reward:risk after charges at least 1.5,
  charges no more than 25 % of the target profit, the position within
  ₹75,000 and the cash buffer, no leverage, no shorts, at most 3 trades a day
  and 1 open position, the ₹1,500 daily and ₹4,000 weekly loss limits
  (realised paper P&L plus open risk), the ₹8,000 drawdown pause, the 09:30
  to 14:45 entry window and NSE holidays, and live mode only with Kite
  enabled and verified today. Getting out is never blocked by a day limit.
  Prints PASS or BLOCK with a plain reason and records the verdicts so
  trader-broker only shows an order that passed. Use when the user says
  "check my order", "is this safe", "risk check", "can I take this trade",
  "how much have I lost today", or before /trader-broker, always.
argument-hint: "[path to orders.json]"
---

# trader-risk

The Risk Manager is the gate between a proposal and the broker, paper or
live. A BLOCK is final for that order: it is not shown to you for a yes. These
limits keep each loss and each day's losses small. They do not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                           # trader/ files; writes trader/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write                # look without recording
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-30 09:51"  # pin the clock (India time)
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget, or trader-broker's flatten) |
| `trader/config.json` | your limits and the mode |
| `trader/state/paper.json` (paper) or `trader/state/live.json` (live) | the account ledger: balance, open trades, closed trades with net P&L |
| `trader/prices.json` | a fresh price raises the entry it measures risk from |
| `trader/state/kite.json` | live mode: today's Kite connection check (trader-broker kite.py) |
| `trader/state/risk.json` | the verdicts it writes, with the date, the mode and a fingerprint of each exact order |

## Entries and exits

An **entry** opens a position (a buy). An **exit** only sells what you hold.
Getting out must always be possible, so an exit is checked for format and
short selling only; every other rule turns into a warning for an exit.

## The checks

| check | blocks when |
| --- | --- |
| format | not an NSE symbol, side not buy or sell, not whole shares, exchange not NSE, product not MIS, entry_type not market or limit |
| mode | `"mode": "live"` without `"kite_enabled": true`, or without a Kite check from today |
| short | a sell of more than you hold |
| pause | (entries) the account is ₹8,000 or more below capital: stop and review |
| daily | (entries) today's realised loss + open risk + this trade's loss at the stop over ₹1,500, or already at it |
| weekly | (entries) the same from Monday over ₹4,000: no new entries until next Monday |
| trades / open | (entries) 3 entries already today / 1 position already open |
| stop / loss | (entries) no stop or a stop not below the entry / the loss at the stop with charges over ₹500 |
| reward | (entries) reward:risk after charges under 1.5 |
| charges | (entries) charges over 25 % of the gross profit at the target |
| position / cash | (entries) over ₹75,000 / into the 10 % cash buffer, or needing margin |
| window | (entries) before 09:30 or after 14:45 India time, or NSE closed (weekend, holiday, Muhurat, a year without a holiday list) |
| blocked / duplicate | (entries) a symbol on your blocked list / the same order twice in one batch |

How the numbers work:

- **The entry it measures from**: the limit price for a limit order, else the
  HIGHER of the plan's entry and a fresh price (under 15 minutes old).
- **Losses count after charges.** Realised P&L is the ledger's net P&L (after
  charges and slippage). A winning trade earlier in the day does not widen the
  daily limit: only losses count against it.
- **Open risk** is the loss at the stop of every open trade.
- **The pause** is not a timer. While the account is ₹8,000 or more under
  capital, nothing new is allowed. Review the record; changing a limit to go on
  is your decision, made in your config.
- **Live mode** is refused unless you set `"mode": "live"` and
  `"kite_enabled": true` yourself AND trader-broker checked the Kite
  connection today (an access token lasts one day, VERIFY.md section 2).
- **A verdict binds to the day and the mode.** A PASS from paper mode does not
  make an order ready in live mode; trader-broker asks for a fresh check.

## Steps

1. If the price may have moved since the plan, refresh it first
   (`alerts.py quote SYMBOL --write`).
2. Run `check.py`. Exit code 0 means every order passed, 1 means one was blocked.
3. Tell the user the result in plain words: the loss at the stop with charges,
   reward:risk after charges, and how much of today's and this week's limits it
   uses. For a BLOCK, give the reason and the fix (fewer shares, a better plan,
   wait for the window, or skip the trade). Never change a limit yourself.
4. If the order changes in any way, run the check again.

## Example (synthetic test data)

```
PASS  #1 buy SYNTHA 45 shares, entry
          entry 1416.00 (the plan's entry), stop 1408.00, target 1440.00: 45 shares, ₹63,720.00 order value
          loss at the stop ₹490.88 incl. charges and slippage (limit ₹500); reward:risk after charges 1.93 (min 1.5)
          today before this: 0 of 3 trades, ₹0.00 of ₹1,500 daily; this week ₹0.00 of ₹4,000
```

46 shares instead: BLOCK, the loss at the stop is over ₹500. The same order at
09:20: BLOCK, no new entries before 09:30. With the paper account at ₹92,000:
BLOCK, the drawdown pause is on.

## What it never does

It never places an order, never edits an order to make it pass, never loosens
a limit, never blocks a sell that only gets you out, and never treats a PASS
as your yes.

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
