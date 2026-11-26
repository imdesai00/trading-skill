#!/usr/bin/env python3
"""Capital, limits, pause, kill switch and the morning check (trader-control). Standard library only, no network.

    python3 control.py morning                         # the whole picture before the open
    python3 control.py status                          # mode, capital, limits, pause, kill switch
    python3 control.py capital --set 150000 --reason "added funds"               # SHOWS old vs new; changes nothing
    python3 control.py capital --set 150000 --reason "added funds" --reply yes   # after the user's typed yes
    python3 control.py set max_loss_per_trade_pct 0.4 --reply yes                # any other limit, same two steps
    python3 control.py pause --until 2026-11-06 --reason "review week"
    python3 control.py resume
    python3 control.py kill                            # creates trader/STOP: no new order in either mode
    python3 control.py unkill --reply yes              # removes trader/STOP (only you decide this)
    python3 control.py edits                           # detect and log hand edits of trader/config.json

How changes work. Every limit is stored as a percent of capital (config "limits_pct"); the rupee amounts are
derived. A change is two steps: this tool first SHOWS old and new side by side and writes nothing; only a
second call with --reply yes (the user's typed yes, which Claude must pass word for word) writes it. A write
updates trader/config.json, records the old and new values in the database (config_history, capital_changes)
and shows in the reports. This tool never changes "mode", "broker" or an "*_enabled" switch: switching to live is yours.
Capital changes are deposits or withdrawals: they move the balance and reset the drawdown baseline, and are
kept apart from trading profit and loss so the statistics stay honest. In live mode the new capital can never
be more than what Kite shows as available (--kite-available, from get_margins).
Editing trader/config.json yourself is fine: `edits` (and `morning`) notice the difference and log it.
"""

import argparse
import datetime
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db  # noqa: E402  (a copy of trader-db/db.py; a test keeps the copies identical)
import guards  # noqa: E402  (shared guard rules; trader-risk, -budget and -scanner carry identical copies)


class ControlError(Exception):
    pass


ClockError = ControlError

# ---------------------------------------------------------------- NSE clock (same block in every script)
# tests/test_repo.py checks that every copy of this block is identical.
# India Standard Time is UTC+05:30 all year (no daylight saving, no tz files).
# Holidays: NSE circular NSE/CMTR/71775 (2025-12-12) and public calendars, read
# 2026-10-29 (VERIFY.md section 3). The 2027 list is not published yet.
IST = datetime.timedelta(hours=5, minutes=30)
NSE_HOLIDAYS = {
    "2026-01-15": "Maharashtra municipal elections (in some lists only)",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
NSE_SPECIAL = {"2026-11-08": "Muhurat trading, Diwali"}  # short special sessions: never traded here
CALENDAR_YEARS = (2026,)
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
CAS_CLOSE = datetime.time(15, 15)  # continuous trading ends here for closing-auction (F&O) stocks


def to_ist(utc):
    """naive UTC -> naive India time."""
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    """naive India time -> naive UTC."""
    return local.replace(tzinfo=None) - IST


def ts_to_ist(ts):
    """Unix seconds -> naive India time. Never uses the computer's own time zone."""
    return datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(ts)) + IST


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is India time."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        raise ClockError('--now wants a time like "2026-10-30 10:15" (India time) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def trading_day(d):
    """(is_trading_day, reason) for an NSE date: weekends, holidays, special sessions, unknown years."""
    iso = d.isoformat()
    if iso in NSE_SPECIAL:
        return False, "special session (%s): not traded by this pack" % NSE_SPECIAL[iso]
    if d.weekday() >= 5:
        return False, "weekend"
    if d.year not in CALENDAR_YEARS:
        return False, ("the NSE holiday list for %d is not in this tool yet: add it from nseindia.com "
                       "(VERIFY.md section 3)" % d.year)
    if iso in NSE_HOLIDAYS:
        return False, "NSE holiday: " + NSE_HOLIDAYS[iso]
    return True, ""


def session(d):
    """(open, close) in India time on a trading day, else None."""
    return (OPEN, CLOSE) if trading_day(d)[0] else None


def inr(x, paise=None):
    """Rupees with Indian digit grouping: 100000 -> "₹1,00,000", 2913.4 -> "₹2,913.40".
    paise=True always shows paise, False never does, None shows them when not zero."""
    p = int(round(abs(float(x)) * 100))
    whole, frac = divmod(p, 100)
    if paise is False and frac:
        whole, frac = int(round(abs(float(x)))), 0
    s = str(whole)
    if len(s) > 3:
        head, groups = s[:-3], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join(([head] if head else []) + groups) + "," + s[-3:]
    if paise is True or (paise is None and frac):
        s += ".%02d" % frac
    return ("-" if float(x) < 0 and p else "") + "₹" + s


def tick_size(price):
    """NSE cash-market tick for a price (circular of 2025-03-13, VERIFY.md section 3).
    Kite's instrument list has each symbol's own tick: pass that instead once connected."""
    p = float(price)
    if p < 250:
        return 0.01
    for upper, tick in ((1000, 0.05), (5000, 0.10), (10000, 0.50), (20000, 1.00)):
        if p <= upper:
            return tick
    return 5.00


def to_tick(price, tick, way="nearest"):
    """A price on the tick grid, in whole paise: way "down", "up" or "nearest" (half up)."""
    t = int(round(float(tick) * 100))
    p = float(price) * 100
    if way == "down":
        n = math.floor(p / t + 1e-9)
    elif way == "up":
        n = math.ceil(p / t - 1e-9)
    else:
        n = math.floor(p / t + 0.5 + 1e-9)
    return round(n * t / 100.0, 2)
# ---------------------------------------------------------------- end NSE clock

NEVER_BY_TOOL = ("mode", "kite_enabled", "dhan_enabled", "fyers_enabled", "broker")
SECRET_WORDS = ("token", "secret", "password", "api_key", "apikey", "totp")
PLAIN_KEYS = {"max_trades_per_day", "max_open_positions", "cash_buffer_pct", "min_reward_risk",
              "max_charges_pct_of_target", "no_entries_before", "no_entries_after", "flat_by", "paper_slippage_pct",
              "paper_period_weeks", "min_trades_for_review", "vix_half", "vix_stop", "gap_half_pct",
              "circuit_margin_pct", "max_data_age_min", "losing_streak_stop", "cooldown_min", "live_start_risk_pct",
              "live_start_weeks", "universe_index", "min_price_inr", "min_avg_volume", "protection_data_required"}


def read_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise ControlError("%s is not valid JSON: %s" % (path, e))


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def paths(a):
    base = os.path.dirname(os.path.abspath(a.config))
    return {"base": base, "state": os.path.join(base, "state"), "db": a.db or os.path.join(base, "trader.db"),
            "stop": os.path.join(base, "STOP")}


def need_cfg(a):
    cfg = read_json(a.config)
    if cfg is None:
        raise ControlError("no %s: create it with trader-budget's size.py init" % a.config)
    return cfg


def need_db(p):
    if not db.exists(p["db"]):
        raise ControlError("no database at %s: run size.py init (new) or db.py init then db.py migrate" % p["db"])


def said_yes(reply):
    return str(reply or "").strip().lower().rstrip(".!") in ("yes", "y")


def clock(a):
    now = parse_now(getattr(a, "now", None))
    return now, to_ist(now)


def ledger_for(p, cfg, mode):
    lp = os.path.join(p["state"], "live.json" if mode == "live" else "paper.json")
    st = db.LedgerStore(lp, p["db"], mode, cfg, lambda c: {"trades": [], "skipped": [], "balance": c.get("capital_inr"),
                                                           "capital": c.get("capital_inr")},
                        lambda q: read_json(q), lambda q, d: None)
    return st.load()


def hand_edits(cfg, p, now, source="manual-edit"):
    """Compare the config file with the last one the database knows. Log a difference. -> list of changed keys."""
    if not db.exists(p["db"]):
        return []
    old = db.last_config(p["db"])
    if old is None:
        db.log_config(p["db"], cfg, "initial", "first time seen", now)
        return []
    changed = sorted(k for k in set(old) | set(cfg) if old.get(k) != cfg.get(k))
    if changed:
        db.log_config(p["db"], cfg, source, "changed: " + ", ".join(changed), now)
    return changed


# ---------------------------------------------------------------- commands

def cmd_status(a):
    p, cfg = paths(a), need_cfg(a)
    now, ist = clock(a)
    mode = str(cfg.get("mode") or "paper")
    c, notes = guards.effective_limits(cfg)
    print("Mode: %s%s." % (mode.upper(), "" if mode == "paper" else " (kite_enabled: %s)" % cfg.get("kite_enabled")))
    print("Capital %s. Limits (percent of capital -> rupees):" % inr(c["capital_inr"]))
    for label, pct, rs in guards.limits_table(c["capital_inr"], c.get("limits_pct") or {}):
        print("  %-22s %5g%%  %s" % (label, pct, inr(rs, True)))
    for n in notes:
        print("NOTE: " + n)
    for r in db.control_blocks(a.config, mode, ist.date().isoformat(), p["db"] if db.exists(p["db"]) else None):
        print("BLOCKED: " + r)
    if not db.control_blocks(a.config, mode, ist.date().isoformat(), p["db"] if db.exists(p["db"]) else None):
        print("No pause and no kill switch: new entries are allowed by this tool (the other checks still apply).")
    return 0


def show_change(title, rows):
    print(title)
    print("  %-24s %18s %18s" % ("", "now", "after"))
    for label, old, new in rows:
        print("  %-24s %18s %18s" % (label, old, new))


def cmd_capital(a):
    p, cfg = paths(a), need_cfg(a)
    need_db(p)
    now, ist = clock(a)
    mode = str(cfg.get("mode") or "paper")
    old = float(cfg.get("capital_inr") or 0)
    new = float(a.set)
    if new <= 0:
        raise ControlError("capital must be more than zero")
    pcts = cfg.get("limits_pct") or dict(guards.DEFAULT_PCT)
    if mode == "live":
        if a.kite_available is None:
            raise ControlError("live mode: pass --kite-available with the cash Kite shows (get_margins). Capital can "
                               "never be more than that.")
        if new > float(a.kite_available) + 0.005:
            raise ControlError("live mode: %s is more than the %s Kite shows as available. Capital cannot exceed it."
                               % (inr(new, True), inr(a.kite_available, True)))
    acct = db.get_account(p["db"], mode) or {"balance": old, "baseline": old}
    delta = new - old
    show_change("Capital change (%s mode): %s -> %s (%s %s)." % (mode.upper(), inr(old), inr(new),
                                                                 "deposit" if delta >= 0 else "withdrawal",
                                                                 inr(abs(delta), True)),
                [("capital", inr(old), inr(new)), ("account balance", inr(acct["balance"], True),
                                                   inr(acct["balance"] + delta, True)),
                 ("drawdown baseline", inr(acct["baseline"], True), inr(new))] +
                [(l, "%s (%g%%)" % (inr(rs_old, True), pct), "%s (%g%%)" % (inr(round(new * pct / 100.0, 2), True), pct))
                 for (l, pct, rs_old) in guards.limits_table(old, pcts)])
    paper_trades = len(ledger_for(p, cfg, "paper")["trades"]) if mode == "paper" else 0
    if mode == "paper" and paper_trades:
        print("WARNING: you are changing capital DURING the paper-test period (%d paper trades so far). The deposit "
              "is kept apart from profit and loss, but the drawdown baseline resets and limits scale, so results "
              "before and after are not comparable. Think about whether you want this." % paper_trades)
    if not said_yes(a.reply):
        print("Nothing was changed. To apply it, the user must type yes; then run the same command with --reply yes.")
        return 2
    if not (a.reason or "").strip():
        raise ControlError("give --reason (it is recorded)")
    cfg["capital_inr"] = new if new != int(new) else int(new)
    cfg["limits_pct"] = pcts
    cfg.update(guards.derive_inr(new, pcts))
    write_json(a.config, cfg)
    db.add_capital_change(p["db"], mode, "set", old, new, a.reason, guards.derive_inr(new, pcts), now)
    db.set_account(p["db"], mode, capital=new, balance=acct["balance"] + delta, baseline=new, now=now)
    db.log_config(p["db"], cfg, "tool", "capital %s -> %s: %s" % (old, new, a.reason), now)
    print("Done. Capital is %s, the balance %s, the baseline reset to %s. Recorded in capital_changes and "
          "config_history." % (inr(new), inr(acct["balance"] + delta, True), inr(new)))
    jp = os.path.join(p["state"], "paper.json" if mode == "paper" else "live.json")
    led = read_json(jp)
    if led is not None:
        led["capital"], led["balance"] = new, round(float(led.get("balance", old)) + delta, 2)
        write_json(jp, led)
    return 0


def cmd_set(a):
    p, cfg = paths(a), need_cfg(a)
    need_db(p)
    now, ist = clock(a)
    key = a.key
    if key in NEVER_BY_TOOL:
        raise ControlError("%s is yours alone: edit trader/config.json yourself. Claude never switches the mode." % key)
    if any(w in key.lower() for w in SECRET_WORDS):
        raise ControlError("no secrets in the config or in this tool")
    pct_keys = {k for k, _ in guards.PCT_KEYS}
    if key not in pct_keys and key not in PLAIN_KEYS:
        raise ControlError("%s is not a setting this tool changes. Percent limits: %s. Others: %s." % (
            key, ", ".join(sorted(pct_keys)), ", ".join(sorted(PLAIN_KEYS))))
    try:
        val = json.loads(a.value)
    except ValueError:
        val = a.value
    old = (cfg.get("limits_pct") or {}).get(key) if key in pct_keys else cfg.get(key)
    rows = [(key, old, val)]
    if key in pct_keys:
        pcts = dict(cfg.get("limits_pct") or guards.DEFAULT_PCT)
        pcts[key] = float(val)
        cap = float(cfg.get("capital_inr") or 0)
        inr_key = dict(guards.PCT_KEYS)[key]
        rows.append((inr_key, inr(guards.derive_inr(cap, cfg.get("limits_pct") or guards.DEFAULT_PCT)[inr_key], True),
                     inr(guards.derive_inr(cap, pcts)[inr_key], True)))
    show_change("Change %s:" % key, [(l, str(o), str(n)) for l, o, n in rows])
    if key == "max_loss_per_trade_pct" and old is not None and float(val) > float(old):
        print("WARNING: this RAISES your risk per trade. Beginners should not loosen limits to make a trade pass.")
    if not said_yes(a.reply):
        print("Nothing was changed. To apply it, the user must type yes; then run the same command with --reply yes.")
        return 2
    if key in pct_keys:
        pcts = dict(cfg.get("limits_pct") or guards.DEFAULT_PCT)
        pcts[key] = float(val)
        cfg["limits_pct"] = pcts
        cfg.update(guards.derive_inr(float(cfg.get("capital_inr")), pcts))
    else:
        cfg[key] = val
    write_json(a.config, cfg)
    db.log_config(p["db"], cfg, "tool", "%s: %s -> %s" % (key, old, val), now)
    print("Done: %s is now %s. Recorded in config_history." % (key, val))
    return 0


def cmd_edits(a):
    p, cfg = paths(a), need_cfg(a)
    need_db(p)
    now, _ = clock(a)
    changed = hand_edits(cfg, p, now)
    if changed:
        print("You edited trader/config.json since the last run. Logged in config_history: " + ", ".join(changed))
        _, notes = guards.effective_limits(cfg)
        for n in notes:
            print("NOTE: " + n)
    else:
        print("No hand edits since the last recorded config.")
    return 0


def cmd_pause(a):
    p, cfg = paths(a), need_cfg(a)
    need_db(p)
    now, ist = clock(a)
    if a.until:
        try:
            datetime.date.fromisoformat(a.until)
        except ValueError:
            raise ControlError("--until wants a date like 2026-11-06")
    db.add_pause(p["db"], "all", "pause", a.until, a.reason, now)
    print("Trading is PAUSED%s in both modes. New orders are blocked; scans, news and exits still work. "
          "`control.py resume` ends it." % ((" until " + a.until) if a.until else " (no end date)"))
    return 0


def cmd_resume(a):
    p, cfg = paths(a), need_cfg(a)
    need_db(p)
    now, ist = clock(a)
    db.add_pause(p["db"], "all", "resume", None, a.reason or "resumed", now)
    print("Pause ended. %s" % ("The kill switch file trader/STOP still exists: that is separate (control.py unkill)."
                               if os.path.exists(p["stop"]) else "New entries are allowed again (other checks still apply)."))
    return 0


def cmd_kill(a):
    p, cfg = paths(a), need_cfg(a)
    now, ist = clock(a)
    os.makedirs(p["base"], exist_ok=True)
    with open(p["stop"], "w", encoding="utf-8") as fh:
        fh.write("KILL SWITCH set %s IST. Delete this file to allow new orders again.\n" % ist.strftime("%Y-%m-%d %H:%M"))
    if db.exists(p["db"]):
        db.add_pause(p["db"], "all", "kill", None, a.reason or "kill switch", now)
    print("KILL SWITCH ON: %s exists. No new order in paper or live mode until it is removed." % p["stop"])
    mode = str(cfg.get("mode") or "paper")
    for m in (["live"] if mode == "live" else []) + ["paper"]:
        led = ledger_for(p, cfg, m) if db.exists(p["db"]) or os.path.exists(os.path.join(p["state"], m + ".json")) else {"trades": []}
        opens = [t for t in led.get("trades", []) if t.get("status") in ("open", "pending_fill")]
        for t in opens:
            print("  open %s position: %s %d %s" % (m.upper(), t["trade_id"], t["qty"], t["symbol"]))
            if m == "live":
                print("    to close it (needs trader-risk, your own yes and the Allow once card): "
                      "ticket.py flatten --symbol %s --qty %d" % (t["symbol"], t["qty"]))
    return 0


def cmd_unkill(a):
    p = paths(a)
    if not os.path.exists(p["stop"]):
        print("There is no kill switch file.")
        return 0
    if not said_yes(a.reply):
        print("The kill switch stays on. Only the user decides to remove it: after their typed yes, run with --reply yes.")
        return 2
    os.remove(p["stop"])
    if db.exists(p["db"]):
        db.add_pause(p["db"], "all", "resume", None, "kill switch removed", clock(a)[0])
    print("Kill switch removed. Entries are allowed again (every other check still applies).")
    return 0


def cmd_morning(a):
    p, cfg = paths(a), need_cfg(a)
    now, ist = clock(a)
    today = ist.date().isoformat()
    mode = str(cfg.get("mode") or "paper")
    c, notes = guards.effective_limits(cfg)
    lines = []
    ok, why = trading_day(ist.date())
    lines.append("MORNING CHECK, %s %s IST" % (ist.strftime("%a %Y-%m-%d"), ist.strftime("%H:%M")))
    lines.append("Market: %s." % ("open day (09:15 to 15:30)" if ok else "CLOSED today: " + why))
    lines.append("Mode: %s%s." % (mode.upper(), "" if mode == "paper" else ", kite_enabled %s" % cfg.get("kite_enabled")))
    if db.exists(p["db"]):
        edits = hand_edits(cfg, p, now)
        if edits:
            lines.append("CONFIG EDITED BY HAND since last time (logged): " + ", ".join(edits))
        for m in ("paper", "live"):
            acct = db.get_account(p["db"], m)
            if acct is None:
                continue
            led = ledger_for(p, cfg, m)
            view = week_view(led, today)
            lines.append("%s account: balance %s (capital %s, baseline %s). Today %s, this week %s." % (
                m.upper(), inr(acct["balance"], True), inr(acct["capital"]), inr(acct["baseline"]),
                inr(view["day"], True), inr(view["week"], True)))
            if m == mode:
                used_d = max(0.0, -view["day"]) + view["open_risk"]
                used_w = max(0.0, -view["week"]) + view["open_risk"]
                lines.append("Limits used: today %s of %s, week %s of %s, trades today %d of %s." % (
                    inr(used_d, True), inr(c["max_daily_loss_inr"]), inr(used_w, True), inr(c["max_weekly_loss_inr"]),
                    view["entries"], cfg.get("max_trades_per_day")))
                down = acct["baseline"] - acct["balance"]
                if down >= c["max_drawdown_pause_inr"] - 0.005:
                    lines.append("DRAWDOWN PAUSE: %s below the baseline (limit %s). Stop and review." % (
                        inr(down, True), inr(c["max_drawdown_pause_inr"])))
        for r in db.control_blocks(a.config, mode, today, p["db"]):
            lines.append("BLOCKED: " + r)
        lines.append("Database: %s" % ("intact" if db.check(p["db"])[0] else "PROBLEMS: run db.py check"))
        folder = os.path.join(p["base"], "backups")
        lines.append("Last backup: %s." % (sorted(os.listdir(folder))[-1] if os.path.isdir(folder) and os.listdir(folder)
                                           else "none yet (db.py backup)"))
    else:
        lines.append("No database yet (%s): run size.py init, or db.py init and db.py migrate." % p["db"])
        for r in db.control_blocks(a.config, mode, today, None):
            lines.append("BLOCKED: " + r)
    prot = guards.load_protection(p["state"])
    wide, _ = guards.events_today(today, "", c, prot)
    lines.append("Events today: %s." % ("; ".join(wide) if wide else "none known (RBI policy days come from your events.json)"))
    results = prot.get("results_today.json")
    if isinstance(results, dict) and results.get("date") == today and results.get("symbols"):
        lines.append("RESULTS TODAY (half size): " + ", ".join(sorted(results["symbols"])))
    m = prot.get("market.json")
    if isinstance(m, dict) and m.get("date") == today:
        half, stop = guards.volatility(prot, today, c)
        lines.append("India VIX %s, NIFTY gap %s%s." % (m.get("vix"), m.get("nifty_gap_pct"),
                                                       ("  " + "; ".join(stop + half)) if stop or half else ""))
    gaps = guards.data_gaps(prot, today)
    if gaps:
        lines.append("PROTECTION DATA MISSING (%s): %s" % ("live entries are blocked" if guards.strict(c, mode)
                                                          else "paper warns only", "; ".join(gaps)))
    wl = read_json(os.path.join(p["base"], "watchlist.json"))
    if wl:
        lines.append("Scan: %s, %d hit%s (%s)." % (wl.get("mode"), len(wl.get("hits", [])),
                                                   "" if len(wl.get("hits", [])) == 1 else "s", wl.get("as_of")))
    nw = read_json(os.path.join(p["state"], "news.json"))
    lines.append("News: last checked %s." % ((nw or {}).get("last_check") or "never"))
    for n in notes:
        lines.append("NOTE: " + n)
    lines.append("Reminder: turn on two-factor login (TOTP) at your broker, and never share broker keys, passwords or "
                 "codes with anyone, including Claude.")
    print("\n".join(lines))
    return 0


def week_view(led, today):
    d = datetime.date.fromisoformat(today)
    mon = (d - datetime.timedelta(days=d.weekday())).isoformat()
    closed = [t for t in led.get("trades", []) if t.get("status") == "closed"]
    day = sum(float(t.get("net_pnl") or 0) for t in closed if str(t.get("exit_date") or t.get("date")) == today)
    week = sum(float(t.get("net_pnl") or 0) for t in closed if mon <= str(t.get("exit_date") or t.get("date")) <= today)
    open_risk = sum(float(t.get("max_loss") or 0) for t in led.get("trades", []) if t.get("status") in ("open", "pending_fill"))
    entries = sum(1 for t in led.get("trades", []) if str(t.get("date")) == today and t.get("status") != "failed")
    return {"day": round(day, 2), "week": round(week, 2), "open_risk": open_risk, "entries": entries}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Capital, limits, pause, kill switch, morning check (trader-control).")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--db", help="default: trader/trader.db")
        p.add_argument("--now", help="pretend it is this India time, e.g. \"2026-10-30 08:45\"")

    for name in ("morning", "status", "edits", "resume", "kill"):
        p = sub.add_parser(name)
        common(p)
        if name in ("resume", "kill"):
            p.add_argument("--reason")
    p = sub.add_parser("capital")
    common(p)
    p.add_argument("--set", type=float, required=True)
    p.add_argument("--reason")
    p.add_argument("--reply", help="the user's typed yes, word for word (without it nothing is written)")
    p.add_argument("--kite-available", type=float, help="live mode: cash Kite shows (get_margins)")
    p = sub.add_parser("set")
    common(p)
    p.add_argument("key")
    p.add_argument("value")
    p.add_argument("--reply")
    p = sub.add_parser("pause")
    common(p)
    p.add_argument("--until")
    p.add_argument("--reason")
    p = sub.add_parser("unkill")
    common(p)
    p.add_argument("--reply")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    fn = {"morning": cmd_morning, "status": cmd_status, "edits": cmd_edits, "resume": cmd_resume, "kill": cmd_kill,
          "capital": cmd_capital, "set": cmd_set, "pause": cmd_pause, "unkill": cmd_unkill}[a.cmd]
    try:
        return fn(a)
    except (ControlError, db.DbError) as e:
        print("control.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):      # Windows consoles default to a code page that cannot print the rupee sign
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
