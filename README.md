# India Day Trader skill

15 Claude skills for NSE day trading: scan charts, track news, time every entry and exit, replay every outcome, and paper trade first. Longs only, no leverage, your yes on every order.

Free, MIT, no signup, no API key. The tools are plain Python (3.8 or newer, nothing to pip install) that read free public data: Yahoo Finance charts, NSE's equity list and RSS feeds, NIFTY index lists and Moneycontrol's RSS. Nothing in this pack can place an order on its own. **Paper trading is the default**, and it is the only mode you should use for the first four weeks.

> **Not financial advice.** Most individual intraday traders lose money (SEBI's study of FY2022-23, as reported in the press: about 7 in 10 in the cash segment; the sebi.gov.in page itself was not checked). You can lose money. This is software that reads public data and does arithmetic.

> **Credit.** This is a conversion of [Jake Schincariol's day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) (MIT) from US stocks to the Indian NSE cash market. The pipeline idea, the Time Machine algorithm and the skill structure are his. The NSE clock and holiday list, tick sizes, Zerodha charges, rupee formatting, the paper broker, the Notion journal, the Kite adapter, the go-live review, the results reader and the insider tracker are new here. His original MIT license and copyright notice are kept in `LICENSE`.

## Documentation

New here? Read [docs/HOW_TO_USE.md](docs/HOW_TO_USE.md) first (one page, start to finish), then the index in [docs/README.md](docs/README.md): [getting started](docs/GETTING_STARTED.md), [the daily routine](docs/DAILY_ROUTINE.md), [safety](docs/SAFETY.md), [the daily data files](docs/DATA_FILES.md), [Notion](docs/NOTION.md), [going live](docs/GO_LIVE.md), [architecture](docs/ARCHITECTURE.md), [troubleshooting](docs/TROUBLESHOOTING.md), [the roadmap](docs/ROADMAP.md) (Phase 2 dashboard, Phase 3 Dhan, with [the Dhan analysis](docs/DHAN.md)), and [an audit of this build against the specification](docs/AUDIT.md) (what is done, what differs, what is still missing).

## What it is

NSE cash equity, intraday (MIS) only. Long side only: no shorts, no F&O, no margin, no leverage. Beginner-conservative limits, all in your `trader/config.json`, stored as percent of capital and changed only by you or with your typed yes through `/trader-control` (defaults for a ₹1,00,000 account):

| limit | default |
| --- | --- |
| max loss per trade, charges and slippage included, measured at the stop | 0.5% = ₹500 |
| max loss per day / per week | 1.5% = ₹1,500 / 4% = ₹4,000 |
| pause after falling this far below your baseline | 8% = ₹8,000 |
| trades per day / open positions | 3 / 1 |
| max position value, cash buffer | 75% = ₹75,000, 10 % |
| minimum reward:risk after charges; refuse if charges are over | 1.5; 25 % of the target profit |
| no entries before / after, flat by | 09:30 / 14:45, 15:00 |
| brakes | 2 losing exits in a row stop the day; 15 minutes cooldown after a loss |
| half size | results day, event day, expiry day, India VIX above 20, NIFTY gap above 1%, and your first 2 live weeks |
| paper slippage | 0.05 % a side |

Two modes. **Paper** (the default) contacts no broker, ever. **Live** needs `"mode": "live"`, `"broker": "dhan"` AND `"dhan_enabled": true`, all set by you in your config, plus a Kite connection verified that day. Nothing in this pack changes those two settings.

Two gates on every order, in both modes: `/trader-risk` must PASS that exact order, and you must type "yes" for that one order. In live mode Claude's own "Allow once" card is a third check, with every broker order tool set to "Needs approval". Never "yes to all", never two orders on one yes, never an order from a scheduled or background task.

## Install

In Claude Code, as a plugin:

```
/plugin marketplace add imdesai00/trading-skill
/plugin install india-day-trader-skill@india-day-trader-skill
```

Installed as a plugin the skills show up as `/india-day-trader-skill:trader-scanner` and so on. To get plain `/trader-scanner`, copy the folders instead:

```bash
git clone https://github.com/imdesai00/trading-skill.git
cp -r trading-skill/trader-* ~/.claude/skills/
```

For Claude Desktop or claude.ai, build one standalone ZIP per skill and upload each in Settings, Capabilities, Skills:

```bash
python3 tests/package.py
ls dist/
```

Each skill folder works on its own (the shared charges calculator is copied into the three skills that need it, and a test checks the copies are identical). Whether your Claude Desktop lets a skill reach the internet and keep a `trader/` folder between chats varies: try `/trader-alerts` with `alerts.py clock` first, then `quote RELIANCE`.

Run everything from one working folder. All state lands in `trader/` there. It is in `.gitignore`.

First time: `python3 trader-budget/size.py init` writes `trader/config.json` with the limits above.

## The fifteen

| command | job | what it does |
| --- | --- | --- |
| `/trader-scanner` | Chart Scanner | Builds the universe (NSE EQ series x NIFTY 100) and scans for daily setups before the open and 15-minute opening-range breakouts after 09:30. |
| `/trader-news` | News Monitor | Yahoo `.NS` headlines, NSE announcements and Moneycontrol RSS matched to your symbols, in IST, with a RESULTS TODAY warning. |
| `/trader-entry` | Entry Timer | Watches a setup bar by bar and tells you the moment to get in. Longs only. |
| `/trader-exit` | Exit Planner | Sets the stop and target on the NSE tick grid before you enter, flat by 15:00. |
| `/trader-timemachine` | Time Machine | Replays the rest of the session 10,000 times from the stock's own recent bars, plus every past day the same setup fired. |
| `/trader-budget` | Budget Manager | Sizes the trade so the loss at the stop, with the broker's charges and slippage, stays inside your limit. Refuses weak trades. |
| `/trader-risk` | Risk Manager | PASS or BLOCK on the exact order against every limit. |
| `/trader-broker` | Order Executor | Shows the ticket, records your yes, then books a paper trade (or prepares the exact Dhan orders in live mode). |
| `/trader-alerts` | Price Alerts | The NSE clock, quotes, and alerts for the stop, target, flat-by and the opening bell. |
| `/trader-db` | Database | The local record of every order, your exact yes, every fill and trade: tamper-evident, with `check`, `backup`, `restore` and `export`. |
| `/trader-control` | Controls | Change capital or limits (only with your typed yes), pause, the kill switch, and the morning check. |
| `/trader-journal` | Notion Journal | Keeps the permanent paper-trading record in Notion through the Notion connector (never a token). |
| `/trader-golive` | Go-Live Review | Reads the paper record plainly (is it luck?) and lists the steps you take yourself to go live. Decides nothing. |
| `/trader-analyst` | Earnings Analyst | Reads a quarterly results table you paste: changes, margins, flags. No forecasts. |
| `/trader-insider` | Insider Tracker | Reads SEBI PIT insider disclosures from a CSV you export from NSE, alerts once per filing, shows how late each is. |

## Your day

The pipeline for one trade: `scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> yes -> paper fill`, with alerts watching the open trade and the journal recording the day.

| when (IST) | what |
| --- | --- |
| Before 09:15 | `/trader-control morning` (the morning check), load the daily files (`scan.py protect`, `scan.py market`; see [docs/DATA_FILES.md](docs/DATA_FILES.md)), `/trader-news` on your watchlist (any RESULTS TODAY?), `/trader-insider check`, `scan.py daily` for the day's setups. |
| 09:30 to 14:45 | `scan.py orb`, then `entry.py check SYMBOL`. On a trigger: `exit.py plan --write`, `timemachine.py run --write`, `size.py --write`, `check.py`, `ticket.py show`. Claude asks "Why are you taking this trade?"; you answer and say yes to that one order; `ticket.py approve --why "..."`, then `paper.py open`. |
| While a trade is open | `alerts.py add --from-plan`, `alerts.py quote`, `alerts.py check`. At the target alert or at 15:00, `ticket.py flatten` goes through risk and your yes too. |
| After 15:30 | `paper.py reconcile` (makes a database backup), `paper.py feel` ("How did you feel about it?"), then `/trader-journal`: queue, sync to Notion, `journal.py verify`, read the daily summary. |
| Friday | `/trader-journal weekly`, `db.py check`. |
| Any time | "change capital to ₹X", "pause trading until DATE", "resume trading", "kill switch": `/trader-control`. |
| After four weeks and at least 40 trades | `golive.py check`, read it, decide for yourself ([docs/GO_LIVE.md](docs/GO_LIVE.md)). |

## A worked example (synthetic data, honestly)

The sandbox this was built in could not reach Yahoo, NSE or Zerodha, so there is **no real NSE data in this repo**. Every fixture is synthetic (made by `tests/fixtures/nse/make_synthetic.py`, symbols `SYNTHA` to `SYNTHG`) and says so. The outputs below are real outputs of the tools on those synthetic bars: they show how the tools behave, not how any stock behaves.

```
$ entry.py check SYNTHA --now "2026-10-30 10:15" --offline tests/fixtures/nse/5m-1d
Opening range 09:15-09:30: high ₹1,414.80, low ₹1,401.20 (3 of 3 bars).
TRIGGERED 09:50 at ₹1,416.00: the 09:45 bar closed above the range high ₹1,414.80 (+0.08%).
That was 25 minutes ago; the last close is ₹1,408.60. A late entry is a different trade.

$ exit.py plan --write
  Entry ₹1,416.00 at 09:50   Stop ₹1,408.00 (opening-range midpoint)   Target ₹1,432.00 (2R)
  Risk a share ₹8.00   Reward:risk 2.0   Flat by 15:00 IST

$ size.py --write --now "2026-10-30 09:51"
  max loss 45 shares ... <- decides it   position cap 52   cash 63
45 shares = ₹63,720.00. If the stop fills at 1408.00: about ₹490.88 lost, of which ₹67.34 is charges.
If the target fills at 1432.00: about ₹587.89 made after ₹68.03 of charges. Reward:risk 2.00 before charges, 1.20 after.
REFUSED: reward:risk after charges is 1.20, under your minimum 1.5. No order.
```

A textbook 2R setup, refused. At this size on a ₹1,416 stock, charges and slippage eat 40 % of the reward:risk, so the budget manager says no and `paper.py skip --from-plan` logs it as a skipped signal. That is the design working: with ₹500 of risk, many trades are not worth taking.

## Notion setup (once)

1. Connect Notion in Claude (Settings, Connectors). Never give any tool a Notion token.
2. Tell `/trader-journal` which Notion page should hold the record. It asks; nothing is created before you name the page. It then creates "India Day Trader - Paper Trading" with four databases: Paper Trades, Skipped Signals, Daily Summary and Weekly Reports.
3. After each day it syncs through the connector. Each row is created once and then updated by its Trade ID, never duplicated. Anything that could not be sent stays queued.
4. If you lose your `trader/` folder, `/trader-journal rebuild` restores the ledger from Notion.

## Going live (only after the paper trial)

Do not do this early. `golive.py steps` prints the full list (`--broker kite` for the Zerodha version), and [docs/DHAN.md](docs/DHAN.md) and [docs/PHASE_3_DHAN.md](docs/PHASE_3_DHAN.md) explain the Dhan route. In short: read `golive.py check` and the Notion record yourself (a result that sits inside the noise is a reason to keep paper trading); connect Dhan's MCP connector yourself (reported address `https://mcp.dhan.co/mcp`; approve its consent screen yourself and never paste a token anywhere); set every order tool (place, super order, modify, cancel, forever orders) to "Needs approval"; build the symbol map with `dhan.py securities`; fill in Dhan's real tool names in `dhan_tools`; re-verify the items in the `RECHECK-AT-DHAN` list in `VERIFY.md` (none of Dhan's tool names, fields or rules was read from Dhan's own pages); then edit your own config to `"mode": "live"`, `"broker": "dhan"`, `"dhan_enabled": true` and `max_loss_per_trade_inr` of 250 (half the paper risk) for the first two weeks.

A live Dhan trade is **one super order**: the entry, the stop-loss and the target travel together as one request, so there is one ticket, one risk check, one typed yes and one Allow once card, and the stop exists from the moment the entry fills. The entry is a limit order (`size.py --limit`). Leaving early is two steps, each with its own yes: cancel the super order, then sell. (The Zerodha adapter keeps its older three-order design: entry, then a separate resting stop, then exit.)

## The tools

```bash
python3 trader-budget/size.py init                               # your limits -> trader/config.json
python3 trader-scanner/scan.py universe                          # NSE EQ series x NIFTY 100
python3 trader-scanner/scan.py daily                             # before 09:15 -> trader/watchlist.json
python3 trader-scanner/scan.py orb --at 10:15                    # opening-range breakouts so far
python3 trader-news/news.py --symbols RELIANCE TCS               # headlines, announcements, RESULTS TODAY
python3 trader-entry/entry.py check RELIANCE --write             # waiting / triggered / expired
python3 trader-exit/exit.py plan --write                         # stop + target -> trader/plan.json
python3 trader-timemachine/timemachine.py run --write            # 10,000 replays + the history
python3 trader-budget/size.py --write                            # -> trader/orders.json (or REFUSED)
python3 trader-risk/check.py                                     # PASS / BLOCK per order
python3 trader-broker/ticket.py show                             # the tickets, READY or not
python3 trader-broker/ticket.py approve --index 1 --reply "yes"  # your yes, for that one order
python3 trader-broker/paper.py open --index 1                    # book the paper trade
python3 trader-broker/paper.py reconcile                         # after the close
python3 trader-alerts/alerts.py clock                            # is NSE open?
python3 trader-journal/journal.py queue                          # then Claude syncs to Notion
python3 trader-journal/journal.py verify --from trader/state/notion_export.json   # counts and totals, database vs Notion
python3 trader-db/db.py check                                    # the database is intact? (also: backup, restore, export)
python3 trader-control/control.py morning                        # the morning check
python3 trader-scanner/scan.py protect --asm asm.csv --gsm gsm.csv --mis mis.csv --bands bands.csv
python3 trader-scanner/scan.py market --fetch                    # India VIX and the NIFTY gap
python3 trader-golive/golive.py check                            # the honest report
python3 trader-analyst/results.py read results.txt --basis consolidated
python3 trader-insider/insider.py import nse_insider_export.csv
```

```bash
python3 -m unittest discover -s tests -v
```

The tests run offline on synthetic fixtures and on mocks of Dhan, Kite and Notion. They check the arithmetic (charges, tick rounding, sizing, limits), the safety gates (a stop that leaks, a yes that names a different order, live mode with the switches off making zero Kite calls) and that every skill folder works on its own. `tests/capture.py` fetches real Yahoo bars on your own machine so you can replace the synthetic fixtures.

## What is not verified

Read `VERIFY.md`. It lists every assumption with its source and date and marks it verified, secondary or unverified. The big ones: the Yahoo `.NS` response shape and delay; the NSE and Moneycontrol RSS URLs and the column names of NSE's CSVs; Dhan's MCP tool names and result fields, whether its order APIs need a static IP on your route, how a super order cancels, and the 2026 SEBI retail-algo rules; the charge rates (check against dhan.co/pricing); the Notion database column names with characters like ₹ and &. The tools say so when they depend on one of these, and the 2027 holiday list is not in the tool, so it refuses to run for 2027 dates until you add it.

## Fine print

**Not financial advice.** Nothing here knows your situation or sees the future.

**No order without your yes.** Only `/trader-broker` can lead to an order, and only after `/trader-risk` passes that exact order and you type yes for it in the chat.

**The Time Machine is a simulation, not a prediction.** The future can do things the last 60 days never did: results, a halt, a headline, a circuit limit.

**A stop is a plan, not a guarantee.** A stop can fill well past its price in a fast market or across a gap. The loss shown is the loss at the stop price. Closing-auction rules (from 3 August 2026, F&O stocks stop continuous trading at 15:15) are why the clock and the time machine stop at 15:15.

**Data is free and unofficial.** Yahoo's chart endpoint can lag, change or rate-limit you. Check anything that matters against your broker before you act on it.

**Insider filings are public and late, not a signal.** Results days are stop signs for this pack's entries.

**Tax.** Intraday equity trading is generally treated as speculative business income in India. That is a general statement, not tax advice: talk to a chartered accountant.

**SEBI algo rules.** SEBI's retail algorithmic-trading framework (reported in 2025, with an effective date in 2026) puts conditions on API order flow, such as a registered static IP and order-rate thresholds. Check Zerodha's current requirements before connecting an API: the details here are from secondary sources.

## Files

```
trader-scanner/      SKILL.md, scan.py, guards.py
trader-news/         SKILL.md, news.py
trader-entry/        SKILL.md, entry.py
trader-exit/         SKILL.md, exit.py
trader-timemachine/  SKILL.md, timemachine.py
trader-budget/       SKILL.md, size.py, charges.py, db.py, guards.py, config.example.json
trader-risk/         SKILL.md, check.py, charges.py, db.py, guards.py
trader-broker/       SKILL.md, ticket.py, paper.py, dhan.py, kite.py, charges.py, db.py
trader-alerts/       SKILL.md, alerts.py
trader-journal/      SKILL.md, journal.py, db.py
trader-db/           SKILL.md, db.py
trader-control/      SKILL.md, control.py, db.py, guards.py
trader-golive/       SKILL.md, golive.py, db.py
trader-analyst/      SKILL.md, results.py
trader-insider/      SKILL.md, insider.py
tests/               unittest suite, synthetic fixtures, package.py, capture.py
docs/                guides for new users, the audit against the specification
PLAN.md, VERIFY.md   the plan and every assumption, with sources
```

MIT. Original by [Jake Schincariol](https://opusjake.ai); NSE conversion in this repository.
