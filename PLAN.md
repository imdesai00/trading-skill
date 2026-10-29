# PLAN.md: converting day-trader-skill to NSE intraday, paper first

Written 2026-10-29, after reading the upstream README, all 11 SKILL.md files,
the 11 scripts and the 12 test files (315 tests, all passing on import), and
after the checks in VERIFY.md. Nothing in the skills has been changed yet.

## What the upstream code is

- 11 skill folders, each with one standard-library script; each script is
  self-contained (its own clock, NYSE calendar, Yahoo fetcher) so a folder can
  be copied alone.
- Handoff through `trader/`: `watchlist.json -> entry.json -> plan.json ->
  timemachine.json -> orders.json -> state/risk.json -> state/broker.json`,
  with `config.json`, `prices.json`, `portfolio.json` beside them.
- Safety model already close to yours: risk fingerprints each exact order,
  `ticket.py approve` only records a strict per-order "yes", nothing calls a
  broker. Tests run offline on real-data fixtures and replay every README number.

## Design decisions (say no to any of these)

1. **Keep the pipeline, folders and file handoff.** Add three skill folders:
   `trader-journal` (Notion outbox, sync, rebuild, daily summary, weekly
   report), `trader-golive` (golive.py) and `trader-insider` (replaces
   trader-pelosi).
2. **Clock:** IST is a fixed UTC+05:30 with no daylight saving, so the time code
   gets simpler. The NSE calendar (holidays, CAS, Muhurat) is copied into each
   script like upstream does, plus a new test that fails if any copy differs.
3. **One charges calculator**, `trader-budget/charges.py`, imported by budget,
   risk, paper broker and journal (a second copy of the rates is how they
   drift apart). Rates are read from `trader/config.json` `charges`, with the
   date they were checked.
4. **Config:** your limits become the code defaults and ship in
   `trader-budget/config.example.json`; `size.py init` writes
   `trader/config.json`. Keys renamed to rupees: `max_loss_per_trade_inr`,
   `max_daily_loss_inr`, `max_weekly_loss_inr`, `max_drawdown_pause_inr`, etc.
   `mode: "paper"`, `kite_enabled: false`. The tools never write these keys.
5. **Orders:** each order is written broker-neutral (`symbol, side, qty,
   entry_type, stop, target, product: MIS`) plus the Kite payload from
   VERIFY.md section 2 (`exchange NSE, tradingsymbol, transaction_type,
   quantity, product MIS, order_type, variety regular, market_protection -1`
   on MARKET / SL-M). The fingerprint covers the neutral order, so risk PASS
   and your yes bind to the same thing in paper and live.
6. **Stop and target in live mode (the bracket replacement).** VERIFY.md says
   Zerodha has no bracket, almost certainly no cover order, and no GTT for MIS.
   So kite.py does: (1) entry order, MIS; (2) after the fill, ONE resting
   `SL` sell (MIS) at the stop, with a limit a few ticks below the trigger;
   (3) the target is NOT resting (a resting target and a resting stop can both
   fill and leave you short); trader-alerts rings at the target, and closing
   needs cancel-stop then sell, each its own order, each after your yes and
   Claude's card. Paper mode simulates exactly this, so the practice matches.
   Every assumption here is marked RECHECK-AT-KITE.
7. **Paper fills (paper.py):** entry at the open of the next 1-minute bar after
   your yes, plus 0.05%. Reconcile replays 1-minute bars after the entry; stop
   and target in one bar counts as the stop. One addition, stricter than your
   spec: if a bar OPENS below the stop (a gap through it), the stop fills at
   that open, not at the stop price. Flat-by exits at the 15:10 bar's close
   minus 0.05%. Full charges on both legs. Trade IDs `PT-YYYYMMDD-NN`.
8. **Risk (check.py):** PDT, Alpaca and US-ticker logic removed. Added: daily
   (realised paper net P&L + open risk at the stop, incl. charges), weekly
   (Mon to Fri, until next Monday), drawdown pause (balance ₹8,000 under
   ₹1,00,000: everything stops until you review), max 1 open position,
   reward:risk ≥ 1.5 after charges, charges ≤ 25% of target profit, no
   shorts / no margin, entry window 09:30 to 14:45, live mode refused unless
   `kite_enabled` and a verified Kite connection. Exits are never blocked.
9. **Notion:** scripts only write `trader/state/notion_outbox.json` (each row:
   database, Trade ID, properties, `notion_page_id` once created, `sent`).
   Claude sends rows with the connector tools in VERIFY.md section 8, records
   the page id, and updates by page id after that, so nothing duplicates. Rebuild
   pages through each database (`view` mode) and recreates the local log.
10. **Universe:** NSE `EQUITY_L.csv` (series EQ) intersected with NIFTY 100
    (configurable 50 / 500 / all); price ≥ ₹50 and an average-volume floor.
    A dated snapshot of the constituents ships in the repo as a fallback.
11. **Muhurat and other special sessions are not traded** by default.
12. **Tests:** every converted script keeps its test classes; US fixtures are
    replaced by NSE ones. The trader-pelosi tests go with trader-pelosi (you
    asked for it to be replaced); trader-insider gets its own. Kite and Notion
    steps are tested against mocks. I report the test count each phase.

## Phases (I stop after each, run the tests and show you the results)

1. Verification -> VERIFY.md -> this plan. **(done; OK given 2026-10-29: flat_by 15:00, branch, stop design)**
2. Core pipeline: scanner, entry, exit, timemachine, budget, risk, alerts; IST,
   NSE calendar with CAS, tick sizes, ₹ formatting, your limits. **(done)** Also
   moved in from later phases because the tests needed them: the charges
   calculator and trader-broker's ticket.py (the yes gate) on the new order format.
3. Paper broker, charges calculator, end-of-day reconcile, skipped signals. **(done;
   slippage in limits and SL-M confirmed 2026-10-29)**
4. Notion setup, outbox sync, rebuild, daily summary, weekly report. **(done)**
5. Kite adapter (disabled, mock-tested) + golive.py check / steps. **(done)**
6. News (Yahoo .NS + NSE RSS if reachable + Moneycontrol RSS, added on your request
   of 2026-10-29), analyst (pasted results), insider (NSE PIT RSS). Moneycontrol's
   feeds are market-wide, so headlines are matched to your stocks by company name
   and tagged with the source; each feed can be switched off in the config. Groww
   is left out: it has no public news feed, only a web page that would have to be
   scraped (VERIFY.md section 10). **(done; trader-pelosi replaced by trader-insider,
   facts.py by results.py; VERIFY.md section 12)**
7. README for India, worked example, full test run, summary. **(done. The worked example uses
   SYNTHETIC data because NSE, Yahoo and Zerodha were unreachable; the README says so.)**

## Changes made in Phase 2 (say no to any of them)

- **Slippage counts in your limits.** `limits_include_slippage: true` counts the
  paper slippage (0.05 % a side) in the ₹500 max loss and the 1.5 reward:risk.
  Without it, a trade sized to exactly ₹500 loses about ₹563 at the stop in the
  paper record. Consequence: a 2R plan on a ₹1,400 stock with an ₹8 risk is about
  1.2R after slippage and charges, so most trades need a 3R target to pass.
- **The live stop is SL-M** (config `stop_order_type`; `SL` is the alternative).
- **Yahoo's spark batch endpoint is dropped** for one chart request per symbol.
- **trader-budget's weights mode (the US 13F split) is removed:** it made plain
  buys with no stop, which your rules block anyway.
- **Upstream's 24 committed `.pyc` files are removed** and `.gitignore` added.
- **Tests:** 315 upstream, 236 now. The difference is tests of removed US
  features (Nasdaq Trader symbol files, spark, NYSE early closes and daylight
  saving, Alpaca bracket orders, PDT, options and crypto symbols, weights mode).
  The upstream tests of logic that still exists (verdict thresholds, seeds,
  stale data, a wick vs a close, a dozing computer, network retries) were ported.
  News, analyst and Pelosi tests were rewritten in Phase 6 (see below).

## What I needed from you before Phase 2 (answered)

1. **`flat_by` 15:10 vs Zerodha's 15:12 forced square-off for CAS stocks.**
   Nearly all NIFTY 100 stocks are CAS stocks, so continuous trading for them
   ends at 15:15 and Zerodha squares you off at 15:12. 15:10 leaves 2 minutes
   for a live exit order, after your yes and Claude's card. I recommend 15:00.
   I will not change it unless you say so.
2. **Network access.** This sandbox cannot reach Yahoo, NSE, niftyindices,
   Zerodha or Kite. Without them I cannot capture real NSE fixtures, test .NS
   data or write the worked example on real data. Either add these hosts to
   the environment's allowed domains (environment settings, Network access:
   `query1.finance.yahoo.com`, `query2.finance.yahoo.com`,
   `feeds.finance.yahoo.com`, `nsearchives.nseindia.com`, `www.nseindia.com`,
   `www.niftyindices.com`, `zerodha.com`, `support.zerodha.com`, `kite.trade`),
   or I build a small `capture.py` you run once on your machine and commit its
   output. Until then, logic tests use clearly labelled synthetic bars.
3. **Branch.** You asked for a branch named `india-conversion`. This session
   is only allowed to push to `claude/us-to-india-stock-conversion-3hpnif`, so
   the work lives there; `main` keeps the original. Rename it later if you like.
4. **The live stop design in decision 6** (stop resting at the broker, target
   closed by hand after an alert). OK?

## Hard truths about the plan itself

- **40 trades cannot tell skill from luck.** With 40 trades, a 50% win rate has
  a 95% range of roughly 35% to 65%. golive.py will say this in plain words
  rather than hand you a green light. 4 weeks is a dress rehearsal, not proof.
- **You may not even reach 40.** One setup, one position at a time, 3 a day,
  entries only 09:30 to 14:45, and a 1.5R-after-charges filter will throw out
  many signals. Expect fewer trades than days x 2.
- **Paper flatters you.** Real stops fill worse than 0.05% in a fast move, and
  a market order in a thin stock moves the price. The gap-through rule above
  helps; it does not fix it.
- **Charges are not small at this size.** About ₹74 a round trip at ₹75,000,
  so a trade aiming for ₹300 gross loses a quarter of it to charges and is
  rejected by your own 25% rule. Small-risk trades will mostly be refused.
- **Going live is heavier than "connect Kite".** If the hosted Kite MCP server
  cannot place orders (likely), you need a self-hosted server, a paid Kite
  Connect API key and possibly a registered static IP. Budget for that now.

## Final test count

See the last commit message and the README. Phase 6 replaced the US news, SEC-facts and Pelosi tests with tests of the NSE news monitor, the results reader and the insider tracker.
