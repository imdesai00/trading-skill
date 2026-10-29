# Going live

Do not read this page until you have finished the paper period: about four weeks (20 trading days) and at least 40 trades. Going live is **your** decision and
**your** edit. No tool in this pack switches it on, and nobody (including Claude) will do it for you.

## 1. Read the honest report

```bash
python3 <pack>/trader-golive/golive.py check
```

It prints, for the paper record: the number of trades (with a warning under 40), net result after charges, win rate, average R, expectancy per trade, deepest drawdown,
longest losing streak, how often limits were hit or a rule broken, the charges paid, how your skipped signals compare, and whether the result can be told apart from luck at this
sample size. With a few dozen trades it usually cannot. It never says "ready" or "not ready". Read the Notion record yourself too.

Most individual intraday traders lose money. A good paper month is weak evidence; a flat or negative one is a reason to keep learning on paper.

## 2. If you still want to go on, these are the steps you do yourself

`golive.py steps` prints them. In order:

1. **Decide the most you can afford to lose**, in money you can afford to lose, before you connect anything.
2. **Connect Kite yourself.** The hosted Kite MCP server (`mcp.kite.trade`) says it leaves out "potentially destructive trading operations", so it probably cannot place orders. Check its tool list in Claude. If
   `place_order` is missing you need Zerodha's open-source `kite-mcp-server` running on your own computer with your own Kite Connect API key and secret.
   - Put the key and secret only in environment variables or the server's own config file, never in a chat, never in this folder.
   - Turn on Zerodha's TOTP two-factor login.
   - Check Zerodha's current requirements for API orders (SEBI's 2026 retail algo rules reportedly include a registered static IP) and the Kite Connect subscription cost.
3. **Set every order tool to "Needs approval"** in Claude (Customize, Connectors, the Kite server): `place_order`, `modify_order`, `cancel_order` and the GTT tools. Read-only tools may stay
   on Always allow. Never set an order tool to Always allow.
4. **Re-verify what could not be verified.** Work through `VERIFY.md`, "Items to re-check when you connect Kite": tool names and arguments, whether SL-M is accepted for an MIS stop, market protection,
   token expiry, MIS square-off times, charge rates, tick sizes, and the shape of the `get_ltp`, `get_margins` and `get_positions` results.
5. **Run the connection check each live morning.** Log in to Kite, save the results of `get_profile`, `get_margins` and `get_positions`, then:
   ```bash
   python3 <pack>/trader-broker/kite.py check --profile profile.json --margins margins.json --positions positions.json --tools-need-approval
   ```
   It refuses unless the account shows cash, no unexpected open position, and you have passed `--tools-need-approval` (your own confirmation of step 3). Kite's login expires every day.
6. **Switch it on yourself.** Edit `trader/config.json`: `"mode": "live"` and `"kite_enabled": true`. For the first two live weeks the pack runs at 50% of normal risk by itself (₹250 a trade).
7. Load the protection files every morning ([DATA_FILES.md](DATA_FILES.md)). In live mode a missing file blocks entries.

## 3. How a live trade differs

A live trade is **three separate orders**, each with its own risk check, your own typed yes and Claude's Allow once card:

1. the **entry** (BUY, MIS) at Kite's live price: if Kite's price is more than 0.3% from the plan, the yes is used up and you start again;
2. after the entry fills, the **stop-loss** (SELL, SL-M) that rests at Zerodha;
3. the **exit**: cancel the stop first (its own yes), then a market SELL. The target is **not** a resting order: an alert rings and you decide.

After placing: `kite.py sync` with the saved order, trade and position results. Act on every WARNING line at once (no stop, rejected stop, position mismatch).

After 15:30: `kite.py reconcile` compares Kite with the record. Kite always wins. Differences land in the Discrepancies table and are shown to you. When the contract note arrives, enter its charges:
`kite.py set-charges --trade ID --amount X`.

Everything goes to the same Notion journal with Mode = live; the Live Account row shows Kite's real balance and positions.

## 4. Stopping

- "Pause trading" or the kill switch (`trader/STOP`) blocks every new order immediately. In live mode the kill switch lists your open positions and the flatten command for each.
- To go back to paper: edit `"mode": "paper"` yourself.

## 5. Tax and records

`db.py export --tax --fy 2026-27` writes the closed live trades with charges (estimated unless you entered the contract note figure) to a CSV. It is **not tax advice**. Intraday equity results are generally treated
as speculative business income in India; talk to a chartered accountant, and use your broker's contract notes and P&L statement for filing.
