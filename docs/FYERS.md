# FYERS as a broker, and TradingView for paper trading: analysis and plan

Written 2026-11-16. Same limits as [DHAN.md](DHAN.md): found by web search and by reading one GitHub page, from a sandbox that could not open `fyers.in`, `tradingview.com` or most broker sites. **Nothing here was read from FYERS's or TradingView's own pages.** Statuses: SECONDARY (a search snippet, an SDK page, a community post) or UNVERIFIED. [VERIFY.md](../VERIFY.md) section 16 holds the same table with sources.

You asked for two things: connect FYERS, and use TradingView for paper trading. The first is built. The second cannot be built the way you described, for reasons below. Read section 3 before you spend any time on it.

## 1. FYERS: what it offers

| topic | finding | status |
| --- | --- | --- |
| Official MCP server | FYERS's docs list an official MCP server at `mcp.fyers.in/mcp` with an Orders category (place, modify, cancel, baskets, scheduled orders), installed as a Claude Desktop connector with a FYERS login ([docs](https://fyers.in/api-docs/ai/mcp/)). An older FYERS Community post (about a year old) says the tool could not place, modify or cancel orders | SECONDARY, conflicting. Confirm with FYERS whether order placement is enabled for your account. RECHECK-AT-FYERS |
| Third-party MCP servers | several exist ([quantabox/fyers-mcp-server](https://github.com/quantabox/fyers-mcp-server) and others) and use your own API credentials. **This pack does not use them**: its rule is that no key or token goes through the pack | n/a |
| Tool names | not found | **UNVERIFIED.** `fyers_tools` in the config takes the real names after you connect |
| Instrument naming | a plain name: `NSE:SBIN-EQ`. No id map is needed (unlike Dhan) | SECONDARY (SDK examples) |
| Product types | `CNC`, `INTRADAY`, `MARGIN`, `MTF`. The FYERS Go SDK README says `BO` and `CO` are **deprecated and rejected by the API**; community threads show BO orders working in some setups and failing in others | SECONDARY, conflicting. The pack avoids BO and CO |
| Attached take-profit and stop-loss | the SDK shows optional `takeProfit`, `stopLoss`, `legType` (1 points, 2 percent) on INTRADAY orders, sent on the sync endpoint. Values are offsets in points or percent, not prices | SECONDARY. Not used until verified |
| Order type and side codes | type 1 limit, 2 market, 3 stop (SL-M), 4 stop-limit; side 1 buy, -1 sell (community answers) | SECONDARY |
| Order fields | `symbol`, `qty`, `type`, `side`, `productType`, `limitPrice`, `stopPrice`, `validity` (`DAY`), `disclosedQty`, `offlineOrder`, `orderTag`. The tag is not supported on BO and CO | SECONDARY |
| Order book | status codes 1 cancelled, 2 traded or filled are confirmed on the GTT order book page; 4 transit, 5 rejected, 6 pending are from memory. The order book returns the tag. Other field names (`tradedPrice`, `filledQty`, `netQty`, `fund_limit`, `equityAmount`, quote `lp`) are not confirmed | **UNVERIFIED. RECHECK-AT-FYERS** |
| Static IP, rate limits, daily login | not read for FYERS. Under the 2026 SEBI retail algo rules a static IP is likely for order APIs, as with Dhan; FYERS's API login is daily | UNVERIFIED |
| Charges | not read | UNVERIFIED. Check fyers.in's pricing page; the pack's rate table was written for Zerodha |

## 2. How the pack uses FYERS

`fyers.py` is a third live adapter next to `kite.py` and `dhan.py`, on the same base. Because FYERS's bracket and cover products are reported as deprecated and its attached TP/SL is unverified, it uses the **conservative three-order design**:

1. the **entry**: a plain INTRADAY market (or limit) order: its own risk check, typed yes and Allow once card;
2. after it fills, a separate **stop-loss** (SL-M) order resting at the broker: its own check, yes and card (`fyers.py stop-ticket`);
3. the **exit**: cancel the stop (own yes), then a market sell (own yes). The target is an alert, not a resting order.

Switches, all set by you: `"mode": "live"`, `"broker": "fyers"`, `"fyers_enabled": true`, plus `fyers.py check` from today (with `--tools-need-approval` and `--static-ip-confirmed`, which only you can confirm). The tools refuse to change any of them. Everything else (protections, database, reports, controls, kill switch, price and cash checks, reconcile) is shared.

**Dhan or FYERS?** Pick one broker for live; the config holds one `broker`. Dhan's super order is a better fit on paper (a stop that exists from the first fill and a resting target, one yes per trade) but needs the id map and has an unverified cancel behaviour. FYERS is simpler to map and uses the older, well-understood three-order flow, but the stop is a separate step you must not forget (`sync` warns loudly if it is missing). Neither has been run against a real account.

## 3. TradingView for paper trading: what I found, and what I did not build

| point | finding | status |
| --- | --- | --- |
| FYERS and TradingView | FYERS is a TradingView broker partner: you log in to FYERS from TradingView's trading panel and place real orders from a chart (stocks, futures, options) ([FYERS](https://fyers.in/products/tradingview), [TradingView](https://www.tradingview.com/blog/en/fyers-options-trading-on-tradingview-45078)). A paid TradingView plan may be needed | SECONDARY |
| TradingView paper trading | exists inside TradingView (the trading panel's "Paper Trading"); FYERS's support desk points to it for practice ([FYERS](https://support.fyers.in/portal/en/kb/articles/i-want-to-practice-trading-without-risking-real-money-how-can-i-do-it)). A FYERS Community post (about 79 days old) says **NSE symbols were blocked** from TradingView paper trading for compliance reasons | SECONDARY, conflicting. May not work for NSE at all |
| An API to place or read TradingView paper orders | **none found.** Alerts can call a webhook, but a webhook cannot reach a program on your computer without a public address, and it cannot place orders in the paper account | UNVERIFIED (nothing found) |
| Exporting paper trade history | not found | UNVERIFIED |

**Plain conclusion.** The pack cannot drive, read or reconcile a TradingView paper account, so a TradingView paper "integration" would be fiction. I did not build one. What I recommend instead:

- **Keep the pack's own paper broker as your paper account.** It is the only paper account that sits behind the risk check, the typed yes with a "why", the tick grid, real charges, the database and the reports. A paper account you cannot connect to those rules teaches habits you cannot test.
- **Use TradingView for what it is good at, by hand:** charts, drawing the opening range, and alerts on your phone. Nothing in the pack needs it.
- **Do not place real orders from TradingView's FYERS panel while you use this pack as your rule-keeper.** An order from the chart bypasses the risk check, your typed yes, the database and the kill switch.
- If you later want TradingView practice results in the database, that needs a verified export format first (Phase 1 task: check whether TradingView exports its paper history, and for which exchanges). Then a small importer is easy. Until then it is not worth the code.

## 4. Plan

| step | what | status |
| --- | --- | --- |
| 1 | This analysis; VERIFY.md section 16 | done |
| 2 | `fyers.py` on the shared adapter base (three-order design); shared connection check; routing for `broker: fyers` in the ticket, the approval hash and the risk check; the switches protected in `control.py` | done |
| 3 | A fix found while building it: after the live price moves and the yes is voided, the same order could not be approved again that day. It now can, but only after a new risk check | done |
| 4 | Tests against a fake FYERS (`tests/test_fyers.py`) | done |
| 5 | TradingView paper trading: analysis only; no code, for the reasons above | decided |
| 6 | **Your part, when Phase 3 starts:** connect FYERS's connector, read its tool list, set order tools to "Needs approval", fill `fyers_tools`, save real samples, confirm static IP | open |
| 7 | Replace guessed field names with the real ones; mark VERIFY.md items VERIFIED | open |

## 5. Risks specific to FYERS

- The tool names and result fields are guesses until you connect; the adapter fails loudly rather than guess.
- The older FYERS post says the MCP tool could not place orders. If that is still true for you, FYERS has no live route here and you would place orders by hand.
- Separate stop order: between the entry fill and the stop order there is a gap in which the position has no stop. The risk is the same as with the Zerodha design, and `sync` warns on it.
- All the earlier honest limits apply: a stop is not a promise, paper flatters reality, charges are a big share of a ₹500 risk, most intraday traders lose money. Not financial advice.
