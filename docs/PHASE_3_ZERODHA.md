# Phase 3: connecting Zerodha Kite

Status: **planned**. Start only after Phase 1's exit test and (ideally) Phase 2 are done ([ROADMAP.md](ROADMAP.md)). Phase 3 is the only phase that can lose real money, so it is slow on purpose and every step can be undone.

## What is already built (and still off)

- `trader-broker/kite.py`: the safety layer for live orders. It checks the gates, builds the exact Kite call from the approved order, refuses a call that differs from the one you approved, refuses duplicates, and records results. It makes **no network call** and holds **no credentials**.
- Live ledger in the database, order events, fills, discrepancies, live reconcile, actual-charges entry, the Live Account in Notion, the first-two-weeks half risk, the 0.3% live-price rule, the cash check, the kill switch and pause.
- Everything is tested against a fake Kite, never a real one.

## What is missing: the actual connection

Nothing in the pack talks to Zerodha. The call would be made by Claude through a Kite connector, or by a new adapter. **Whether a usable connector exists is not confirmed.** The notes in [VERIFY.md](../VERIFY.md) (sections 1, 2 and 14) come from search snippets, not from reading Zerodha's pages:

- Zerodha's open-source `kite-mcp-server` exists, according to those snippets.
- The hosted copy at `mcp.kite.trade` reportedly leaves out trading operations. Self-hosting with your own API key reportedly gives the rest.
- Zerodha's own Kite Connect API is the documented route to place orders. Its terms, fees, daily login and, under SEBI's 2026 retail algo rules, possibly a registered static IP must be read on kite.trade and with Zerodha directly.

**The first task of Phase 3 is to settle this from primary sources**, not from this page.

## Decision to make first: which route

| route | what it is | main cost |
| --- | --- | --- |
| **A. Self-hosted Kite MCP server** | you run Zerodha's open-source MCP server on your computer with your own Kite Connect app, and add it to Claude as a connector. The existing `kite.py` layer is used as designed | your own API key and secret live in the server's environment; a daily login; Kite Connect fees |
| **B. Direct Kite Connect adapter** | a new standard-library module that calls the Kite Connect REST API itself | the pack would have to read your access token each day, a new place for secrets to leak; more code to test; the same fees and rules |
| **C. Manual** | the pack makes the ticket; you place the order by hand in the Kite app, and enter the fill and the contract-note charges into the pack | no API, no connector, no credentials; you are the "Allow once" card. Reconcile compares your record with Kite's trade book export |

Recommendation: decide after reading Zerodha's current docs. If route A or B cannot be verified, or the fees and rules are not worth it for a ₹1,00,000 account, route C gives most of the discipline with none of the credentials. Route B is only worth building if A does not exist, and it must keep the same rule: **the pack never stores or prints a key or token** (environment variables only, read at call time, never written, never logged).

## Prerequisites (all must be true before any live order)

1. Phase 1 exit test passed. You have read `golive.py check` and decided, in writing, how much money you can afford to lose.
2. Zerodha account in good standing, TOTP two-factor login on.
3. Route chosen, with the primary-source documents saved or linked in VERIFY.md.
4. Every item in VERIFY.md's "Items to re-check when you connect Kite" checked, with its status changed and dated. In particular:
   - the tool names and argument names of whatever connector or API you use;
   - whether SL-M is accepted for an MIS stop through the API (else set `"stop_order_type": "SL"`);
   - whether `market_protection` is required, and its allowed values;
   - the shape of `get_ltp`, `get_margins`, `get_positions`, `get_orders`, `get_trades`;
   - token expiry time and the daily login steps;
   - MIS square-off times and the charge rates on the day;
   - the SEBI rules for API orders on your account (static IP and anything else);
   - whether cover orders or GTT can protect an MIS position (they are assumed not to).
5. The protection files load every morning without trouble (live mode blocks entries without them).
6. Every order tool in Claude is on "Needs approval" (never "Always allow").

## Work breakdown

1. **Primary-source verification** (above). Output: VERIFY.md updated, a short "what Zerodha allows" note, a go or no-go on route A, B or C.
2. **Connector setup** (route A): install, configure with environment variables, add to Claude, list its tools, set permissions. Save its real tool list and a sample (with identifiers removed) of each read tool's output.
3. **Adapter changes**: adjust `build_call` and the readers in `kite.py` to the real names and shapes; add the real sample outputs as test fixtures; keep the fake-Kite tests passing and add tests built from the samples.
4. **A dry run with no money**: connect, run `kite.py check`, read orders, trades, positions and margins, and run the live reconcile on an account with no activity. Place nothing.
5. **Smallest real order** (route A or B): one share of a liquid stock, through the full chain: risk PASS, your typed yes, the Allow once card, the entry, the stop, the exit. Compare every field with Kite's own order book and trade book. Reconcile. Enter the contract-note charges. Fix anything that differs.
6. **Two-week half-risk period** at ₹250 a trade, which the pack enforces by itself, with the Live Account in Notion and a live reconcile after every day.
7. **Review**: compare live against paper (slippage, fills, charges), using the separate live record. Decide whether to continue, stay at half risk, or go back to paper.

## Security rules (non-negotiable)

- API key, secret, password, TOTP code and access token never go into the chat, the repository, the database, Notion, logs or this pack's config. They live in environment variables, or in the connector's own config file that you edit, outside the repository.
- No tool asks for them. If anything does, stop.
- `mode` and `kite_enabled` are set only by you, by hand. `control.py` refuses them.
- No order from a scheduled or background task. Each live order is one risk PASS, one typed yes, one Allow once card.
- Cancel the stop before exiting, as its own yes. No automatic retries. Read the order book before any retry.
- Revoke the API access in Kite's developer console if a laptop is lost or you stop using it.

## Tests

- All existing live tests keep passing (they use a fake Kite).
- New fixtures from real, anonymised connector outputs for each read tool and the place, cancel and error cases.
- A test that `kite.py` with live off makes zero calls (already present; keep it).
- A test that no key, token or secret appears in any file the pack writes.
- If route B: a mock HTTP server test for every endpoint, with credentials read only from the environment and never written anywhere.

## Acceptance criteria

- Every item in the re-check list is VERIFIED with a date, or the feature that depends on it is removed.
- One full real round trip (entry, stop, exit) matches Kite's own records to the paisa, charges included once the contract note is entered.
- The live reconcile shows no unexplained difference for ten consecutive live days.
- Turning `kite_enabled` off, setting the kill switch, or deleting the access token each stops all new orders immediately.
- You can describe from memory what each of the three orders of a live trade does and which gate stops it.

## Rollback

- Set `"mode": "paper"` (your edit), or create `trader/STOP`, or pause. New orders stop at once.
- Flatten open positions with the exit tickets (each needs risk PASS, your yes and the card), or in the Kite app.
- Revoke the API key. Nothing else in the pack depends on it.

## Honest risks

- A resting stop is an order, not a promise: gaps can fill far below it.
- Live fills are worse than paper fills, and your reaction time is slower than the simulation.
- Charges are a large share of a ₹250 to ₹500 risk. Many trades will still be refused, and some profitable-looking ones will lose after costs.
- Most individual intraday traders lose money. Passing every test in this repository does not change that.
- Not financial advice.
