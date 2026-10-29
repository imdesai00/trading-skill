# Glossary

| term | plain meaning |
| --- | --- |
| **NSE** | National Stock Exchange of India. This pack trades NSE cash shares only |
| **cash equity** | ordinary shares (not futures, options, currency or commodities) |
| **intraday / MIS** | Margin Intraday Square-off: you must close the position the same day. Zerodha closes any left open around 15:12 to 15:25. This pack never uses extra margin: leverage is off |
| **long** | you buy first and sell later. Shorting (selling first) is off |
| **IST** | India Standard Time, UTC+05:30, with no daylight saving |
| **paper trading** | practising with pretend money. Fills are simulated from real prices, with slippage and full charges |
| **live trading** | real orders at Zerodha Kite with real money |
| **Zerodha Kite / Kite Connect / Kite MCP** | Zerodha's trading app, its programming interface, and the connector that lets Claude call it |
| **ORB (opening-range breakout)** | the first 15 minutes (09:15 to 09:30) give a high and a low. A close above the high is the breakout |
| **stop / stop-loss** | the price at which you give up on a trade and sell. The loss at the stop is your maximum planned loss |
| **target** | the price at which you plan to take profit |
| **SL-M** | a stop-loss market order: when the trigger price is touched, it becomes a market order. Fills at whatever the market gives |
| **flat-by** | the time by which you are out of everything (15:00 here) |
| **R multiple** | profit or loss divided by what you risked. -1R is a full planned loss; +1.5R is one and a half times your risk |
| **reward to risk** | target profit divided by loss at the stop, after charges |
| **expectancy** | the average result per trade (in rupees, after charges) |
| **drawdown** | how far below your best (or your start) the account has fallen |
| **slippage** | getting a worse price than expected. Paper uses 0.05% each side |
| **tick size** | the smallest price step a stock moves in (₹0.05 for most stocks under ₹1,000, and other values in other price ranges) |
| **charges** | brokerage, STT, exchange and SEBI fees, stamp duty and GST. Small per trade but large against a ₹500 risk |
| **fingerprint** | a short code made from an order's symbol, side, quantity, price, stop and target. If any changes, the risk PASS and your yes no longer apply |
| **risk PASS / BLOCK** | the risk manager's verdict on one exact order |
| **ASM / GSM** | NSE's Additional and Graded Surveillance Measures: lists of stocks under extra restrictions because they are unusually volatile or suspect |
| **circuit limit / price band** | the most a stock may move up or down in a day. At the limit trading stops in that direction |
| **MIS-eligible** | stocks the broker allows intraday orders in |
| **India VIX** | an index of expected market volatility (fear). High means big swings |
| **gap** | the market opening well above or below yesterday's close |
| **results day** | the day a company announces quarterly results: prices often jump |
| **PIT / insider filing** | disclosures under SEBI's insider-trading rules by promoters, directors and key staff when they buy or sell their company's shares |
| **contract note** | the broker's official statement of your trades and exact charges |
| **hash chain** | each database row stores a code made from the row before it, so changing or removing a row in the middle is detectable |
| **append-only** | rows can be added but not changed or deleted. Corrections are new rows |
| **outbox** | the waiting list of rows to be written to Notion |
| **kill switch** | the file `trader/STOP`: while it exists, no new order can be made |
| **Allow once** | Claude's own permission card that appears before a Kite order tool runs |
| **TOTP** | the six-digit code from an authenticator app, used for two-factor login. Never share it |
| **speculative business income** | the usual tax treatment of intraday equity trading in India. Not tax advice |
