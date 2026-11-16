# Getting started

This takes about an afternoon. You do not need to know how to program. You do need: a computer, Python 3.8 or newer, Claude (Claude
Code, Claude Cowork or Claude Desktop with skills), and a Notion account if you want the journal (you do).

## 1. Check Python

```bash
python3 --version
```

It must say 3.8 or higher. Nothing needs installing: the pack uses only what Python already includes (including SQLite).

## 2. Put the pack where Claude can use it

Pick one.

- **Claude Code, as a plugin**: `/plugin marketplace add imdesai00/trading-skill` then
  `/plugin install india-day-trader-skill@india-day-trader-skill`. The skills appear as `/india-day-trader-skill:trader-scanner` and so on.
- **Claude Code or Cowork, plain names**: copy the folders: `git clone https://github.com/imdesai00/trading-skill.git` then
  `cp -r trading-skill/trader-* ~/.claude/skills/`.
- **Claude Desktop or claude.ai**: build the upload files with `python3 tests/package.py`, then upload each ZIP in `dist/` under Settings,
  Capabilities, Skills. Each skill is standalone. Whether your Claude can reach the internet and keep a `trader/` folder between chats
  varies, so try `alerts.py clock` and then `alerts.py quote RELIANCE` first. If quotes fail, use Claude Code or Cowork on your own computer.

## 3. Make a working folder

Make one empty folder, for example `~/trading`, and always start Claude in it. Everything the tools make goes in a subfolder called
`trader/` there. That folder is private: it holds your trades and your database, and it is in `.gitignore` so it never goes to GitHub.

## 4. Create your config and database

```bash
python3 <path-to-pack>/trader-budget/size.py init
```

This writes `trader/config.json` with your limits for a ₹1,00,000 account (max loss ₹500 a trade, ₹1,500 a day, ₹4,000 a week, pause at
₹8,000 below your start, 3 trades a day, 1 open position) and creates `trader/trader.db`. Read [CONFIG.md](CONFIG.md) once. The limits
are stored as percentages of your capital, so changing the capital later scales them.

```bash
python3 <path-to-pack>/trader-db/db.py check
```

It should say the database is intact.

## 5. Connect Notion (the journal)

Connect Notion to Claude through Claude's Connectors screen. **Never give a Notion token to any tool in this pack**; it never asks. Then
tell Claude "set up the Notion journal" and name the Notion page where it may create things. Details are in [NOTION.md](NOTION.md).

## 6. Try the safe things first

```bash
python3 <path-to-pack>/trader-alerts/alerts.py clock          # is NSE open? (needs no internet)
python3 <path-to-pack>/trader-control/control.py morning      # the morning check
python3 <path-to-pack>/trader-scanner/scan.py universe        # needs internet: NSE's equity list
```

The first two work offline. The third downloads NSE's list; if it fails, download the two CSV files in your browser and pass them with
`--equity-file` and `--index-file` (the error message says so).

## 7. The daily files

The risk checks use four small files you refresh each morning (ASM/GSM lists, the MIS list, price bands, and India VIX). Without them the
checks that need them cannot run, and in paper mode you get a warning instead of a block. [DATA_FILES.md](DATA_FILES.md) explains where
to get them and how to load them. Do this on day one: it is the part most people skip.

## 8. Your first paper day

Follow [DAILY_ROUTINE.md](DAILY_ROUTINE.md). On the first day, expect most setups to be refused by the budget manager or the risk manager. That is
the pack working: with ₹500 of risk and the broker's charges, many trades are not worth taking.

## 9. Security reminders (read these now, not later)

- Turn on **two-factor login (TOTP) at your broker** before you ever connect an API.
- **Never share a broker API key, secret, password, TOTP code or access token** with anyone, **including Claude**. If a tool or a chat asks for one,
  stop.
- Broker credentials, if you ever use them, live in environment variables or in a config file you edit yourself, outside this folder. This pack never
  asks for them and never stores them.

## What "done" looks like for week one

You can run the morning check without help, you have loaded the protection files at least twice, you have seen a trade refused and understood why,
you have paper-traded at least one setup from start to finish, `db.py check` passes, and the Notion journal shows the day.
