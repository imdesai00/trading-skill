# Configuration: `trader/config.json`

`size.py init` writes this file. You may edit it with any text editor; the tools never change `mode`, `broker`, `dhan_enabled`, `fyers_enabled` or `kite_enabled`, and change nothing else
unless you say yes to `control.py`. If you edit the file by hand, the next `check.py` or `control.py morning` notices and records the edit in the
database (`config_history`), so there is a trail.

**Limits are percentages of capital.** `limits_pct` is the source of truth; the `*_inr` rupee values are derived from it (capital times percent). If
you type a different rupee value by hand, the percent wins and the tools tell you so. To change a limit properly, ask Claude (it uses
`control.py set`), or edit `limits_pct` yourself.

## Mode (yours alone)

| key | default | meaning |
| --- | --- | --- |
| `mode` | `"paper"` | `"paper"` contacts no broker, ever. `"live"` is used only together with `kite_enabled` and a Kite check from today. |
| `broker` | `"dhan"` | which live broker the pack uses: `"dhan"`, `"fyers"` or `"kite"` (Zerodha; also what a config without this key means). One broker at a time. Set by you |
| `dhan_enabled` | `false` | the second switch for live trading with Dhan. Live also needs a Dhan check from today. Set by you |
| `fyers_enabled` | `false` | the same switch for the FYERS route (`"broker": "fyers"`). Set by you |
| `kite_enabled` | `false` | the same switch for the Zerodha route (`"broker": "kite"`). Set by you |
| `fyers_tools` | `{}` | FYERS's real MCP tool names once you have read the connector's tool list (keys as for `dhan_tools`, without `place_super`). Anything left out uses a guessed default |
| `dhan_tools` | `{}` | Dhan's real MCP tool names, once you have read the connector's tool list. Keys you may set: `profile`, `orders`, `positions`, `trades`, `place`, `place_super`, `cancel`, `cancel_super`, `ltp`, `margins`, `holdings`. Anything you leave out uses a guessed default |

## Capital and limits

| key | default | meaning |
| --- | --- | --- |
| `capital_inr` | 100000 | your trading capital (change it with `control.py capital`, which shows old against new and asks for your yes) |
| `limits_pct` | see below | the limits as percent of capital |
| `max_loss_per_trade_inr` | 500 | derived: 0.5% of capital. The most one trade may lose at its stop, including charges and slippage |
| `max_daily_loss_inr` | 1500 | derived: 1.5%. Realised loss plus open risk today; at the limit, no new entries today |
| `max_weekly_loss_inr` | 4000 | derived: 4%. Same for the week; resets Monday |
| `max_drawdown_pause_inr` | 8000 | derived: 8%. Below capital by this much, everything stops until you review |
| `max_position_value_inr` | 75000 | derived: 75%. The most one position may cost |
| `cash_buffer_pct` | 10 | percent of capital that must stay in cash |
| `max_trades_per_day` | 3 | entries per day |
| `max_open_positions` | 1 | positions open at once |
| `min_reward_risk` | 1.5 | reward to risk after charges |
| `max_charges_pct_of_target` | 25 | refuse a trade whose charges are more than this share of its target profit |
| `allow_margin` | false | leverage is off |
| `allow_short` | false | short selling is off |
| `require_stop` | true | every entry needs a stop |
| `limits_include_slippage` | true | count the 0.05% slippage inside the loss limit (you confirmed this) |

`limits_pct` holds `max_loss_per_trade_pct` 0.5, `max_daily_loss_pct` 1.5, `max_weekly_loss_pct` 4, `max_drawdown_pause_pct` 8 and `max_position_value_pct` 75.

## Time

| key | default | meaning |
| --- | --- | --- |
| `no_entries_before` | `"09:30"` | the opening range forms first |
| `no_entries_after` | `"14:45"` | no new entries after this |
| `flat_by` | `"15:00"` | everything is closed by this time. The specification said 15:10; you chose 15:00. Zerodha squares off MIS positions itself from about 15:12 (unverified) |

## Brakes and protections

| key | default | meaning |
| --- | --- | --- |
| `losing_streak_stop` | 2 | this many losing exits in a row today and there are no new entries today |
| `cooldown_min` | 15 | minutes with no entry after a losing exit |
| `live_start_risk_pct` | 50 | the first live weeks run at this percent of normal risk |
| `live_start_weeks` | 2 | how long that lasts, counted from the first live trade |
| `vix_half` | 20 | India VIX above this halves the per-trade risk |
| `vix_stop` | 25 | India VIX above this: no entries |
| `gap_half_pct` | 1.0 | a NIFTY opening gap bigger than this percent halves the risk |
| `circuit_margin_pct` | 2.0 | no entry within this percent of the upper or lower circuit |
| `max_data_age_min` | 3 | a price older than this blocks an entry |
| `protection_data_required` | null | `null` means: warn in paper mode, block in live mode when the daily protection files are missing. `true` always blocks, `false` never |
| `event_days` | {} | extra half-size days you name, like `{"2026-11-06": "RBI policy"}` |
| `blocked` | [] | symbols you never want to trade |
| `index_expiry_weekday`, `budget_day`, `half_size_factor` | optional | expiry weekday (Monday is 0; default Tuesday), the budget date as `"02-01"`, and the factor (default 0.5). Not written by `init`; add them only to change the defaults |
| `dhan_securities_file` | optional | where the symbol-to-securityId map is (default `trader/state/dhan_securities.json`; built by `dhan.py securities`). Not written by `init` |
| `live_price_move_pct` | optional | live mode: how far Kite's price may be from the plan before a new yes is needed (default 0.3). Not written by `init` |

## Paper trading and review

| key | default | meaning |
| --- | --- | --- |
| `paper_slippage_pct` | 0.05 | each side of a paper fill is worse by this percent |
| `paper_period_weeks` | 4 | how long to paper trade before reviewing |
| `min_trades_for_review` | 40 | `golive.py` warns under this many trades |

## Orders, universe, charges, Notion

| key | default | meaning |
| --- | --- | --- |
| `exchange`, `product` | `"NSE"`, `"MIS"` | cash equity, intraday only |
| `stop_order_type` | `"SL-M"` | the resting stop at Zerodha. If Kite rejects SL-M for MIS, set `"SL"` |
| `stop_limit_buffer_pct` | 0.5 | the limit buffer used if the stop type is `"SL"` |
| `universe_index` | `"nifty100"` | `nifty50`, `nifty100`, `nifty500` or `all` |
| `min_price_inr`, `min_avg_volume` | 50, 500000 | price and liquidity floors for scans |
| `charges` | rates and date | Zerodha's intraday charges. Check them against zerodha.com/charges; the tools say when they were last checked |
| `notion` | ids | filled in by the Notion setup; no secrets |

## Files in `trader/state/` that the tools read

`surveillance.json`, `mis.json`, `bands.json`, `market.json`, `results_today.json`, `events.json`: see [DATA_FILES.md](DATA_FILES.md).

## The two things the tools will never write

`mode`, `broker`, `dhan_enabled`, `fyers_enabled` and `kite_enabled`. To go live you edit them yourself, after [GO_LIVE.md](GO_LIVE.md).
