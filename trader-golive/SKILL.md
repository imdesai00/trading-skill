---
name: trader-golive
description: >-
  Go-live readiness for the NSE paper-trading trial. After four weeks it reads
  the paper record and reports honestly: number of trades (with a warning
  under 40), net P&L after charges, win rate, average R, expectancy per
  trade, deepest drawdown, longest losing streak, how often the limits were
  hit or a rule was broken, how much the charges took, how the skipped
  signals compare, and whether the result can be told apart from luck at this
  sample size. It never decides for you and never switches a mode. A second
  command lists the exact steps you take yourself to go live with Zerodha
  Kite: connect it, set every order tool to Needs approval, re-verify the
  open questions, set mode and kite_enabled, start at half the paper risk
  (₹250 a trade) for two weeks, and keep logging in Notion. Use when the user
  says "am I ready to go live", "review my paper trading", "how did the four
  weeks go", "is this luck", "go live", "connect Kite", or after the paper
  period.
argument-hint: "[check | steps]"
---

# trader-golive

This skill answers one question honestly: what does the paper record actually
show? It does not answer "should I go live". That is the user's decision. It
reads the record, says what is and is not distinguishable from luck at this
sample size, and, if the user decides to go on, lists what going live involves.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives). If your Claude does not
set that variable, use the path of the folder this SKILL.md is in. Run every command from your working
folder (the one that holds `trader/`), not from inside the skill folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/golive.py" check                    # the report on trader/state/paper.json
python3 "${CLAUDE_SKILL_DIR}/golive.py" check --json
python3 "${CLAUDE_SKILL_DIR}/golive.py" steps                    # what YOU do to go live
```

If the local files were lost, rebuild the paper log from Notion first
(trader-journal, "rebuild"); `check` says when the record was rebuilt.

## What `check` reports

1. **Trades**: how many closed, over how many days, and a warning if fewer than
   your minimum of 40.
2. **Net P&L after charges**, and the balance against ₹1,00,000.
3. **Charges**: total paid, and what share of the gross result they took.
4. **Win rate** with its 95 % range, **average R**, and the win rate you need to
   break even at your average win and loss.
5. **Expectancy** per trade after charges.
6. **Deepest drawdown**, and the most below your start.
7. **Longest losing streak.**
8. **Limits hit** (days at the daily loss limit, weeks at the weekly limit, days
   at the 3-trade cap) and **rules broken** (a loss beyond the per-trade limit).
9. **Skipped signals**: what they would have netted against the trades you took.
10. **Luck test**: resamples your own trades 10,000 times (seed 19, so the same
    record gives the same answer) and shows the range the true average result
    per trade could plausibly be in. If the range includes zero, the result
    cannot be told apart from luck. With a few dozen trades it almost always does.

## How to present it

- Read the report to the user as printed. Do not add adjectives, do not call it
  good or bad, do not say "you should" or "you shouldn't".
- Say the limits of the paper record in plain words once: real fills are worse,
  a few weeks is one market mood, and the report only sees the trades taken.
- If the trade count is under 40, say so first.
- If the range includes zero, say that this means the result could be chance,
  and that this is not the same as saying the method does not work.
- Then ask whether they want the steps. Do not push.

## `steps`

Prints, in order: decide for yourself; connect Kite (the hosted server probably
cannot place orders, so a self-hosted server with your own API key may be
needed; keys only in environment variables or the server's own config, never in
the chat); set every order tool (`place_order`, `modify_order`, `cancel_order`
and the three GTT tools) to Needs approval; re-verify the open questions in
VERIFY.md; edit `trader/config.json` yourself to `"mode": "live"`,
`"kite_enabled": true` and `"max_loss_per_trade_inr": 250` for the first two
weeks; the daily routine live (a login each day, three orders per trade, each
with its own risk check, typed yes and Allow once card); keep logging in Notion
with Mode = live; and only after two weeks consider the full risk again.

## What it never does

It never decides, never recommends going live, never edits `trader/config.json`,
never sets `mode` or `kite_enabled`, never connects to Kite, and never asks for
a key, secret or token.

## Added (database edition)

`golive.py check` reads the database when there is one (`--mode paper` by default; `--mode live` reports the live record
on its own). Paper and live are never added together. The first 2 live weeks run at 50% of normal risk automatically.

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
