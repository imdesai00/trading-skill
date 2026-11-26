# Troubleshooting

Find the message (or what you saw) in the first column.

| what you saw | what it means | what to do |
| --- | --- | --- |
| `no database at trader/trader.db` | the database does not exist yet | `size.py init` (new) or `db.py init` then `db.py migrate` (you have older JSON files) |
| `db check` says **PROBLEMS FOUND** | a row was edited or removed, a trigger is missing, or the file is damaged | do not trade. `db.py restore trader/backups/<newest>.db` (it only restores a backup that passes). If many backups fail, tell Claude what `check` printed |
| `... was changed outside the tools and no longer matches the database` | you or something edited `paper.json` / `live.json` by hand | the database wins. The file is rewritten on the next save. Nothing is lost |
| `... has N record(s) the database does not have yet` | JSON records exist that were never imported | they are used now and imported at the next save, or run `db.py migrate` |
| `BLOCK ... KILL SWITCH` / `trading is PAUSED` | `trader/STOP` exists, or you paused | `control.py unkill --reply yes` after you decide, or `control.py resume` |
| `BLOCK ... protection data missing` | live mode, and today's ASM/GSM, MIS, band or VIX file is missing | load them ([DATA_FILES.md](DATA_FILES.md)) |
| `protection data is incomplete, so these checks could NOT run` (a warning) | the same in paper mode | load the files; until then circuit, surveillance and MIS protections are off |
| `BLOCK ... stale: the latest SYMBOL price is N minutes old` | the price file is older than 3 minutes | `alerts.py quote SYMBOL --write` and check again; or in live mode `kite.py prices --ltp FILE` |
| `BLOCK ... cooldown` / `... losing trades in a row today` | the brakes after losses | wait, or stop for the day. That is what they are for |
| `BLOCK ... halved today: ...` | a half-size day (results, event, VIX, gap, first live weeks) | size the trade again: `size.py` already knows. A smaller order passes |
| `REFUSED: reward:risk after charges ...` | the budget manager says the trade is not worth its costs | skip it; `paper.py skip --from-plan` logs it |
| `before the yes, ask the user: "Why are you taking this trade?"` | an entry needs your reason | pass it with `--why "..."` |
| `Not recorded: ...` from `ticket.py approve` | your reply was not a plain yes for that order | reply `yes` (or `yes 45 SYMBOL`), not "ok" |
| `order N is not ready: ...` | no risk PASS on this exact order today, or already decided | run `check.py` again; if you changed anything, you need a new check and a new yes |
| `dhan.py: OFF. ...` or `kite.py: OFF. ...` (exit code 2) | live mode is not fully switched on (mode, broker, the enabled switch, or no check today) | by design. See [GO_LIVE.md](GO_LIVE.md). No broker call was made |
| `dhan.py: not verified ... static IP` / `... Needs approval` | you have not confirmed the two things only you can check | confirm, then pass `--static-ip-confirmed` and `--tools-need-approval` |
| `... no securityId` / `no symbol-to-securityId map` | Dhan uses numbers, not symbols | `dhan.py securities --csv api-scrip-master.csv` |
| `a Dhan entry must be a limit order` | API market orders become limit orders | `size.py --limit` |
| `kite.py: not verified. Every Kite tool ...` | you have not confirmed "Needs approval" | set the tools in Claude, then pass `--tools-need-approval` |
| `Kite's last price ... from the planned entry` | the live price moved more than 0.3% | the yes is used up. Run `check.py` again with Kite's price and say yes again |
| `no live price` / `no margin figures` | an entry needs `--ltp` and `--margins` | call `get_ltp` and `get_margins`, save the results, pass them |
| `WARNING ... NO stop-loss at Kite` | a live position has no resting stop | `kite.py stop-ticket --trade ID` now, then risk, your yes and the card; or flatten |
| `DISCREPANCY (...)` | live reconcile found a difference from Kite | Kite is right. Look at each one; it is in the Discrepancies table. Nothing was changed for you |
| `the NSE holiday list for 2027 is not in this tool yet` | the calendar covers 2026 only | add the NSE holiday list to the clock block (see VERIFY.md section 3); the tools refuse to trade a date they cannot check |
| `NSE is closed (...)` | weekend, holiday, Muhurat or outside 09:15 to 15:30 | wait |
| `could not reach ...` / HTTP errors from Yahoo, NSE or Moneycontrol | the internet, or a changed address, or the source blocks scripts | the tool says which source failed and carries on with the others. For NSE lists, download the CSVs in a browser and pass the files |
| every quote fails inside Claude Desktop | that environment may not allow internet access | use Claude Code or Cowork on your own computer |
| `Python ... syntax error` | Python older than 3.8 | install 3.8 or newer |

## Starting again safely

- Nothing in `trader/` is ever uploaded. To start a fresh paper account, move the `trader/` folder aside (do not delete it: it is your record), then `size.py init`.
- To go back to a backup: `db.py restore trader/backups/<file>`. The file you replaced is kept as `trader.db.before-restore`.
- If you lose everything on your computer, the pack has no second copy. Copy `trader/backups/` somewhere safe now and then (another drive or a cloud folder).

## Asking for help

Give the exact message, the command you ran, the output of `db.py check` and `control.py morning`, and the relevant part of `VERIFY.md`. Never paste a key, token, password or TOTP code.
