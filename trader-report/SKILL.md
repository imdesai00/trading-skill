---
name: trader-report
description: >-
  Daily and weekly reports for the paper and live record. Reads trader/trader.db
  and prints, saves (trader/reports/, as Markdown) and keeps in the database a
  daily report (trades, wins and losses, net after charges, charges, balance,
  limits hit, rules broken, and for each trade the user's "why I took it" and
  "how I felt", plus skipped signals with what would have happened) and a Friday
  weekly report (trades, win rate, average R, expectancy after charges, charges
  paid, best and worst trade, results by setup and by time-machine odds, skipped
  signals compared with trades taken, limits hit, balance against the starting
  capital). Paper and live are always reported separately. Use when the user
  says "daily summary", "how did today go", "weekly report", "Friday report",
  "show this week", "summarise my trades", or after paper.py reconcile.
argument-hint: "[daily | weekly] [--mode paper|live]"
---

# trader-report

The record lives in `trader/trader.db` (see trader-db). This skill turns it into plain reports. It reads the record and
writes only report files; it never changes a trade, a limit or a setting, and it makes no network call. There is no outside
service: the reports are Markdown files in `trader/reports/` that you can open, print or share yourself.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives). If your Claude does not
set that variable, use the path of the folder this SKILL.md is in. Run every command from your working
folder (the one that holds `trader/`), not from inside the skill folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/report.py" daily                           # today, paper
python3 "${CLAUDE_SKILL_DIR}/report.py" daily --date 2026-10-30 --mode live
python3 "${CLAUDE_SKILL_DIR}/report.py" weekly                          # the week of today
python3 "${CLAUDE_SKILL_DIR}/report.py" weekly --week-of 2026-10-30 --mode live
```

## How to use it

1. After `paper.py reconcile` (and the "how did you feel" answer), run `report.py daily` and read it to the user in plain
   words: trades, wins and losses, net after charges, the balance against the starting capital, limits hit, rules broken, the
   user's own "why" and "how I felt" for each trade, and the skipped signals.
2. On Fridays run `report.py weekly` and read it without adding adjectives. It covers trades, win rate, average R, net expectancy
   per trade after charges, total charges paid, best and worst trade, results by setup type, results against the time machine's
   odds, skipped signals compared with trades taken, limits hit, and the balance against the starting capital.
3. Paper and live are separate: pass `--mode live` for the live record. Never add them together.
4. Each report is also kept in the database (`daily_summary`, `weekly_reports`); running a report again replaces it, never
   duplicates it.

## What it never does

It never changes a trade or a limit, never places an order, never calls a network service, and never stores a key or a token. A
report says what happened; it never says what to do next.

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
