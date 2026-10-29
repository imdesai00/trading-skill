---
name: trader-timemachine
description: >-
  Time Machine for NSE intraday. Replays a planned trade before you risk a
  rupee: 10,000 simulated sessions plus every past time the setup fired.
  Takes the plan from trader-exit (entry, stop, target), rebuilds the rest
  of the day 10,000 times from the stock's own 5-minute bars over the last
  60 days (full NSE sessions, 09:15 to 15:15), and re-runs the same rule on
  every past session. Prints the odds of target first, stop first, or
  neither by your flat-by time, where the price sits then, the average R,
  the history, one honest verdict and its limits (independent bar draws, a
  small sample, not a prediction), then hands off to /trader-budget. Use
  when the user says "run the time machine", "replay this trade", "what are
  the odds", "simulate this trade", "monte carlo", "backtest this setup",
  "is this a coin flip", or after /trader-exit writes a plan.
argument-hint: "[path to plan.json, or SYMBOL]"
---

# trader-timemachine

The Time Machine sits between the plan and the money. trader-exit has set
the entry, the stop and the target. Before anything is sized, this skill asks
one question: if today plays out like the stock's own last 60 days, how often
does this plan hit the target first, the stop first, or neither by the time
you must be out? It answers with 10,000 replays and with the real record of
the same rule on every past session.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives). If your Claude does not
set that variable, use the path of the folder this SKILL.md is in. Run every command from your working
folder (the one that holds `trader/`), not from inside the skill folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run                    # trader/plan.json, 10,000 replays, seed 19
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --write            # also save trader/timemachine.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:00      # default: the plan's flat_by
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol RELIANCE --date 2026-11-02 \
    --entry 1420.50 --stop 1412.00 --target 1446.00 --entry-time 09:50
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" chart --from           # ASCII fan chart of the saved run
```

Other flags: `--replays N`, `--seed N` (default 19), `--window N` (default 2
bars), `--offline FILE` (a saved Yahoo chart JSON or a folder), `--now
"2026-10-30 10:15"` (India time).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the plan from trader-exit: symbol, date, entry, entry_time / entry_bar, stop, target, tick, flat_by, opening range |
| Yahoo chart API | one request: 60 days of 5-minute bars for `SYMBOL.NS` (`range=60d&interval=5m`), free and unofficial |
| `trader/timemachine.json` | with `--write`: `{plan, mc, history}` plus the verdict. trader-broker records the odds with each paper trade |

## How it works

1. **The pool.** It keeps only FULL sessions before the plan's date: all 72
   five-minute bars from 09:15 to 15:15 India time. After 15:15 the stocks in
   NSE's closing auction stop continuous trading (VERIFY.md section 3), so
   those bars are left out. Every bar becomes four ratios to the bar before it.
2. **One replay.** Start at the entry price. For each bar left until your
   flat-by time, pick a past bar from the same time of day (plus or minus 2
   bars) at random, and scale it to the current price. Bar by bar, it checks
   the stop first, then the target. A bar that touches both counts as the stop.
3. **10,000 replays** give the odds: target first, stop first, neither (still
   open at the flat-by time), and green.
4. **The history.** The plan's own rule (the same opening range, stop rule and
   target R, prices on the tick grid) runs again on every past session.
5. **The verdict** is one line from the numbers, weighing the target's R:
   break-even is 1 in (1 + R) of the replays that hit a side.

## Its limits, said plainly every time

- **Independent bar draws.** Each replayed bar is drawn on its own. Trends,
  momentum, news and the way one bar leads to the next are not in it.
- **A small sample.** About 40 to 60 past sessions. A handful of odd days
  moves the odds.
- **Not a prediction.** Results day, a block deal, an index rebalance or a
  halt can do what those sessions never did.
- **No charges in R.** The R numbers here are before charges and slippage;
  trader-budget and trader-risk count those.

## Steps

1. Make sure `trader/plan.json` is today's plan from `/trader-exit`.
2. Run `timemachine.py run --write`. The replays end at the plan's flat-by
   time (15:00), so "neither" means still open when you would get out.
3. Tell the user, in this order: the four odds, the median and the 5 to 95
   range of the price at the flat-by time, the average R, the history line,
   then the verdict line and any line under it exactly as printed. Say the seed.
4. If the verdict is a coin flip or close to break-even, say so plainly.
5. Say the limits once, in plain words. Check `/trader-news` before going on.
6. If the user still wants the trade, hand off to `/trader-budget`.

## Example (synthetic test data)

SYNTHA plan from trader-exit (entry 1,416.00 at 09:50, stop 1,408.00, target
1,440.00, 3R), on the made-up stock's 59 prior sessions:

```
SYNTHETIC test data, not market prices.
10,000 replays of the rest of the session, 09:50 to 15:00 (62 five-minute bars), seed 19.
  target first, stop first, neither, green ... (see the run)
Close to break-even: at 3R the target has to come first in 25 % of the replays that hit one side ...
```

Synthetic numbers mean nothing about real stocks; they only prove the tool works.

## Rules

- Every number comes from the run. Never round a coin flip up into an edge.
- Repeat the verdict and the lines under it as printed.
- Simulated odds are not predictions and not promises.
- Always say the seed, so any run can be repeated.

## What it never does

It never places an order, never sizes a position, never changes the plan,
never calls a result a sure thing, and never mixes today's bars into the pool.

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
