---
name: trader-budget
description: >-
  Budget Manager for NSE intraday. Sizes every trade in whole shares so the
  loss at the stop PLUS Zerodha's charges (and the paper slippage) stays
  within your max loss per trade (₹500) and the position within ₹75,000,
  keeps a 10 % cash buffer, never uses leverage, and says which limit decided
  the size. Refuses a trade whose reward:risk after charges is under 1.5 or
  whose charges would eat more than 25 % of the target profit. Writes ONE
  order to trader/orders.json: broker-neutral fields plus the Kite Connect
  payload (entry, the stop-loss that follows the fill). Holds the charges
  calculator and writes trader/config.json with your limits. Nothing is
  placed. Use when the user says "how many shares", "size this trade", "how
  much should I buy", "what are the charges", "set my rules", or after
  /trader-exit and /trader-timemachine.
argument-hint: "[init] [--limit PRICE]"
---

# trader-budget

The Budget Manager turns a plan ("buy SYNTHA at 1,416, stop 1,408") into an
exact order ("buy 45 shares"), inside limits you set once. The stop decides
the size: the further away the stop, the fewer shares. Charges are part of
the loss: on a ₹63,000 position a round trip costs about ₹67 with Zerodha, so
they are counted before anything else. It writes a proposal only.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives). If your Claude does not
set that variable, use the path of the folder this SKILL.md is in. Run every command from your working
folder (the one that holds `trader/`), not from inside the skill folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" init                   # trader/config.json with your limits, once
python3 "${CLAUDE_SKILL_DIR}/size.py"                        # trader/plan.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                # write trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit --write        # a limit order at the plan's entry
python3 "${CLAUDE_SKILL_DIR}/charges.py" --qty 45 --entry 1416 --exit 1408   # the charges on any round trip
```

`--now "2026-10-30 09:51"` pins the clock (India time). A plan for another
day is refused: make a new one with trader-exit.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | your limits (`size.py init` writes it from `config.example.json`) |
| `trader/plan.json` | the trade: entry, stop, target, tick, flat-by (trader-exit writes it) |
| `trader/prices.json` | optional: a fresh price above the plan's entry means a bigger risk (trader-alerts writes it) |
| `trader/state/paper.json` | optional: the paper account's cash caps the size (the paper broker keeps it) |
| `trader/orders.json` | the one proposed order it writes with `--write` |

## Step 1: your limits, once

`size.py init` writes `trader/config.json` with the limits you gave
(2026-10-29). Only you change them; the tools never do.

| key | value | meaning |
| --- | --- | --- |
| `capital_inr` | 100000 | the paper account starts at ₹1,00,000 |
| `max_loss_per_trade_inr` | 500 | the loss at the stop, charges included |
| `max_daily_loss_inr` | 1500 | realised loss + open risk today: then no new entries today |
| `max_weekly_loss_inr` | 4000 | the same from Monday: then no new entries until next Monday |
| `max_drawdown_pause_inr` | 8000 | ₹8,000 under capital: all paper trading stops for your review |
| `max_trades_per_day` / `max_open_positions` | 3 / 1 | |
| `max_position_value_inr` / `cash_buffer_pct` | 75000 / 10 | |
| `min_reward_risk` | 1.5 | after charges |
| `max_charges_pct_of_target` | 25 | charges may not eat more than this of the target's gross profit |
| `allow_margin` / `allow_short` | false / false | cash only, longs only |
| `no_entries_before` / `no_entries_after` / `flat_by` | 09:30 / 14:45 / 15:00 | India time |
| `paper_slippage_pct` | 0.05 | per side, in paper fills |
| `limits_include_slippage` | true | count that slippage in the ₹500 and the 1.5 (see below) |
| `mode` / `kite_enabled` | paper / false | live needs both changed, by you |
| `charges` | rates, with the date checked | the charges calculator reads these |

## Step 2: size the trade

Whole shares only. The size is the smallest of three limits:

| limit | shares |
| --- | --- |
| max loss | the most shares whose loss at the stop, with slippage and charges, is within ₹500 |
| position cap | ₹75,000 / entry |
| cash | (cash minus the 10 % buffer of capital) / entry; cash is the paper balance when there is one |

Then, at that size, it refuses (no order) when reward:risk after charges is
under 1.5, when charges are over 25 % of the gross profit at the target, when
no share fits, when the stop or target is on the wrong side, when the plan is
for another day, or when the symbol is blocked. Say the reason in one line.

**Slippage is counted in the limits by default.** The paper broker fills you
0.05 % worse on each side, so without it a trade sized to exactly ₹500 would
lose about ₹563 at the stop in the paper record. With `"limits_include_slippage":
false` the ₹500 and the 1.5 count charges only, as your rule was written;
paper fills still include the slippage.

## The charges (Zerodha intraday equity, VERIFY.md section 5)

Brokerage the lower of ₹20 or 0.03 % per executed order; STT 0.025 % on the
sell; NSE transaction 0.00297 % (unverified, three values seen); SEBI ₹10 per
crore; stamp 0.003 % on the buy; GST 18 % on brokerage + exchange + SEBI.
Treat the total as an estimate; the contract note is the final word.

## Step 3: show the user

Show the three limits, which one decided the size, the shares, the rupees,
the loss at the stop with charges, the net profit at the target, reward:risk
before and after charges, and the charges' share of the target. Say plainly
that a stop is an order, not a promise. Then hand over to `/trader-risk`.

## The order it writes

```json
{"symbol": "SYNTHA", "exchange": "NSE", "side": "buy", "qty": 45, "product": "MIS",
 "entry_type": "market", "limit_price": null, "stop": 1408.0, "target": 1440.0,
 "kite": {"entry": {"variety": "regular", "exchange": "NSE", "tradingsymbol": "SYNTHA",
                    "transaction_type": "BUY", "quantity": 45, "product": "MIS",
                    "order_type": "MARKET", "validity": "DAY", "market_protection": -1},
          "stop_after_fill": {"...": "SELL 45 SL-M trigger 1408.0, MIS"},
          "target": "not a resting order: ..."},
 "why": "...", "plan": {"max_loss": 490.88, "reward_risk_net": 1.93, "...": "..."}}
```

The first nine fields are the broker-neutral order; trader-risk and your yes
bind to exactly those. The Kite payload follows the verified Kite Connect
field names (pykiteconnect) and must be re-checked against the Kite MCP tool
before live use. Only the stop rests at the broker: Zerodha offers no bracket
order, and a resting target plus a resting stop could both fill and leave you
short.

## Example (synthetic test data)

SYNTHA, entry 1,416.00, stop 1,408.00, target 1,440.00 (3R): the max loss
decides 45 shares (₹63,720), about ₹490.88 lost at the stop of which ₹67.34 is
charges; reward:risk 3.00 before charges, 1.93 after. With a 2R target
(1,432.00) the same trade is refused: 1.20 after charges, under your 1.5.

## What it never does

It never places, approves or schedules an order, never raises a limit on its
own, never shrinks a stop to fit more shares, and never sizes F&O, margin or
short positions.

## Added (database edition)

- `size.py init` writes the limits as **percent of capital** (`limits_pct`; the rupee values are derived) and creates
  `trader/trader.db`. Only `control.py` (with the user's yes) or the user's own edit changes them.
- Sizing uses the same **half-size** and **protection** rules as trader-risk (results day, events, VIX, the first live
  weeks, ASM/GSM, MIS list), so the order you are shown is one the risk check will accept. It says when it halved
  the risk and why.

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
