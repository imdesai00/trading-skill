# Phase 2: a local dashboard

Status: **planned**. Start only after the Phase 1 exit test in [ROADMAP.md](ROADMAP.md) has passed. Until then the way to use the pack is to talk to Claude from your project folder ([HOW_TO_USE.md](HOW_TO_USE.md)).

## Why a dashboard, and why not yet

Chat is good for deciding (the "yes" and the "why" are a deliberate pause). It is poor for looking: balance, today's trades, limits used, a chart, a list. A dashboard fixes that. It should not become a faster way to trade, because speed and clicks are how beginners overtrade.

It waits for Phase 1 because a dashboard built on feeds that have never run on real data just shows empty or wrong boxes.

## Principle: look and plan, never decide

| in scope (Phase 2) | not in scope (not now) |
| --- | --- |
| balance, capital, baseline, today's and this week's P&L | approve, place, modify, cancel or close any order |
| trades today, wins and losses, charges, limits used as bars (daily, weekly, drawdown, trades, open positions) | changing capital, limits, `mode` or `kite_enabled` |
| pause or kill-switch banner, events, India VIX, results today, protection-file status, report status, database health | the kill switch button (use chat or the `STOP` file; see "Open questions") |
| trades table (entry, stop, target, exit, charges, net, R) and skipped signals with what would have happened | anything for live mode that needs Claude's Allow once card |
| equity curve (balance after each trade) | scheduled or automatic actions |
| price chart for one symbol with entry, stop and target lines | |
| watchlist view with a simple add and remove (writes `trader/watchlist.json`) | |
| a planner: type entry, stop and target; see shares, loss at the stop with charges, profit at the target after charges, reward:risk, which limit decides, and whether the risk check would pass | |

The planner uses the same sizing and risk code as the real tools, so the numbers cannot disagree. It writes nothing except an optional `trader/plan.json` that you can hand to `/trader-budget` in chat. A plan is not an order.

## How it runs

- One new skill folder, `trader-ui/`, with `ui.py` and a `SKILL.md`. Standard-library Python only: `http.server` plus static HTML, CSS and JavaScript files. No installs, no build step, no CDN (the page must work offline).
- Start it with `python3 trader-ui/ui.py` (or ask Claude to). Open `http://127.0.0.1:8765`. It binds to the local machine only, never to a network address.
- It reads `trader/trader.db` in **read-only mode** (`file:...?mode=ro`), so a bug in the UI cannot change the record.
- Claude Desktop cannot embed it, so it runs in your browser next to the chat. In Claude Code, the port can be shown in a preview.
- Charts are drawn in inline SVG by a small script in the page. No chart library.

## Screens

1. **Today**: the morning-check facts as cards, with the limits bars and any banner (pause, kill switch, drawdown, protection data missing).
2. **Trades**: today and history, filter by mode, symbol and date. A click opens a trade with its chart, your "why" and "how I felt", and the order events.
3. **Planner**: the what-if form, with a chart of entry, stop and target.
4. **Watchlist**: the scanner's hits and your own list.
5. **Record**: database check result, last backup, discrepancies (live), latest report.

Paper and live are always shown separately, never summed.

## Data it needs

- From the database: `account`, `trades`, `fills`, `skipped_signals`, `orders`, `order_events`, `approvals`, `trade_notes`, `risk_verdicts`, `pauses`, `discrepancies`, `config_history`.
- From files in `trader/` and `trader/state/`: the config, `watchlist.json`, `plan.json`, the protection files, `news.json`.
- For price charts: 5-minute bars through the same Yahoo fetch code the scanner uses. If that feed is not working, the chart says so and the rest still works.

## Work breakdown

1. `ui.py`: server, read-only database access, JSON endpoints (`/api/today`, `/api/trades`, `/api/trade/<id>`, `/api/equity`, `/api/limits`, `/api/chart/<symbol>`, `/api/plan`, `/api/watchlist`).
2. Reuse, do not copy, the logic: the sizing, charges and risk functions are imported the way other skills do it (identical copies of the shared modules, kept in step by the copy tests).
3. Front end: five pages, plain HTML, one CSS file with light and dark themes, one JavaScript file for fetching and SVG.
4. Planner endpoint: calls `size_trade` and `check` in memory with the typed numbers and returns the same text the chat tools print, plus structured numbers.
5. Watchlist endpoint: the only write, to `trader/watchlist.json`, limited to valid NSE symbols.
6. `SKILL.md`, docs page, and a README line.

## Security

- Bind to `127.0.0.1` only. Refuse any request whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>` (guards against DNS rebinding).
- Read-only database connection. The single write (the watchlist) is validated and limited to one file.
- No secrets exist in the UI's reach, and it never asks for any. It shows no API key, token, password or TOTP code because it never reads one.
- No order endpoints exist. A test fails if one is added.
- No telemetry and no outside requests except the optional price-bar fetch the scanner already makes.

## Tests (offline, standard library)

- Start the server on a free port against a temporary database with known trades; check every endpoint's numbers against the database and against the CLI tools' output.
- Planner: for a grid of entries, stops and targets, the UI's shares, loss and reward:risk equal `size.py` and `check.py`.
- The database file is unchanged after the server runs (hash before and after).
- Host-header and bind-address tests; a test that no route accepts POST except the watchlist; a test that the source contains no `place`, `approve` or `order` write endpoint.
- Page tests: required elements exist, the HTML has no external URLs.
- The skill unzips and runs on its own like every other skill.

## Acceptance criteria

- You can answer "how am I doing today and this week, and how close am I to my limits" in under ten seconds from the first screen.
- The planner's numbers match the chat tools exactly for 20 sampled plans.
- Killing the server, or a crash in it, changes nothing in the database or the config.
- All previous tests still pass, plus the new ones.

## Open questions to settle at the start of Phase 2

1. A kill-switch button: it only creates a file and makes the system safer, so it is arguably fine. Decide once you have used the chat version.
2. Approving a **paper** trade from the page, with the typed word "yes" and a required "why" field. Possible, but it removes the pause that the chat gives. Default answer: no.
3. Whether to show the chart from Yahoo bars, or only from the bars the paper broker already stored for reconcile.
4. Port number and whether the page should auto-refresh (default: a manual refresh button, no polling loops).
