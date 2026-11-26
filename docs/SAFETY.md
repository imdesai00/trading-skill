# Safety: what stops a bad trade

This page lists every protection, who or what enforces it, and what it cannot do. A protection you do not understand is one you will switch off at the
worst moment, so read it once.

## The two modes

| | paper (the default) | live |
| --- | --- | --- |
| broker contacted | never | Zerodha Kite, through the Kite MCP server |
| needs | nothing | you set `"mode": "live"`, `"broker": "dhan"` and `"dhan_enabled": true` yourself, Dhan connected and checked today |
| record | the database (paper account) | the database (live account read from the broker) |

If `mode` is live but the broker is not verified today, every order is refused and the tool says why. No tool ever changes `mode`, `broker`, `dhan_enabled`, `fyers_enabled` or `kite_enabled`.

## The gates on every order (both modes)

1. **Risk PASS on that exact order.** The order is fingerprinted from its symbol, side, quantity, price, stop and target. Change any of them and the earlier PASS
   no longer counts.
2. **Your typed "yes" for that one order.** "sure", "ok", "go ahead" and "yes to all" are not a yes; Claude asks again. One yes covers one order, once. Your words are stored
   unchanged in the database.
3. **Live only: Claude's own "Allow once" card**, with every broker tool that places, modifies, cancels or exits orders (Dhan: including super orders and forever orders) set to "Needs approval". The pack cannot see that setting, so
   `dhan.py check` (or `kite.py check`) refuses to verify until you confirm it yourself, and `dhan.py check` also asks you to confirm the static-IP point.
4. **Nothing from a background or scheduled task.** There is no scheduler in the pack. If you schedule a task yourself, it cannot place orders: there is nobody to say yes.

## What the risk check enforces

| protection | what happens |
| --- | --- |
| max loss per trade (0.5% of capital, ₹500), including charges and slippage, measured at the stop | BLOCK |
| daily loss (1.5%, realised plus open risk), weekly (4%) | BLOCK new entries |
| drawdown pause (8% below baseline) | BLOCK everything; the morning check says "stop and review" |
| 3 trades a day, 1 open position, 75% position cap, 10% cash buffer | BLOCK |
| reward to risk 1.5 after charges; charges over 25% of the target profit | BLOCK |
| two losing exits in a row today | no new entries today |
| 15 minutes after a losing exit | cooldown |
| no entries before 09:30 or after 14:45; NSE closed (weekend, holiday, special session) | BLOCK |
| price older than 3 minutes | BLOCK |
| within 2% of the upper or lower circuit | BLOCK (needs the bands file) |
| ASM/GSM stocks; stocks not MIS-eligible | BLOCK (needs the daily files) |
| results day for the stock, RBI/event day, Union Budget day, index expiry day, India VIX above 20, NIFTY gap above 1% | half the per-trade risk (once, not per reason) |
| India VIX above 25 | no entries |
| first 2 live weeks | 50% of normal risk |
| the file `trader/STOP`, or a pause | BLOCK all new entries |
| leverage, short selling | off |

Exits are never blocked by the day limits, the window, or the kill switch. Getting out must always be possible.

## Capital, limits, pause and the kill switch

- **Change capital or a limit**: ask Claude. It shows old and new values side by side and changes nothing until you type a plain yes. Every change is recorded (what, when,
  why) in the database. Raising a risk limit prints a warning. Changing capital during the paper test prints another.
- **In live mode, capital can never be more than Kite shows as available.**
- **Pause**: "pause trading until 6 November" blocks new orders in both modes; scans, news and exits still work. "resume trading" ends it.
- **Kill switch**: say "kill switch" or create a file named `STOP` in `trader/`. No new order is possible in either mode until the file is removed. In live mode Claude lists
  your open positions and offers to flatten them; each flatten needs risk, your yes and the Allow once card.
- You can also edit `trader/config.json` yourself. The next run notices and records it.

## Live mode's extra protections

- Entries use **the broker's live price**, not Yahoo's. If it is more than 0.3% from the planned entry, nothing is placed, the yes is used up and you must run the risk check and say yes again.
- The available cash must cover the whole order.
- With Dhan the entry is one **super order** that carries its own stop-loss and target legs (one risk check, one yes, one card); `dhan.py sync` warns if the order is rejected or cancelled. With Zerodha the stop is a separate SL-M order after the fill, and `kite.py sync` warns if a position has no stop.
- **No automatic retries.** After a failure the yes is used up. Before any retry, the order book is read so one yes can never create two orders.
- **Live reconcile** compares the broker's trades and positions with the record. The broker wins. Differences go into a Discrepancies table and are shown to you, never fixed silently.

## Your own part

- Before every yes, Claude asks "Why are you taking this trade?". Answer honestly; it is stored. After the exit it asks "How did you feel about it?".
- Turn on Zerodha's TOTP two-factor login. Never share Kite credentials with anyone, including Claude. No tool in this pack asks for them.
- Do not loosen a limit to make a trade pass. A BLOCK is information.

## What none of this can do

- A stop is a plan, not a promise: in a fast market or across a gap it can fill well below its price. The "maximum loss" shown is the loss at the stop price.
- The time machine is a simulation, not a prediction.
- Free data is unofficial and can be wrong or late. Check anything that matters in your broker.
- Paper results flatter reality: real fills slip more.
- Most individual intraday traders lose money. The database cannot make a bad strategy good.
- A script cannot check your Claude settings: whether your Kite order tools are on "Needs approval" is your responsibility.

## Where the record lives and how it is protected

Orders, approvals, fills, trades and the rest are append-only and hash-chained in `trader/trader.db` (see [ARCHITECTURE.md](ARCHITECTURE.md)). `db.py check` finds an edited or removed
row. Dated backups are made after every reconcile.
