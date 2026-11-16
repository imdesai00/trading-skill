# Dhan as the broker: analysis and integration plan

Written 2026-11-16, after you chose Dhan instead of Zerodha. Everything below was found by web search and by reading a few GitHub pages from a sandbox that could not open Dhan's own sites (`dhan.co`, `dhanhq.co`, `docs.dhanhq.co`). So **nothing here is read from Dhan's pages directly**. Each fact carries a status: SECONDARY (a search snippet or a community or SDK page) or UNVERIFIED (from memory or inferred). [VERIFY.md](../VERIFY.md) section 15 holds the same table with sources.

## 1. What Dhan offers (analysis)

| topic | finding | status |
| --- | --- | --- |
| Official MCP server | Dhan describes a first-party MCP server that connects a live Dhan account to Claude Desktop, Cursor, VS Code or any MCP client: holdings, positions, funds, quotes, market depth, option chains, margin, and placing, modifying and cancelling orders, **including super orders** with target and stop loss. A guide gives the connector address as `https://mcp.dhan.co/mcp`. Sources: [Dhan's blog](https://dhan.co/blog/trading-strategies/why-trade-intraday-with-claude-try-dhan-mcp-skills/), [the DhanHQ docs](https://docs.dhanhq.co/mcp/tools/orders), [a walkthrough](https://medium.com/@manavghosh/your-broker-now-speaks-english-part-1-research-and-portfolio-reviews-with-claude-and-dhan-d544c87629b5) | SECONDARY (search snippets) |
| Official MCP tool names | **not found.** The docs page `docs/mcp/tools/orders` exists but could not be opened. Community servers use names like `place_order`, `get_fund_limits`, `get_positions`, but those are not Dhan's | UNVERIFIED. The adapter takes the names from a config table you fill in after connecting |
| Authentication | Dhan's API: an access token valid for 24 hours, renewable; an API key valid 12 months but a login session each day ([authentication docs](https://dhanhq.co/docs/v2/authentication/)). The hosted MCP presumably handles login through Dhan's own consent screen; the Medium guide says every session asks for explicit consent | SECONDARY / UNVERIFIED for MCP |
| Static IP | the DhanHQ release notes say a static IP is required for all **order** APIs (normal, super and forever orders); data calls are said to work without one. Rate limit for orders: 10 per second ([rate limits](https://dhan.co/support/platforms/dhanhq-api/what-are-the-api-rate-limits-for-dhan/)). Whether the hosted MCP changes this is not known | SECONDARY. RECHECK |
| Instruments | Dhan identifies a stock by a numeric `securityId` plus an exchange segment (for NSE equity, `NSE_EQ`), not by trading symbol. The mapping comes from Dhan's instrument list (`api-scrip-master.csv`). The same symbol can have several ids (a bond called IRFC-EQ was reported), so exchange, segment and series must be filtered ([instruments](https://dhanhq.co/docs/v2/instruments), [forum](https://madefortrade.in/t/issue-same-equity-symbol-for-multiple-instrument/16779)) | SECONDARY. Column names UNVERIFIED |
| Product type for intraday | `INTRADAY` (alongside `CNC`, `MARGIN`, `MTF`) | SECONDARY |
| Order types | `LIMIT`, `MARKET`, `STOP_LOSS`, `STOP_LOSS_MARKET`. An API **market order is converted to a limit order with market price protection** (per the `dhanhq-skills` reference). `STOP_LOSS` needs both a price and a trigger price | SECONDARY ([dhanhq-skills orders.md](https://github.com/dhan-oss/dhanhq-skills/blob/main/skills/dhanhq/references/orders.md)) |
| **Super order** | one request that clubs an **entry leg, a target leg and a stop-loss leg**, with an optional trailing stop. Product types INTRADAY, CNC, MARGIN, MTF. The entry leg can be modified only while the order is PENDING or PART_TRADED; after it trades, only the target and stop legs can change. Untriggered intraday orders are cancelled automatically ([super order docs](https://dhanhq.co/docs/v2/super-order/), [terms](https://dhan.co/regulatory-and-other-information-pdf/Dhan_Super_Order_TC.pdf)). A GitHub issue reports validation quirks with market super orders ([#87](https://github.com/dhan-oss/DhanHQ-py/issues/87)) | SECONDARY |
| Order and trade fields | order list: `orderId`, `correlationId`, `orderStatus`, `transactionType`, `exchangeSegment`, `productType`, `remainingQuantity`, `averageTradedPrice`, `filledQty`; statuses `TRANSIT`, `PENDING`, `REJECTED`, `CANCELLED`, `TRADED`, `EXPIRED`. One thread says the trade book's average price field is `avgTradePrice`. Success response: `status: "success"`, `data.orderId`, `data.orderStatus` | SECONDARY, partly conflicting. The reader accepts several spellings |
| Intraday auto square-off | Dhan's FAQ pages disagree: **3:10 PM** (equity, before the closing auction), 3:18 PM onward, and 3:20 PM appear. Auto square-off charge ₹20 per order + GST | SECONDARY, conflicting. Your 15:00 flat-by is earlier than all of them |
| Charges | intraday brokerage ₹20 or 0.03% per executed order, whichever is lower; NSE exchange charge 0.00297% appears on Dhan's pricing page. These match the rates already in `trader/config.json` (written for Zerodha). STT, SEBI fee and stamp duty are statutory and the same for every broker | SECONDARY. Check [dhan.co/pricing](https://dhan.co/pricing) |

## 2. What changes, compared with the Zerodha design

| | Zerodha design (built) | Dhan design (this plan) |
| --- | --- | --- |
| how Claude reaches the broker | self-hosted Kite MCP (hosted one reportedly cannot place orders) | **Dhan's official hosted MCP** (reported), nothing to host |
| a live trade | three orders: entry, then a separate resting SL-M stop, then exit | **one super order** (entry + stop + target legs) = one ticket, one risk check, one typed yes, one Allow once card. The stop exists from the instant the entry fills; the target is now a resting leg too |
| exit before the target or stop | cancel the stop, then sell | cancel the super order's legs (its own yes), then sell (its own yes): same safe two-step |
| instrument | trading symbol | `securityId` from a mapping file you build from Dhan's instrument list |
| entry price | market order | a **marketable limit** at plan entry plus a small buffer (default 0.1%), because API market orders become limit orders anyway; the price is fixed on the ticket so the payload hash still matches |
| tool names | known from the Kite MCP README | **unknown**: listed in `dhan_tools` in your config, filled in after you connect and read the tool list |
| static IP | possibly needed | stated as required for order APIs. Whether the MCP route is exempt is unknown |
| the two switches | `mode: live` and `kite_enabled: true` | `mode: live`, `broker: "dhan"` and `dhan_enabled: true` (all set by you) |

The super order also fixes a gap flagged in [AUDIT.md](AUDIT.md): the Zerodha design could not give a resting target. Dhan's super order does, if its behaviour matches the documentation.

## 3. What stays exactly the same

Paper mode and everything before the broker: the scanner, news, entry, exit, time machine, budget, risk check, the ticket, your typed yes with a "why", the database, the Notion journal, the controls and the protections. All safety rules are unchanged: paper by default; `mode` and the broker switches set only by you; a risk PASS on that exact order, your typed yes for that one order and Claude's Allow once card; no secrets anywhere in the pack; no order from a scheduled task.

## 4. The integration plan

| step | what | where | status |
| --- | --- | --- | --- |
| 1 | Analysis (this page) and VERIFY.md section 15 | docs | done |
| 2 | Refactor `kite.py` so broker differences sit in a few overridable hooks (tool names, payload builder, field names, state file); all 44 existing Kite tests must still pass | `trader-broker/kite.py` | done in this change |
| 3 | `dhan.py`: `DhanAdapter` (super-order payload, plain sell, cancel, `securityId` lookup, tolerant readers for order, trade, position, funds and LTP results), `securities` command to build the id mapping, same commands as `kite.py` (`check`, `prepare`, `record`, `sync`, `reconcile`, `set-charges`, `prices`, `status`) | `trader-broker/dhan.py` | done in this change |
| 4 | Route by broker: `live_call`, the approval's payload hash and the ticket header in `ticket.py`; the live gate in `check.py`; `control.py` refuses to change `broker` and `dhan_enabled` | broker, risk, control | done in this change |
| 5 | Tests against a **fake Dhan**: payload, gates, one yes one order, price and cash checks, fills, stop and target closes, reconcile, mapping, routing | `tests/test_dhan.py` | done in this change |
| 6 | Docs: Phase 3 becomes Dhan (`PHASE_3_DHAN.md`), this page, CONFIG, SAFETY, GO_LIVE, README, glossary, troubleshooting | `docs/` | done in this change |
| 7 | **Your part, on your machine**: connect Dhan's MCP, read its real tool list, fill `dhan_tools`, save a real sample of each read tool's output, register the static IP if Dhan says it is needed | you | open |
| 8 | Replace guessed field names with the real ones; add the real samples as fixtures; mark VERIFY.md items VERIFIED | next | open |
| 9 | Dry run with no order, then one share, then the half-risk two weeks | next | open |

## 5. How you connect it (when Phase 3 starts)

Not now: Phase 1 (paper trading on real data) comes first. When it is time:

1. Open a Dhan account and read, on Dhan's own site, what API and MCP access needs: any Data or Trading API subscription, the static-IP rule, and whether the hosted MCP route needs it.
2. In Claude, add the Dhan connector at the address Dhan documents (reported: `https://mcp.dhan.co/mcp`) and approve its consent screen yourself. Do **not** paste a token, client id secret or password into a chat or a file in this repository.
3. Open the connector's tool list in Claude. Set every tool that places, modifies, cancels or exits orders (including super orders and forever orders) to **Needs approval**. Read-only tools may stay on Always allow.
4. Build the id mapping from Dhan's instrument list: download `api-scrip-master.csv` yourself, then `python3 trader-broker/dhan.py securities --csv api-scrip-master.csv` (NSE equity only; it reports what it kept).
5. Put the real tool names in `trader/config.json` under `dhan_tools` (the defaults are guesses).
6. Run the morning connection check described in [GO_LIVE.md](GO_LIVE.md), then edit `"mode": "live"`, `"broker": "dhan"` and `"dhan_enabled": true` yourself. The first two live weeks run at half risk automatically.

## 6. Risks specific to Dhan

- **Tool names and fields are guesses until you connect.** The adapter fails loudly (an unknown tool or a missing field stops the order) rather than guessing.
- **A super order is three legs you did not place one by one.** The ticket shows all three prices. If Dhan's behaviour differs from its documentation (for example a leg that is not cancelled with the main order), the cancel-then-exit step can leave an order behind. The reconcile step looks for exactly that.
- **A marketable limit entry may not fill** if the price runs away; the order then waits PENDING. It is cancelled at flat-by like any other.
- **Static IP**: if you use a laptop on changing networks, the order routes may refuse your orders.
- All the earlier honest limits still apply: a stop is not a promise, paper flatters reality, charges are a big share of a ₹500 risk, and most intraday traders lose money. Not financial advice.
