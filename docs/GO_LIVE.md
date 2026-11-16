# Going live (with Dhan)

Do not read this page until you have finished the paper period: about four weeks (20 trading days) and at least 40 trades. Going live is **your** decision and **your** edit. No tool in this pack switches it on, and nobody (including Claude) will do it for you. The broker is Dhan; the reasons, and what is and is not verified about it, are in [DHAN.md](DHAN.md). The plan in order is [PHASE_3_DHAN.md](PHASE_3_DHAN.md).

## 1. Read the honest report

```bash
python3 <pack>/trader-golive/golive.py check
```

It prints, for the paper record: the number of trades (with a warning under 40), net result after charges, win rate, average R, expectancy per trade, deepest drawdown, longest losing streak, how often limits were hit or a rule broken, the charges paid, how your skipped signals compare, and whether the result can be told apart from luck at this sample size. With a few dozen trades it usually cannot. It never says "ready" or "not ready". Read the Notion record yourself too.

Most individual intraday traders lose money. A good paper month is weak evidence; a flat or negative one is a reason to keep learning on paper.

## 2. If you still want to go on, these are the steps you do yourself

`golive.py steps` prints them (`--broker kite` prints the Zerodha version). In order:

1. **Decide the most you can afford to lose**, in money you can afford to lose, before you connect anything.
2. **Connect Dhan yourself.** Dhan describes a first-party MCP server (reported address `https://mcp.dhan.co/mcp`). Add it to Claude and approve its consent screen yourself. Never paste a token, password, client secret or TOTP code into a chat or into this folder. If any route needs a credential, it lives in an environment variable or in a config you edit yourself. Read on Dhan's own site what API and MCP access needs, including the static-IP rule for order APIs and whether your route needs it.
3. **Set every order tool to "Needs approval"** in Claude (Customize, Connectors, the Dhan connector): the place-order, place-super-order, modify, cancel and forever-order tools, and any square-off tool. Read-only tools may stay on Always allow. Never set an order tool to Always allow.
4. **Map symbols to Dhan's ids.** Download Dhan's instrument list yourself, then:
   ```bash
   python3 <pack>/trader-broker/dhan.py securities --csv api-scrip-master.csv
   ```
   An order for a symbol with no id is refused.
5. **Put Dhan's real tool names in the config.** Open the connector's tool list in Claude and fill `dhan_tools` in `trader/config.json` (the defaults are guesses).
6. **Re-verify what could not be verified.** Work through `VERIFY.md`, "Items to re-check when you connect Dhan": the tool list, the static IP, the login, how a super order behaves and cancels, the real field names, the square-off time, the charges, and ticks.
7. **Run the connection check each live morning.** Connect Dhan, save the profile, funds and positions results, then:
   ```bash
   python3 <pack>/trader-broker/dhan.py check --profile profile.json --margins funds.json --positions positions.json --tools-need-approval --static-ip-confirmed
   ```
   It refuses unless the account shows cash, no unexpected open position, the `securityId` map exists, and you have confirmed step 3 and the static-IP point yourself (a script cannot see either).
8. **Switch it on yourself.** Edit `trader/config.json`: `"mode": "live"`, `"broker": "dhan"` and `"dhan_enabled": true`. For the first two live weeks the pack runs at 50% of normal risk by itself (₹250 a trade).
9. Load the protection files every morning ([DATA_FILES.md](DATA_FILES.md)). In live mode a missing file blocks entries.

## 3. How a live trade differs

An entry is **one super order**: the entry, the stop-loss and the target together.

1. `size.py --limit` makes a limit order (the price becomes part of what you approve; Dhan turns API market orders into limit orders anyway).
2. `check.py` (one risk check), `ticket.py show` (you see the entry, stop and target prices), your typed yes with a "why", Claude's Allow once card. Before the order, `dhan.py prepare` needs Dhan's own last price and funds: if the price is more than 0.3% from your plan, nothing is placed, the yes is used up and you start again.
3. The stop exists from the moment the entry fills. The target rests too. When one leg fills, the other is cancelled (Dhan's documented behaviour; to be re-checked).
4. To leave early: **cancel the super order** (its own risk check, yes and card), then **sell** (its own). Order matters: cancel first, or the sell could leave you short.

After placing: `dhan.py sync` with the saved orders, trades and positions results. Act on every WARNING line at once. After 15:30: `dhan.py reconcile` compares Dhan with the record. Dhan wins. Differences land in the Discrepancies table and are shown to you. When the contract note arrives, enter its charges: `dhan.py set-charges --trade ID --amount X`.

Everything goes to the same Notion journal with Mode = live; the Live Account row shows Dhan's real balance and positions.

## 4. Stopping

- "Pause trading" or the kill switch (`trader/STOP`) blocks every new order immediately. In live mode the kill switch lists your open positions and the flatten command for each.
- To go back to paper: edit `"mode": "paper"` yourself.

## 5. Tax and records

`db.py export --tax --fy 2026-27` writes the closed live trades with charges (estimated unless you entered the contract note figure) to a CSV. It is **not tax advice**. Intraday equity results are generally treated as speculative business income in India; talk to a chartered accountant, and use your broker's contract notes and P&L statement for filing.
