---
name: trader-exit
description: >-
  Exit Planner for NSE intraday. Sets your stop and your target before you
  enter, so you know when to get out. Reads the entry from trader-entry (or a
  price you give), puts the stop at the opening-range midpoint by default (or
  the range low, your price, a percent, or a multiple of the ATR), rounds it
  DOWN onto the NSE tick grid (away from the entry), and sets the target at
  2R (or 3R, 1.5R, or your price), rounded down to the tick too. Refuses a
  stop at or above the entry, a target at or below it, a stop under two
  ticks or 0.05 % away, an entry off the tick grid, and a flat-by time at or
  after 15:15; flags one too close to Zerodha's 15:12 square-off. Writes
  trader/plan.json for the Time Machine, the Budget Manager and Price Alerts.
  Long side only. Use when the user says "where's my stop", "set my stop and
  target", "plan the exit", "stop loss", "what's my R", "risk reward", or
  right after /trader-entry triggers, before /trader-timemachine.
argument-hint: "[--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 2R|3R|price:X]"
---

# trader-exit

The Exit Planner decides how a trade ends before it starts. The stop is where
the setup has failed. The target is where you take what you came for. Both
are set BEFORE the entry, written down and handed on, so nobody has to decide
anything in the middle of a fast move. A stop is a plan, not a guarantee: a
gap or a fast market can fill past it.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives). If your Claude does not
set that variable, use the path of the folder this SKILL.md is in. Run every command from your working
folder (the one that holds `trader/`), not from inside the skill folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                          # trader/entry.json: midpoint stop, 2R target
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 3R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5           # needs the session's bars (Yahoo or --offline)
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol RELIANCE --entry 1420.50 --or-high 1418 --or-low 1405
python3 "${CLAUDE_SKILL_DIR}/exit.py" show                          # the current trader/plan.json
```

Other flags: `--flat-by 15:00`, `--tick 0.05` (the symbol's own tick from
Kite, once connected), `--json`, `--now "2026-10-30 09:51"` (India time),
`--entry PRICE` with a waiting entry.json to plan ahead, `--setup level`,
`--offline FILE` (a saved Yahoo chart JSON for an ATR stop).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | the trigger from trader-entry: symbol, entry price, time, bar, opening range |
| `trader/config.json` | `flat_by` (15:00) and `min_reward_risk` (1.5: a warning here, trader-risk enforces it after charges) |
| Yahoo chart API | only for an `atr:K` stop: the session's bars up to the entry bar |
| `trader/plan.json` | with `--write`: entry, stop and its rule, target and its R, risk a share, reward:risk, tick, flat by, product MIS |

## Stops and targets

| stop | where it goes |
| --- | --- |
| `or-mid` | the opening-range midpoint (the default for an opening-range breakout) |
| `or-low` | the opening-range low: wider, so fewer shares for the same max loss |
| `price:X` | your price |
| `pct:X` | X percent under the entry |
| `atr:K` | K times the 14-bar ATR under the entry, on the session's bars up to the entry bar |

- **Tick sizes.** NSE prices move in ticks set by price band (VERIFY.md
  section 3): ₹0.01 under ₹250, ₹0.05 to ₹1,000, ₹0.10 to ₹5,000, ₹0.50 to
  ₹10,000, ₹1 to ₹20,000, ₹5 above. NSE reviews each stock's tick monthly, so
  once Kite is connected pass the symbol's own tick with `--tick`.
- **Rounding.** The stop rounds DOWN to the tick, away from the entry. The
  target also rounds DOWN, toward the entry, so the reward is never overstated.
- **Refused, with the reason.** A stop at or above the entry. A target at or
  below it. A risk under two ticks or under 0.05 % of the price. An entry
  that is not on the tick grid. An entry.json that has not triggered (unless
  you give `--entry`). A short.
- **Charges are not in reward:risk here.** A 2R plan before charges can be
  about 1.2R after slippage and charges on a ₹8 risk. trader-budget and
  trader-risk measure it after charges, against your 1.5 minimum, so a 3R
  target is often what it takes.
- **Flat by.** 15:00 by default (your config). Out by then, win or lose, no
  overnight hold (MIS). A flat-by at or after 15:15 is refused: closing-auction
  stocks stop continuous trading then. A flat-by after 15:10 is flagged: Zerodha
  squares off MIS positions in those stocks at 15:12 (VERIFY.md section 4).

## Steps

1. Run it right after `/trader-entry` triggers.
2. Use the default stop for an opening-range breakout unless the user names
   another. A level setup needs one: ask.
3. Run `exit.py plan --write`. Read back the entry, the stop and its rule,
   the target and its rule, the risk a share, reward:risk, the tick and flat by.
4. If it refuses, say the reason and the fix. Never loosen anything on your own.
5. Say once that the stop is a plan, not a guarantee.
6. Hand off to `/trader-timemachine`, then `/trader-budget`. Offer
   `/trader-alerts` for the stop, the target and the flat-by time.

## Example (synthetic test data)

SYNTHA, Friday 2026-10-30: entry 1,416.00 at 09:50 (bar 6), opening range
1,401.20 to 1,414.80. `exit.py plan --target 3R --write`:

```
Exit plan: SYNTHA long, Fri 2026-10-30, opening-range breakout on 5-minute bars
  Entry         ₹1,416.00 at 09:50 (bar 6)
  Stop          ₹1,408.00  opening-range midpoint: (₹1,414.80 + ₹1,401.20) / 2 = ₹1408
  Target        ₹1,440.00  3R (three times the risk)
  Risk a share  ₹8.00 (0.56% of the entry)
  Reward:risk   3.0
  Tick          ₹0.10 (NSE price band)
  Flat by       15:00 IST, win or lose: no overnight hold (MIS)
```

## Rules

- The stop and the target are set before the entry, never after.
- Never move a stop further from the entry once the trade is on.
- Plain numbers only. It never prints a P&L or a return.
- A target is a plan, not a prediction that the price gets there.

## What it never does

It never places an order, never sizes a position, never widens a stop to
make a plan pass, and never calls a stop a guarantee.

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
