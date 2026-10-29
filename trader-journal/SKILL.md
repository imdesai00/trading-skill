---
name: trader-journal
description: >-
  Notion journal for the NSE paper-trading record. Sets up (after you name the
  parent page) a page "India Day Trader – Paper Trading" with four databases:
  Paper Trades, Skipped Signals, Daily Summary and Weekly Reports. After each
  day it syncs every paper trade, skipped signal and daily summary to Notion
  through the Notion connector (never a token), creating each row once and
  updating it after that by its Trade ID, keeping anything unsent queued. It
  writes the Friday weekly report (win rate, average R, expectancy after
  charges, charges paid, best and worst trade, results by setup and by time
  machine odds, skipped-signal comparison, limits hit, balance against
  ₹1,00,000) and rebuilds the local paper log from Notion if the files are
  lost. Use when the user says "set up Notion", "sync my trades", "save
  today to Notion", "daily summary", "weekly report", "Friday report", "rebuild
  from Notion", "I lost my files", or after paper.py reconcile.
argument-hint: "[setup | sync | daily | weekly | rebuild]"
---

# trader-journal

Notion is the permanent record of the four-week paper trial. The files under
`trader/` are a working copy that can be rebuilt from it. `journal.py` never
calls Notion and never sees a Notion token: it queues what should be in Notion
in `trader/state/notion_outbox.json`, and Claude writes each queued row through
the Notion connector, then marks it sent. Use the connector only. Never ask the
user for a Notion token or API key.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives). If your Claude does not
set that variable, use the path of the folder this SKILL.md is in. Run every command from your working
folder (the one that holds `trader/`), not from inside the skill folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/journal.py" schema --json                 # the page and the 4 databases to create
python3 "${CLAUDE_SKILL_DIR}/journal.py" setup-save --page-id ID --db paper_trades=DS_ID ...
python3 "${CLAUDE_SKILL_DIR}/journal.py" queue                         # trades, skipped signals, day summaries
python3 "${CLAUDE_SKILL_DIR}/journal.py" pending --json                # the Notion calls to make
python3 "${CLAUDE_SKILL_DIR}/journal.py" mark-sent --key KEY --page-id ID --hash HASH
python3 "${CLAUDE_SKILL_DIR}/journal.py" daily --date 2026-10-30       # print and queue one day
python3 "${CLAUDE_SKILL_DIR}/journal.py" weekly --week-of 2026-10-30   # print and queue the week
python3 "${CLAUDE_SKILL_DIR}/journal.py" status
python3 "${CLAUDE_SKILL_DIR}/journal.py" rebuild --from trader/state/notion_export.json
```

## Step 1: set up Notion (once)

1. **Ask the user which Notion page to use as the parent.** Do not guess and do
   not create anything until they name it. Find it with `notion-search` and
   confirm the title back to them.
2. Run `journal.py schema --json`. Create the page with `notion-create-pages`
   (`parent: {type: page_id, page_id: <their page>}`, title "India Day Trader –
   Paper Trading").
3. For each of the four databases, call `notion-create-database` with
   `parent: {type: page_id, page_id: <the page from step 2>}`, the database
   title and the `schema` text from the plan, exactly as printed.
4. `notion-fetch` each new database; its `<data-source url="collection://...">`
   gives the data source id. Then:
   `journal.py setup-save --page-id <page> --db paper_trades=<id> --db skipped_signals=<id> --db daily_summary=<id> --db weekly_reports=<id>`
   This writes only the `notion` section of `trader/config.json`; it never
   touches the user's limits.
5. Tell the user what was created, with the link.

If the schema is rejected (a column name or type the connector does not
accept), say exactly what the error was and stop. Do not improvise columns.

## Step 2: after each trading day

1. The paper broker has reconciled (`paper.py reconcile`). Run `journal.py queue`.
2. `journal.py pending --json`. For each row, make the `calls` in order:
   - **create**: first the `notion-query-data-sources` call (is there already a
     row with this Trade ID / Signal ID / date?). If it returns a row, update
     that page instead of creating one. Otherwise `notion-create-pages`.
   - **update**: `notion-update-page` with the page id.
   Then `journal.py mark-sent --key KEY --page-id PAGE --hash HASH` with the
   page id from the response and the `hash` from the listing. If the row changed
   in between, mark-sent refuses and the row stays queued: list again.
3. A row that could not be written (Notion down, error) is NOT marked sent. It
   stays in the queue and goes next time. Say which ones are waiting.
4. `journal.py daily` prints the day in plain words: trades, wins and losses,
   net after charges, balance, limits hit, rules broken. Read it to the user.

An updated trade (it was open, now closed) is matched on its Trade ID and
updated in place. Rows are never duplicated, and Notion pages are never deleted
by this skill.

## Step 3: Friday weekly report

`journal.py weekly --week-of <the Friday>` prints the report and queues it as a
page in the Weekly Reports database; the report text is the page content. Then
sync as in step 2. The report covers: trades, win rate, average R, net
expectancy per trade after charges, total charges paid, best and worst trade,
results by setup type, results against the time machine's odds, the skipped
signals compared with the trades taken, limits hit, rules broken, and the
balance against ₹1,00,000. Read it to the user without adding adjectives.

## Step 4: rebuild from Notion (if local files are lost)

1. Query the Paper Trades and Skipped Signals databases with
   `notion-query-data-sources` (`rows` mode, up to 100 rows a call; use `view`
   mode and its cursor beyond 100). Query all rows, not only recent ones.
2. Save them to `trader/state/notion_export.json` as
   `{"paper_trades": [ {<property name>: <value>, ...}, ...], "skipped_signals": [...]}`,
   one flat object per row, keyed by the Notion property names exactly as in the
   schema (a date may be `"Date": "2026-10-30"`). Numbers as numbers or
   numeric text, selects as their name.
3. `journal.py rebuild --from trader/state/notion_export.json`. It refuses to
   overwrite an existing ledger without `--force` and keeps a `.bak`. It
   rebuilds trades, skipped signals and the balance. Any trade still open in
   Notion comes back without its internal fingerprint: tell the user to check
   whether it really is open before reconciling.
4. Run `journal.py queue` and `pending`: nothing should be waiting except what
   was genuinely unsent.

## What the Notion columns are

Paper Trades: Trade ID, Date, Symbol, Setup type, Side, Qty, Planned entry,
Entry fill, Stop, Target, Exit price, Exit time, Exit reason (target / stop /
flat-by / manual), Gross P&L ₹, Charges ₹, Net P&L ₹, R multiple, Risk at stop ₹,
Time-machine odds (target/stop/open %), Risk verdict, News tags, My approval
(exact words), Mode (paper / live), Status (open / closed), Account balance
after ₹, Notes, plus Entry time and Flat by (added so a rebuild can restore an
open trade). Skipped Signals: the same core fields plus Why skipped and
Hypothetical net P&L ₹ (and exit, R, skipped-at time). Daily Summary: Date,
Trades, Wins, Losses, Net P&L ₹, Charges ₹, Win rate, Avg R, Balance ₹,
Drawdown from ₹1,00,000, Limits hit, Rules broken. Weekly Reports: one page per
week, report as the page body.

"Rules broken" means a trade whose loss ended above the per-trade limit
(₹500), which can happen when a fill is worse than planned. "Win rate" is a
plain percentage number (50 means 50 %).

## What it never does

It never calls Notion itself, never asks for or stores a Notion token, never
deletes a Notion page, never writes to a Notion page the user did not name, and
never changes the user's limits. Live trades are logged the same way with Mode
= live once Kite is connected.

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
