# The Notion journal

Notion is your readable record: your paper brokerage account, every order, trade, skipped signal, daily summary and weekly report. The database
on your computer is the truth; Notion is a copy you can read on a phone and share with a mentor. The two are compared after every sync.

## How it works (no tokens, ever)

The scripts never talk to Notion. They queue rows in the database (the outbox). Claude then writes each waiting row to Notion through the **Notion connector**
you connected in Claude, and marks it sent. You are never asked for a Notion token or an API key. If anything asks, refuse.

## One-time setup

1. In Claude, connect Notion (Connectors).
2. Say "set up the Notion journal". Claude **asks which Notion page to use** and creates nothing until you name it.
3. It creates a page called **India Day Trader** under that page with nine databases:

| database | what is in it |
| --- | --- |
| **Account** | one row per mode: Paper Account (your simulated brokerage account: balance, capital baseline, open positions, realised P&L) and, in live mode, Live Account as read from Kite |
| **Orders** | every order: id, time, mode, symbol, side, quantity, type, price, stop, target, risk verdict, your approval in your exact words, why you took it, broker order id (live), status (approved, placed, filled, cancelled, rejected, failed) |
| **Trades** | one row per trade: setup, planned entry, fill, stop, target, exit, exit reason (target, stop, flat-by, manual), gross P&L, charges (and whether estimated or actual), net P&L, R multiple, risk at stop, time-machine odds, news tags, why I took it, how I felt, balance after, status |
| **Skipped Signals** | trades you said no to or the risk check blocked, with what would have happened |
| **Daily Summary** | per day and per mode: trades, wins, losses, net P&L, charges, win rate, average R, balance, drawdown, limits hit, rules broken |
| **Weekly Reports** | one page per week and mode, in plain language |
| **Capital Changes** | every deposit, withdrawal or capital change with the reason |
| **Pauses** | pauses, resumes and kill switches |
| **Discrepancies** | live mode: anything where the broker and the record disagree |

The Notion database ids are saved in `trader/config.json` (`notion` section). They are not secrets.

If Notion rejects a column (the rupee sign or an ampersand in a name, for example), Claude shows you the exact error and stops; it does not invent columns. This
has not been exercised against a real workspace yet, so expect to look at the first setup carefully.

## After each trading day

1. `journal.py queue` (paper) or `journal.py queue --mode live`.
2. `journal.py pending --json` lists the Notion calls. For each row Claude first looks for an existing row with the same title and updates it if found; otherwise it creates one. Rows are
   matched by id and never duplicated. A row that could not be written stays queued.
3. Claude marks each row sent with the page id.
4. **Verify**: Claude reads the rows back from Notion into `trader/state/notion_export.json` and runs `journal.py verify --from trader/state/notion_export.json`. It prints counts and totals for
   both sides, and any mismatch.

## If something is lost

- **Lost Notion** (deleted by mistake): `journal.py requeue` marks every row as waiting; Claude writes them all again, looking each one up by title first.
- **Lost database**: ask Claude to read all rows of the Trades and Skipped Signals databases (and Orders, Capital Changes, Pauses) into `notion_export.json`, then
  `journal.py rebuild --from trader/state/notion_export.json --into-db`. The hash chain starts fresh from that import; run `db.py check`. Open trades come back without their internal fingerprint:
  check whether they really are open.
- **Lost backup**: `db.py restore trader/backups/<file>`.

## Paper and live are always separate

Every row carries its mode, daily and weekly rows are per mode, and the weekly report for live never includes paper trades.

## What is never written to Notion

Keys, tokens, passwords and TOTP codes. Nothing like that is ever stored in the database either.
