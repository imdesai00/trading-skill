# The 15 skills

Each skill is a folder with a `SKILL.md` (what Claude reads) and one or more Python scripts (what does the arithmetic). Claude runs the scripts and
explains the result. You can also run any script yourself. In the commands below, `<pack>` is the folder that holds the skill folders.

The usual order for one trade: scanner, news, entry, exit, timemachine, budget, risk, broker. Alerts watch the open trade; the journal records the day.

## The trade pipeline

| skill | command | what it does | main commands |
| --- | --- | --- | --- |
| trader-scanner | `/trader-scanner` | Builds the universe (NSE EQ series x NIFTY 100 by default), scans daily bars before the open and 15-minute opening-range breakouts after 09:30. Drops ASM/GSM and non-MIS stocks when you have loaded the daily files. | `scan.py universe`, `daily`, `orb`, `protect`, `market` |
| trader-news | `/trader-news` | Headlines for your symbols from Yahoo, NSE announcements and Moneycontrol; flags results today; shows how far each stock has moved. | `news.py` |
| trader-entry | `/trader-entry` | Says waiting, triggered or expired for a setup, bar by bar. Long side only. | `entry.py check SYMBOL`, `watch` |
| trader-exit | `/trader-exit` | Sets the stop and target before you enter, on the NSE tick grid, flat by 15:00. Refuses nonsense stops. | `exit.py plan --write` |
| trader-timemachine | `/trader-timemachine` | Replays the rest of the session 10,000 times from the stock's own recent bars and re-runs the rule on past days. A reality check, not a prediction. | `timemachine.py run --write` |
| trader-budget | `/trader-budget` | Sizes the trade in whole shares so the loss at the stop, with charges and slippage, fits your limit. Says which limit decided it. Refuses weak trades. Also creates your config and database (`init`). | `size.py init`, `size.py --write` |
| trader-risk | `/trader-risk` | PASS or BLOCK on the exact order against every limit and protection, with plain reasons. | `check.py` |
| trader-broker | `/trader-broker` | Shows the ticket, records your typed yes, books the paper trade (paper mode) or prepares the exact Dhan, FYERS or Zerodha orders (live mode, off by default; one broker at a time). | `ticket.py show/approve/decline/flatten`, `paper.py open/skip/reconcile/feel/status`, `dhan.py ...`, `fyers.py ...` or `kite.py ...` |
| trader-alerts | `/trader-alerts` | The NSE clock, quotes, and alerts for stop, target, flat-by and the opening bell. | `alerts.py clock/quote/add/check` |

## The record, the controls and the review

| skill | command | what it does | main commands |
| --- | --- | --- | --- |
| trader-db | `/trader-db` | The local database: check, backup, restore, export (including a live P&L file for tax time, not tax advice), migrate old files. | `db.py init/migrate/check/backup/restore/export/status` |
| trader-journal | `/trader-journal` | Sets up and syncs the Notion journal through the Notion connector (never a token), verifies counts and totals, rebuilds either side from the other. | `journal.py schema/queue/pending/mark-sent/daily/weekly/verify/rebuild/requeue` |
| trader-control | `/trader-control` | Changes capital and limits (only with your yes), pause and resume, the kill switch, and the morning check. | `control.py morning/status/capital/set/pause/resume/kill/unkill/edits` |
| trader-golive | `/trader-golive` | An honest report on the paper record (including whether it can be told apart from luck) and the steps you take yourself to go live. Decides nothing. | `golive.py check/steps` |

## Reading and research

| skill | command | what it does | main commands |
| --- | --- | --- | --- |
| trader-analyst | `/trader-analyst` | Reads a quarterly results table you paste and sums up what changed, with flags. No forecasts. | `results.py read FILE` |
| trader-insider | `/trader-insider` | SEBI insider-trading disclosures from a CSV you export from NSE: alerts once per filing, shows how late each one is. | `insider.py import/check/feed` |

## What each skill reads and writes

See [ARCHITECTURE.md](ARCHITECTURE.md) for the files and tables. In short: the pipeline skills pass small JSON files along in `trader/` (`watchlist.json`,
`entry.json`, `plan.json`, `timemachine.json`, `orders.json`); the broker, risk and journal skills also write to the database; nothing writes your config except
`size.py init` (first time) and `control.py` (with your yes).

## Rules that apply to every skill

- Not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading is the default. Real orders need `"mode": "live"`, `"broker": "dhan"` and `"dhan_enabled": true` set by you, a Dhan check from today, a risk PASS on the exact order,
  your typed yes for that one order, and Claude's own Allow once card.
- NSE cash equity, intraday (MIS), longs only. No F&O, no margin, no short selling.
- Broker keys and tokens live in environment variables or config you edit yourself, never in the chat.
