# VERIFY.md: what was checked, how, and what is still unverified

Checked on **2026-10-29** (Thursday) from a cloud build sandbox, for the India
conversion of day-trader-skill (NSE cash, intraday MIS, Zerodha Kite later,
Notion journal).

## Read this first: how much of this is really verified

The build sandbox's network policy **blocked** every Indian market host:
`zerodha.com`, `kite.trade`, `mcp.kite.trade`, `www.nseindia.com`,
`nsearchives.nseindia.com`, `api.bseindia.com`, `query2.finance.yahoo.com`,
`feeds.finance.yahoo.com` (HTTP 403 at the proxy, or DNS failure). Only
`github.com` / `raw.githubusercontent.com` could be read directly. Everything
else below comes from web search results (snippets of the official page, or
third-party pages quoting it), not from reading the official page myself.

Status labels used below:

| label | meaning |
| --- | --- |
| **VERIFIED** | I read the primary source itself (official repo / source code / tool schema). |
| **PRIMARY-SNIPPET** | A search result quoted the official page (NSE circular, Zerodha page); I did not open the page. |
| **SECONDARY** | Only third-party pages (brokers, blogs, news, forum) say it. |
| **UNVERIFIED** | Conflicting or no source. Do not rely on it. |
| **RECHECK-AT-KITE** | Must be re-checked by you, on your machine, before `mode: live`. |

Every number the code uses from this file lives in `trader/config.json` (or a
data file next to the script), so you can correct it without touching code.

---

## 1. Kite MCP server

| item | finding | status |
| --- | --- | --- |
| Repo | github.com/zerodha/kite-mcp-server | VERIFIED (README read 2026-10-29) |
| Hosted endpoint | `https://mcp.kite.trade/mcp` (also `/sse`), "no installation or API keys required" | VERIFIED |
| Read-only tools | `login`, `get_quotes`, `get_ltp`, `get_ohlc`, `get_historical_data`, `search_instruments`, `get_profile`, `get_margins`, `get_holdings`, `get_positions`, `get_mf_holdings`, `get_orders`, `get_trades`, `get_order_history`, `get_order_trades`, `get_gtts` | VERIFIED (names; read-only is inferred from the names, the README does not label them) |
| Order tools | `place_order`, `modify_order`, `cancel_order`, `place_gtt_order`, `modify_gtt_order`, `delete_gtt_order` | VERIFIED (names) |
| Can the HOSTED server place orders today? | README: "The hosted version at mcp.kite.trade excludes potentially destructive trading operations for security. For accessing the other operations you can generate your own API keys and run the server locally." It does not name the excluded tools. | **UNVERIFIED / RECHECK-AT-KITE**. Working assumption: the hosted server **cannot** place orders; live orders need a self-hosted server with your own Kite Connect API key. |
| Self-hosting | needs a Kite Connect API key + secret, Go 1.21+; `EXCLUDED_TOOLS` env var removes tools (example `place_order,modify_order,cancel_order`) | VERIFIED |
| Tool parameter names (`place_order` arguments) | not in the README | **UNVERIFIED / RECHECK-AT-KITE**. The adapter will emit the Kite Connect field names (below) and must be re-checked against the MCP tool schema Claude shows once connected. |

Sources: https://github.com/zerodha/kite-mcp-server (README), read 2026-10-29.

## 2. Kite Connect (REST API)

From the official Python client source (github.com/zerodha/pykiteconnect,
`kiteconnect/connect.py`, master, read 2026-10-29). **VERIFIED** for the client;
the API docs page kite.trade/docs/connect/v3/orders/ was **blocked** for me.

| item | finding |
| --- | --- |
| Place | `POST /orders/{variety}` |
| Modify / cancel | `PUT` / `DELETE /orders/{variety}/{order_id}` |
| Read | `GET /orders`, `/orders/{order_id}`, `/orders/{order_id}/trades`, `/trades`, `/portfolio/positions` |
| Varieties | `regular`, `co`, `amo`, `iceberg`, `auction` |
| Products | `MIS`, `CNC`, `NRML`, `CO` |
| Order types | `MARKET`, `LIMIT`, `SL`, `SL-M` |
| Validity | `DAY`, `IOC`, `TTL` |
| place_order required | `variety, exchange, tradingsymbol, transaction_type, quantity, product, order_type`; optional `price, validity, validity_ttl, disclosed_quantity, trigger_price, iceberg_legs, iceberg_quantity, auction_number, algo_id, tag, market_protection` |
| GTT | `POST /gtt/triggers`, `trigger_type` `single` or `two-leg` (OCO); each leg needs `transaction_type, quantity, order_type, product, price` |
| `market_protection` | constant `MARKET_PROTECTION_AUTO = -1` in the client. Forum + GitHub issue #242: **required** for API MARKET and SL-M orders since the April 2026 exchange rules; `0` is rejected; LIMIT and SL are unaffected. SECONDARY / RECHECK-AT-KITE |

**Brackets, cover orders, GTT for MIS (this changes the design):**

| item | finding | status |
| --- | --- | --- |
| Bracket orders | Discontinued at Zerodha (Zerodha's own X account, 2025) | SECONDARY |
| Cover orders (CO) | Third-party explainers say BO and CO were discontinued (2020, peak-margin rules). The `co` variety still exists in the client library, which does not prove Zerodha accepts it. | **UNVERIFIED / RECHECK-AT-KITE** |
| GTT / GTT OCO for MIS | Forum + third-party guide: GTT is for CNC / MTF (and NRML for F&O), **not MIS**. A Zerodha reply said MIS GTT was "being worked on" (old post). | SECONDARY / RECHECK-AT-KITE |

**Consequence:** there is probably **no broker-side stop + target pair for an MIS
intraday trade at Zerodha**. The verified replacement is: entry order (MIS) and,
after it fills, ONE separate `SL` (stop-loss limit) or `SL-M` sell order (MIS)
resting at the broker as the stop. The target cannot rest at the same time: a
resting target LIMIT sell plus a resting stop sell for the same shares can BOTH
fill and leave you short. So the target is watched by trader-alerts and closed
by a new order after your yes (cancel the stop first). Section "Plan" in
PLAN.md has the details.

| item | finding | status |
| --- | --- | --- |
| Access token expiry | Kite staff on the developer forum: all access tokens are flushed around **07:30 IST** each morning; the exchange requires a manual login at least once a day; Kite does not recommend automating login | SECONDARY / RECHECK-AT-KITE |
| Kite Connect API cost, static IP | SEBI's April 2026 rules (below) are reported to require a static IP registered with the broker for API orders. Kite Connect subscription price not checked. | UNVERIFIED / RECHECK-AT-KITE |

Sources: https://github.com/zerodha/pykiteconnect (connect.py),
https://github.com/zerodha/pykiteconnect/issues/242,
https://kite.trade/forum/discussion/15177/market-protection-feature-in-kite-connect-api,
https://kite.trade/forum/discussion/4108/exact-time-of-my-access-token-expiry,
https://x.com/zerodha/status/1948224554905596109,
https://kite.trade/forum/discussion/11185/gtt-orders.

## 3. NSE market rules

| item | finding | status |
| --- | --- | --- |
| Normal session | 09:15 to 15:30 IST, pre-open 09:00 to 09:15 | SECONDARY (well known; not re-read today) |
| **Closing Auction Session (CAS)** | Live from **2026-08-03**. Phase I covers stocks with F&O contracts. For those, continuous trading ends **15:15**; the auction runs 15:15 to about 15:35. Reference price = VWAP 15:00 to 15:15. Sub-phase times differ between sources. NSE page: nseindia.com/static/products-services/closing-auction-session | PRIMARY-SNIPPET + SECONDARY |
| What CAS means for you | Almost every NIFTY 50 / NIFTY 100 stock has F&O contracts, so for your default universe the market you can actually trade continuously closes at **15:15**, not 15:30. | inference |
| Tick sizes (cash) | NSE circular (Mar 13 2025, effective Apr 15 2025): below ₹250: ₹0.01; ₹250 to ₹1,000: ₹0.05; ₹1,001 to ₹5,000: ₹0.10; ₹5,001 to ₹10,000: ₹0.50; ₹10,001 to ₹20,000: ₹1.00; above ₹20,000: ₹5.00. "The Exchange shall review the tick size of a security on monthly basis." | PRIMARY-SNIPPET (nsearchives.nseindia.com/content/circulars/CMTR67133.pdf). Band edges (e.g. exactly ₹1,000.50) are ambiguous in the snippet: **RECHECK**. Kite's instrument list carries each symbol's `tick_size`; once connected, that wins. |
| 2026 trading holidays (weekdays) | Jan 26, Mar 3, Mar 26, Mar 31, Apr 3, Apr 14, May 1, May 28, Jun 26, Sep 14, Oct 2, Oct 20, **Nov 10**, **Nov 24**, **Dec 25**. Jan 15 (Maharashtra municipal election) appears in some lists only. | First six PRIMARY-SNIPPET (NSE circular NSE/CMTR/71775, 2025-12-12); the rest SECONDARY (several calendars agree). Only Nov 10, Nov 24 and Dec 25 still matter for your paper period. |
| 2027 trading holidays | NSE normally publishes the next year's list in mid-December. Not found. | **UNVERIFIED**: the calendar will say "2027 holidays unknown" and refuse to call a 2027 day a trading day until you add the list. |
| Muhurat 2026 | **Sunday 2026-11-08**, normal market about 14:20 to 15:30 (CAS stocks to 15:15); timings differ by source. NSE circular DS71968. | PRIMARY-SNIPPET + SECONDARY. The tools will treat it as a special session and **not trade it** by default. |

Sources: https://www.nseindia.com/static/products-services/closing-auction-session,
https://nsearchives.nseindia.com/content/circulars/CMTR67133.pdf,
https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf,
https://nsearchives.nseindia.com/content/circulars/DS71968.pdf,
https://www.outlookmoney.com/invest/nse-extended-fo-timings-and-new-closing-auction-session-rules-explained,
https://groww.in/p/nse-holidays.

## 4. Zerodha MIS auto square-off

| item | finding | status |
| --- | --- | --- |
| Non-CAS equity | **15:25** (moved from 15:20 on 2025-12-26) | PRIMARY-SNIPPET (Zerodha Z-Connect + support page) |
| **CAS stocks** | **15:12** (from 15:10, after the August 2026 CAS change) | PRIMARY-SNIPPET (Zerodha product update 2026-08-10) + SECONDARY table dated 2026-10-13 |
| Square-off charge | not checked | UNVERIFIED |

**Your `flat_by: 15:10` is 2 minutes before Zerodha's forced square-off for
CAS stocks.** See the question in PLAN.md.

Sources: https://zerodha.com/z-connect/updates/changes-to-the-auto-square-off-timings-for-equity-and-fo,
https://support.zerodha.com/category/trading-and-markets/trading-faqs/market-sessions/articles/intraday-auto-square-off-timings,
https://zerodha.com/z-connect/?p=454739.

## 5. Zerodha intraday equity charges (for the charges calculator)

| charge | value used | status |
| --- | --- | --- |
| Brokerage | lower of ₹20 or 0.03% per executed order | PRIMARY-SNIPPET (zerodha.com/charges) |
| STT | 0.025% on the **sell** side | PRIMARY-SNIPPET |
| NSE transaction charge | **0.00297%** per side | **UNVERIFIED**: cached copies of Zerodha's page show 0.00297%, 0.00307% and 0.00322%. 0.00297% is the most recent figure seen. RECHECK. |
| SEBI fee | ₹10 per crore per side | PRIMARY-SNIPPET (one broker page says ₹20; majority ₹10) |
| Stamp duty | 0.003% on the **buy** side | SECONDARY |
| GST | 18% on brokerage + transaction charges + SEBI fee | PRIMARY-SNIPPET |
| IPFT | not seen on the intraday line | UNVERIFIED |

Worked check at your maximum position (₹75,000 buy and sell): brokerage ₹40,
STT ₹18.75, NSE ₹4.46, SEBI ₹0.15, stamp ₹2.25, GST ₹8.03: **about ₹73.64 a
round trip** (each part kept to the paisa; `trader-budget/charges.py`). All rates go in `trader/config.json` under `charges` with the
date they were read, so you can fix them in one place.

Sources: https://zerodha.com/charges (via search snippets), https://www.angelone.in/exchange-transaction-charges,
https://www.chittorgarh.com/stockbroker/zerodha/18/?p=4.

## 6. SEBI retail algo / API rules

| item | finding | status |
| --- | --- | --- |
| Framework | SEBI circular of 2025-02-04; NSE implementation standards 2025-05-05; mandatory from **2026-04-01** | SECONDARY (Zerodha's own explainer + broker blogs) |
| Threshold | about **10 orders per second per exchange per client**; below it you are an ordinary API user, above it the algo must be registered and carry an exchange Algo ID | SECONDARY (sources disagree on "more than 10" vs "10 or more") |
| Other controls reported | OAuth only, 2FA, static IP registered with the broker, API sessions closed at end of day, market protection on market orders | SECONDARY / RECHECK-AT-KITE |
| How it applies here | One order at a time, each after your typed yes and Claude's "Allow once" card, is far below 10 orders a second. It is still an API order: the static-IP and market-protection rules would apply to it. | inference; RECHECK-AT-KITE |

Sources: https://zerodha.com/z-connect/business-updates/explaining-the-latest-sebi-algo-trading-regulations,
https://inthemoneybyzerodha.substack.com/p/sebi-algo-trading-changes-april-2026,
https://kite.trade/forum/discussion/15912/preparing-to-comply-with-sebis-retail-algo-rules-static-ip-ratelimits-order-types.

## 7. SEBI intraday study (for the fine print)

SEBI press release PR No. 13/2024 (2024-07-24): **71% of individual intraday
traders in the equity cash segment made a net loss in FY 2022-23**; 80% of those
with more than 500 trades a year lost money; 76% of traders under 30 lost money.
Status: SECONDARY (news reports quoting the press release; the sebi.gov.in page
itself was not opened). README will cite it with that caveat unless you can
open the SEBI page and confirm.

Sources: https://www.business-standard.com/markets/news/over-70-intra-day-traders-incur-losses-during-fy23-reveals-sebi-study-124072401110_1.html,
https://moneylife.in/article/7-out-of-10-individual-intraday-traders-in-equity-cash-segment-make-losses-sebi-study/74733.html.

## 8. Notion connector (VERIFIED in this session)

The Notion connector's tool schemas were loaded and read in this Claude session
on 2026-10-29. Tool names as Claude sees them here: `notion-search`,
`notion-fetch`, `notion-create-database`, `notion-create-pages`,
`notion-update-page`, `notion-query-data-sources`, `notion-update-data-source`
(plus others not needed). Your Cowork session may show them under a different
prefix; the names after the prefix should match.

| need | tool | notes |
| --- | --- | --- |
| Find the parent page you name | `notion-search`, `notion-fetch` | |
| Create a page under a parent | `notion-create-pages` with `parent: {page_id}` | |
| Create the 4 databases | `notion-create-database` with `parent: {page_id}` and a SQL DDL `schema` (`CREATE TABLE ("Trade ID" TITLE, "Net P&L ₹" NUMBER, "Exit reason" SELECT(...), ...)`) | returns a `collection://` data source id; that id is what goes in config |
| Add a row | `notion-create-pages` with `parent: {data_source_id}` | dates as `date:Date:start`; numbers as numbers |
| Update a row | `notion-update-page` `command: update_properties` with the row's page id | the outbox stores the page id once a row is created, so updates never duplicate |
| Find a row by Trade ID / rebuild | `notion-query-data-sources`: `rows` mode (max 100 rows, no cursor), `view` mode (100 a page, with a cursor), or `sql` mode | SQL mode has a shared workspace quota on non-Business plans, so rebuild uses `view` mode with paging |

Not used: any Notion API token. The scripts never call Notion.

## 9. Yahoo Finance for NSE (.NS)

| item | finding | status |
| --- | --- | --- |
| Delay | Yahoo's help page "Exchanges and data providers" lists NSE `.NS` as **Real-time, ICE Data Services** | PRIMARY-SNIPPET; one third-party page claims 15 minutes. RECHECK on your machine by comparing a quote with Kite. |
| Reachable from your machine | **Not tested**: blocked in this sandbox. | **UNVERIFIED**. Phase 2 starts with a one-line probe you run: `python3 trader-alerts/alerts.py quote RELIANCE` |
| 1-minute history | Upstream notes Yahoo keeps about 7 days of 1-minute bars and 60 days of 5-minute bars (US experience) | UNVERIFIED for .NS. Matters for paper fills: reconcile must run the same day. |
| Headlines RSS | `feeds.finance.yahoo.com/rss/2.0/headline?s=RELIANCE.NS&region=IN&lang=en-IN` | UNVERIFIED for .NS |
| Terms | Yahoo: "for informational purposes only, not intended for trading" | PRIMARY-SNIPPET |

Source: https://help.yahoo.com/kb/finance/SLN2310.html.

## 10. Universe, news, results, insider data (NSE/BSE)

| item | finding | status |
| --- | --- | --- |
| Equity list | NSE `EQUITY_L.csv` (series column; keep `EQ`). Current URL believed to be `https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv` | UNVERIFIED (blocked here) |
| NIFTY 50 / 100 / 500 constituents | `https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv` pattern; `ind_nifty100list.csv`, `ind_nifty500list.csv` by analogy | UNVERIFIED. Fallback: a dated snapshot file in the repo that you refresh. |
| NSE announcements RSS | `https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml` | SECONDARY (third-party feed directory; NSE's RSS page lists the feed) |
| NSE insider trading (SEBI PIT) RSS | `https://nsearchives.nseindia.com/content/RSS/Insider_Trading.xml` | SECONDARY, same |
| NSE corporate actions RSS | `https://nsearchives.nseindia.com/content/RSS/Corporate_action.xml` | SECONDARY |
| NSE JSON APIs (`/api/...`) | need browser cookies from a first page load | **not used**: that is a scraping trick |
| BSE API | needs browser-like headers | **not used** |
| Quarterly results | no stable, cookie-free public JSON found | analyst falls back to a results PDF or text you paste, as you allowed |

RSS feeds are plain public files, so trader-news and trader-insider will try
them; if they fail on your machine, the tools say so and fall back to Yahoo
headlines only.

### Moneycontrol and Groww (your request of 2026-10-29)

| source | finding | status |
| --- | --- | --- |
| Moneycontrol RSS | Section feeds at `https://www.moneycontrol.com/rss/<section>.xml`: `latestnews`, `business`, `marketreports`, `economy`, `results`, `iponews`, `buzzingstocks` (names from third-party directories and an old GitHub code sample). They are market-wide feeds, not one feed per stock, so a headline is matched to your symbols by the company's name. | **UNVERIFIED**: moneycontrol.com could not be fetched from the sandbox, the feed list page was not read, and the terms of use for the feeds were not seen. Personal, non-commercial reading is assumed. RECHECK on your machine. |
| Groww news | groww.in/stock-feed is a web page with no public RSS or news API found. Groww's Trade API (₹499 a month + taxes) is for market data and orders, not news. A Groww Blog "stocks" feed exists in directories, but it is educational articles, not news. | **No stable public news source.** Reading groww.in/stock-feed would mean scraping a web page, which your rules rule out. Not built. |

Sources: https://feeder.co/discover/01b3ab436c/moneycontrol-com,
https://www.einpresswire.com/world-media-directory/detail/25453,
https://gist.github.com/cd55ff5a4416e766803a13c078a08b88,
https://rss.feedspot.com/Moneycontrol_rss_feeds/, https://groww.in/stock-feed,
https://groww.in/trade-api.

## 11. Assumptions made while building (Phase 2, 2026-10-29)

| assumption | where | status |
| --- | --- | --- |
| Yahoo's v8 chart JSON for `.NS` has the same shape as for US symbols (meta `symbol`, `regularMarketTime`, `dataGranularity`, `timestamp`, `indicators.quote`) | every data tool | UNVERIFIED for `.NS`: the sandbox could not reach Yahoo. The synthetic fixtures copy the US shape. Run `tests/capture.py RELIANCE` on your machine and the tools against it. |
| Yahoo's 5-minute bars for NSE start at 09:15 and sit on the 5-minute grid | scanner, entry, time machine | UNVERIFIED |
| NSE `EQUITY_L.csv` columns `SYMBOL`, `NAME OF COMPANY`, `SERIES`; niftyindices CSV columns `Symbol`, `Series` | scanner universe | from memory, UNVERIFIED; the parser finds columns by name |
| Treat every stock as a closing-auction stock (continuous trading ends 15:15) | time machine pool, exit flat-by limit | conservative choice: almost all NIFTY 100 stocks have F&O |
| The live stop is an `SL-M` sell with `market_protection: -1`, placed after the entry fills (config `stop_order_type`, `SL` with a limit 0.5 % below also possible) | trader-budget Kite payload | RECHECK-AT-KITE: whether Zerodha accepts SL-M on NSE equity MIS via the API today |
| The paper slippage (0.05 % a side) is counted in the ₹500 max loss and the 1.5 reward:risk (`limits_include_slippage: true`) | trader-budget, trader-risk | a design choice, yours to change; see PLAN.md |
| Yahoo's spark (batch) endpoint is not used for `.NS`: one chart request per symbol | scanner | avoids an unverified endpoint; NIFTY 100 is about 100 requests |

## 12. Assumptions made while building news, results and insider (Phase 6, 2026-10-29)

None of these could be fetched from the build sandbox. Every fixture for them is SYNTHETIC (symbols SYNTHA to SYNTHG, example.invalid links), so the tests prove the code, not the feeds.

| item | what the code assumes | status |
| --- | --- | --- |
| Yahoo headline RSS for `.NS` | `https://feeds.finance.yahoo.com/rss/2.0/headline?s=RELIANCE.NS&region=IN&lang=en-IN` returns standard RSS 2.0 with `item/title/link/guid/pubDate`. The old US feed used `region=US`; `region=IN` is a guess. | UNVERIFIED. If it returns nothing for Indian symbols, trader-news prints a note and the other feeds still run. |
| NSE announcements RSS | Standard RSS 2.0; the title starts with the symbol, then the company and the subject. The matching also works when it does not. | UNVERIFIED (URL from a third-party list). |
| Moneycontrol RSS | Section feeds as in section 10, standard RSS 2.0, `pubDate` with a zone. Market-wide: matched by symbol or company name. Personal reading only. | UNVERIFIED. Terms of use not read. |
| Groww | No public news feed; not read. | Not built. |
| Name matching | A symbol of 4 or more letters matches as a bare capitalised word; shorter symbols only as `(SYM)`, `NSE: SYM` or the full company name. A first word that starts two of your companies' names, or a common word (Tata, Bank, India ...), is never used alone. A name-only match is shown as POSSIBLE. | Design choice, tested. It will miss some stories (a bare "ITC") and show some false POSSIBLEs. |
| Results today | Any item for your symbol tagged results or board meeting and dated today (IST) raises RESULTS TODAY. It cannot see a results date that has not been announced yet. | Design choice. Warning only: trader-risk does not read it. |
| Results table text | Rows labelled revenue from operations / net sales, other income, total income, total expenses, exceptional items, profit before tax, tax, net profit / profit for the period, basic EPS, with the numbers after the label; column order from date headers, else assumed current, previous quarter, year ago. | Written from the usual layout of Indian quarterly results; not tested on a real filing. The report says when it assumed the order. |
| Insider CSV columns | Found by words in the header (SYMBOL, NAME OF THE PERSON, CATEGORY OF PERSON, NO. OF SECURITIES, VALUE OF SECURITY, TRANSACTION TYPE, MODE OF ACQUISITION, DATE OF ALLOTMENT/ACQUISITION FROM, DATE OF INITIMATION, BROADCAST DATE). The names are from memory of NSE's export, not from a file. | UNVERIFIED. A missing required column stops the import and prints the columns that were there. |
| Insider RSS | `https://nsearchives.nseindia.com/content/RSS/Insider_Trading.xml` gives notices (title and link), not the numbers. | UNVERIFIED (section 10). |
| Disclosure deadline | "About two trading days" to disclose, used only to mark a gap as late. | SECONDARY: from the SEBI PIT Regulations as commonly summarised; the regulation text was not read here. |
| Moneycontrol and Yahoo times | Items with no time zone are taken as India time. | Assumption. |

## 13. What the whole build did NOT test against reality

No real NSE, Yahoo, Moneycontrol, Zerodha or Notion call was made by the tests. Real calls happen first on your machine. The first things to look at: `scan.py universe` (did the CSVs download and parse?), one `entry.py check` on a real symbol after 09:35, `news.py` (which of the three feeds answered?), the first Notion sync (did the database schema create?), and `alerts.py quote RELIANCE` against Kite's price. `tests/capture.py` saves real Yahoo bars so the synthetic fixtures can be replaced.

## Items to re-check when you connect Kite (RECHECK-AT-KITE)

1. Whether the hosted `mcp.kite.trade` exposes `place_order` / `modify_order` / `cancel_order` (assumed: no).
2. The exact argument names of the MCP `place_order` tool.
3. Whether `variety=co` (cover order) is accepted for NSE equity MIS (assumed: no).
4. Whether GTT (single or two-leg) accepts `product=MIS` (assumed: no).
5. `market_protection` required for MARKET and SL-M; allowed values (`-1` auto, or 1 to 100).
6. Static IP and any other SEBI April-2026 API requirement for your account.
7. Access-token flush time (forum: about 07:30 IST).
8. MIS square-off times (15:12 CAS, 15:25 others) on zerodha.com/marketintel/bulletin.
9. Every rate in the charges table, against zerodha.com/charges on the day.
10. Tick size per symbol from Kite's instrument list, against the NSE band table.
11. Whether `SL-M` (with `market_protection`) is accepted for an NSE equity MIS stop-loss sell; if not, set
    `"stop_order_type": "SL"` in your config.
