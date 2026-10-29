# The daily routine

All times are India time (IST). Every command below is also available as a request in plain words ("run the morning check"). Nothing in this
routine places a real order. Paper mode books a simulated trade only after a risk PASS and your typed yes.

## Before 09:15

1. **Morning check**: `control.py morning`. Read it. It tells you whether NSE is open, your mode, balance, the week so far, how much of the daily and
   weekly limits are used, any pause or kill switch, events today, results due today, India VIX, whether the protection files are loaded, when the last
   backup was, and whether Notion is up to date.
2. **Load the daily files** ([DATA_FILES.md](DATA_FILES.md)): `scan.py protect --asm ... --gsm ... --mis ... --bands ...` and
   `scan.py market --fetch` (or type the VIX in).
3. **News on your watchlist**: `news.py`. Look for RESULTS TODAY. A company reporting results today is a half-size day for that stock.
4. **Insider filings**: `insider.py check` (after importing a fresh CSV from NSE if you use that route).
5. **Daily scan**: `scan.py daily` finds stocks with setups on daily bars and writes `trader/watchlist.json`.
6. Optional: `alerts.py add --opening-bell`.

## 09:15 to 09:30

Do nothing. The first 15 minutes make the opening range; no entries are allowed before 09:30.

## 09:30 to 14:45: looking for one trade

7. `scan.py orb` lists stocks that closed above their first-15-minute high.
8. Pick **one**. `entry.py check SYMBOL` says waiting, triggered or expired.
9. If triggered: `exit.py plan --write` sets the stop and target on the NSE tick grid. Then `timemachine.py run --write` replays the rest of the
   session 10,000 times (a reality check, not a prediction).
10. `size.py --write` sizes the trade so the loss at the stop, with charges and slippage, stays inside your limit. If it says REFUSED, the answer is
    no. Log it with `paper.py skip --from-plan` so you can see what you skipped.
11. `check.py` (trader-risk) gives PASS or BLOCK on that exact order with plain reasons. A BLOCK is final: do not loosen a limit to pass.
12. `ticket.py show` prints the ticket: mode in capitals, stock, quantity, entry, stop, target, maximum loss including charges, the risk verdict.
13. **Claude asks: "Why are you taking this trade?"** Answer in a sentence. Then reply **yes** (or no) to *that one order*.
14. Paper: `ticket.py approve ... --why "..."` records it, `paper.py open` books the paper trade.

## While a trade is open

15. `alerts.py add --from-plan`, then `alerts.py quote` and `alerts.py check` rings on the stop, the target and the flat-by time (15:00).
16. At the target alert or at flat-by, `ticket.py flatten --symbol X --qty N` makes an exit ticket. It still needs risk PASS and your yes.

## After 15:30

17. `paper.py reconcile` replays the day's bars, decides which of stop, target and flat-by came first (a tie counts as the stop), applies the full
    charges, fills the skipped signals' "what would have happened", and makes a database backup.
18. **Claude asks: "How did you feel about it?"** `paper.py feel --trade ID --text "..."`.
19. **Notion**: `journal.py queue`, then Claude writes each waiting row with the Notion connector and marks it sent. Claude reads the rows back and runs
    `journal.py verify` so you see "Everything agrees" or the mismatch.
20. `journal.py daily` prints the day in plain words.

## Every Friday

21. `journal.py weekly`: trades, win rate, average R, expectancy after charges, charges paid, best and worst trade, results by setup and against the
    time machine's odds, skipped signals compared with trades taken, limits hit, balance against your starting capital. Paper and live are separate.
22. `db.py check` and `db.py backup`.

## When something is not routine

- **Change capital or a limit**: ask Claude ("change capital to ₹1,50,000"). It shows old against new and waits for your yes. See [SAFETY.md](SAFETY.md).
- **Take a break**: "pause trading until 6 November" (both modes) and "resume trading".
- **Stop everything now**: "kill switch". A file `trader/STOP` blocks every new order until you remove it.
- **Lost files**: `db.py restore` from `trader/backups/`, or `journal.py rebuild --into-db` from Notion. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## What a good week looks like

Few trades, several refusals, every trade with a written reason, every loss inside ₹500, the database check passing, and Notion matching. Profit is not the
test in the first four weeks.
