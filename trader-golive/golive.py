#!/usr/bin/env python3
"""Go-live readiness for the NSE paper-trading trial. Standard library only, no network.

It reads the paper record and tells you the truth about it. It NEVER decides
for you, never edits trader/config.json and never switches a mode.

    python3 golive.py check                # the honest report on the paper record
    python3 golive.py check --json
    python3 golive.py steps                # the exact steps YOU take to go live, in order

check reports: how many trades (a warning under 40), net P&L after charges, win
rate, average R, expectancy per trade, the deepest drawdown, the longest losing
streak, how often the limits were hit or a rule was broken, how much the charges
took, how your skipped signals compare, and whether the result can be told apart
from luck at this sample size (a bootstrap of the average net result per trade,
seed 19, 10,000 resamples, so the same record gives the same answer).

The luck test is the part to read twice. With a few dozen trades, a result that
looks good or bad is usually still inside the noise.
"""

import argparse
import datetime
import json
import math
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db  # noqa: E402  (a copy of trader-db/db.py; a test keeps the copies identical)

DEFAULTS = {"capital_inr": 100000, "max_loss_per_trade_inr": 500, "max_daily_loss_inr": 1500,
            "max_weekly_loss_inr": 4000, "max_drawdown_pause_inr": 8000, "max_trades_per_day": 3,
            "min_trades_for_review": 40, "paper_period_weeks": 4}
SEED = 19
RESAMPLES = 10000

# the VERIFY.md items to re-check before going live (kept here so the skill works on its own)
RECHECK = [
    "Does the hosted Kite MCP server (mcp.kite.trade) expose place_order / modify_order / cancel_order? (assumed: no)",
    "The exact argument names of the Kite MCP place_order and cancel_order tools.",
    "Is variety=co (cover order) accepted for NSE equity MIS? (assumed: no)",
    "Does GTT (single or two-leg) accept product=MIS? (assumed: no)",
    "market_protection: required for MARKET and SL-M orders, and its allowed values (-1 auto, or 1 to 100).",
    "Is SL-M accepted for an NSE equity MIS stop-loss sell through the API? If not, set \"stop_order_type\": \"SL\".",
    "SEBI's April 2026 API rules for your account: static IP registration and anything else Zerodha requires.",
    "When the access token expires (forum: about 07:30 IST) and how you log in each day.",
    "Zerodha's MIS square-off times (15:12 closing-auction stocks, 15:25 others) on zerodha.com/marketintel/bulletin.",
    "Every rate in the charges table in trader/config.json, against zerodha.com/charges today.",
    "Tick size per symbol from Kite's instrument list, against the NSE band table.",
    "Whether Yahoo's .NS quotes match Kite's prices closely enough to size trades on.",
]

RECHECK_DHAN = [
    "The real tool list of Dhan's MCP connector (reported address https://mcp.dhan.co/mcp), and which tools place, modify, "
    "cancel or exit orders, including super orders and forever orders. Put the real names in \"dhan_tools\" in your config.",
    "Whether Dhan's order APIs need a registered static IP on your route (the API pages say they do) and whether it is registered.",
    "The access login: how long it lasts and how you connect each morning (the API token is documented as 24 hours).",
    "That a super order (entry, stop-loss and target legs) works for NSE equity INTRADAY, and what cancelling it does to its legs.",
    "That an entry as a LIMIT super order fills as the docs say; Dhan converts API market orders to limit orders with price protection.",
    "The numeric securityId for each symbol you trade, from Dhan's instrument list (python3 dhan.py securities --csv FILE).",
    "The field names in get_orders, get_trades, get_positions, the funds result and the last-price result (VERIFY.md section 15).",
    "Dhan's intraday auto square-off time (its own pages disagree: 3:10, 3:18 and 3:20 PM) against your flat_by.",
    "Every rate in the charges table in trader/config.json, against dhan.co/pricing today.",
    "Tick size per symbol against the NSE band table.",
    "Whether Yahoo's .NS quotes match Dhan's prices closely enough to size trades on.",
]
DHAN_TOOLS_APPROVAL = ["the place-order tool", "the place-super-order tool", "the modify tools", "the cancel tools (order and "
                       "super order)", "any forever-order (GTT) tool", "any exit-all or square-off tool"]
KITE_TOOLS_APPROVAL = ["place_order", "modify_order", "cancel_order", "place_gtt_order", "modify_gtt_order",
                       "delete_gtt_order"]
KITE_TOOLS_READONLY = ["get_profile", "get_orders", "get_trades", "get_order_history", "get_order_trades",
                       "get_positions", "get_holdings", "get_margins", "get_quotes", "get_ltp", "get_ohlc",
                       "get_historical_data", "search_instruments", "get_gtts"]


class GoLiveError(Exception):
    pass


def inr(x, paise=None):
    """Rupees with Indian digit grouping: 100000 -> "₹1,00,000"."""
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


def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise GoLiveError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise GoLiveError("%s is not valid JSON: %s" % (path, e))


# ---------------------------------------------------------------- the numbers

def r2(x):
    return round(float(x) + 1e-9, 2)


def mean(xs):
    return sum(xs) / float(len(xs))


def wilson(wins, n, z=1.96):
    """95 % Wilson interval for a win rate, as fractions."""
    if n == 0:
        return None
    p = wins / float(n)
    d = 1 + z * z / n
    c = (p + z * z / (2.0 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4.0 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def bootstrap(values, seed=SEED, resamples=RESAMPLES):
    """(low, high, share of resamples with a mean at or below zero) for the mean of `values`."""
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(resamples):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / float(n))
    means.sort()
    return means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1], \
        sum(1 for m in means if m <= 0) / float(resamples)


def closed_trades(led):
    return sorted([t for t in led.get("trades", []) if t.get("status") == "closed"],
                  key=lambda t: (str(t.get("exit_date") or t["date"]), t["trade_id"]))


def monday(day):
    d = datetime.date.fromisoformat(day)
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def analyse(led, cfg):
    c = dict(DEFAULTS)
    c.update(cfg or {})
    capital = float(led.get("capital") or c["capital_inr"])
    trades = closed_trades(led)
    n = len(trades)
    out = {"capital": capital, "n": n, "min_trades": c["min_trades_for_review"],
           "open": sum(1 for t in led.get("trades", []) if t.get("status") in ("open", "pending_fill")),
           "skipped": len(led.get("skipped", [])), "rebuilt": bool(led.get("rebuilt_from_notion")),
           "period_weeks": c["paper_period_weeks"]}
    if n == 0:
        return out
    nets = [t["net_pnl"] for t in trades]
    rs = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] < 0]
    gross = sum(t["gross_pnl"] for t in trades)
    charges = sum(t["charges"] for t in trades)
    days = sorted({str(t.get("exit_date") or t["date"]) for t in trades})
    # drawdown from the balance curve, starting at capital
    bal, peak, dd, dd_from_start = capital, capital, 0.0, 0.0
    for t in trades:
        bal += t["net_pnl"]
        peak = max(peak, bal)
        dd = max(dd, peak - bal)
        dd_from_start = max(dd_from_start, capital - bal)
    streak = best = 0
    for t in trades:
        streak = streak + 1 if t["net_pnl"] < 0 else 0
        best = max(best, streak)
    # limits and rules, by day
    by_day = {}
    for t in trades:
        by_day.setdefault(str(t.get("exit_date") or t["date"]), []).append(t)
    daily_hits = sum(1 for d, ts in by_day.items()
                     if -sum(x["net_pnl"] for x in ts if x["net_pnl"] < 0) >= c["max_daily_loss_inr"] - 0.005)
    cap_trades = sum(1 for d, ts in by_day.items() if len(ts) >= c["max_trades_per_day"])
    weekly = {}
    for d, ts in by_day.items():
        weekly.setdefault(monday(d), 0.0)
        weekly[monday(d)] += -sum(x["net_pnl"] for x in ts if x["net_pnl"] < 0)
    weekly_hits = sum(1 for v in weekly.values() if v >= c["max_weekly_loss_inr"] - 0.005)
    rules = [t for t in trades if t["net_pnl"] < -c["max_loss_per_trade_inr"] - 0.005]
    pause = dd_from_start >= c["max_drawdown_pause_inr"] - 0.005
    avg_win = mean([t["net_pnl"] for t in wins]) if wins else None
    avg_loss = mean([t["net_pnl"] for t in losses]) if losses else None
    be = None
    if avg_win and avg_loss:
        be = -avg_loss / (avg_win - avg_loss)          # win rate that breaks even at these average sizes
    out.update({
        "first_day": days[0], "last_day": days[-1], "days": len(days),
        "net": r2(sum(nets)), "gross": r2(gross), "charges": r2(charges),
        "win_rate": 100.0 * len(wins) / n, "wins": len(wins), "losses": len(losses),
        "avg_r": round(mean(rs), 2) if rs else None, "expectancy": r2(mean(nets)),
        "avg_win": r2(avg_win) if avg_win is not None else None,
        "avg_loss": r2(avg_loss) if avg_loss is not None else None,
        "breakeven_win_rate": round(100.0 * be, 1) if be is not None else None,
        "max_drawdown": r2(dd), "max_drawdown_pct": round(100.0 * dd / peak, 2) if peak else None,
        "below_start_max": r2(dd_from_start), "longest_losing_streak": best,
        "daily_limit_days": daily_hits, "weekly_limit_weeks": weekly_hits, "trade_cap_days": cap_trades,
        "pause_reached": pause, "rules_broken": len(rules),
        "rules_detail": ["%s lost %s" % (t["trade_id"], inr(-t["net_pnl"], True)) for t in rules][:10],
        "balance": r2(bal), "wilson": wilson(len(wins), n)})
    if n >= 2:
        lo, hi, p_le0 = bootstrap(nets)
        out.update(ci_low=r2(lo), ci_high=r2(hi), share_le_zero=round(100.0 * p_le0, 1))
        out["luck"] = ("inside" if lo <= 0 <= hi else "above" if lo > 0 else "below")
    sk = [s for s in led.get("skipped", []) if s.get("hypothetical_net_pnl") is not None]
    out["skipped_replayed"] = len(sk)
    out["skipped_net"] = r2(sum(s["hypothetical_net_pnl"] for s in sk)) if sk else None
    return out


def report_lines(a):
    L = []
    n = a["n"]
    L.append("Go-live readiness: the paper record, read plainly. This does not decide anything for you.")
    L.append("")
    if n == 0:
        L.append("There are no closed paper trades yet. Nothing to report.")
        return L
    short = n < a["min_trades"]
    L.append("Trades: %d closed over %d trading day%s (%s to %s)%s." % (
        n, a["days"], "" if a["days"] == 1 else "s", a["first_day"], a["last_day"],
        ("; %d still open" % a["open"]) if a["open"] else ""))
    if short:
        L.append("WARNING: %d trades is under the %d you set as the minimum for a review. Everything below is "
                 "even less reliable than it looks." % (n, a["min_trades"]))
    L.append("Net result after charges: %s (balance %s against %s you started with)." % (
        inr(a["net"], True), inr(a["balance"], True), inr(a["capital"])))
    L.append("Charges: %s paid. Gross result before charges was %s, so charges %s." % (
        inr(a["charges"], True), inr(a["gross"], True),
        ("took %.0f %% of the gross profit" % (100.0 * a["charges"] / a["gross"])) if a["gross"] > 0
        else "added to a losing result"))
    L.append("Win rate: %.0f %% (%d wins, %d losses), 95 %% range %.0f to %.0f %%. Average R: %s." % (
        a["win_rate"], a["wins"], a["losses"], 100 * a["wilson"][0], 100 * a["wilson"][1], a["avg_r"]))
    if a["avg_win"] is not None and a["avg_loss"] is not None:
        L.append("Average win %s, average loss %s: at those sizes you need to win %.0f %% of trades to break even."
                 % (inr(a["avg_win"], True), inr(-a["avg_loss"], True), a["breakeven_win_rate"]))
    L.append("Expectancy: %s per trade after charges." % inr(a["expectancy"], True))
    L.append("Deepest drawdown: %s from a peak (%.1f %%); the most below your start was %s%s." % (
        inr(a["max_drawdown"], True), a["max_drawdown_pct"] or 0, inr(a["below_start_max"], True),
        "; the ₹%s pause level was reached" % format(DEFAULTS["max_drawdown_pause_inr"], ",") if a["pause_reached"]
        else ""))
    L.append("Longest losing streak: %d trade%s." % (a["longest_losing_streak"],
                                                     "" if a["longest_losing_streak"] == 1 else "s"))
    L.append("Limits hit: daily loss limit on %d day%s, weekly limit in %d week%s, 3-trade cap on %d day%s. "
             "Rules broken (a loss over the per-trade limit): %d%s." % (
                 a["daily_limit_days"], "" if a["daily_limit_days"] == 1 else "s", a["weekly_limit_weeks"],
                 "" if a["weekly_limit_weeks"] == 1 else "s", a["trade_cap_days"], "" if a["trade_cap_days"] == 1
                 else "s", a["rules_broken"], (" (" + "; ".join(a["rules_detail"]) + ")") if a["rules_broken"]
                 else ""))
    if a["skipped_replayed"]:
        L.append("Signals you skipped: %d replayed, they would have netted %s after charges (against %s from the "
                 "trades you took)." % (a["skipped_replayed"], inr(a["skipped_net"], True), inr(a["net"], True)))
    L.append("")
    L.append("Can this be told apart from luck?")
    if "luck" not in a:
        L.append("  Not with %d trade. There is nothing to measure." % n)
    else:
        L.append("  The average net result per trade is %s. Resampling your own %d trades 10,000 times (seed %d), "
                 "the true average could plausibly be anywhere from %s to %s." % (
                     inr(a["expectancy"], True), n, SEED, inr(a["ci_low"], True), inr(a["ci_high"], True)))
        if a["luck"] == "inside":
            L.append("  That range includes zero. At this sample size the result CANNOT be told apart from luck: "
                     "a coin-flip system could have produced it.")
        elif a["luck"] == "above":
            L.append("  The whole range is above zero. Pure luck is unlikely to explain this sample, but that only "
                     "covers these %d trades on paper." % n)
        else:
            L.append("  The whole range is below zero. On paper, before real-world slippage, this lost money.")
        L.append("  About %.0f %% of the resamples had an average at or below zero." % a["share_le_zero"])
    L.append("")
    L.append("What this report cannot see:")
    L.append("  - Paper fills are kinder than real ones: real stops slip, market orders move the price, and you")
    L.append("    will take longer than a minute to type yes.")
    L.append("  - A few weeks is one market mood. A different month can behave differently.")
    L.append("  - It counts the trades you took. Trades you skipped, and the days you did not trade, are in Notion.")
    if a["rebuilt"]:
        L.append("  - This record was rebuilt from Notion: open trades came back without their internal details.")
    L.append("")
    L.append("Most intraday traders lose money: SEBI's study of FY2022-23, as reported in the press (the sebi.gov.in")
    L.append("page itself was not checked), found about 7 in 10 individual intraday traders in the cash segment lost")
    L.append("money. Not financial advice. Whether to go on is your decision; `golive.py steps` lists what going")
    L.append("live involves.")
    return L


# ---------------------------------------------------------------- the steps

def steps(half_risk=250, broker="dhan"):
    if broker == "dhan":
        return _steps_dhan(half_risk)
    if broker == "fyers":
        return _steps_fyers(half_risk)
    return _steps_kite(half_risk)


RECHECK_FYERS = [
    "Whether FYERS's official MCP server places orders for your account today, and its real tool list. Put the real names in "
    "\"fyers_tools\" in your config.",
    "Whether FYERS's order APIs need a registered static IP on your route, and that it is registered.",
    "How long the login lasts and how you connect each morning.",
    "That a plain INTRADAY market order and an SL-M stop order are accepted for NSE equity (else set stop_order_type to SL).",
    "The real field names and status codes in the orders, trades, positions, funds and quotes results.",
    "That the returned order tag carries your orderTag (the duplicate guard reads it).",
    "FYERS's intraday auto square-off time against your flat_by.",
    "Every rate in the charges table in trader/config.json, against FYERS's pricing page.",
    "Tick size per symbol against the NSE band table.",
    "Whether Yahoo's .NS quotes match FYERS's prices closely enough to size trades on.",
]


def _steps_fyers(half_risk):
    return [
        ("Decide", [
            "Read the report above and your records yourself. Go live only if YOU are satisfied. If the result sat inside "
            "the noise, that is a reason to keep paper trading, not to stop reading the report.",
            "Decide the most you can lose, in money you can afford to lose, before you connect anything."]),
        ("Connect FYERS (only you do this)", [
            "FYERS's docs list an official MCP server (reported address https://mcp.fyers.in/mcp); an older FYERS post says "
            "it could not place orders. Ask FYERS or read its docs, then add it to Claude yourself and approve its login yourself.",
            "Never paste a token, password, app secret or TOTP code into a chat, and never put one in this folder. Do not use "
            "third-party FYERS servers through this pack.",
            "Do not place real orders from TradingView's FYERS panel while you use this pack: those orders skip the risk "
            "check, your typed yes and the database."]),
        ("Set every order tool to \"Needs approval\"", [
            "In Claude (Customize > Connectors > the FYERS connector > tool permissions) set the place, modify, cancel, basket "
            "and scheduled-order tools to Needs approval, never Always allow. Read-only tools may stay on Always allow."]),
        ("Re-verify what could not be verified", ["Check each of these against the live docs and your account:"] +
         ["[ ] " + x for x in RECHECK_FYERS]),
        ("Switch it on yourself, at half the paper risk", [
            "Edit trader/config.json yourself (the tools never do): set \"mode\": \"live\", \"broker\": \"fyers\", "
            "\"fyers_enabled\": true and \"max_loss_per_trade_inr\": %d for the first 2 weeks live, half the paper risk."
            % half_risk,
            "At half the risk the shares halve, so charges are a bigger share of each small target: expect more trades refused."]),
        ("Every live day", [
            "Connect FYERS yourself, save the profile, funds and positions results, then: fyers.py check --profile F "
            "--margins F --positions F --tools-need-approval --static-ip-confirmed.",
            "A live trade is three orders, each with its own trader-risk check, its own typed yes and Claude's own Allow once "
            "card: the entry, the stop-loss after it fills (fyers.py stop-ticket), and the exit (cancel the stop first). "
            "Never \"yes to all\"; never from a scheduled task.",
            "Sync after the fills: fyers.py sync; after 15:30 run fyers.py reconcile. Act on every WARNING and DISCREPANCY line at once."]),
        ("Keep the record", [
            "Every live trade is in the database with Mode = live: run report.py weekly --mode live each Friday.",
            "Compare the live results with the paper record. Real slippage will show up there."]),
        ("Only then think about full size", [
            "After 2 weeks at %s per trade, look at the live record the same way as this report. Whether to go "
            "back to the full per-trade risk is your decision, made by editing your own config." % inr(half_risk)])]


def _steps_dhan(half_risk):
    return [
        ("Decide", [
            "Read the report above and the Notion record yourself. Go live only if YOU are satisfied. If the "
            "result sat inside the noise, that is a reason to keep paper trading, not to stop reading the report.",
            "Decide the most you can lose, in money you can afford to lose, before you connect anything."]),
        ("Connect Dhan (only you do this)", [
            "Dhan describes a first-party MCP server (reported address https://mcp.dhan.co/mcp) that can place, modify "
            "and cancel orders, including super orders. Add it to Claude yourself and approve its consent screen yourself.",
            "Never paste a token, password, client secret or TOTP code into a chat, and never put one in this folder. "
            "If a route needs credentials, they live in environment variables or in a config you edit yourself.",
            "Read on Dhan's own site what API and MCP access needs: any subscription, and the static-IP rule for order "
            "APIs (and whether your route needs it)."]),
        ("Set every order tool to \"Needs approval\"", [
            "In Claude (Customize > Connectors > the Dhan connector > tool permissions) set each of these to Needs "
            "approval, never Always allow: " + ", ".join(DHAN_TOOLS_APPROVAL) + ".",
            "Read-only tools (profile, funds, orders, trades, positions, quotes) may stay on Always allow."]),
        ("Map symbols to Dhan's securityIds", [
            "Download Dhan's instrument list yourself, then: python3 trader-broker/dhan.py securities --csv FILE. "
            "An order for a symbol with no id is refused."]),
        ("Re-verify what could not be verified", ["Check each of these against the live docs and your account:"] +
         ["[ ] " + x for x in RECHECK_DHAN]),
        ("Switch it on yourself, at half the paper risk", [
            "Edit trader/config.json yourself (the tools never do): set \"mode\": \"live\", \"broker\": \"dhan\", "
            "\"dhan_enabled\": true and \"max_loss_per_trade_inr\": %d for the first 2 weeks live, half the paper risk."
            % half_risk,
            "At half the risk the shares halve, so charges are a bigger share of each small target: expect more "
            "trades refused by the 25 % charges rule. That is the rule working.",
            "Leave the daily, weekly and drawdown limits as they are unless you decide otherwise."]),
        ("Every live day", [
            "Connect Dhan yourself, save the profile, funds and positions results, then: dhan.py check --profile F "
            "--margins F --positions F --tools-need-approval --static-ip-confirmed.",
            "An entry is ONE super order (entry, stop-loss and target together): one trader-risk check, one typed yes, "
            "one Allow once card. Entries are limit orders (size.py --limit). Leaving early is two steps, each with its "
            "own yes: cancel the super order, then sell. Never \"yes to all\"; never from a scheduled task.",
            "Sync after the fills: dhan.py sync with the saved orders, trades and positions results; after 15:30 run "
            "dhan.py reconcile. Act on every WARNING and DISCREPANCY line at once."]),
        ("Keep the record", [
            "Keep logging every live trade in Notion with Mode = live: journal.py queue --mode live, then sync.",
            "Compare the live results with the paper record each Friday. Real slippage will show up there."]),
        ("Only then think about full size", [
            "After 2 weeks at %s per trade, look at the live record the same way as this report. Whether to go "
            "back to the full per-trade risk is your decision, made by editing your own config." % inr(half_risk)])]


def _steps_kite(half_risk):
    return [
        ("Decide", [
            "Read the report above and the Notion record yourself. Go live only if YOU are satisfied. If the "
            "result sat inside the noise, that is a reason to keep paper trading, not to stop reading the report.",
            "Decide the most you can lose, in money you can afford to lose, before you connect anything."]),
        ("Connect Kite (only you do this)", [
            "The hosted Kite MCP server (https://mcp.kite.trade/mcp) says it leaves out \"potentially destructive "
            "trading operations\", so it probably cannot place orders. Check its tool list in Claude. If place_order "
            "is missing, you need Zerodha's open-source kite-mcp-server (github.com/zerodha/kite-mcp-server) running "
            "on your own computer with your own Kite Connect API key and secret.",
            "Put the key and secret ONLY in environment variables or in the server's own config file that you edit "
            "yourself. Never paste them into a chat, never put them in this folder.",
            "Check Zerodha's current requirements for API orders (SEBI's April 2026 rules reportedly include a "
            "registered static IP) and the Kite Connect subscription cost on kite.trade."]),
        ("Set every order tool to \"Needs approval\"", [
            "In Claude (Customize > Connectors > the Kite server > tool permissions) set each of these to Needs "
            "approval, never Always allow: " + ", ".join(KITE_TOOLS_APPROVAL) + ".",
            "Read-only tools may stay on Always allow: " + ", ".join(KITE_TOOLS_READONLY) + ".",
            "If your Claude lets you block tools, consider blocking the GTT tools (place_gtt_order, "
            "modify_gtt_order, delete_gtt_order): this pack does not use them."]),
        ("Re-verify what could not be verified", ["Check each of these against the live docs and your account:"] +
         ["[ ] " + x for x in RECHECK]),
        ("Switch it on yourself, at half the paper risk", [
            "Edit trader/config.json yourself (the tools never do): set \"mode\": \"live\", \"kite_enabled\": true "
            "and \"max_loss_per_trade_inr\": %d for the first 2 weeks live, half the paper risk." % half_risk,
            "At half the risk the shares halve, so charges are a bigger share of each small target: expect more "
            "trades refused by the 25 % charges rule. That is the rule working.",
            "Leave the daily, weekly and drawdown limits as they are unless you decide otherwise; they are yours "
            "to change, in your config."]),
        ("Every live day", [
            "Log in to Kite yourself (a token lasts until the next morning), call get_profile, then "
            "kite.py check --profile FILE.",
            "Each live trade is three orders, each with its own trader-risk check, its own typed yes and Claude's "
            "own Allow once card: the entry, the stop-loss after it fills, and the exit (cancel the stop first). "
            "Never \"yes to all\"; never from a scheduled task.",
            "Sync after the fills: kite.py sync with the saved get_orders / get_trades / get_positions results. "
            "Act on every WARNING line at once (no stop, rejected stop, position mismatch)."]),
        ("Keep the record", [
            "Keep logging every live trade in Notion with Mode = live: journal.py queue --mode live, then sync.",
            "Compare the live results with the paper record each Friday. Real slippage will show up there."]),
        ("Only then think about full size", [
            "After 2 weeks at %s per trade, look at the live record the same way as this report. Whether to go "
            "back to the full per-trade risk is your decision, made by editing your own config." % inr(half_risk)])]


def steps_lines(half_risk=250, broker="dhan"):
    L = ["Going live: the steps YOU take, in order. Nothing here is done for you, and nothing switches itself on.", ""]
    for i, (title, items) in enumerate(steps(half_risk, broker), 1):
        L.append("%d. %s" % (i, title))
        for it in items:
            L.append("   - " + it if not it.startswith("[ ]") else "   " + it)
        L.append("")
    L.append("Config you will edit yourself (shown, not written):")
    L.append('   "mode": "live", %s, "max_loss_per_trade_inr": %d' % (
        '"broker": "dhan", "dhan_enabled": true' if broker == "dhan" else
        '"broker": "fyers", "fyers_enabled": true' if broker == "fyers" else '"kite_enabled": true', half_risk))
    L.append("Not financial advice. Real money can be lost.")
    return L


# ---------------------------------------------------------------- commands

def cmd_check(a):
    cfg = read_json(a.config, "config", required=False) or {}
    sqlite_path = a.sqlite or db.default_path(a.config)
    if db.exists(sqlite_path):
        led = db.load_ledger(sqlite_path, a.mode, cfg)            # the database is the record
        if not led["trades"]:
            raise GoLiveError("the database holds no %s trades yet" % a.mode)
    else:
        led = read_json(a.ledger or "trader/state/%s.json" % a.mode, "%s ledger (the paper broker writes it)" % a.mode)
    res = analyse(led, cfg)
    if a.json:
        res = dict(res)
        if res.get("wilson"):
            res["wilson"] = [round(x, 4) for x in res["wilson"]]
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print("\n".join(report_lines(res)))
    return 0


def cmd_steps(a):
    if a.json:
        print(json.dumps([{"title": t, "items": i} for t, i in steps(a.half_risk, a.broker)], indent=2, ensure_ascii=False))
    else:
        print("\n".join(steps_lines(a.half_risk, a.broker)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Go-live readiness (trader-golive). Never decides, never switches.")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("check", help="the honest report on the paper record")
    p.add_argument("--ledger", help="default trader/state/<mode>.json (used when there is no database)")
    p.add_argument("--sqlite", help="the database (default trader/trader.db, next to the config)")
    p.add_argument("--mode", choices=["paper", "live"], default="paper",
                   help="paper and live are always reported separately")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("steps", help="the steps you take yourself to go live")
    p.add_argument("--half-risk", type=int, default=250)
    p.add_argument("--broker", choices=["dhan", "fyers", "kite"], default="dhan")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"check": cmd_check, "steps": cmd_steps}[a.cmd](a)
    except GoLiveError as e:
        print("golive.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
