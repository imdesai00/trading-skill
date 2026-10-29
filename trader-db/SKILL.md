---
name: trader-db
description: >-
  The local database (SQLite, trader/trader.db) that is the source of truth for
  every order, approval (your exact words), fill, trade, skipped signal, risk
  verdict, capital change, pause and config change. Creates it, imports the old
  JSON files (and reports counts so you can confirm nothing was lost), checks it
  (integrity, foreign keys, the tamper-evident hash chains), makes dated backups
  (keeps the last 30), restores one, and exports CSV per table plus a yearly
  live P&L file labelled "not tax advice". Use when the user says "db check",
  "db backup", "db export", "restore my database", "is my data safe", "did
  anything get edited", "export for tax", "set up the database", "migrate my
  files", or after a crash or a lost trader/ folder.
argument-hint: "[init | migrate | check | backup | restore FILE | export [--tax]]"
---

# trader-db

Every trade you take, every yes you type and every rule that blocked you is kept in one SQLite file,
`trader/trader.db`, built into Python (nothing to install). Notion is the readable copy; this file is the
record. It is in `.gitignore`, so it never goes to GitHub.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives). If your Claude does not
set that variable, use the path of the folder this SKILL.md is in. Run every command from your working
folder (the one that holds `trader/`), not from inside the skill folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/db.py" init                      # create it (size.py init does this for you)
python3 "${CLAUDE_SKILL_DIR}/db.py" migrate                   # import the older JSON files; prints counts
python3 "${CLAUDE_SKILL_DIR}/db.py" status                    # rows per table, chain heads, last backup
python3 "${CLAUDE_SKILL_DIR}/db.py" check                     # integrity + hash chains + triggers
python3 "${CLAUDE_SKILL_DIR}/db.py" backup                    # trader/backups/trader-DATE.db (last 30 kept)
python3 "${CLAUDE_SKILL_DIR}/db.py" restore trader/backups/trader-2026-10-30-153000.db
python3 "${CLAUDE_SKILL_DIR}/db.py" export                    # one CSV per table in trader/exports/
python3 "${CLAUDE_SKILL_DIR}/db.py" export --tax --fy 2026-27 # live P&L for the financial year. NOT TAX ADVICE.
```

## How it protects your record

- **Append-only.** Orders, order events, approvals, fills, trades, skipped signals, risk verdicts, notes,
  capital changes, pauses, config changes and discrepancies can only be added to. The database refuses an
  UPDATE or a DELETE on them. A trade that goes from open to closed is a new row (version 2) that points
  to the one before.
- **Tamper-evident.** Each of those rows stores a hash of the row before it. `db check` recomputes them
  all: an edited or removed row in the middle of a chain is found. A removed *last* row cannot be seen from
  inside the file: that is what the backups, and Notion, are for. Compare them when `db check` fails.
- **Money is exact.** Amounts are stored as whole paise. The full record of each trade is kept beside them.
- **One transaction per write.** A write lands completely or not at all.
- **Paper and live never mix.** A `mode` column tells them apart in every table, and every report keeps
  them separate.

## What to do

1. First time: `db.py init` (or `size.py init`, which also writes your config). If you already have
   `trader/state/*.json` files from before, run `db.py migrate` and read the counts back to the user.
   The old files are left exactly as they were until the user says to delete them.
2. Run `db.py check` at the start of the week and whenever something looks odd. Say plainly if it fails: do
   not trade on a failed check, restore a backup (`db.py restore`) and compare with Notion.
3. `paper.py reconcile` makes a backup by itself after every run. `db.py backup` makes one on demand.
4. For tax time: `db.py export --tax --fy 2026-27` lists the closed LIVE trades with charges (estimated, or
   actual where the contract note was entered). Paper results are never exported as tax figures. Say it is
   not tax advice and that intraday equity results are generally treated as speculative business income.

## What it never does

It never talks to Notion or a broker, never stores a key, password or token, and never changes a limit.
`restore` copies a backup over the live file only after the backup passes `check`, and keeps the old file
as `trader.db.before-restore`.

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
