# Architecture

## The shape

Fifteen skills, each a folder: a `SKILL.md` for Claude and Python scripts for the arithmetic. The scripts use only the Python standard library and never reach into a sibling folder,
so each skill can be uploaded on its own. The few shared files are byte-identical copies, and a test checks they are identical:

| shared file | copies in | what it is |
| --- | --- | --- |
| `charges.py` | trader-budget (source), trader-risk, trader-broker | Zerodha intraday charges |
| `db.py` | trader-db (source), trader-broker, trader-risk, trader-report, trader-golive, trader-budget, trader-control | the database module and its CLI |
| `guards.py` | trader-risk (source), trader-budget, trader-scanner, trader-control | limits as percent, protections, half-size, brakes |
| the NSE clock block | inside news, insider, control, scan, entry, exit, timemachine, size, check, ticket, paper, kite, report, alerts (`dhan.py` reuses `kite.py`'s, in the same folder) | IST, the 2026 holidays, ₹ formatting, tick sizes |

## The data flow of one trade

```
scanner -> watchlist.json
news    -> results_today.json                       (half size on results day)
entry   -> entry.json
exit    -> plan.json
timemachine -> timemachine.json
budget  -> orders.json (+ state/sizing.json)        broker-neutral order (the broker's call is built from it at approval time)
risk    -> state/risk.json + risk_verdicts table     PASS or BLOCK per exact order (fingerprint)
broker  -> ticket.py approve: state/broker.json + orders, order_events, approvals tables   your yes, word for word
           paper.py open/reconcile: trades, fills, positions, account    (paper)
           dhan.py (or kite.py) prepare/record/sync/reconcile              (live: off by default)
alerts  -> state/alerts.json, prices.json, alerts table
report  -> trader/reports/*.md, daily_summary and weekly_reports tables
```

## Where the truth lives

`trader/trader.db` (SQLite) is the source of truth once it exists. `paper.json` and `live.json` in `trader/state/` are then read-only copies rewritten after each save; a hand edit of them is ignored and reported.
With no database, the older JSON files are the ledgers (so the tools still work, and `db.py migrate` imports them later). Approvals and risk verdicts also stay in the day's JSON files, because the gates read those.

### Tables

Append-only and hash-chained (the database refuses UPDATE and DELETE; each row stores a hash of the row before it):

| table | one row per |
| --- | --- |
| `orders` | an approved order (never changed; status changes go to `order_events`) |
| `order_events` | a status change of an order: approved, placed, filled, cancelled, rejected, failed, with the broker order id |
| `approvals` | every typed yes or no, with your exact words and your "why" |
| `fills` | an entry or exit fill (paper or broker) |
| `trades` | a version of a trade (open is version 1, closed is version 2, pointing back) |
| `skipped_signals` | a version of a skipped signal (later with what would have happened) |
| `risk_verdicts` | a PASS or BLOCK with reasons and warnings |
| `trade_notes` | your "how I felt" (and "why") notes |
| `capital_changes` | a capital change with old, new and the reason |
| `pauses` | a pause, resume or kill switch |
| `config_history` | a change of the config: by a tool, or by your hand |
| `discrepancies` | a live difference between the broker and the record |

Working tables, meant to change: `account` (balance, capital, baseline per mode), `positions`, `daily_summary`, `weekly_reports`, `alerts`, `news_items`, `scans`, `meta` (for example the last account read from Kite),
`schema_version`. Money is stored as whole paise in columns such as `net_paise`; a trade's full record is kept beside them as JSON in `doc`. Every table has its keys, and the ones you filter by (date, symbol, trade id,
fingerprint) have indexes. Paper and live are told apart by `mode` and are never added together.

### Why hash chains, and what they cannot do

`db.py check` recomputes every hash. An edited row, or a row removed from the middle, is found. A removed *last* row cannot be seen from inside the file. That is why `reconcile` makes a backup every time, and why you should copy the backups elsewhere:
a database that fails its check is restored from the newest backup that passes, then checked again with `db.py check`.

## The state files in `trader/`

| file | written by | read by |
| --- | --- | --- |
| `config.json` | `size.py init`, `control.py` (with your yes), you | everything |
| `trader.db`, `backups/`, `exports/` | the db module | everything that records |
| `STOP` | `control.py kill`, you | ticket, paper, dhan, kite, risk |
| `watchlist.json`, `entry.json`, `plan.json`, `timemachine.json`, `orders.json`, `prices.json` | the pipeline skills | the next skill |
| `state/risk.json`, `state/broker.json`, `state/sizing.json`, `state/dhan.json` / `state/kite.json` | risk, ticket, budget, dhan / kite check | the gates |
| `state/paper.json`, `state/live.json` | the ledgers' read-only copies (or the ledgers, with no database) | risk, report, golive |
| `state/surveillance.json`, `mis.json`, `bands.json`, `market.json`, `results_today.json`, `events.json` | `scan.py protect/market`, news, you | risk, budget, scanner |
| `state/news.json`, `alerts.json`, `insider.json` | news, alerts, insider, Claude | the same tools |

## The three layers of defence for an order

1. The tools that make an order will not make one without the gates (risk PASS on the fingerprint, then a recorded yes, then in live mode the payload hash).
2. The database makes the record tamper-evident.
3. Claude's own permission card (live mode), which no script can replace.

## Tests

`python3 -m unittest discover -s tests -v` runs the whole suite offline. Fixtures are synthetic (symbols SYNTHA to SYNTHG), because the sandbox the pack was built in could not reach the real feeds. Dhan, FYERS and Kite are tested
with mocks. A test unzips every skill alone and runs every script, to catch a missing shared file. `tests/capture.py` fetches real bars on your own machine.

## Adding to it

Keep the rules: standard library only, Python 3.8+, no secrets, a test for every behaviour, VERIFY.md for every outside fact. If you change `db.py` or `guards.py`, change the source and copy it over the others; the copy test tells you if you forgot.
