# Roadmap

Three phases. Each starts only when the one before it has passed its exit test. The order is deliberate: prove the rules on real data, then make them easier to see, then (only then) connect real money.

| phase | name | status | detail |
| --- | --- | --- | --- |
| 1 | Paper trading on real data | **Built, not yet proven on real data** | this page, below |
| 2 | Local dashboard (read-only, plus a planner) | Planned | [PHASE_2_UI.md](PHASE_2_UI.md) |
| 3 | Dhan connection | Adapter built and tested against a fake Dhan; the real connection is planned | [PHASE_3_DHAN.md](PHASE_3_DHAN.md), [DHAN.md](DHAN.md) |

## Phase 1: paper trading on real data (now)

What exists: the whole pipeline, the paper broker, the database, the Notion journal, the controls, the protections, the live-order safety layer and the Dhan adapter (off), and the docs. All of it was tested offline on synthetic data. Nothing has run against real NSE, Yahoo, Moneycontrol, Zerodha or Notion.

What Phase 1 is for: finding out what breaks on real data, and learning the routine ([HOW_TO_USE.md](HOW_TO_USE.md)).

### Work in Phase 1
1. Run the setup and the checks on your own machine ([GETTING_STARTED.md](GETTING_STARTED.md)). Report every error message word for word.
2. Run the morning routine on real data for one week. For each feed, note whether it worked: Yahoo `.NS` quotes and bars, NSE announcements, Moneycontrol, India VIX and NIFTY from Yahoo, the NSE equity list and NIFTY list.
3. Fix what is broken. Typical fixes: a feed URL, a CSV column name, a response shape, a Notion column rejected by the connector. Each fix gets a test (using a small saved real sample, with no personal data) and a line in [VERIFY.md](../VERIFY.md) changing its status from UNVERIFIED to VERIFIED, with the date.
4. Run `tests/capture.py` on your machine to save real Yahoo bars, and replace the synthetic fixtures for the scanner, entry, exit, time machine and paper broker tests.
5. Replace the README's synthetic worked example with a real one (a real symbol, a real date).
6. Paper trade for about 20 trading days, at least 40 trades if the setups allow. Journal every day in Notion and run `journal.py verify` after each sync.
7. Run `db.py check` weekly, `db.py backup` weekly, and practise `db.py restore` once.

### Exit test for Phase 1 (all must be true)
- Every feed the daily routine depends on has been seen working on real data, and its row in VERIFY.md says VERIFIED with a date. Anything that cannot be made to work is replaced or removed, not left broken.
- The synthetic fixtures are replaced by real captured ones, or the README says plainly which are still synthetic.
- `journal.py verify` has said "Everything agrees" after at least 10 trading days.
- `db.py check` passes, and a restore from a backup has been tried.
- You have completed at least 20 paper trading days and can say, from the Notion record, how many trades you took, how many you skipped and why, and what your charges were.
- You have run `golive.py check` and read it. It never says "ready"; the decision is yours.
- You have not broken a rule in a way you did not understand (a loss bigger than the limit, a trade without a reason).

If the paper result is poor or sits inside the noise, the right move is more paper trading, not Phase 3.

## Why this order

- A dashboard on feeds that do not work shows empty boxes. A dashboard on proven feeds shows what you actually need.
- Real money needs the rules to have survived real data. The Dhan adapter is built but off, and stays off until Phase 3's own checklist is done.
- Each phase adds a way to lose money or to fool yourself (a clickable UI invites impulse; a live account has consequences). Neither is added before the one under it is solid.

## Rules that do not change in any phase

- Paper is the default. `mode`, `broker`, `dhan_enabled` and `kite_enabled` are set only by you, by hand.
- Every order needs a risk PASS on that exact order and your typed yes for that one order. In live mode it also needs Claude's own Allow once card.
- No secrets in the repository, the database or Notion. Standard-library Python 3.8+ only. No order from a scheduled or background task.
- No test is deleted or weakened to make something pass. Every outside fact goes in VERIFY.md with a source and a date.
- Not financial advice. Most people who day trade lose money.
