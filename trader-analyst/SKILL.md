---
name: trader-analyst
description: >-
  Earnings Analyst for NSE companies. Reads a quarterly results table that the
  user pastes (text copied from the NSE/BSE filing or the press release) and
  sums up what changed: revenue, profit before tax and net profit against the
  previous quarter and the same quarter last year, margins, EPS, and flags
  (loss, swing to loss, turnaround, revenue or profit down, margin squeeze,
  big exceptional items, profit leaning on other income, odd tax rate, columns
  that do not add up). Says plainly what it assumed about the column order,
  the unit and consolidated vs standalone. Never forecasts. Use when the user
  says "analyse these results", "how were the results", "quarterly results of
  TCS", "did profit grow", "read this results table", "results today", or
  pastes a results statement.
argument-hint: "[symbol] (paste the results table)"
---

# trader-analyst

NSE has no stable, cookie-free results feed this pack may use (VERIFY.md section 10), so this skill works
on a table the user pastes. It reads numbers and does arithmetic. It does not fetch anything.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives). If your Claude does not
set that variable, use the path of the folder this SKILL.md is in. Run every command from your working
folder (the one that holds `trader/`), not from inside the skill folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/results.py" read results.txt --symbol RELIANCE --basis consolidated
python3 "${CLAUDE_SKILL_DIR}/results.py" read results.txt --columns cur,pq,yoy,skip,skip --json
```

## How to use it

1. Ask the user to paste the statement of financial results (the quarterly table) as text. If they only
   have a PDF, ask them to copy the table's text out of it. Save it to `trader/results-SYMBOL.txt`.
   Do not retype numbers yourself: save exactly what was pasted.
2. Ask whether it is the **consolidated** or **standalone** statement and pass `--basis`. They differ.
3. Run `results.py read`. Read the report back plainly: the table, the changes, then the FLAG and NOTE
   lines word for word.
4. **Check the columns.** If the report says the column order was ASSUMED, show the header to the user and
   confirm, or rerun with `--columns` (for example `cur,pq,yoy,skip,skip` when two half-year columns follow).
5. If a NOTE says net profit does not equal profit before tax minus tax, say it can be minority
   interest or associates, or wrong columns, and ask the user to check the table. Do not call it an error
   in the company's accounts.

## The rules

- Report the numbers and the flags. **Never forecast**, never give a rating, never say a stock will rise or
  fall because of results. Results are already public; the price has usually moved.
- A percentage is "n/m" (not meaningful) when the earlier figure was zero or a loss. Do not invent one.
- Say which unit the table was in (crore, lakh, million) or that none was found.
- Results day is a stop sign for this pack's entries (see /trader-news). Reading the numbers afterwards does
  not make a gap safer to trade.

## What it never does

It never fetches a filing, never reads a PDF, never places an order and never changes a limit.

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
