# Setup without a broker account (Windows, Mac, and what a phone can and cannot do)

You do **not** need a broker account for the first month. Paper mode never contacts a broker. It books simulated trades in a local database,
using Yahoo Finance prices, charges and slippage. The Dhan, FYERS and Kite adapters stay switched off (`dhan_enabled`, `fyers_enabled`,
`kite_enabled` are all `false`). Connect a broker only after the month below, and only if the scorecard at the end says it was worth it.

Nothing here is financial advice. Most people who day trade lose money. Paper results are kinder than real ones.

## The honest truth about a phone

The pack is Python scripts that read and write files in a `trader/` folder. A phone cannot run them. So:

| What you want | Does it work? |
| --- | --- |
| Run the pack on the phone itself | **No.** |
| Use the Claude app on a phone to start a Claude Code session in the cloud | **Not for this.** A cloud session cannot reach Yahoo or NSE (tested: the proxy refused both), and its files vanish when the container is reclaimed. You would lose your paper record. |
| Keep the pack on a **Windows or Mac computer** and look at it from your phone | **Yes, this is the route.** Run `claude remote-control` in your trading folder on that computer; the session then shows in the Claude app on your phone, so you can read results and type "yes" from there. The computer must stay on, awake and online. *This comes from Claude's own documentation and is not tested in this pack: check that it works for you on day 1.* |
| Read the daily report on the phone | **Yes.** Copy `trader/reports/` and `trader/backups/` to a cloud folder (OneDrive, Google Drive) and open the Markdown files on the phone. |

If you have no always-on computer, paper trade only on days you are at your computer. Missing days is fine; inventing data is not.

## Part A: Windows, step by step

Open **PowerShell** (Start menu, type "PowerShell"). Lines starting with `#` are comments.

1. **Install Python 3.8 or newer** from https://www.python.org/downloads/windows/. Tick "Add python.exe to PATH" in the first screen.
   Check it:
   ```powershell
   python --version
   ```
   If that prints nothing or opens the Microsoft Store, use `py -3 --version` and write `py -3` wherever this guide says `python`.
2. **Install Git** from https://git-scm.com/download/win (accept the defaults).
3. **Install Claude Code** by following the install page for Windows at https://claude.ai/code. Then sign in:
   ```powershell
   claude --version
   ```
4. **Get the pack.** It must be on `main` (it is, once the merge in the next section is done):
   ```powershell
   cd $HOME
   git clone https://github.com/imdesai00/trading-skill.git
   ```
5. **Make your working folder** and always start from it:
   ```powershell
   mkdir $HOME\trading
   cd $HOME\trading
   ```
6. **Make your config and database** (paper, ₹1,00,000, max loss ₹500 a trade):
   ```powershell
   python $HOME\trading-skill\trader-budget\size.py init
   python $HOME\trading-skill\trader-db\db.py check
   ```
   The second command must end with "the database is intact".
7. **Put the skills where Claude finds them.** In PowerShell:
   ```powershell
   mkdir $HOME\.claude\skills -Force
   Copy-Item -Recurse $HOME\trading-skill\trader-* $HOME\.claude\skills\
   ```
8. **Start Claude in the working folder:**
   ```powershell
   cd $HOME\trading
   claude
   ```
   In Claude, type: `run the morning check`. It must print mode PAPER and your limits.
9. **First-day data test (this decides whether the whole month is possible).** Nothing here has been run against the live internet yet.
   ```powershell
   python $HOME\trading-skill\trader-alerts\alerts.py clock
   python $HOME\trading-skill\trader-scanner\scan.py universe --index nifty50
   python $HOME\trading-skill\trader-alerts\alerts.py quote RELIANCE
   ```
   If `universe` or `quote` fails, **stop and tell Claude exactly what was printed.** A feed URL or response shape probably needs fixing, and that is
   the first job (see [ROADMAP.md](ROADMAP.md), phase 1). Do not start the month until quotes work.

### Mac or Linux instead

Same steps with `python3`, `~` instead of `$HOME`, and `cp -r trading-skill/trader-* ~/.claude/skills/`.

## Part A2: what was tested

The whole chain below, from an empty folder to the daily report, was run end to end on synthetic bars with no broker: entry check, exit plan, sizing, risk check, ticket, your yes, paper trade, reconcile, "how I felt", daily and weekly report, database check (`tests/test_no_broker_flow.py`). What has **not** been tested is any real Yahoo or NSE response; that is the first-day data test above.

## Part B: the one-month paper plan

You are testing two things: **does the system behave** (limits hold, nothing breaks), and **do you follow it** (the real risk is you, not the code).

### Every trading day (about 20 minutes, India time)

| When | Do this (say it to Claude, or run the command) |
| --- | --- |
| 08:45 | `run the morning check` (`control.py morning`). Read every line. |
| 08:50 | `scan.py market --fetch` loads India VIX and the NIFTY gap. Paper warns when ASM/GSM, MIS and price-band files are missing; load them weekly if you can ([DATA_FILES.md](DATA_FILES.md)). |
| 09:00 | News on your watchlist (`news.py`). Results today means half size. |
| 09:30 | `scan.py orb`. Pick **one** stock. |
| 09:35 on | `entry.py check SYMBOL`. WAIT means you do nothing. READY continues below. |
| after READY | `exit.py plan --from trader/entry.json --target 3R --write`, then `size.py --plan trader/plan.json --write`, then `check.py`. REFUSED or BLOCK means no trade, and you log it with `paper.py skip`. **Use `--target 3R`:** the default 2R target is usually REFUSED, because after charges it is only about 1.2 times your risk and you need 1.5. |
| the ticket | `ticket.py show`, then write your one-sentence **why**, then type `yes`. That books the paper trade. |
| to 15:00 | Watch with `alerts.py`. Stop, target or 15:00 ends the trade. You never hold overnight. |
| 15:35 | `paper.py reconcile`, then `paper.py feel --trade ID --text "..."`, then `report.py daily`, then `db.py backup`. |

### Every Friday

`report.py weekly`, `db.py check`, copy `trader/backups/` and `trader/reports/` to a cloud folder, and read the week's skipped signals against the trades you took.

### Rules for the month

- **0 to 1 trade a day is normal.** Three is the maximum. Zero trades on a WAIT day is a good day.
- Never raise `max_loss_per_trade_inr` or any limit to make a trade pass. A BLOCK is final.
- Never skip the "why". Without it the month teaches you nothing.
- Do not change settings mid-month. Changing them resets what you can conclude.
- Stop for the day after 2 losses (the pack enforces a 15-minute cooldown, but the real rule is to stop).

## Part C: the scorecard at the end of the month

Run `golive.py check` and `report.py weekly`. Then answer these honestly. All must be **yes** before you even think about a broker.

| Question | Needed |
| --- | --- |
| Trading days completed | 20 or more |
| Closed paper trades | 40 or more (fewer than that cannot be told apart from luck) |
| `db.py check` passed every day | yes |
| Any loss above ₹500 (a rule broken) | none. Note: a market order can fill worse than the 0.05% slippage the sizing assumes, and the report then counts the overshoot as "rules broken" (in the offline test a ₹490 planned loss became ₹518). A few small overshoots are the real cost of market orders; a big one, or several, means your stops are too tight or the stock too jumpy. |
| Any trade taken without a written why | none |
| Any limit loosened or BLOCK overridden | none |
| Net result after charges | positive, **and** `golive.py check` says it is outside the luck range |
| Quotes and bars worked on real data without hand fixes | yes |

If the net result is negative or inside the luck range, that is a real answer: it means **do not go live**, and keep paper trading or stop. Losing ₹0 on paper
is far cheaper than losing real money to find out the same thing.

## Part D: what paper trading cannot tell you (why a broker is still a later step)

- Real fills: a real order can be partly filled, rejected, or filled at a worse price than 0.05% slippage.
- Broker behaviour: order types, stop orders and rate limits differ per broker, and **none of the Dhan or FYERS tool names or fields was verified**; see `VERIFY.md` sections 15 and 16.
- Your nerves with real money.

So after a good month the next step is not "go live". It is: connect the broker **read-only first**, run `dhan.py check`, re-verify the
`RECHECK-AT-DHAN` list, then trade ₹250 a trade (half risk) for two weeks. [GO_LIVE.md](GO_LIVE.md) has the full list.

## If something breaks

[TROUBLESHOOTING.md](TROUBLESHOOTING.md) first. Windows-specific: if a rupee sign prints as `?`, that is only your console font and harmless. The
scripts now force UTF-8 output, so they no longer crash on it.
