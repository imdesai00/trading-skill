---
name: trader-broker
description: >-
  Order Executor for NSE intraday, paper first. Shows each order as a plain
  ticket (what is bought, the stop that follows the fill, the target, the
  loss at the stop with charges), records your yes word for word and only
  for that one order, and refuses anything trader-risk did not pass today in
  the same mode. In paper mode (the default) no broker is ever contacted:
  the paper broker records the trade. In live mode, which only you can turn
  on and only with Zerodha Kite connected and verified, it shows the exact
  Kite payload and Claude's own Allow once / Deny card asks again. Builds
  the exit ticket for the flat-by time or the target. Use when the user says
  "place the order", "buy it", "take the trade", "paper trade it", "close my
  position", "flatten", "get me out", "connect Kite", or after /trader-risk
  passes.
argument-hint: "[order number]"
---

# trader-broker

The Order Executor is the only skill in this pack that can lead to an order,
paper or real, so it is the strictest. Every order goes through these gates,
every time:

1. **trader-risk passed this exact order today, in this mode** (matched by fingerprint).
2. **You typed an explicit yes for this one order** in the chat.
3. **Live mode only: you clicked Allow once** on Claude's own permission card,
   which appears because every Kite order tool is set to "Needs approval".

No yes, no order. Paper mode keeps gates 1 and 2 so the practice is real.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" decline --index 1 --reply "no"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol SYNTHA --qty 45
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id ID   # live only
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` never talks to a broker. It renders tickets, checks that a reply
is a real yes for that order, builds the exit ticket, and keeps the log in
`trader/state/broker.json`. `--now "2026-10-30 09:51"` pins the clock (India time).

```bash
python3 "${CLAUDE_SKILL_DIR}/paper.py" open --index 1            # after your yes: book the paper trade
python3 "${CLAUDE_SKILL_DIR}/paper.py" reconcile                 # replay the bars: stop, target or flat-by
python3 "${CLAUDE_SKILL_DIR}/paper.py" skip --index 1            # a no or a BLOCK: log the skipped signal
python3 "${CLAUDE_SKILL_DIR}/paper.py" skip --from-plan          # a trade trader-budget refused
python3 "${CLAUDE_SKILL_DIR}/paper.py" status                    # the paper account
```

`paper.py` is the paper broker: it never contacts a broker. The Kite adapter
(`kite.py`, disabled unless live) comes in a later build phase.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes it; `flatten` replaces it) |
| `trader/config.json` | `mode` and `kite_enabled`, which only you change |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today, in the same mode, is READY |
| `trader/state/broker.json` | every yes, no, placed and failed, word for word |
| `trader/state/paper.json` | the paper account: balance, every paper trade, every skipped signal (trader-risk reads it) |
| `trader/timemachine.json` | the time machine's odds, copied into the paper trade |

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/trader-risk`.
2. Show ONE ticket and ask: "Paper-trade this order? Reply yes or no." (live:
   "Place this order?"). One order per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   If it prints "Not recorded", the reply was not an explicit yes for this
   order (for example "sure", "yes to all", or a yes that names a different
   stock or amount). Ask again. Never turn a reply into a yes yourself.
4. Paper mode: no broker is contacted. Run `paper.py open --index N` to book it.
   Live mode: call the Kite `place_order` tool with exactly the payload the
   ticket printed; Claude's card appears: Allow once or Deny. Then
   `ticket.py result`. A failed order is not retried without a new check and a new yes.
5. If the user says no, `ticket.py decline`, then `paper.py skip --index N`. A BLOCK from trader-risk
   gets `paper.py skip --index N` too, and a plan trader-budget refused gets `paper.py skip --from-plan`.
6. After 15:30: `paper.py reconcile`. Read the result back: the exit, gross, charges, net, R, the balance,
   and every note (a fill worse than planned, a gap, a stop and target in one bar).

## Paper trading: how a paper trade is filled

| step | rule |
| --- | --- |
| entry | after your yes, `paper.py open` books it; it fills at the OPEN of the first 1-minute bar that starts at or after your yes, plus 0.05 % |
| exits | `paper.py reconcile` walks the 1-minute bars from the entry bar on. The stop is checked before the target; a bar that touches both counts as the STOP |
| stop | fills at the stop, or at the bar's open if the bar opened below it (a gap), minus 0.05 % |
| target | fills at the target, minus 0.05 %, never better |
| flat-by | still open at 15:00: the close of the last bar that ends by 15:00, minus 0.05 % |
| your own exit | a yes to an exit ticket closes it at the next 1-minute bar's open, minus 0.05 % |
| charges | the full Zerodha intraday charges on both legs (trader-budget/charges.py) |
| record | Trade ID (PT-YYYYMMDD-NN), fills, exit reason, gross, charges, net, R, the loss planned at the stop, the time machine's odds, the risk verdict, news tags, your exact words, the balance after |

A paper trade can lose more than the ₹500 planned when the fill comes in above
the planned entry or the price gaps through the stop. The record says so, in
plain words, with the amount. That is what live trading does too.

Reconcile the same day, after 15:30: Yahoo keeps only about a week of 1-minute
bars. `--interval 5m` replays on 5-minute bars when the 1-minute bars are gone,
and the trade is noted as replayed on 5-minute bars. Run during the day, it
only uses bars that have closed and leaves a trade open if nothing hit.

**Skipped signals.** When you say no, or trader-risk blocks an order, or
trader-budget refuses a plan, log it: `paper.py skip --index N` (or
`--from-plan`). reconcile replays it the same way from the moment you skipped
it and records what it would have made or lost after charges. It never touches
the balance. Over four weeks this is how you compare your judgement with the
system's.

## Steps: while it is open, and out by 15:00

- trader-alerts watches the stop, the target and the flat-by time.
- Live: only the stop rests at Kite (an SL-M sell placed after the entry fills,
  its own yes and card). Zerodha has no bracket order and, as far as could be
  checked, no cover order or GTT for intraday (VERIFY.md section 2).
- When the target alert rings, or at 15:00: `ticket.py flatten --symbol S --qty Q`
  writes the market sell. Then `/trader-risk` (an exit is never blocked by the
  day limits or the window), `ticket.py show`, and the user's yes. Live: cancel
  the resting stop first (its own yes and card), or the sell could leave you short.
- Zerodha squares off MIS positions in closing-auction stocks at 15:12 (and
  others at 15:25), and may charge for it (the charge was not verified).
  Being flat by 15:00 avoids that.

## Connecting Kite later (only you do this)

The full steps come with the Kite adapter. The outline, from VERIFY.md:

1. The hosted Kite MCP server is `https://mcp.kite.trade/mcp`
   (`claude_desktop_config.example.json` here). Its README says it leaves out
   "potentially destructive trading operations", so it most likely cannot place
   orders; placing orders likely needs the server self-hosted with your own Kite
   Connect API key. Re-check this when you connect.
2. Set every Kite tool that places, modifies or cancels anything (`place_order`,
   `modify_order`, `cancel_order`, `place_gtt_order`, `modify_gtt_order`,
   `delete_gtt_order`) to **Needs approval**. Never Always allow. Read-only
   tools may be Always allow.
3. Log in to Kite once a day yourself (an access token lasts until about 07:30
   the next morning, per Kite staff on the forum).
4. Set `"mode": "live"` and `"kite_enabled": true` in `trader/config.json`
   yourself, only after the go-live review.

## What it never does

- Never places an order without every gate, and never two orders on one yes.
- Never places orders from a scheduled or background task: nobody is there to say yes.
- Never asks for, reads, stores or repeats API keys, secrets or access tokens.
- Never switches `"mode"` or `"kite_enabled"`. You do that yourself.
- Never promises a fill price: a market order and a stop fill at whatever the market gives.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading is the default (`"mode": "paper"`): no broker is contacted, ever. Live mode needs
  `"mode": "live"` AND `"kite_enabled": true`, both set by you, plus a Kite connection verified today.
  The tools never change those settings.
- Every order, paper or live, needs a trader-risk PASS on that exact order and your typed "yes" for
  that one order. A live order also needs Claude's own "Allow once" card, with every Kite order tool set
  to "Needs approval". Never "yes to all", never two orders on one yes, never an order from a scheduled
  or background task.
- NSE cash equity, intraday (MIS), longs only. No F&O, no margin or leverage, no short selling.
- Kite keys and access tokens live in environment variables or config you edit yourself, never in the chat.
