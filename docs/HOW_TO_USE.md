# How to use it: one page, start to finish

This is the single page to read first. It tells you what to say to Claude, what you will see, and what you must do yourself. The other pages in this folder are the detail.

## The idea in four sentences

You talk to Claude in plain words ("run the morning check", "scan for breakouts"). Claude runs the small Python tools in this pack and explains the results. **Nothing places an order unless a risk check passes and you type a plain "yes" for that one order.** For the first four weeks you only practise on paper: a simulated account, kept in a local database and mirrored in Notion. Real money (Zerodha Kite) is a separate decision you make yourself, much later.

## Part 1: set up once (an afternoon)

1. Make an empty folder (for example `~/trading`) and start Claude Code, Cowork or Claude Desktop in it. Add the skills ([GETTING_STARTED.md](GETTING_STARTED.md), step 2).
2. Say: **"Create my config and database."** Claude runs `size.py init`. You now have your limits for a ₹1,00,000 account: max loss ₹500 a trade, ₹1,500 a day, ₹4,000 a week, pause at ₹8,000 below your start, 3 trades a day, 1 position at a time.
3. Say: **"Check the database."** It should say the database is intact.
4. Connect Notion in Claude's Connectors screen, then say: **"Set up the Notion journal."** Claude asks which Notion page to use. Name one. Never give Claude or any tool a Notion token.
5. Turn on two-factor login (TOTP) at your broker (Dhan) now, and never share a broker key, password or code with anyone, including Claude.

## Part 2: every trading morning (before 09:15)

Say these one at a time and read the answers:

| you say | what happens |
| --- | --- |
| "Run the morning check." | Market open or closed, mode (PAPER), balance, week so far, limits used, pause or kill switch, events, VIX, whether Notion and the database are fine. |
| "Load today's protection files." | You give Claude the ASM/GSM list, the MIS list and the price bands you saved from NSE and Zerodha ([DATA_FILES.md](DATA_FILES.md)). Skipping this means the circuit, surveillance and MIS checks cannot run. |
| "Get today's VIX and NIFTY gap." | Saves India VIX and the opening gap; a high VIX or a big gap makes the day half size. |
| "News on my watchlist." | Headlines from Yahoo, NSE and Moneycontrol. **RESULTS TODAY** next to a stock means half size for it. |
| "Any insider filings?" | Only if you export a CSV from NSE first. |
| "Run the daily scan." | Finds stocks with setups and saves a watchlist. |

## Part 3: finding one trade (09:30 to 14:45)

Do not trade in the first 15 minutes. Then:

1. **"Scan for opening-range breakouts."** Pick **one** stock from the list.
2. **"Check the entry for RELIANCE."** (use your stock) It says waiting, triggered or expired.
3. If triggered: **"Plan the stop and target."** The stop is set before you enter, on the NSE tick grid, and everything is closed by 15:00.
4. **"Run the time machine."** It replays the rest of the day 10,000 times. It is a reality check, not a prediction. A coin-flip result is a fine reason to skip.
5. **"Size the trade."** It works out whole shares so the loss at the stop, including charges and slippage, fits inside ₹500. If it says **REFUSED**, the answer is no. Say "log it as skipped".
6. **"Run the risk check."** PASS or BLOCK with plain reasons. A BLOCK is final; never loosen a limit to make it pass.
7. **"Show me the ticket."** You see one ticket: **PAPER** in capitals, stock, quantity, entry, stop, target, maximum loss including charges, the risk verdict.
8. Claude asks: **"Why are you taking this trade?"** Answer in one honest sentence. It is stored with the trade.
9. Claude asks: "Place this order? Reply yes or no." Reply **yes** (or **no**) to that one order. "ok", "sure" and "yes to all" are not a yes. One yes covers one order.
10. Claude books the paper trade. No broker was contacted.

Expect most days to end at step 5 or 6. With ₹500 of risk and the broker's charges, many trades are not worth taking, and that is the pack working.

## Part 4: while the trade is open

- **"Set alerts for my trade."** It rings on the stop, the target and the 15:00 flat-by time.
- **"Check alerts."** Run it every few minutes (nothing runs by itself; you ask).
- At the target alert or at 15:00, **"Close the position."** That makes an exit ticket. It still needs a risk check and your yes.

## Part 5: after 15:30

1. **"Reconcile today."** Claude replays the day's bars, decides whether the stop, the target or the flat-by time came first (if both in one bar, the stop wins), applies full charges, and makes a database backup.
2. Claude asks: **"How did you feel about it?"** Answer honestly.
3. **"Sync to Notion."** Claude writes each waiting row through the Notion connector, then reads Notion back and compares counts and totals with your database. You should see "Everything agrees".
4. **"Show today's summary."** Plain words: trades, wins and losses, net after charges, balance, limits hit.

## Part 6: every Friday

- **"Weekly report."** Trades, win rate, average R, expectancy after charges, charges paid, best and worst trade, results by setup, skipped signals compared with trades taken, limits hit, balance against your start. Paper and live are always separate.
- **"Check the database and back it up."**

## Changing things safely

| you say | what happens |
| --- | --- |
| "Change my capital to ₹1,50,000." | Claude shows old against new limits and waits for your typed yes. It warns you during the paper test. |
| "Raise my max loss to 0.6%." | Same two steps, plus a warning that you are raising risk. |
| "Pause trading until 6 November." / "Resume trading." | Blocks or allows new orders in both modes. |
| **"Kill switch."** | Creates `trader/STOP`. No new order can be made until you remove it. |

## The four weeks

- Paper trade about 20 trading days. You want at least 40 trades before any review.
- Judge yourself on **process**, not profit: few trades, every one with a written reason, every loss inside ₹500, nothing forced.
- After four weeks say: **"Go-live check."** You get an honest report, including whether the result can be told apart from luck. With a few dozen trades it usually cannot. It never says "ready".

## Going live (later, and only if you choose to)

Read [GO_LIVE.md](GO_LIVE.md) first. In short: you connect Dhan's connector yourself, set every order tool to "Needs approval", re-check the items in VERIFY.md, run the connection check each morning, and edit `"mode": "live"` and `"kite_enabled": true` in your config yourself. The first two live weeks run at half risk automatically. A live trade is three separate orders (entry, stop, exit), each needing a risk check, your typed yes and Claude's own Allow once card. No tool and no scheduled task can do any of that for you.

## What Claude will never do

- Place an order without a risk PASS on that exact order and your typed yes.
- Switch to live mode, or change `mode`, `broker`, `dhan_enabled` or `kite_enabled`.
- Change a limit or your capital without showing you and getting your yes.
- Ask for, store or print a key, password, token or TOTP code.
- Run anything by itself in the background.

## If something goes wrong

Look the message up in [TROUBLESHOOTING.md](TROUBLESHOOTING.md). If the database check fails, do not trade: restore the newest backup. If you want to stop everything, say "kill switch".

## Honest limits

Nothing here is financial advice and most people who day trade lose money. The tools were tested on synthetic data because the real feeds could not be reached when they were built, so expect small fixes on your first real day ([AUDIT.md](AUDIT.md) lists what is unverified). Paper results flatter reality: real fills slip more and you will be slower than the simulation.
