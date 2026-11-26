#!/usr/bin/env python3
"""The local database for the India day-trader pack. Standard library only (sqlite3), no network.

trader/trader.db is the local source of truth. This file is BOTH the module the
other skills import (a byte-identical copy lives in each skill that needs it;
tests/test_db.py checks the copies) AND a command line tool:

    python3 db.py init                        # create trader/trader.db (safe to run again)
    python3 db.py migrate                     # import the old JSON files; prints counts; JSON files are kept
    python3 db.py status                      # row counts, chain heads, last backup
    python3 db.py check                       # integrity + foreign keys + the hash chains + the triggers
    python3 db.py backup                      # dated copy in trader/backups/, keeps the last 30
    python3 db.py restore trader/backups/trader-2026-10-30-153000.db   # copy a backup back (asks nothing: you chose it)
    python3 db.py export                      # CSV per table into trader/exports/
    python3 db.py export --tax --fy 2026-27   # live P&L for a financial year. NOT TAX ADVICE.

How it is built (and why):
  - Money is stored as integer paise in columns (price_paise, net_paise ...) so sums are exact. Each
    row also keeps the full record as JSON (`doc`), which is what the other tools load, so no
    tool had to change how it thinks about a trade.
  - orders, order_events, approvals, fills, trades, skipped_signals, risk_verdicts, trade_notes,
    capital_changes, pauses, config_history and discrepancies are APPEND-ONLY: triggers refuse
    UPDATE and DELETE. A trade that goes from open to closed is a NEW row (version 2) that points to
    the one it replaces. The 'current' state is the newest version.
  - Every append-only row stores row_hash = sha256(previous row's hash + this row), so
    `db check` finds an edited or removed row in the middle of a chain. A truncated tail is not
    detectable from inside the file: that is what the dated backups and your exports are for.
  - positions, account, daily_summary, weekly_reports, alerts, news_items and scans
    are working tables that are meant to change (they can all be rebuilt from the append-only ones,
    except the logs).
  - Paper and live live in the same tables, told apart by the `mode` column. They are never mixed in a sum.
  - Every write is one transaction: all of it lands or none of it.
Nothing here talks to a broker or to the network.
"""

import argparse
import csv
import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import sys

SCHEMA_VERSION = 1
GENESIS = "GENESIS"
DEFAULT_DB = "trader/trader.db"
KEEP_BACKUPS = 30
APPEND_ONLY = ["capital_changes", "config_history", "orders", "order_events", "approvals", "fills", "trades",
               "skipped_signals", "risk_verdicts", "trade_notes", "pauses", "discrepancies"]


class DbError(Exception):
    pass


def _chain_cols(extra):
    return ("seq INTEGER PRIMARY KEY AUTOINCREMENT, prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL, "
            "recorded_at TEXT NOT NULL, " + extra)


SCHEMA = [
    "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
    # working tables (meant to change)
    "CREATE TABLE IF NOT EXISTS account (mode TEXT PRIMARY KEY, capital_paise INTEGER NOT NULL, "
    "balance_paise INTEGER NOT NULL, baseline_paise INTEGER NOT NULL, updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS positions (mode TEXT NOT NULL, symbol TEXT NOT NULL, qty INTEGER NOT NULL, "
    "avg_price_paise INTEGER, trade_id TEXT, updated_at TEXT, PRIMARY KEY (mode, symbol))",
    "CREATE TABLE IF NOT EXISTS daily_summary (date TEXT NOT NULL, mode TEXT NOT NULL, doc TEXT NOT NULL, "
    "PRIMARY KEY (date, mode))",
    "CREATE TABLE IF NOT EXISTS weekly_reports (week TEXT NOT NULL, mode TEXT NOT NULL, doc TEXT NOT NULL, "
    "PRIMARY KEY (week, mode))",
    "CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, rule TEXT, "
    "symbol TEXT, kind TEXT, detail TEXT)",
    "CREATE TABLE IF NOT EXISTS news_items (item_hash TEXT PRIMARY KEY, at TEXT NOT NULL, source TEXT, "
    "symbol TEXT, title TEXT, tags TEXT, link TEXT)",
    "CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT, "
    "universe TEXT, hits INTEGER, doc TEXT)",
    # append-only, hash-chained
    "CREATE TABLE IF NOT EXISTS capital_changes (" + _chain_cols(
        "change_id TEXT NOT NULL UNIQUE, at TEXT NOT NULL, mode TEXT NOT NULL, kind TEXT NOT NULL, "
        "old_paise INTEGER NOT NULL, new_paise INTEGER NOT NULL, reason TEXT, limits_json TEXT") + ")",
    "CREATE TABLE IF NOT EXISTS config_history (" + _chain_cols(
        "at TEXT NOT NULL, source TEXT NOT NULL, config_hash TEXT NOT NULL, config_json TEXT NOT NULL, "
        "note TEXT") + ")",
    "CREATE TABLE IF NOT EXISTS orders (" + _chain_cols(
        "order_id TEXT NOT NULL UNIQUE, at TEXT NOT NULL, mode TEXT NOT NULL, date TEXT NOT NULL, "
        "symbol TEXT NOT NULL, side TEXT NOT NULL, qty INTEGER NOT NULL, order_type TEXT, price_paise INTEGER, "
        "stop_paise INTEGER, target_paise INTEGER, fingerprint TEXT NOT NULL, doc TEXT NOT NULL") + ")",
    "CREATE TABLE IF NOT EXISTS order_events (" + _chain_cols(
        "order_id TEXT NOT NULL REFERENCES orders(order_id), at TEXT NOT NULL, status TEXT NOT NULL, "
        "broker_order_id TEXT, detail TEXT") + ")",
    "CREATE TABLE IF NOT EXISTS approvals (" + _chain_cols(
        "approval_id TEXT NOT NULL UNIQUE, at TEXT NOT NULL, mode TEXT NOT NULL, date TEXT NOT NULL, "
        "order_id TEXT REFERENCES orders(order_id), fingerprint TEXT NOT NULL, symbol TEXT, decision TEXT NOT NULL, "
        "reply TEXT NOT NULL, why TEXT, payload_hash TEXT") + ")",
    "CREATE TABLE IF NOT EXISTS risk_verdicts (" + _chain_cols(
        "at TEXT NOT NULL, mode TEXT NOT NULL, date TEXT NOT NULL, fingerprint TEXT NOT NULL, symbol TEXT, "
        "verdict TEXT NOT NULL, reasons TEXT, warnings TEXT") + ")",
    "CREATE TABLE IF NOT EXISTS trades (" + _chain_cols(
        "trade_id TEXT NOT NULL, version INTEGER NOT NULL, supersedes_seq INTEGER, at TEXT NOT NULL, "
        "mode TEXT NOT NULL, date TEXT NOT NULL, symbol TEXT NOT NULL, setup TEXT, qty INTEGER NOT NULL, "
        "status TEXT NOT NULL, planned_entry_paise INTEGER, entry_fill_paise INTEGER, stop_paise INTEGER, "
        "target_paise INTEGER, exit_fill_paise INTEGER, gross_paise INTEGER, charges_paise INTEGER, "
        "net_paise INTEGER, risk_at_stop_paise INTEGER, r_multiple REAL, exit_reason TEXT, exit_date TEXT, "
        "charges_kind TEXT, order_id TEXT REFERENCES orders(order_id), doc TEXT NOT NULL, "
        "UNIQUE (trade_id, version)") + ")",
    "CREATE TABLE IF NOT EXISTS fills (" + _chain_cols(
        "trade_id TEXT NOT NULL, order_id TEXT REFERENCES orders(order_id), leg TEXT NOT NULL, at TEXT, "
        "symbol TEXT NOT NULL, qty INTEGER NOT NULL, price_paise INTEGER NOT NULL, source TEXT NOT NULL, "
        "mode TEXT NOT NULL, broker_trade_id TEXT, UNIQUE (trade_id, leg, source)") + ")",
    "CREATE TABLE IF NOT EXISTS skipped_signals (" + _chain_cols(
        "signal_id TEXT NOT NULL, version INTEGER NOT NULL, at TEXT NOT NULL, mode TEXT NOT NULL, date TEXT NOT NULL, "
        "symbol TEXT NOT NULL, why_skipped TEXT, hypothetical_net_paise INTEGER, doc TEXT NOT NULL, "
        "UNIQUE (signal_id, version)") + ")",
    "CREATE TABLE IF NOT EXISTS trade_notes (" + _chain_cols(
        "trade_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, at TEXT NOT NULL, mode TEXT NOT NULL") + ")",
    "CREATE TABLE IF NOT EXISTS pauses (" + _chain_cols(
        "at TEXT NOT NULL, mode TEXT NOT NULL, kind TEXT NOT NULL, until TEXT, reason TEXT") + ")",
    "CREATE TABLE IF NOT EXISTS discrepancies (" + _chain_cols(
        "at TEXT NOT NULL, mode TEXT NOT NULL, kind TEXT NOT NULL, symbol TEXT, detail TEXT, status TEXT NOT NULL") + ")",
    "CREATE INDEX IF NOT EXISTS ix_trades_date ON trades(date)",
    "CREATE INDEX IF NOT EXISTS ix_trades_symbol ON trades(symbol)",
    "CREATE INDEX IF NOT EXISTS ix_trades_id ON trades(trade_id)",
    "CREATE INDEX IF NOT EXISTS ix_orders_date ON orders(date)",
    "CREATE INDEX IF NOT EXISTS ix_orders_symbol ON orders(symbol)",
    "CREATE INDEX IF NOT EXISTS ix_fills_trade ON fills(trade_id)",
    "CREATE INDEX IF NOT EXISTS ix_skipped_date ON skipped_signals(date)",
    "CREATE INDEX IF NOT EXISTS ix_verdicts_fp ON risk_verdicts(fingerprint, date)",
    "CREATE INDEX IF NOT EXISTS ix_approvals_fp ON approvals(fingerprint, date)",
    "CREATE INDEX IF NOT EXISTS ix_news_symbol ON news_items(symbol)",
    "CREATE INDEX IF NOT EXISTS ix_alerts_symbol ON alerts(symbol)",
]


def _triggers():
    out = []
    for t in APPEND_ONLY:
        for op in ("UPDATE", "DELETE"):
            out.append("CREATE TRIGGER IF NOT EXISTS %s_no_%s BEFORE %s ON %s BEGIN "
                       "SELECT RAISE(ABORT, '%s is append-only: add a new row instead'); END"
                       % (t, op.lower(), op, t, t))
    return out


# ---------------------------------------------------------------- basics

def paise(x):
    """Rupees (a float or a string) -> integer paise. None stays None."""
    if x is None or x == "":
        return None
    return int(round(float(x) * 100 + (1e-7 if float(x) >= 0 else -1e-7)))


def rupees(p):
    return None if p is None else p / 100.0


def now_iso(now=None):
    n = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)
    return n.strftime("%Y-%m-%dT%H:%M:%SZ")


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def default_path(sibling):
    """Where the database lives for a file inside trader/ or trader/state/: trader/trader.db."""
    d = os.path.dirname(os.path.abspath(sibling))
    if os.path.basename(d) == "state":
        d = os.path.dirname(d)
    return os.path.join(d, "trader.db")


def exists(path):
    return bool(path) and os.path.isfile(path)


def connect(path=DEFAULT_DB, create=False):
    if not create and not exists(path):
        raise DbError("no database at %s (create it with: db.py init)" % path)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)   # we BEGIN/COMMIT ourselves
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(path=DEFAULT_DB, now=None):
    conn = connect(path, create=True)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for stmt in SCHEMA + _triggers():
            conn.execute(stmt)
        if conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
            conn.execute("INSERT INTO schema_version VALUES (?, ?)", (SCHEMA_VERSION, now_iso(now)))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return path


class Tx(object):
    """with Tx(path) as conn: ... one transaction; commits on success, rolls back on any error."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.conn = connect(self.path)
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, et, ev, tb):
        try:
            self.conn.execute("COMMIT" if et is None else "ROLLBACK")
        finally:
            self.conn.close()
        return False


def append(conn, table, row, now=None):
    """Add one row to an append-only table inside the caller's transaction. Returns its seq."""
    if table not in APPEND_ONLY:
        raise DbError("%s is not an append-only table" % table)
    last = conn.execute("SELECT row_hash FROM %s ORDER BY seq DESC LIMIT 1" % table).fetchone()
    prev = last[0] if last else GENESIS
    stamp = now_iso(now)
    body = dict(row)
    body["recorded_at"] = stamp
    digest = hashlib.sha256((prev + canon(body)).encode("utf-8")).hexdigest()
    cols = ["prev_hash", "row_hash", "recorded_at"] + list(row)
    cur = conn.execute("INSERT INTO %s (%s) VALUES (%s)" % (table, ", ".join(cols), ", ".join("?" * len(cols))),
                       [prev, digest, stamp] + [row[k] for k in row])
    return cur.lastrowid


def verify_chain(conn, table):
    """-> (rows, problems). Recomputes every hash from the first row."""
    prev, n, problems = GENESIS, 0, []
    cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]
    data_cols = [c for c in cols if c not in ("seq", "prev_hash", "row_hash")]
    for r in conn.execute("SELECT * FROM %s ORDER BY seq" % table):
        n += 1
        if r["prev_hash"] != prev:
            problems.append("%s row %d: the chain is broken (a row before it was removed or changed)" % (table, r["seq"]))
        body = {c: r[c] for c in data_cols}
        want = hashlib.sha256((r["prev_hash"] + canon(body)).encode("utf-8")).hexdigest()
        if want != r["row_hash"]:
            problems.append("%s row %d: its content does not match its hash (the row was edited)" % (table, r["seq"]))
        prev = r["row_hash"]
    return n, problems


def check(path=DEFAULT_DB):
    """-> (ok, lines)."""
    lines, ok = [], True
    conn = connect(path)
    try:
        res = [r[0] for r in conn.execute("PRAGMA integrity_check")]
        if res != ["ok"]:
            ok = False
            lines.append("FAIL sqlite integrity_check: " + "; ".join(res[:5]))
        else:
            lines.append("ok   sqlite integrity_check")
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            ok = False
            lines.append("FAIL %d foreign key violations (first: table %s row %s)" % (len(fk), fk[0][0], fk[0][1]))
        else:
            lines.append("ok   foreign keys")
        trig = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        missing = [t for t in APPEND_ONLY for op in ("update", "delete") if "%s_no_%s" % (t, op) not in trig]
        if missing:
            ok = False
            lines.append("FAIL append-only triggers missing: " + ", ".join(missing[:6]))
        else:
            lines.append("ok   append-only triggers present on %d tables" % len(APPEND_ONLY))
        for t in APPEND_ONLY:
            n, problems = verify_chain(conn, t)
            if problems:
                ok = False
                lines += ["FAIL " + p for p in problems[:5]]
            else:
                lines.append("ok   %-17s %d rows, hash chain intact" % (t, n))
        ver = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        if ver != SCHEMA_VERSION:
            ok = False
            lines.append("FAIL schema version %s, this tool expects %s" % (ver, SCHEMA_VERSION))
    finally:
        conn.close()
    lines.append("Result: " + ("the database is intact." if ok else "PROBLEMS FOUND. Do not trade on this file; "
                                "restore a backup (db.py restore) and compare it with your last export (db.py export)."))
    return ok, lines


# ---------------------------------------------------------------- backup

def backup(path=DEFAULT_DB, folder=None, now=None, keep=KEEP_BACKUPS):
    folder = folder or os.path.join(os.path.dirname(path) or ".", "backups")
    os.makedirs(folder, exist_ok=True)
    n = now or datetime.datetime.now()
    name = "trader-%s.db" % n.strftime("%Y-%m-%d-%H%M%S")
    dest = os.path.join(folder, name)
    src = connect(path)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    old = sorted(f for f in os.listdir(folder) if f.startswith("trader-") and f.endswith(".db"))
    for f in old[:-keep]:
        os.remove(os.path.join(folder, f))
    return dest


def restore(backup_path, path=DEFAULT_DB):
    if not exists(backup_path):
        raise DbError("no backup at %s" % backup_path)
    ok, lines = check(backup_path)
    if not ok:
        raise DbError("that backup fails db check, not restoring it:\n" + "\n".join(lines))
    if exists(path):
        keep = path + ".before-restore"
        shutil.copyfile(path, keep)
    shutil.copyfile(backup_path, path)
    return path


# ---------------------------------------------------------------- the ledger (what paper.py and kite.py load)

def _jd(doc):
    return json.dumps(doc, sort_keys=True, ensure_ascii=False)


def _latest(conn, table, key, mode):
    q = ("SELECT t.* FROM %s t JOIN (SELECT %s k, MAX(version) v FROM %s WHERE mode=? GROUP BY %s) m "
         "ON t.%s = m.k AND t.version = m.v WHERE t.mode=? ORDER BY t.seq" % (table, key, table, key, key))
    return conn.execute(q, (mode, mode)).fetchall()


def load_ledger(path, mode, cfg, label_note=""):
    """The ledger document paper.py / kite.py / trader-risk read: newest version of every trade and signal."""
    conn = connect(path)
    try:
        acct = conn.execute("SELECT * FROM account WHERE mode=?", (mode,)).fetchone()
        capital = rupees(acct["capital_paise"]) if acct else float(cfg.get("capital_inr") or 100000)
        balance = rupees(acct["balance_paise"]) if acct else capital
        trades = [json.loads(r["doc"]) for r in _latest(conn, "trades", "trade_id", mode)]
        skipped = [json.loads(r["doc"]) for r in _latest(conn, "skipped_signals", "signal_id", mode)]
    finally:
        conn.close()
    trades.sort(key=lambda t: t["trade_id"])
    skipped.sort(key=lambda s: s["signal_id"])
    return {"mode": mode, "capital": capital, "balance": balance, "trades": trades, "skipped": skipped,
            "note": label_note, "source": "database"}


def _trade_row(t, mode, version, supersedes, at):
    return {"trade_id": t["trade_id"], "version": version, "supersedes_seq": supersedes, "at": at, "mode": mode,
            "date": t["date"], "symbol": t["symbol"], "setup": t.get("setup"), "qty": int(t["qty"]),
            "status": t.get("status") or "open", "planned_entry_paise": paise(t.get("planned_entry")),
            "entry_fill_paise": paise(t.get("entry_fill")), "stop_paise": paise(t.get("stop")),
            "target_paise": paise(t.get("target")), "exit_fill_paise": paise(t.get("exit_fill")),
            "gross_paise": paise(t.get("gross_pnl")), "charges_paise": paise(t.get("charges")),
            "net_paise": paise(t.get("net_pnl")), "risk_at_stop_paise": paise(t.get("risk_at_stop")),
            "r_multiple": t.get("r_multiple"), "exit_reason": t.get("exit_reason"), "exit_date": t.get("exit_date"),
            "charges_kind": t.get("charges_kind") or ("estimated" if t.get("charges") is not None else None),
            "order_id": t.get("order_id"), "doc": _jd(t)}


def save_ledger(path, mode, led, now=None, source="paper"):
    """Write the changes in a ledger document: a changed trade becomes a NEW version row. One transaction."""
    stamp = now_iso(now)
    changed = 0
    with Tx(path) as conn:
        known = {r["trade_id"]: r for r in _latest(conn, "trades", "trade_id", mode)}
        for t in led.get("trades", []):
            cur = known.get(t["trade_id"])
            if cur is not None and cur["doc"] == _jd(t):
                continue
            order_id = t.get("order_id")
            if order_id and not conn.execute("SELECT 1 FROM orders WHERE order_id=?", (order_id,)).fetchone():
                order_id = None
            row = _trade_row(t, mode, (cur["version"] + 1) if cur else 1, cur["seq"] if cur else None, stamp)
            row["order_id"] = order_id
            append(conn, "trades", row, now)
            changed += 1
            for leg, px_key, t_key in (("entry", "entry_fill", "entry_time"), ("exit", "exit_fill", "exit_time")):
                if t.get(px_key) is not None and not conn.execute(
                        "SELECT 1 FROM fills WHERE trade_id=? AND leg=? AND source=?",
                        (t["trade_id"], leg, source)).fetchone():
                    day = t.get("exit_date") or t["date"] if leg == "exit" else t["date"]
                    append(conn, "fills", {"trade_id": t["trade_id"], "order_id": order_id, "leg": leg,
                                           "at": "%s %s" % (day, t.get(t_key) or ""), "symbol": t["symbol"],
                                           "qty": int(t["qty"]), "price_paise": paise(t[px_key]), "source": source,
                                           "mode": mode, "broker_trade_id": t.get("broker_trade_id")}, now)
        knows = {r["signal_id"]: r for r in _latest(conn, "skipped_signals", "signal_id", mode)}
        for s in led.get("skipped", []):
            cur = knows.get(s["signal_id"])
            if cur is not None and cur["doc"] == _jd(s):
                continue
            append(conn, "skipped_signals", {"signal_id": s["signal_id"], "version": (cur["version"] + 1) if cur else 1,
                                             "at": stamp, "mode": mode, "date": s["date"], "symbol": s["symbol"],
                                             "why_skipped": s.get("why_skipped"),
                                             "hypothetical_net_paise": paise(s.get("hypothetical_net_pnl")),
                                             "doc": _jd(s)}, now)
            changed += 1
        capital = paise(led.get("capital"))
        balance = paise(led.get("balance"))
        old = conn.execute("SELECT baseline_paise FROM account WHERE mode=?", (mode,)).fetchone()
        conn.execute("INSERT INTO account (mode, capital_paise, balance_paise, baseline_paise, updated_at) "
                     "VALUES (?,?,?,?,?) ON CONFLICT(mode) DO UPDATE SET capital_paise=excluded.capital_paise, "
                     "balance_paise=excluded.balance_paise, updated_at=excluded.updated_at",
                     (mode, capital, balance, old[0] if old else capital, stamp))
        conn.execute("DELETE FROM positions WHERE mode=?", (mode,))
        for t in led.get("trades", []):
            if t.get("status") == "open":
                conn.execute("INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?)",
                             (mode, t["symbol"], int(t["qty"]), paise(t.get("entry_fill")), t["trade_id"], stamp))
    return changed


def set_account(path, mode, capital=None, balance=None, baseline=None, now=None):
    with Tx(path) as conn:
        cur = conn.execute("SELECT * FROM account WHERE mode=?", (mode,)).fetchone()
        cap = paise(capital) if capital is not None else (cur["capital_paise"] if cur else 0)
        bal = paise(balance) if balance is not None else (cur["balance_paise"] if cur else cap)
        base = paise(baseline) if baseline is not None else (cur["baseline_paise"] if cur else cap)
        conn.execute("INSERT INTO account VALUES (?,?,?,?,?) ON CONFLICT(mode) DO UPDATE SET "
                     "capital_paise=excluded.capital_paise, balance_paise=excluded.balance_paise, "
                     "baseline_paise=excluded.baseline_paise, updated_at=excluded.updated_at",
                     (mode, cap, bal, base, now_iso(now)))


def get_account(path, mode):
    conn = connect(path)
    try:
        r = conn.execute("SELECT * FROM account WHERE mode=?", (mode,)).fetchone()
        return None if r is None else {"capital": rupees(r["capital_paise"]), "balance": rupees(r["balance_paise"]),
                                       "baseline": rupees(r["baseline_paise"])}
    finally:
        conn.close()


# ---------------------------------------------------------------- the audit rows

def next_order_id(conn, mode, day):
    n = conn.execute("SELECT COUNT(*) FROM orders WHERE mode=? AND date=?", (mode, day)).fetchone()[0]
    return "%s-%s-%02d" % ("PAPER" if mode == "paper" else "LIVE", day.replace("-", ""), n + 1)


def record_verdicts(path, mode, day, results, now=None):
    """results: the list from trader-risk's risk.json."""
    with Tx(path) as conn:
        for r in results:
            append(conn, "risk_verdicts", {
                "at": now_iso(now), "mode": mode, "date": day, "fingerprint": r.get("fingerprint") or "",
                "symbol": r.get("symbol"), "verdict": r.get("verdict") or "", "reasons": canon(r.get("reasons") or []),
                "warnings": canon(r.get("warnings") or [])}, now)
    return len(results)


def record_decision(path, rec, order=None, why=None, now=None):
    """One typed yes or no from ticket.py (rec is its decision record). A yes also creates the order row.
    Returns the order id or None."""
    mode = rec.get("mode") or "paper"
    with Tx(path) as conn:
        order_id = None
        if rec.get("status") == "approved" and order is not None:
            order_id = next_order_id(conn, mode, rec["date"])
            append(conn, "orders", {
                "order_id": order_id, "at": rec["at"], "mode": mode, "date": rec["date"],
                "symbol": rec.get("symbol") or order.get("symbol"), "side": order.get("side") or "buy",
                "qty": int(order.get("qty") or 0), "order_type": order.get("entry_type"),
                "price_paise": paise(order.get("limit_price")), "stop_paise": paise(order.get("stop")),
                "target_paise": paise(order.get("target")), "fingerprint": rec["fingerprint"], "doc": _jd(order)}, now)
            append(conn, "order_events", {"order_id": order_id, "at": rec["at"], "status": "approved",
                                          "broker_order_id": None, "detail": "your yes, word for word, is in approvals"}, now)
        n = conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
        append(conn, "approvals", {
            "approval_id": "AP-%s-%04d" % (rec["date"].replace("-", ""), n + 1), "at": rec["at"], "mode": mode,
            "date": rec["date"], "order_id": order_id, "fingerprint": rec["fingerprint"], "symbol": rec.get("symbol"),
            "decision": "yes" if rec.get("status") == "approved" else "no", "reply": rec.get("reply") or "",
            "why": why, "payload_hash": rec.get("payload_hash")}, now)
    return order_id


def order_for_fingerprint(path, fp, day, mode):
    conn = connect(path)
    try:
        r = conn.execute("SELECT order_id FROM orders WHERE fingerprint=? AND date=? AND mode=? ORDER BY seq DESC",
                         (fp, day, mode)).fetchone()
        return r[0] if r else None
    finally:
        conn.close()


def record_order_event(path, order_id, status, broker_order_id=None, detail=None, now=None):
    with Tx(path) as conn:
        append(conn, "order_events", {"order_id": order_id, "at": now_iso(now), "status": status,
                                      "broker_order_id": broker_order_id, "detail": detail}, now)


def add_note(path, trade_id, kind, text, mode, now=None):
    with Tx(path) as conn:
        append(conn, "trade_notes", {"trade_id": trade_id, "kind": kind, "text": text, "at": now_iso(now),
                                     "mode": mode}, now)


def notes_for(path, trade_id=None):
    conn = connect(path)
    try:
        q = "SELECT * FROM trade_notes" + (" WHERE trade_id=?" if trade_id else "") + " ORDER BY seq"
        return [dict(r) for r in conn.execute(q, (trade_id,) if trade_id else ())]
    finally:
        conn.close()


def add_pause(path, mode, kind, until=None, reason=None, now=None):
    with Tx(path) as conn:
        append(conn, "pauses", {"at": now_iso(now), "mode": mode, "kind": kind, "until": until, "reason": reason}, now)


def pause_state(path, mode, today):
    """'pause until' / 'resume' / 'kill' history -> {'paused': bool, 'until': ..., 'reason': ...}."""
    conn = connect(path)
    try:
        state = {"paused": False, "until": None, "reason": None}
        for r in conn.execute("SELECT * FROM pauses WHERE mode IN (?, 'all') ORDER BY seq", (mode,)):
            if r["kind"] == "pause":
                state = {"paused": True, "until": r["until"], "reason": r["reason"]}
            elif r["kind"] == "resume":
                state = {"paused": False, "until": None, "reason": None}
        if state["paused"] and state["until"] and today > state["until"]:
            state = {"paused": False, "until": None, "reason": "pause ended on %s" % state["until"]}
        return state
    finally:
        conn.close()


def control_blocks(config_path, mode, today, db_path=None):
    """Reasons no NEW entry may be placed right now: the STOP file (kill switch) or a pause. [] when clear.
    Exits are never blocked by this."""
    reasons = []
    base = os.path.dirname(os.path.abspath(config_path))
    if os.path.exists(os.path.join(base, "STOP")):
        reasons.append("KILL SWITCH: the file %s exists. Nothing new is placed until you delete it "
                       "(control.py resume, or remove the file)" % os.path.join(os.path.basename(base), "STOP"))
    path = db_path or os.path.join(base, "trader.db")
    if exists(path):
        ps = pause_state(path, mode, today)
        if ps["paused"]:
            reasons.append("trading is PAUSED%s%s (control.py resume ends it)" % (
                (" until " + ps["until"]) if ps["until"] else "", (": " + ps["reason"]) if ps["reason"] else ""))
    return reasons


def set_meta(path, key, value, now=None):
    with Tx(path) as conn:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, canon(value)))


def get_meta(path, key):
    conn = connect(path)
    try:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else None
    finally:
        conn.close()


def add_discrepancy(path, mode, kind, symbol, detail, now=None):
    with Tx(path) as conn:
        append(conn, "discrepancies", {"at": now_iso(now), "mode": mode, "kind": kind, "symbol": symbol,
                                       "detail": detail, "status": "open"}, now)


def log_config(path, cfg, source, note=None, now=None):
    """Record the config if it differs from the last recorded one. Returns True when a row was added.
    source: 'tool' (a skill changed it with your yes) or 'manual-edit' (you edited the file)."""
    blob = canon(cfg)
    h = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    with Tx(path) as conn:
        last = conn.execute("SELECT config_hash FROM config_history ORDER BY seq DESC LIMIT 1").fetchone()
        if last and last[0] == h:
            return False
        append(conn, "config_history", {"at": now_iso(now), "source": source if last else "initial", "config_hash": h,
                                        "config_json": blob, "note": note}, now)
    return True


def last_config(path):
    conn = connect(path)
    try:
        r = conn.execute("SELECT config_json FROM config_history ORDER BY seq DESC LIMIT 1").fetchone()
        return json.loads(r[0]) if r else None
    finally:
        conn.close()


def add_capital_change(path, mode, kind, old, new, reason, limits=None, now=None):
    with Tx(path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM capital_changes").fetchone()[0]
        append(conn, "capital_changes", {"change_id": "CAP-%04d" % (n + 1), "at": now_iso(now), "mode": mode,
                                         "kind": kind, "old_paise": paise(old), "new_paise": paise(new),
                                         "reason": reason, "limits_json": canon(limits or {})}, now)


# ---------------------------------------------------------------- working-table helpers

def log_row(path, table, row):
    """INSERT OR IGNORE into alerts / news_items / scans. Returns True if a row was added."""
    if table not in ("alerts", "news_items", "scans"):
        raise DbError("log_row is for alerts, news_items and scans")
    cols = list(row)
    with Tx(path) as conn:
        cur = conn.execute("INSERT OR IGNORE INTO %s (%s) VALUES (%s)" % (table, ", ".join(cols),
                                                                         ", ".join("?" * len(cols))),
                           [row[c] for c in cols])
        return cur.rowcount > 0


# ---------------------------------------------------------------- migration from the JSON files

def migrate(base="trader", path=None, now=None):
    """Import the JSON state into the database. Never changes or deletes the JSON files. Returns a report."""
    path = path or os.path.join(base, "trader.db")
    init(path, now)
    rep = {}

    def rj(*parts):
        p = os.path.join(base, *parts)
        if not os.path.isfile(p):
            return None
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    cfg = rj("config.json") or {}
    for mode, name in (("paper", "paper.json"), ("live", "live.json")):
        led = rj("state", name)
        if not led:
            continue
        before = _counts(path)
        save_ledger(path, mode, led, now, source="paper" if mode == "paper" else "kite")
        after = _counts(path)
        rep["%s trades" % mode] = (len(led.get("trades", [])), after["trades"] - before["trades"])
        rep["%s skipped signals" % mode] = (len(led.get("skipped", [])), after["skipped_signals"] - before["skipped_signals"])
    broker = rj("state", "broker.json") or {}
    n_dec = 0
    existing = _approval_keys(path)
    for rec in broker.get("orders", []):
        key = (rec.get("fingerprint"), rec.get("at"), rec.get("reply"))
        if key in existing or not rec.get("fingerprint") or not rec.get("date"):
            continue
        record_decision(path, rec, None, now=now)
        n_dec += 1
    rep["decisions (approvals)"] = (len(broker.get("orders", [])), n_dec)
    risk = rj("state", "risk.json") or {}
    n_rv = 0
    if risk.get("results"):
        n_rv = record_verdicts(path, risk.get("mode") or "paper", risk.get("date") or "", risk["results"], now)
    rep["risk verdicts"] = (len(risk.get("results", [])), n_rv)
    if cfg:
        log_config(path, cfg, "initial", "imported by db.py migrate", now)
    return rep


def _counts(path):
    conn = connect(path)
    try:
        return {t: conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                for t in APPEND_ONLY + ["positions", "account"]}
    finally:
        conn.close()


def _approval_keys(path):
    conn = connect(path)
    try:
        return {(r["fingerprint"], r["at"], r["reply"]) for r in conn.execute("SELECT fingerprint, at, reply FROM approvals")}
    finally:
        conn.close()


# ---------------------------------------------------------------- export

TABLES_FOR_EXPORT = APPEND_ONLY + ["account", "positions", "daily_summary", "weekly_reports", "alerts", "news_items",
                                   "scans", "schema_version", "meta"]


def export_csv(path, folder):
    os.makedirs(folder, exist_ok=True)
    conn = connect(path)
    out = {}
    try:
        for t in TABLES_FOR_EXPORT:
            cur = conn.execute("SELECT * FROM %s" % t)
            names = [d[0] for d in cur.description]
            dest = os.path.join(folder, t + ".csv")
            with open(dest, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(names)
                n = 0
                for r in cur:
                    w.writerow(list(r))
                    n += 1
            out[t] = n
    finally:
        conn.close()
    return out


def fy_range(fy):
    """'2026-27' -> ('2026-04-01', '2027-03-31')."""
    try:
        a = int(fy.split("-")[0])
    except (ValueError, IndexError):
        raise DbError('--fy wants a financial year like "2026-27"')
    return "%d-04-01" % a, "%d-03-31" % (a + 1)


def export_tax(path, folder, fy, mode="live"):
    """Closed trades of one mode in a financial year, with totals. NOT TAX ADVICE. Paper is never tax."""
    start, end = fy_range(fy)
    os.makedirs(folder, exist_ok=True)
    conn = connect(path)
    try:
        rows = [r for r in _latest(conn, "trades", "trade_id", mode)
                if r["status"] == "closed" and start <= (r["exit_date"] or r["date"]) <= end]
    finally:
        conn.close()
    dest = os.path.join(folder, "tax-pnl-%s-%s.csv" % (mode, fy))
    gross = charges = net = 0
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["NOT TAX ADVICE. Intraday equity (MIS) results as recorded by this tool, with charges "
                    "%s. Generally treated as speculative business income: confirm with a chartered accountant "
                    "and the broker's contract notes and P&L statement." % (
                        "estimated unless marked actual" if mode == "live" else "estimated")])
        w.writerow(["financial year", fy, "mode", mode])
        w.writerow(["trade_id", "date", "exit_date", "symbol", "qty", "entry_fill", "exit_fill", "gross", "charges",
                    "charges_kind", "net"])
        for r in rows:
            w.writerow([r["trade_id"], r["date"], r["exit_date"], r["symbol"], r["qty"], rupees(r["entry_fill_paise"]),
                        rupees(r["exit_fill_paise"]), rupees(r["gross_paise"]), rupees(r["charges_paise"]),
                        r["charges_kind"], rupees(r["net_paise"])])
            gross += r["gross_paise"] or 0
            charges += r["charges_paise"] or 0
            net += r["net_paise"] or 0
        w.writerow([])
        w.writerow(["TOTAL", "", "", "", "", "", "", gross / 100.0, charges / 100.0, "", net / 100.0])
    return dest, len(rows), (gross, charges, net)


# ---------------------------------------------------------------- the ledger store the tools use

class LedgerStore(object):
    """Hides where the ledger lives. With trader/trader.db present the database is the truth and the JSON
    file is rewritten after every save as a read-only copy; without a database the JSON file is the ledger
    (the older behaviour). A JSON copy that no longer matches what the database last wrote is reported."""

    def __init__(self, json_path, db_path, mode, cfg, new_ledger, read_json, write_json, source="paper"):
        self.json_path, self.db_path, self.mode, self.cfg = json_path, db_path, mode, cfg
        self._new, self._read, self._write, self.source = new_ledger, read_json, write_json, source
        self.warnings = []

    @property
    def uses_db(self):
        return exists(self.db_path)

    def load(self):
        if self.uses_db:
            led = load_ledger(self.db_path, self.mode, self.cfg)
            if not led["trades"] and not led["skipped"] and get_account(self.db_path, self.mode) is None:
                copy = self._read(self.json_path)
                if copy is not None and (copy.get("trades") or copy.get("skipped")):
                    copy.setdefault("trades", [])
                    copy.setdefault("skipped", [])
                    self.warnings.append("%s has %s record(s) the database does not have yet; they are used now and "
                                         "go into the database at the next save (or run db.py migrate)." % (
                                             self.json_path, len(copy["trades"]) + len(copy["skipped"])))
                    return copy
                led = self._new(self.cfg)
            else:
                led["note"] = self._new(self.cfg).get("note", "")
            copy = self._read(self.json_path)
            if copy is not None and canon({"t": copy.get("trades"), "s": copy.get("skipped")}) != \
                    canon({"t": led["trades"], "s": led["skipped"]}):
                self.warnings.append("%s was changed outside the tools and no longer matches the database. The "
                                     "database is used; the file will be rewritten on the next save." % self.json_path)
            return led
        led = self._read(self.json_path)
        if led is None:
            return self._new(self.cfg)
        led.setdefault("trades", [])
        led.setdefault("skipped", [])
        return led

    def save(self, led, now=None):
        if self.uses_db:
            save_ledger(self.db_path, self.mode, led, now, self.source)
        self._write(self.json_path, led)


# ---------------------------------------------------------------- command line

def _db(a):
    return a.db


def cmd_init(a):
    existed = exists(a.db)
    init(a.db)
    print("%s the database at %s (schema version %d)." % ("Checked" if existed else "Created", a.db, SCHEMA_VERSION))
    print("It is in .gitignore. Run: db.py migrate (if you have older JSON files), db.py check, db.py backup.")
    return 0


def cmd_migrate(a):
    base = os.path.dirname(a.db) or "trader"
    rep = migrate(base, a.db)
    print("Migration into %s. Each line: found in the JSON files -> added to the database." % a.db)
    for k, (found, added) in rep.items():
        print("  %-26s %4d found, %4d added%s" % (k, found, added, "" if found == added else "  (the rest were already there)"))
    print("The JSON files were not changed or deleted. Check the counts against your records, then db.py check.")
    return 0


def cmd_status(a):
    c = _counts(a.db)
    for t, n in c.items():
        print("  %-18s %d" % (t, n))
    conn = connect(a.db)
    try:
        for t in APPEND_ONLY:
            last = conn.execute("SELECT row_hash FROM %s ORDER BY seq DESC LIMIT 1" % t).fetchone()
            if last:
                print("  chain head %-17s %s" % (t, last[0][:16]))
    finally:
        conn.close()
    folder = os.path.join(os.path.dirname(a.db) or ".", "backups")
    last = sorted(os.listdir(folder))[-1] if os.path.isdir(folder) and os.listdir(folder) else None
    print("Last backup: %s" % (last or "none yet. Run: db.py backup"))
    return 0


def cmd_check(a):
    ok, lines = check(a.db)
    print("\n".join(lines))
    return 0 if ok else 1


def cmd_backup(a):
    dest = backup(a.db, a.folder)
    print("Backup written: %s (the last %d are kept)." % (dest, KEEP_BACKUPS))
    return 0


def cmd_restore(a):
    restore(a.file, a.db)
    print("Restored %s from %s. The previous file was kept as %s.before-restore. Run db.py check now." % (a.db, a.file, a.db))
    return 0


def cmd_export(a):
    folder = a.folder or os.path.join(os.path.dirname(a.db) or ".", "exports")
    if a.tax:
        fy = a.fy or _current_fy()
        dest, n, (g, c, nt) = export_tax(a.db, folder, fy, a.mode)
        print("Wrote %s: %d closed %s trades, gross %.2f, charges %.2f, net %.2f." % (dest, n, a.mode, g / 100.0, c / 100.0, nt / 100.0))
        print("NOT TAX ADVICE. Charges are estimates unless marked actual. Use your broker's contract notes and P&L statement.")
        return 0
    out = export_csv(a.db, folder)
    print("Wrote one CSV per table into %s:" % folder)
    for t, n in out.items():
        print("  %-18s %d rows" % (t, n))
    return 0


def _current_fy():
    d = datetime.date.today()
    a = d.year if d.month >= 4 else d.year - 1
    return "%d-%02d" % (a, (a + 1) % 100)


def main(argv=None):
    ap = argparse.ArgumentParser(description="The local database (trader-db).")
    sub = ap.add_subparsers(dest="cmd")
    for name, fn in (("init", cmd_init), ("migrate", cmd_migrate), ("status", cmd_status), ("check", cmd_check),
                     ("backup", cmd_backup), ("restore", cmd_restore), ("export", cmd_export)):
        p = sub.add_parser(name)
        p.add_argument("--db", default=DEFAULT_DB)
        p.set_defaults(fn=fn)
        if name == "backup":
            p.add_argument("--folder")
        if name == "restore":
            p.add_argument("file")
        if name == "export":
            p.add_argument("--folder")
            p.add_argument("--tax", action="store_true")
            p.add_argument("--fy")
            p.add_argument("--mode", choices=["live", "paper"], default="live")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return a.fn(a)
    except (DbError, sqlite3.Error) as e:
        print("db.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
