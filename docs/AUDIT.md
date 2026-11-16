# Audit: the build against your full specification

> **Update 2026-11-16:** the live broker is now Dhan, not Zerodha. Every "Kite" row below describes the Zerodha adapter, which is kept as an alternative; the Dhan adapter, its differences and what is unverified about Dhan are in [DHAN.md](DHAN.md). The gaps listed under "What we will miss" still apply, with one change: a Dhan super order gives a resting target and a stop that exists from the first fill, which the Zerodha design could not.

Date of this audit: 2026-10-29. What was checked: every section of the specification you gave, against the code in this
repository (15 skill folders, about 20,000 lines of Python, 495 passing tests). "Done" means built and covered by an offline
test. Almost nothing here has touched real NSE, Yahoo, Moneycontrol, Zerodha or Notion: the sandbox it was built in could not
reach them. That is the first thing to know, and it is repeated under "What we will miss".

## Code map

| folder | main file(s) | job | reads / writes |
| --- | --- | --- | --- |
| trader-scanner | scan.py, guards.py | universe, daily and opening-range scans, protection files, VIX | `trader/watchlist.json`, `trader/state/{surveillance,mis,bands,market}.json`, scans table |
| trader-news | news.py | Yahoo, NSE and Moneycontrol headlines, results-today warning | `state/news.json`, `state/results_today.json`, news_items table |
| trader-entry | entry.py | is the setup triggering | `trader/entry.json` |
| trader-exit | exit.py | stop, target and flat-by on the tick grid | `trader/plan.json` |
| trader-timemachine | timemachine.py | 10,000 replays plus history | `trader/timemachine.json` |
| trader-budget | size.py, charges.py, guards.py, db.py | sizing, config init, limits as percent | `trader/orders.json`, `trader/config.json` |
| trader-risk | check.py, guards.py, charges.py, db.py | PASS or BLOCK on the exact order | `state/risk.json`, risk_verdicts table |
| trader-broker | ticket.py, paper.py, kite.py, charges.py, db.py | the ticket and your yes, the paper broker, the Kite adapter | `state/broker.json`, `state/paper.json`, `state/live.json`, orders, approvals, fills, trades tables |
| trader-alerts | alerts.py | NSE clock, quotes, alerts | `state/alerts.json`, `trader/prices.json`, alerts table |
| trader-journal | journal.py, db.py | the Notion outbox, verify, rebuild | notion_outbox table, nine Notion databases |
| trader-db | db.py | the database: check, backup, restore, export, migrate | `trader/trader.db`, `trader/backups/`, `trader/exports/` |
| trader-control | control.py, db.py, guards.py | capital, limits, pause, kill switch, morning check | `trader/config.json`, `trader/STOP`, capital_changes, pauses, config_history |
| trader-golive | golive.py, db.py | honest readiness report, go-live steps | reads the database |
| trader-analyst | results.py | pasted quarterly results | none |
| trader-insider | insider.py | SEBI PIT disclosures | `state/insider.json` |

Shared files exist as byte-identical copies in each skill that needs them (so each skill can be uploaded to Claude on its own):
`charges.py` (budget, risk, broker), `db.py` (db, broker, risk, journal, golive, budget, control), `guards.py` (risk, budget,
scanner, control). A test fails if any copy differs, and another unzips every skill alone and runs every script.

## Specification against the build

Legend: **Done**, **Different** (built, but not as written; the reason is given), **Partial**, **Missing**.

### 1 to 4: where it runs, you, limits, rules

| item | status | note |
| --- | --- | --- |
| Notion only through the connector, no token code | Done | No script calls Notion; no tool asks for a token. Tested. |
| Nothing runs on its own, no background orders | Done | There is no scheduler anywhere. The only unattended things are your own scheduled tasks, and `ticket.py`, `paper.py` and `kite.py` refuse orders without a typed yes. |
| NSE cash equity, MIS, beginner defaults | Done | |
| Limits stored as percent of capital | Done (new) | `limits_pct` in the config; rupee values derived; a hand-edited rupee value that disagrees is overruled and reported. |
| max loss per trade incl. charges, at the stop | Done | Includes slippage too (`limits_include_slippage`, you confirmed). |
| daily loss = realised + open risk; weekly; drawdown pause | Done | Pause at 8% below the baseline blocks entries, and `morning` says "stop and review". |
| 3 trades a day, 1 open position, 75% position, 10% buffer, reward:risk 1.5 after charges, charges 25% | Done | |
| losing-streak brake (2 losses) and 15-minute cooldown | Done (new) | |
| live start at 50% risk for 2 weeks | Done (new) | Automatic from the first live trade date; `live_start_risk_pct`, `live_start_weeks`. |
| flat_by | **Different** | The spec says 15:10; the build uses 15:00 because you chose 15:00 earlier. It is one config value (`flat_by`). Zerodha squares off MIS at 15:12 to 15:25 (unverified). |
| slippage 0.05%, 4 weeks, 40 trades | Done | |
| Modes: paper default, live only when you set both switches and Kite is verified | Done | No tool writes `mode` or `kite_enabled`; `control.py set` refuses them. |
| Gates: risk PASS on the exact order, your typed yes recorded word for word | Done | Fingerprint match; words stored in the approvals table. |
| Gate (c): Claude's own Allow once card with every order tool on "Needs approval" | **Partial** | A script cannot see that setting. `kite.py check` refuses to verify until you pass `--tools-need-approval` yourself. |
| Never "yes to all", never two orders on one yes, no orders from scheduled tasks | Done | |
| No secrets stored, printed or logged | Done | Tests scan the code and the database module. `.gitignore` covers the whole `trader/` folder, `*.db` and `STOP`. |
| Standard-library Python 3.8+ | Done | A test rejects 3.9-only syntax. |
| Tests with offline fixtures and mocks, none deleted | Done | Some tests were changed because the rules changed (the "why" is now required, Notion has nine databases, live entries need Kite's price); each change is a stricter rule, none was weakened. |
| Verify, don't invent; record in VERIFY.md | Partial | Everything is recorded with its status. Most items are SECONDARY or UNVERIFIED because nothing could be fetched. |

### 5: verification

| item | status |
| --- | --- |
| Kite MCP tool list and whether it can place orders | SECONDARY. The hosted server probably cannot; you may need Zerodha's self-hosted server. RECHECK-AT-KITE. |
| Kite Connect order parameters, varieties, rate limits, token expiry | SECONDARY / RECHECK-AT-KITE. |
| Cover orders and GTT OCO for MIS | UNVERIFIED (assumed unavailable). See "Different" under section 10. |
| NSE tick sizes, circuit limits, ASM/GSM, holidays 2026, Muhurat, expiry day | Tick sizes and 2026 holidays SECONDARY. Circuit and ASM/GSM data have no official machine-readable link found, so they are files you export. 2027 holidays do not exist yet: the tools refuse to trade a 2027 date. Expiry day: Tuesday (SECONDARY). |
| Zerodha MIS list, square-off, charges | SECONDARY. The MIS list is a file you export. |
| SEBI retail algo rules | SECONDARY. Written up in VERIFY.md section 6; how they apply to a one-order-at-a-time setup is NOT settled. Ask Zerodha. |
| Notion connector | VERIFIED from its tool definitions; never exercised against your workspace. |
| Yahoo `.NS` reachable and typical delay | BLOCKED from the sandbox: untested. |

### 6: data architecture

| item | status | note |
| --- | --- | --- |
| SQLite at `trader/trader.db`, path configurable, in `.gitignore` | Done | `--db` on every tool; `db_path` in the example config is informational. |
| The table list | Done | All 18 tables you listed exist (22 in all). Extra: order_events (status changes of an order, because orders never change), trade_notes (your "why" and "how I felt"), discrepancies, meta. |
| Money as integer paise, keys, indexes, one db.py, every write a transaction | Done | The full record of a trade is also stored as JSON beside the paise columns. |
| Append-only with hash chain | Done | Triggers refuse UPDATE and DELETE on 12 tables; `db check` recomputes the chains. A removed *last* row cannot be detected from inside the file (backups and Notion cover that). |
| Positions, account, summaries, alerts, news, scans, outbox | Done | Working tables, meant to change. |
| `db backup` (30 kept, automatic after reconcile), `db check`, `db export` with the yearly tax file | Done | Plus `restore`, `status`, `migrate`. The tax file covers live trades only. |
| Migration from JSON, counts reported, JSON kept | Done | Tools without a database keep working on the JSON files. |
| Source of truth | **Different** | With a database, the tools load and save through it and rewrite `paper.json` / `live.json` as a read-only copy; a hand-edited copy is ignored and reported. Approvals and risk verdicts are written to both the day's JSON file and the database. |

### 7 and 8: skill by skill, charges

| skill | status | note |
| --- | --- | --- |
| scanner | Done | NIFTY 100 default (50, 500, all). ASM/GSM and non-MIS exclusion work from files you export. Price floor ₹50 and volume floor are configurable. |
| entry, exit | Done | Tick rounding, nonsense-stop refusals. |
| timemachine | Done | Limits stated in its output. |
| budget | Done | Whole shares, says which limit decided, writes a broker-neutral order plus the Kite payload. |
| risk | Done | No PDT logic. All limits and protections. |
| broker (router) | Done | paper.py and kite.py are the only two that create orders. |
| alerts | Done | NSE calendar; stop, target, flat-by. |
| news | Done | Yahoo `.NS`, NSE announcements, Moneycontrol; feed URLs unverified. |
| analyst | Done | Pasted results table or text; no stable public source found. |
| insider | Done | A CSV you export from NSE, or the RSS (best effort). |
| charges calculator | Done | Used by budget, risk, paper, live P&L and reports; charges shown separately; live charges "estimated" until you enter the contract note. |

### 9: paper mode with Notion as the paper account

| item | status |
| --- | --- |
| "Paper Account" in Notion: balance, baseline, open positions, realised P&L, mode | Done (new): the Account database. |
| Order created on your yes, filled at the next 1-minute bar's open plus 0.05%, stop and target replay, stop wins a tie, flat-by exit, full charges | Done |
| Skipped signals with what would have happened | Done |
| Paper history kept forever and separate from live | Done |

### 10: live mode

| item | status | note |
| --- | --- | --- |
| Entry order, cancel, flatten, reads | Done | Entry BUY MIS, stop SELL SL-M, cancel, market SELL exit; reads orders, trades, positions, margins. `get_holdings` is named but not used. |
| Stop and target "via the verified replacement for brackets (CO or entry + GTT OCO)" | **Different** | Neither CO nor GTT for MIS could be verified, so the stop rests at Zerodha as an SL-M order and the target is an alert followed by a new order with your yes. |
| Connection check: connected, margins, positions, no unexpected position, tools on "Needs approval", login reminder | Done | The "Needs approval" setting is your attestation. |
| Kite's live price, 0.3% move re-check, margin | Done (new) | A moved price uses the yes up; a new check and a new yes are needed. |
| Placing exactly the ticket's payload, recording the broker order id | Done | Payload hash is checked against the one you approved. |
| After placing: read status, fill from Kite, confirm the stop exists, alert loudly | Done | `kite.py sync` prints WARNING lines; the "offer an immediate exit" step is a message pointing to the stop ticket and the flatten command, each needing your yes. |
| No auto-retry; order book read first | Done | |
| Exits recorded from Kite; flatten by flat_by with your yes | Done | |
| Notion in live mode with Mode = live, broker ids, Live Account from Kite | Done (new) | |
| Live reconcile with a Discrepancies database, Kite wins, never silently fixed | Done (new) | |
| Actual contract-note charges | Partial | Entered by hand with `kite.py set-charges`; no contract-note parser. |
| Everything for live is built, mock-tested and disabled | Done | Nothing was run against a real Kite account. |

### 11 to 14

| item | status |
| --- | --- |
| Notion setup asks for the parent page; nine databases; IDs saved in the config | Done (needs your page name) |
| Sync only through the outbox; ID-matched updates; counts and totals compared after each sync | Done (`journal.py verify`) |
| Rebuild both ways | Done (`rebuild --into-db`, `requeue`) |
| Weekly report, paper and live separately | Done |
| Capital change, other limits (show, confirm, record), hand-edit detection | Done (new) |
| Pause and resume; kill switch with the STOP file | Done (new) |
| Circuit within 2%; ASM/GSM; MIS-only | Done (needs your daily files) |
| Results, RBI, Budget, expiry days: warn and half size | Done; RBI days are yours to enter |
| VIX above 20 or NIFTY gap above 1% half size; VIX above 25 no entries | Done (data from `scan.py market`) |
| Stale data over 3 minutes blocks entries | Done |
| "Why are you taking this trade?" before the yes; "How did you feel?" after | Done (required `--why`; `paper.py feel`) |
| TOTP reminder in the setup guide | Done (docs and the morning check) |
| Go-live check and steps | Done |

### 15 and 16: talking to you, docs

| item | status |
| --- | --- |
| Plain language, one ticket per order with PAPER/LIVE in capitals and the exact payload | Done |
| Morning check | Done (`control.py morning`) |
| README, setup, Notion setup, daily routine, capital/pause/kill, paper to live, backup and restore | Done (new docs folder) |
| A worked example on a real NSE stock with real data, with a date | **Missing** | No real NSE data was reachable. The README example uses synthetic bars and says so. |
| Fine print incl. SEBI's study "only if verified" | Done | The README says the sebi.gov.in page itself was not read. |
| MIT licence kept, original author credited, manifests renamed | Done |
| Branch named `india-conversion` | **Different** | This work lives on `claude/us-to-india-stock-conversion-3hpnif`, the branch the session was bound to. Merge or rename as you like. |

## What we will miss (the honest list)

1. **Nothing has run on real data.** Feed URLs, CSV column names, Yahoo response shapes, Notion column names with the rupee sign and the
   ampersand, and every Kite tool argument are written from documentation and memory. Expect to fix small things on day one.
2. **Protection data is manual.** ASM/GSM lists, the MIS list and the price bands are files you export each morning. In paper mode a missing
   file is a warning; in live mode it blocks. If you skip the files, the circuit, surveillance and MIS protections do not run.
3. **No bracket or OCO target.** The target is an alert and a new order with your yes. In a fast move the price can pass it.
4. **"Needs approval" cannot be verified by code.** It is your attestation.
5. **The hosted Kite MCP server probably cannot place orders.** You may have to run Zerodha's own server with your own API key; SEBI's retail
   API rules (static IP and more) are unconfirmed for your account.
6. **The hash chain cannot see a deleted last row.** Use the backups and the Notion record to cross-check after a crash.
7. **2027 holidays, RBI policy days and the Union Budget date** are not in the tools. The tools refuse 2027 dates until you add the holiday list.
8. **Charges are estimates** until you enter the contract note figure.
9. **Paper results flatter reality.** Real fills slip more, and you will be slower than the simulation.
10. **A worked example on real data is missing** until you run the tools on your own machine.

## Plan for what is left (in order)

1. On your machine: `python3 trader-budget/size.py init`, `db.py check`, then `scan.py universe`. Fix anything that fails and tell me what it said.
2. Export the four protection files each morning for a week and see how long it takes. If it is too much, tell me, and I will look at what
   can be fetched without scraping tricks.
3. Name the Notion page and run the Notion setup; read the first sync back with `journal.py verify`.
4. Paper trade for four weeks (about 20 trading days, at least 40 trades for a review).
5. `golive.py check`, then decide for yourself. Before any live day, work through VERIFY.md "Items to re-check when you connect Kite".
