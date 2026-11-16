---
name: trader-control
description: >-
  Capital, limits, pause, kill switch and the morning check. "change capital to
  ₹X" shows old against new limits (all stored as a percent of capital) and
  waits for the user's typed yes; any other limit change works the same way and
  is recorded in config_history; hand edits of trader/config.json are noticed
  and logged. "pause trading until DATE" / "resume trading" block new orders in
  both modes; the kill switch (a file trader/STOP, or the user saying "kill
  switch") blocks every new order at once and, in live mode, lists open
  positions and offers to flatten them (each needing its own yes). The morning
  check prints market open or closed, mode, balance, week P&L, limits used,
  pauses, events, India VIX, results today, scan and news status, report status
  and database health. Use when the user says "morning check", "good morning",
  "change capital", "change my limit", "pause trading", "resume", "kill
  switch", "stop everything", or "what is my status".
argument-hint: "[morning | status | capital | set | pause | resume | kill]"
---

# trader-control

This skill is where limits change, and it changes them only in front of the user. It never changes
`"mode"` or `"kite_enabled"`: switching to live is the user's own edit of `trader/config.json`.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives). If your Claude does not
set that variable, use the path of the folder this SKILL.md is in. Run every command from your working
folder (the one that holds `trader/`), not from inside the skill folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/control.py" morning
python3 "${CLAUDE_SKILL_DIR}/control.py" status
python3 "${CLAUDE_SKILL_DIR}/control.py" capital --set 150000 --reason "added funds"                # shows, changes nothing
python3 "${CLAUDE_SKILL_DIR}/control.py" capital --set 150000 --reason "added funds" --reply yes    # after the user's yes
python3 "${CLAUDE_SKILL_DIR}/control.py" set max_loss_per_trade_pct 0.4 --reply yes
python3 "${CLAUDE_SKILL_DIR}/control.py" pause --until 2026-11-06 --reason "review week"
python3 "${CLAUDE_SKILL_DIR}/control.py" resume
python3 "${CLAUDE_SKILL_DIR}/control.py" kill
python3 "${CLAUDE_SKILL_DIR}/control.py" unkill --reply yes
python3 "${CLAUDE_SKILL_DIR}/control.py" edits
```

## The morning check

Run `control.py morning` first thing, read it back in short plain sentences and say what it means for today:
market open or closed (and why), the mode in capitals, balance and week P&L, how much of the daily and weekly
limit is used, any pause or kill switch, events (budget day, index expiry, RBI days from your `events.json`),
results due today, India VIX, whether the protection files are loaded (ASM/GSM, MIS list, price bands),
the last scan and news check, whether the last backup is recent, whether the database passed its check, and the
age of the last backup. It also logs any hand edit of `trader/config.json`. End with the reminder to use
Zerodha's TOTP two-factor login and never to share Kite credentials with anyone, including Claude.

## Changing capital or a limit (always two steps)

1. Run the command **without** `--reply`. It prints old and new side by side (capital, balance, drawdown
   baseline, and every limit in percent and rupees) and changes nothing.
2. Show that to the user. If they are in the paper-test period, say the tool's warning: a capital change resets
   the drawdown baseline, and results before and after are not comparable.
3. Only when the user types a plain yes ("yes", "y"), run the same command again with `--reply yes`, the
   user's own word. "ok", "sure", "go ahead" and "yes to all" are not a yes: ask again.
4. In live mode capital can never be more than what Kite shows as available: pass `--kite-available` from
   `get_margins`.
5. Deposits and withdrawals are kept apart from trading profit and loss, so the statistics stay honest. Raising a
   risk limit carries a warning: beginners should not loosen limits to make a trade pass.

## Pause and kill switch

- `pause` blocks new orders in both modes until a date (or until `resume`). Scans, news, alerts and exits still work.
- `kill` creates `trader/STOP`. While that file exists no new order is possible in either mode: trader-risk
  blocks, the yes is refused, the paper broker and the Kite adapter refuse. Only the user removes it
  (`unkill --reply yes`, or deleting the file). In live mode `kill` lists open positions and the exact
  flatten command for each; flattening still needs trader-risk, the user's yes and Claude's Allow once card.

## What it never does

It never changes the mode, never switches Kite on, never asks for or stores a key, never raises a limit on its own
and never changes anything without the user's typed yes. It makes no network call.

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
