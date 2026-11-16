# Phase 3: connecting Dhan

Status: **planned for the connection itself; the adapter code is built and tested against a fake Dhan.** Start the real connection only after Phase 1's exit test and (ideally) Phase 2 are done ([ROADMAP.md](ROADMAP.md)). Phase 3 is the only phase that can lose real money, so it is slow on purpose and every step can be undone.

You chose Dhan instead of Zerodha on 2026-11-16. The analysis (what Dhan offers, what is verified and what is not, how it differs from the Zerodha design) is in [DHAN.md](DHAN.md). The Zerodha adapter (`kite.py`) is still in the pack, unused unless you set `"broker": "kite"`; it is the shared base that `dhan.py` builds on.

## What is already built (and still off)

- `trader-broker/dhan.py`: the Dhan adapter. It builds the exact tool call for an approved order, refuses a call that differs from the one you approved, refuses duplicates (by Dhan's `correlationId`), and records results. It makes **no network call** and holds **no credentials**.
- A live trade is **one super order** (entry, stop-loss and target legs). Leaving early is cancel (own yes) then sell (own yes).
- The `securityId` map builder (`dhan.py securities`), the connection check (`dhan.py check`), `prepare`, `record`, `sync`, `reconcile`, `set-charges`, `prices`, `status`.
- Routing by `"broker"` in the ticket, the approval hash and the risk check; the switches `broker` and `dhan_enabled` are yours alone (`control.py` refuses them).
- Everything shared with Zerodha: the live ledger in the database, order events, fills, discrepancies, the live reconcile, actual charges, the Live Account in Notion, the first-two-weeks half risk, the 0.3% price rule, the cash check, the kill switch and pause.
- Tested only against `FakeDhan` in `tests/test_dhan.py`.

## What is missing: the real connection

- **Dhan's real tool names** and the real **field names** in its results. The defaults in `dhan.py` are guesses (`dhan_tools` overrides the names; the readers accept several spellings of each field). Until you connect and read them, nothing is verified. See [VERIFY.md](../VERIFY.md) section 15.
- Whether Dhan's hosted MCP route needs a **registered static IP** (its order APIs are documented as needing one).
- The login routine each morning.

## Work breakdown

1. **Primary-source check** (you or me, once `dhan.co`, `dhanhq.co` and `docs.dhanhq.co` are reachable): read Dhan's MCP docs and order docs; replace SECONDARY rows in VERIFY.md section 15 with VERIFIED and a date, or correct them.
2. **Connect the connector** in Claude, approve its consent screen yourself, and open its tool list. Save the list and a sample of each read tool's output (identifiers removed) as fixtures.
3. **Set permissions**: every order, super-order, modify, cancel and forever-order tool on "Needs approval". Read-only tools may stay on Always allow.
4. **Fill `dhan_tools`** in `trader/config.json` with the real names; run `dhan.py securities --csv api-scrip-master.csv`.
5. **Fix the adapter to the real shapes**: update the field spellings in `dhan.py`, add the real samples to `tests/test_dhan.py`, keep every existing test passing.
6. **Dry run, no order**: `dhan.py check`, read orders, trades, positions and funds, run `reconcile` on an account with no activity.
7. **Smallest real order**: one share of a liquid stock through the whole chain (risk PASS, typed yes, Allow once card, the super order, the stop or target leg, the exit). Compare every field with Dhan's own order book and trade book. Enter the contract-note charges.
8. **Two-week half-risk period** at ₹250 a trade (enforced by the pack), with the Live Account in Notion and a reconcile every day.
9. **Review**: live against paper (slippage, fills, charges), from the separate live record.

## Prerequisites (all must be true before any live order)

1. Phase 1 exit test passed and `golive.py check` read; you decided in writing how much money you can afford to lose.
2. A Dhan account in good standing with two-factor login on; Dhan's own documents on API and MCP access read.
3. Every item in VERIFY.md "Items to re-check when you connect Dhan" checked, with its status changed and dated.
4. The protection files load each morning (live mode blocks entries without them).
5. Every order-capable tool in Claude on "Needs approval", never "Always allow".

## Security rules (non-negotiable)

- Tokens, passwords, client secrets and TOTP codes never go into the chat, the repository, the database, Notion, logs or this pack's config. If a route needs a credential, it lives in an environment variable or in the connector's own configuration that you edit. The adapter reads none, and `dhan.py check` stores no account detail.
- No tool asks for them. If anything does, stop.
- `mode`, `broker`, `dhan_enabled` and `kite_enabled` are set only by you, by hand. `control.py` refuses them.
- No order from a scheduled or background task. Each live order is one risk PASS, one typed yes, one Allow once card.
- A Dhan entry is a limit order (`size.py --limit`): the price is part of what you approve.
- No automatic retries. After a failure the yes is used up. Before any retry the order book is read, so one yes can never create two orders.

## Tests

- All existing tests pass (the Kite route is unchanged).
- `tests/test_dhan.py`: the call, the gates, one yes one order, price and cash checks, the whole trade (stop and target), reconcile, the connection check, the `securityId` map, routing, and a check that `dhan.py` holds no network code or credential names.
- To add in step 5: fixtures from real, anonymised connector outputs.

## Acceptance criteria

- Every item in the Dhan re-check list is VERIFIED with a date, or the feature that depends on it is removed.
- One full real round trip matches Dhan's own records to the paisa, charges included once the contract note is entered.
- The live reconcile shows no unexplained difference for ten consecutive live days.
- Turning `dhan_enabled` off, setting the kill switch, or disconnecting the connector each stops all new orders at once.
- You can say from memory what the super order does, what cancelling it does, and which gate stops each step.

## Rollback

- Set `"mode": "paper"` (your edit), or create `trader/STOP`, or pause. New orders stop at once.
- Cancel open super orders and flatten positions with the exit tickets (each needs risk PASS, your yes and the card), or in Dhan's own app.
- Disconnect the connector. Nothing else in the pack depends on it.

## Honest risks

- Tool names and fields are guesses until you connect; the adapter fails loudly rather than guess.
- A super order has three legs you did not place one by one. If Dhan's behaviour differs from its documentation, the cancel-then-exit step can leave an order behind; the reconcile looks for exactly that.
- A marketable-limit entry may not fill.
- A resting stop is an order, not a promise: gaps can fill far below it.
- Live fills are worse than paper fills; charges are a large share of a ₹250 to ₹500 risk.
- Most individual intraday traders lose money. Passing every test here does not change that. Not financial advice.
