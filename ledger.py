"""
Append-only SQLite trade ledger.

Source of truth for paper-trading research:
  - events: immutable audit log (whale events, decisions, fills, exits, marks, resolutions)
  - lots: open/closed position lots attributed per whale
  - projections (account, whale_inventory, logs, equity_snapshots) derived from events

Dashboard/state dict is a projection of this ledger, not the authority.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Keep the SQLite ledger on the same configurable persistent volume as config
# and the JSON mirror. This is essential on hosts with ephemeral source disks.
DATA_DIR = os.environ.get("POLYCOPY_DATA_DIR", os.path.join(BASE_DIR, "data"))

def _verify_data_dir(d_dir):
    try:
        os.makedirs(d_dir, exist_ok=True)
        # Try writing a temporary file to check write access
        test_file = os.path.join(d_dir, ".write_test")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        return d_dir
    except Exception:
        fallback = os.path.join(BASE_DIR, "data")
        print(f"WARNING: Configured DATA_DIR '{d_dir}' is not writable. Falling back to local data directory '{fallback}'.")
        os.makedirs(fallback, exist_ok=True)
        return fallback

DATA_DIR = _verify_data_dir(DATA_DIR)
DEFAULT_DB_PATH = os.path.join(DATA_DIR, "ledger.db")

_lock = threading.RLock()
_db_path: str = DEFAULT_DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT,
    idempotency_key TEXT,
    payload TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_idempotency
    ON events(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(event_type, ts);

CREATE TABLE IF NOT EXISTS lots (
    lot_id TEXT PRIMARY KEY,
    token_id TEXT NOT NULL,
    condition_id TEXT,
    whale_address TEXT NOT NULL,
    whale_name TEXT,
    market_title TEXT,
    market_slug TEXT,
    outcome TEXT,
    outcome_index INTEGER,
    quantity REAL NOT NULL,
    remaining_qty REAL NOT NULL,
    avg_price REAL NOT NULL,
    invested_usdc REAL NOT NULL,
    opened_at REAL NOT NULL,
    closed_at REAL,
    strategy_exits_enabled INTEGER DEFAULT 1,
    grandfathered INTEGER DEFAULT 0,
    strategy_version INTEGER,
    win_probability REAL,
    conviction_score REAL,
    best_bet_score REAL,
    entry_event_id INTEGER,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_lots_token ON lots(token_id);
CREATE INDEX IF NOT EXISTS idx_lots_whale ON lots(whale_address);
CREATE INDEX IF NOT EXISTS idx_lots_open ON lots(remaining_qty) WHERE remaining_qty > 0;

CREATE TABLE IF NOT EXISTS account (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_keys (
    idempotency_key TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    cash REAL NOT NULL,
    holdings_bid REAL NOT NULL,
    holdings_mid REAL NOT NULL,
    total_equity_bid REAL NOT NULL,
    total_equity_mid REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    whale_address TEXT,
    whale_name TEXT,
    token_id TEXT,
    condition_id TEXT,
    side TEXT,
    tx_hash TEXT,
    decision TEXT NOT NULL,
    reason TEXT,
    strategy_version INTEGER,
    feature_snapshot TEXT
);

CREATE TABLE IF NOT EXISTS whale_inventory (
    whale_address TEXT NOT NULL,
    token_id TEXT NOT NULL,
    quantity REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (whale_address, token_id)
);

CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    trader_address TEXT,
    trader_name TEXT,
    original_trader_address TEXT,
    original_trader_name TEXT,
    market_title TEXT,
    market_slug TEXT,
    outcome TEXT,
    type TEXT NOT NULL,
    quantity REAL,
    price REAL,
    usdc_size REAL,
    win_probability REAL,
    conviction_score REAL,
    best_bet_score REAL,
    tx_hash TEXT,
    lot_id TEXT,
    realized_pnl REAL DEFAULT 0,
    fill_detail TEXT,
    strategy_version INTEGER
);
CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts);
CREATE INDEX IF NOT EXISTS idx_trades_type ON trades(type);
"""


_local = threading.local()

def close_connection() -> None:
    if hasattr(_local, "conn") and _local.conn is not None:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None


def configure(db_path: Optional[str] = None) -> None:
    global _db_path
    with _lock:
        if db_path:
            _db_path = db_path
        close_connection()


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    _ensure_dir(_db_path)
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(_db_path, check_same_thread=False, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.commit()
        _local.conn = conn
    return _local.conn


@contextmanager
def transaction():
    conn = get_connection()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def make_idempotency_key(
    source: str,
    transaction_hash: str,
    asset: str,
    side: str,
    extra: str = "",
) -> str:
    parts = [source or "unknown", transaction_hash or "", asset or "", (side or "").upper()]
    if extra:
        parts.append(extra)
    return "|".join(parts)


def get_meta(key: str, default: Optional[str] = None) -> Optional[str]:
    conn = get_connection()
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(key: str, value: str) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def is_initialized() -> bool:
    return get_meta("initialized") == "1"


def init_account(starting_capital: float, note: str = "init") -> None:
    """Initialize cash once. Does not wipe existing history."""
    with transaction() as conn:
        row = conn.execute("SELECT value FROM account WHERE key = 'cash_usdc'").fetchone()
        if row is not None:
            return
        now = time.time()
        conn.execute(
            "INSERT INTO account(key, value) VALUES('cash_usdc', ?)",
            (str(float(starting_capital)),),
        )
        conn.execute(
            "INSERT INTO account(key, value) VALUES('starting_capital', ?)",
            (str(float(starting_capital)),),
        )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('initialized', '1') ON CONFLICT(key) DO UPDATE SET value = '1'"
        )
        payload = json.dumps({"cash": starting_capital, "note": note})
        conn.execute(
            "INSERT INTO events(ts, event_type, source, idempotency_key, payload) VALUES(?,?,?,?,?)",
            (now, "account_init", "system", f"system|account_init|{now}", payload),
        )
        conn.execute(
            "INSERT INTO equity_snapshots(ts, cash, holdings_bid, holdings_mid, total_equity_bid, total_equity_mid) VALUES(?,?,?,?,?,?)",
            (now, float(starting_capital), 0.0, 0.0, float(starting_capital), float(starting_capital)),
        )
        conn.execute(
            "INSERT INTO logs(ts, message) VALUES(?, ?)",
            (now, f"Ledger initialized with starting capital {starting_capital:.2f} USDC."),
        )


def get_cash() -> float:
    conn = get_connection()
    row = conn.execute("SELECT value FROM account WHERE key = 'cash_usdc'").fetchone()
    return float(row["value"]) if row else 0.0


def set_cash(amount: float, reason: str = "set_cash") -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO account(key, value) VALUES('cash_usdc', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(float(amount)),),
        )


def adjust_cash(delta: float) -> float:
    with transaction() as conn:
        row = conn.execute("SELECT value FROM account WHERE key = 'cash_usdc'").fetchone()
        current = float(row["value"]) if row else 0.0
        new_val = current + float(delta)
        conn.execute(
            "INSERT INTO account(key, value) VALUES('cash_usdc', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(new_val),),
        )
        return new_val


def is_processed(idempotency_key: str) -> bool:
    if not idempotency_key:
        return False
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM processed_keys WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    return row is not None


def mark_processed(idempotency_key: str, note: str = "") -> bool:
    """Return True if newly marked, False if already present."""
    if not idempotency_key:
        return False
    with transaction() as conn:
        try:
            conn.execute(
                "INSERT INTO processed_keys(idempotency_key, ts, note) VALUES(?,?,?)",
                (idempotency_key, time.time(), note or None),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def append_event(
    event_type: str,
    payload: Dict[str, Any],
    source: str = "system",
    idempotency_key: Optional[str] = None,
    ts: Optional[float] = None,
) -> Optional[int]:
    """
    Append an immutable event. Returns event id, or None if duplicate idempotency key.
    """
    ts = float(ts if ts is not None else time.time())
    payload_str = json.dumps(payload, default=str)
    with transaction() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO events(ts, event_type, source, idempotency_key, payload) VALUES(?,?,?,?,?)",
                (ts, event_type, source, idempotency_key, payload_str),
            )
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            return None


def add_log(message: str, ts: Optional[float] = None) -> None:
    ts = float(ts if ts is not None else time.time())
    with transaction() as conn:
        conn.execute("INSERT INTO logs(ts, message) VALUES(?, ?)", (ts, message))
        # Keep last 2000 log rows
        conn.execute(
            """
            DELETE FROM logs WHERE id NOT IN (
                SELECT id FROM logs ORDER BY id DESC LIMIT 2000
            )
            """
        )
    print(f"[LOG] {message}")


def record_decision(
    decision: str,
    reason: str,
    whale_address: str = "",
    whale_name: str = "",
    token_id: str = "",
    condition_id: str = "",
    side: str = "",
    tx_hash: str = "",
    strategy_version: Optional[int] = None,
    feature_snapshot: Optional[Dict[str, Any]] = None,
    mark_tx_processed: bool = True,
    source: str = "polymarket",
) -> None:
    """Persist a decision (accept or reject) with optional feature snapshot."""
    ts = time.time()
    side_u = (side or "").upper()
    idem = None
    if tx_hash and token_id and side_u:
        idem = make_idempotency_key(source, tx_hash, token_id, side_u, f"decision:{decision}")

    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO decisions(
                ts, whale_address, whale_name, token_id, condition_id, side, tx_hash,
                decision, reason, strategy_version, feature_snapshot
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ts,
                (whale_address or "").lower() or None,
                whale_name or None,
                token_id or None,
                condition_id or None,
                side_u or None,
                tx_hash or None,
                decision,
                reason,
                strategy_version,
                json.dumps(feature_snapshot or {}, default=str),
            ),
        )
        try:
            conn.execute(
                "INSERT INTO events(ts, event_type, source, idempotency_key, payload) VALUES(?,?,?,?,?)",
                (
                    ts,
                    "decision",
                    source,
                    idem,
                    json.dumps(
                        {
                            "decision": decision,
                            "reason": reason,
                            "whale_address": whale_address,
                            "token_id": token_id,
                            "side": side_u,
                            "tx_hash": tx_hash,
                            "feature_snapshot": feature_snapshot or {},
                        },
                        default=str,
                    ),
                ),
            )
        except sqlite3.IntegrityError:
            pass

        if mark_tx_processed and tx_hash and token_id and side_u:
            proc_key = make_idempotency_key(source, tx_hash, token_id, side_u)
            try:
                conn.execute(
                    "INSERT INTO processed_keys(idempotency_key, ts, note) VALUES(?,?,?)",
                    (proc_key, ts, f"decision:{decision}:{reason[:80]}"),
                )
            except sqlite3.IntegrityError:
                pass


def update_whale_inventory(whale_address: str, token_id: str, delta_qty: float) -> float:
    """Track observed whale holdings independently of whether we copied."""
    addr = (whale_address or "").lower()
    with transaction() as conn:
        row = conn.execute(
            "SELECT quantity FROM whale_inventory WHERE whale_address = ? AND token_id = ?",
            (addr, token_id),
        ).fetchone()
        current = float(row["quantity"]) if row else 0.0
        new_qty = max(0.0, current + float(delta_qty))
        conn.execute(
            """
            INSERT INTO whale_inventory(whale_address, token_id, quantity) VALUES(?,?,?)
            ON CONFLICT(whale_address, token_id) DO UPDATE SET quantity = excluded.quantity
            """,
            (addr, token_id, new_qty),
        )
        return new_qty


def set_whale_inventory(whale_address: str, token_id: str, quantity: float) -> None:
    addr = (whale_address or "").lower()
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO whale_inventory(whale_address, token_id, quantity) VALUES(?,?,?)
            ON CONFLICT(whale_address, token_id) DO UPDATE SET quantity = excluded.quantity
            """,
            (addr, token_id, max(0.0, float(quantity))),
        )


def get_whale_inventory(whale_address: Optional[str] = None) -> Dict[str, Dict[str, float]]:
    conn = get_connection()
    if whale_address:
        rows = conn.execute(
            "SELECT whale_address, token_id, quantity FROM whale_inventory WHERE whale_address = ?",
            (whale_address.lower(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT whale_address, token_id, quantity FROM whale_inventory"
        ).fetchall()
    out: Dict[str, Dict[str, float]] = {}
    for r in rows:
        out.setdefault(r["whale_address"], {})[r["token_id"]] = float(r["quantity"])
    return out


def open_lot(
    token_id: str,
    whale_address: str,
    quantity: float,
    avg_price: float,
    invested_usdc: float,
    *,
    condition_id: str = "",
    whale_name: str = "",
    market_title: str = "",
    market_slug: str = "",
    outcome: str = "",
    outcome_index: int = 0,
    strategy_exits_enabled: bool = True,
    grandfathered: bool = False,
    strategy_version: Optional[int] = None,
    win_probability: float = 0.0,
    conviction_score: float = 0.0,
    best_bet_score: float = 0.0,
    metadata: Optional[Dict[str, Any]] = None,
    entry_event_id: Optional[int] = None,
    opened_at: Optional[float] = None,
    lot_id: Optional[str] = None,
) -> str:
    lot_id = lot_id or str(uuid.uuid4())
    opened_at = float(opened_at if opened_at is not None else time.time())
    addr = (whale_address or "").lower()
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO lots(
                lot_id, token_id, condition_id, whale_address, whale_name,
                market_title, market_slug, outcome, outcome_index,
                quantity, remaining_qty, avg_price, invested_usdc, opened_at,
                strategy_exits_enabled, grandfathered, strategy_version,
                win_probability, conviction_score, best_bet_score,
                entry_event_id, metadata
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                lot_id,
                token_id,
                condition_id,
                addr,
                whale_name,
                market_title,
                market_slug,
                outcome,
                outcome_index,
                float(quantity),
                float(quantity),
                float(avg_price),
                float(invested_usdc),
                opened_at,
                1 if strategy_exits_enabled else 0,
                1 if grandfathered else 0,
                strategy_version,
                win_probability,
                conviction_score,
                best_bet_score,
                entry_event_id,
                json.dumps(metadata or {}, default=str),
            ),
        )
    return lot_id


def get_open_lots_for_whale_token(whale_address: str, token_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM lots
        WHERE whale_address = ? AND token_id = ? AND remaining_qty > 1e-12
        ORDER BY opened_at ASC
        """,
        ((whale_address or "").lower(), token_id),
    ).fetchall()
    return [dict(r) for r in rows]


def get_open_lots_for_token(token_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM lots
        WHERE token_id = ? AND remaining_qty > 1e-12
        ORDER BY opened_at ASC
        """,
        (token_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_open_lots() -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM lots WHERE remaining_qty > 1e-12 ORDER BY opened_at ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def reduce_lot(lot_id: str, qty: float) -> Tuple[float, float]:
    """
    Reduce remaining quantity on a lot.
    Returns (qty_closed, cost_basis_released).
    """
    qty = float(qty)
    if qty <= 0:
        return 0.0, 0.0
    with transaction() as conn:
        row = conn.execute("SELECT * FROM lots WHERE lot_id = ?", (lot_id,)).fetchone()
        if not row:
            return 0.0, 0.0
        remaining = float(row["remaining_qty"])
        close_qty = min(remaining, qty)
        if close_qty <= 0:
            return 0.0, 0.0
        avg = float(row["avg_price"])
        cost = avg * close_qty
        new_rem = remaining - close_qty
        invested = float(row["invested_usdc"])
        # Pro-rate invested
        if remaining > 0:
            new_invested = invested * (new_rem / remaining)
        else:
            new_invested = 0.0
        closed_at = time.time() if new_rem <= 1e-12 else None
        conn.execute(
            """
            UPDATE lots SET remaining_qty = ?, invested_usdc = ?, closed_at = COALESCE(?, closed_at)
            WHERE lot_id = ?
            """,
            (max(0.0, new_rem), max(0.0, new_invested), closed_at, lot_id),
        )
        return close_qty, cost


def close_lots_for_whale(
    whale_address: str,
    token_id: str,
    quantity: float,
) -> List[Dict[str, Any]]:
    """
    Close up to `quantity` shares from this whale's lots only (FIFO).
    Returns list of {lot_id, qty, cost_basis, avg_price, ...meta}.
    """
    remaining_to_close = float(quantity)
    closed: List[Dict[str, Any]] = []
    if remaining_to_close <= 0:
        return closed

    lots = get_open_lots_for_whale_token(whale_address, token_id)
    for lot in lots:
        if remaining_to_close <= 1e-12:
            break
        take = min(float(lot["remaining_qty"]), remaining_to_close)
        closed_qty, cost = reduce_lot(lot["lot_id"], take)
        if closed_qty <= 0:
            continue
        closed.append(
            {
                "lot_id": lot["lot_id"],
                "qty": closed_qty,
                "cost_basis": cost,
                "avg_price": float(lot["avg_price"]),
                "whale_address": lot["whale_address"],
                "whale_name": lot.get("whale_name"),
                "market_title": lot.get("market_title"),
                "market_slug": lot.get("market_slug"),
                "outcome": lot.get("outcome"),
                "condition_id": lot.get("condition_id"),
            }
        )
        remaining_to_close -= closed_qty
    return closed


def close_all_lots_for_token(token_id: str) -> List[Dict[str, Any]]:
    """Close every open lot for a token (resolution / full strategy exit)."""
    closed: List[Dict[str, Any]] = []
    for lot in get_open_lots_for_token(token_id):
        closed_qty, cost = reduce_lot(lot["lot_id"], float(lot["remaining_qty"]))
        if closed_qty <= 0:
            continue
        closed.append(
            {
                "lot_id": lot["lot_id"],
                "qty": closed_qty,
                "cost_basis": cost,
                "avg_price": float(lot["avg_price"]),
                "whale_address": lot["whale_address"],
                "whale_name": lot.get("whale_name"),
                "market_title": lot.get("market_title"),
                "market_slug": lot.get("market_slug"),
                "outcome": lot.get("outcome"),
                "condition_id": lot.get("condition_id"),
            }
        )
    return closed


def record_trade(trade: Dict[str, Any]) -> str:
    trade_id = trade.get("id") or str(uuid.uuid4())
    ts = float(trade.get("timestamp") or trade.get("ts") or time.time())
    with transaction() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO trades(
                id, ts, trader_address, trader_name, original_trader_address, original_trader_name,
                market_title, market_slug, outcome, type, quantity, price, usdc_size,
                win_probability, conviction_score, best_bet_score, tx_hash, lot_id,
                realized_pnl, fill_detail, strategy_version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trade_id,
                ts,
                trade.get("trader_address"),
                trade.get("trader_name"),
                trade.get("original_trader_address"),
                trade.get("original_trader_name"),
                trade.get("market_title"),
                trade.get("market_slug"),
                trade.get("outcome"),
                trade.get("type"),
                trade.get("quantity"),
                trade.get("price"),
                trade.get("usdc_size"),
                trade.get("win_probability"),
                trade.get("conviction_score"),
                trade.get("best_bet_score"),
                trade.get("tx_hash"),
                trade.get("lot_id"),
                float(trade.get("realized_pnl") or 0.0),
                json.dumps(trade.get("fill_detail") or {}, default=str),
                trade.get("strategy_version"),
            ),
        )
        conn.execute(
            "INSERT INTO events(ts, event_type, source, idempotency_key, payload) VALUES(?,?,?,?,?)",
            (
                ts,
                "trade",
                "simulator",
                trade.get("idempotency_key") or f"trade|{trade_id}",
                json.dumps({**trade, "id": trade_id}, default=str),
            ),
        )
    return trade_id


def append_equity_snapshot(
    cash: float,
    holdings_bid: float,
    holdings_mid: float,
    ts: Optional[float] = None,
) -> None:
    ts = float(ts if ts is not None else time.time())
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO equity_snapshots(ts, cash, holdings_bid, holdings_mid, total_equity_bid, total_equity_mid)
            VALUES(?,?,?,?,?,?)
            """,
            (
                ts,
                float(cash),
                float(holdings_bid),
                float(holdings_mid),
                float(cash) + float(holdings_bid),
                float(cash) + float(holdings_mid),
            ),
        )
        # Cap history
        conn.execute(
            """
            DELETE FROM equity_snapshots WHERE id NOT IN (
                SELECT id FROM equity_snapshots ORDER BY id DESC LIMIT 2000
            )
            """
        )


def get_trades(limit: int = 500) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM trades ORDER BY ts DESC LIMIT ?", (limit,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["timestamp"] = d.pop("ts")
        d["id"] = d["id"]
        try:
            d["fill_detail"] = json.loads(d.get("fill_detail") or "{}")
        except Exception:
            d["fill_detail"] = {}
        out.append(d)
    return list(reversed(out))


def get_logs(limit: int = 500) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT ts, message FROM logs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [{"timestamp": int(r["ts"]), "message": r["message"]} for r in reversed(rows)]


def get_equity_history(limit: int = 500) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT ts, cash, holdings_bid, holdings_mid, total_equity_bid, total_equity_mid
        FROM equity_snapshots ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    hist = []
    for r in reversed(rows):
        hist.append(
            {
                "timestamp": int(r["ts"]),
                "cash": float(r["cash"]),
                "holdings_value": float(r["holdings_bid"]),
                "holdings_value_mid": float(r["holdings_mid"]),
                "total_equity": float(r["total_equity_bid"]),
                "total_equity_mid": float(r["total_equity_mid"]),
            }
        )
    return hist


def get_processed_keys(limit: int = 5000) -> List[str]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT idempotency_key FROM processed_keys ORDER BY ts DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [r["idempotency_key"] for r in rows]


def aggregate_positions_from_lots(
    marks: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Build token_id -> aggregated position dict from open lots.
    marks: optional {token_id: {bid, mid, current_price}}
    """
    marks = marks or {}
    positions: Dict[str, Dict[str, Any]] = {}
    for lot in get_all_open_lots():
        tid = lot["token_id"]
        rem = float(lot["remaining_qty"])
        if rem <= 1e-12:
            continue
        avg = float(lot["avg_price"])
        invested = float(lot["invested_usdc"])
        if tid not in positions:
            m = marks.get(tid, {})
            positions[tid] = {
                "token_id": tid,
                "condition_id": lot.get("condition_id") or "",
                "market_title": lot.get("market_title") or "",
                "market_slug": lot.get("market_slug") or "",
                "outcome": lot.get("outcome") or "",
                "outcome_index": lot.get("outcome_index") or 0,
                "avg_price": avg,
                "quantity": rem,
                "invested_usdc": invested,
                "current_price": m.get("mid", m.get("bid", avg)),
                "bid_price": m.get("bid", avg),
                "mid_price": m.get("mid", m.get("bid", avg)),
                "win_probability": lot.get("win_probability") or 0,
                "conviction_score": lot.get("conviction_score") or 0,
                "best_bet_score": lot.get("best_bet_score") or 0,
                "trader_address": lot.get("whale_address") or "",
                "trader_name": lot.get("whale_name") or "",
                "source_whales": {
                    lot.get("whale_address"): {
                        "name": lot.get("whale_name"),
                        "quantity": rem,
                        "invested_usdc": invested,
                    }
                },
                "lots": [lot["lot_id"]],
                "last_updated": int(time.time()),
                "opened_at": int(lot.get("opened_at") or time.time()),
                "strategy_exits_enabled": bool(lot.get("strategy_exits_enabled", 1)),
                "grandfathered": bool(lot.get("grandfathered", 0)),
            }
        else:
            pos = positions[tid]
            old_qty = float(pos["quantity"])
            new_qty = old_qty + rem
            if new_qty > 0:
                pos["avg_price"] = (pos["avg_price"] * old_qty + avg * rem) / new_qty
            pos["quantity"] = new_qty
            pos["invested_usdc"] = float(pos["invested_usdc"]) + invested
            # Weighted scores
            for key in ("win_probability", "conviction_score", "best_bet_score"):
                old_s = float(pos.get(key) or 0)
                new_s = float(lot.get(key) or 0)
                pos[key] = (old_s * old_qty + new_s * rem) / new_qty if new_qty else old_s
            addr = lot.get("whale_address") or ""
            sw = pos.setdefault("source_whales", {})
            if addr in sw:
                sw[addr]["quantity"] = float(sw[addr]["quantity"]) + rem
                sw[addr]["invested_usdc"] = float(sw[addr]["invested_usdc"]) + invested
            else:
                sw[addr] = {
                    "name": lot.get("whale_name"),
                    "quantity": rem,
                    "invested_usdc": invested,
                }
            pos["lots"].append(lot["lot_id"])
            pos["opened_at"] = min(int(pos.get("opened_at") or 0), int(lot.get("opened_at") or 0))
            # Aggregated position uses strategy exits only if all lots allow it
            if not lot.get("strategy_exits_enabled", 1):
                pos["strategy_exits_enabled"] = False
            if lot.get("grandfathered"):
                pos["grandfathered"] = True
            # Keep primary trader as largest source
            largest = max(sw.items(), key=lambda kv: kv[1]["quantity"])
            pos["trader_address"] = largest[0]
            pos["trader_name"] = largest[1].get("name") or pos.get("trader_name")
    return positions


def project_state(marks: Optional[Dict[str, Dict[str, float]]] = None) -> Dict[str, Any]:
    """Build the in-memory state dict used by the engine/dashboard."""
    cash = get_cash()
    positions = aggregate_positions_from_lots(marks)
    trades = get_trades(limit=2000)
    logs = get_logs(limit=500)
    history = get_equity_history(limit=500)
    whale_positions = get_whale_inventory()
    processed = get_processed_keys(limit=10000)

    # Backward-compatible processed_tx_hashes: extract tx hash segment when possible
    processed_tx_hashes = []
    for key in processed:
        parts = key.split("|")
        if len(parts) >= 2 and parts[1]:
            # source|tx|asset|side
            processed_tx_hashes.append(parts[1] if len(parts) > 2 else parts[0])
        else:
            processed_tx_hashes.append(key)
    # Also keep raw keys for new logic
    return {
        "cash_usdc": cash,
        "positions": positions,
        "trades": trades,
        "logs": logs,
        "portfolio_value_history": history,
        "whale_positions": whale_positions,
        "processed_tx_hashes": processed_tx_hashes,
        "processed_keys": processed,
        "ledger_backed": True,
        "lots_open": len(get_all_open_lots()),
    }


def migrate_from_json_state(state: Dict[str, Any], starting_capital: float = 10000.0) -> Dict[str, Any]:
    """
    Import historical JSON state into the ledger without erasing evidence.
    Positions without trade journal become grandfathered synthetic lots.
    """
    report = {
        "migrated_positions": 0,
        "migrated_trades": 0,
        "migrated_logs": 0,
        "migrated_processed": 0,
        "cash": 0.0,
        "warnings": [],
    }

    if is_initialized() and get_all_open_lots():
        report["warnings"].append("Ledger already has open lots; migration skipped to avoid double-import.")
        return report

    cash = float(state.get("cash_usdc", starting_capital))
    now = time.time()

    with transaction() as conn:
        # 1. Accounts/Metadata
        conn.execute(
            "INSERT INTO account(key, value) VALUES('cash_usdc', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(cash),),
        )
        conn.execute(
            "INSERT INTO account(key, value) VALUES('starting_capital', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(float(starting_capital)),),
        )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('initialized', '1') ON CONFLICT(key) DO UPDATE SET value = '1'"
        )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('migrated_from_json', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(time.time()),),
        )

        # 2. Append Migration Event
        event_payload = json.dumps({
            "source": "state.json",
            "cash": cash,
            "position_count": len(state.get("positions") or {}),
            "trade_count": len(state.get("trades") or []),
            "note": "JSON state imported; missing journal means positions are unaudited grandfathered lots.",
        })
        conn.execute(
            "INSERT INTO events(ts, event_type, source, idempotency_key, payload) VALUES(?, ?, ?, ?, ?)",
            (now, "migration", "migration", f"migration|json|{int(now)}", event_payload),
        )

        # 3. Trades
        for t in state.get("trades") or []:
            try:
                conn.execute(
                    """
                    INSERT INTO trades(
                        id, ts, trader_address, trader_name, original_trader_address, original_trader_name,
                        market_title, market_slug, outcome, type, quantity, price, usdc_size,
                        win_probability, conviction_score, best_bet_score, tx_hash, realized_pnl, fill_detail, strategy_version
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        t.get("id") or str(uuid.uuid4()),
                        float(t.get("timestamp") or now),
                        t.get("trader_address"),
                        t.get("trader_name"),
                        t.get("original_trader_address"),
                        t.get("original_trader_name"),
                        t.get("market_title"),
                        t.get("market_slug"),
                        t.get("outcome"),
                        t.get("type"),
                        float(t.get("quantity") or 0.0),
                        float(t.get("price") or 0.0),
                        float(t.get("usdc_size") or 0.0),
                        float(t.get("win_probability") or 0.0) if t.get("win_probability") is not None else None,
                        float(t.get("conviction_score") or 0.0) if t.get("conviction_score") is not None else None,
                        float(t.get("best_bet_score") or 0.0) if t.get("best_bet_score") is not None else None,
                        t.get("tx_hash"),
                        float(t.get("realized_pnl") or 0.0),
                        json.dumps(t.get("fill_detail") or {}),
                        t.get("strategy_version") or 3,
                    ),
                )
                report["migrated_trades"] += 1
            except Exception as e:
                report["warnings"].append(f"trade import failed: {e}")

        # 4. Positions -> Lots
        for token_id, pos in (state.get("positions") or {}).items():
            try:
                qty = float(pos.get("quantity") or 0)
                if qty <= 0:
                    continue
                whale = (pos.get("trader_address") or "unknown").lower()
                lot_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO lots(
                        lot_id, token_id, condition_id, whale_address, whale_name, market_title, market_slug, outcome, outcome_index,
                        quantity, remaining_qty, avg_price, invested_usdc, opened_at, closed_at, strategy_exits_enabled,
                        grandfathered, strategy_version, win_probability, conviction_score, best_bet_score, entry_event_id, metadata
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 1, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        lot_id,
                        token_id,
                        pos.get("condition_id") or "",
                        whale,
                        pos.get("trader_name") or "",
                        pos.get("market_title") or "",
                        pos.get("market_slug") or "",
                        pos.get("outcome") or "",
                        int(pos.get("outcome_index") or 0),
                        qty,
                        qty,
                        float(pos.get("avg_price") or 0.0),
                        float(pos.get("invested_usdc") or 0.0),
                        float(pos.get("opened_at") or now),
                        1 if pos.get("strategy_exits_enabled", False) else 0,
                        pos.get("strategy_version") or 3,
                        float(pos.get("win_probability") or 0.0) if pos.get("win_probability") is not None else None,
                        float(pos.get("conviction_score") or 0.0) if pos.get("conviction_score") is not None else None,
                        float(pos.get("best_bet_score") or 0.0) if pos.get("best_bet_score") is not None else None,
                        json.dumps({
                            "migrated_from_json": True,
                            "unaudited": True,
                            "original_position": pos,
                        }),
                    ),
                )
                report["migrated_positions"] += 1
            except Exception as e:
                report["warnings"].append(f"position {token_id[:12]}… import failed: {e}")

        # 5. Whale inventory
        for addr, tokens in (state.get("whale_positions") or {}).items():
            for tid, q in (tokens or {}).items():
                try:
                    conn.execute(
                        "INSERT INTO whale_inventory(whale_address, token_id, quantity) VALUES(?, ?, ?) ON CONFLICT(whale_address, token_id) DO UPDATE SET quantity = excluded.quantity",
                        (addr.lower(), tid, float(q)),
                    )
                except Exception:
                    pass

        # 6. Processed hashes
        for h in state.get("processed_tx_hashes") or []:
            try:
                key = make_idempotency_key("polymarket", str(h), "*", "*")
                conn.execute(
                    "INSERT INTO processed_keys(idempotency_key, ts, note) VALUES(?, ?, ?) ON CONFLICT(idempotency_key) DO NOTHING",
                    (key, now, "migrated_tx_hash"),
                )
                report["migrated_processed"] += 1
            except Exception:
                pass

        # 7. Logs
        for log in state.get("logs") or []:
            try:
                conn.execute(
                    "INSERT INTO logs(ts, message) VALUES(?, ?)",
                    (float(log.get("timestamp") or now), log.get("message") or ""),
                )
                report["migrated_logs"] += 1
            except Exception:
                pass

        # 8. Equity history
        for snap in state.get("portfolio_value_history") or []:
            try:
                cash_s = float(snap.get("cash") or cash)
                hv = float(snap.get("holdings_value") or 0)
                ts = float(snap.get("timestamp") or now)
                conn.execute(
                    """
                    INSERT INTO equity_snapshots(ts, cash, holdings_bid, holdings_mid, total_equity_bid, total_equity_mid)
                    VALUES(?,?,?,?,?,?)
                    """,
                    (ts, cash_s, hv, hv, cash_s + hv, cash_s + hv),
                )
            except Exception:
                pass

    # Direct log write after transaction commits successfully
    add_log(
        f"Migrated JSON state into ledger: {report['migrated_positions']} positions, "
        f"{report['migrated_trades']} trades, cash={cash:.2f}. "
        f"Open positions without trade journal are flagged unaudited/grandfathered."
    )

    if not state.get("trades"):
        report["warnings"].append(
            "Zero retained trades in source JSON — equity attribution for open positions cannot be audited."
        )

    return report


def archive_and_reset(starting_capital: float, confirm: bool = False) -> str:
    """
    Archive current DB then re-init. Requires confirm=True.
    Returns path to archive file.
    """
    if not confirm:
        raise ValueError("Reset refused: confirm=True required. Export/archive first.")

    with _lock:
        if hasattr(_local, "conn") and _local.conn is not None:
            try:
                _local.conn.close()
            except Exception:
                pass
            _local.conn = None

        _ensure_dir(_db_path)
        archive_path = ""
        if os.path.exists(_db_path):
            ts = time.strftime("%Y%m%d_%H%M%S")
            archive_path = f"{_db_path}.archive_{ts}"
            shutil.copy2(_db_path, archive_path)
            # Also copy WAL if present
            for suffix in ("-wal", "-shm"):
                side = _db_path + suffix
                if os.path.exists(side):
                    shutil.copy2(side, archive_path + suffix)
            os.remove(_db_path)
            for suffix in ("-wal", "-shm"):
                side = _db_path + suffix
                if os.path.exists(side):
                    os.remove(side)

        # Reopen fresh
        get_connection()
        init_account(starting_capital, note="reset")
        add_log(f"Simulation reset. Previous ledger archived to {archive_path or '(none)'}.")
        return archive_path


def reconcile_report() -> Dict[str, Any]:
    """Sanity checks between lots, cash, and trades."""
    cash = get_cash()
    lots = get_all_open_lots()
    invested = sum(float(l["invested_usdc"]) for l in lots)
    qty = sum(float(l["remaining_qty"]) for l in lots)
    trades = get_trades(limit=10000)
    buys = [t for t in trades if t.get("type") == "BUY"]
    sells = [t for t in trades if t.get("type") in ("SELL", "RESOLVE")]
    return {
        "cash_usdc": cash,
        "open_lots": len(lots),
        "open_quantity": qty,
        "invested_usdc": invested,
        "trade_count": len(trades),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "processed_keys": len(get_processed_keys(limit=100000)),
        "events": get_connection().execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"],
        "initialized": is_initialized(),
        "migrated_from_json": get_meta("migrated_from_json"),
    }


def copy_lot_qty_for_whale_sell(
    whale_address: str,
    token_id: str,
    whale_sell_qty: float,
    whale_holdings_before: float,
) -> float:
    """
    Map a whale's sell of `whale_sell_qty` (their shares) onto our copy lots.

    If we know whale holdings before the sell, sell the same fraction of our
    lots attributed to that whale. Otherwise sell min(our_lots, proportional guess).
    """
    our_lots = get_open_lots_for_whale_token(whale_address, token_id)
    our_qty = sum(float(l["remaining_qty"]) for l in our_lots)
    if our_qty <= 0:
        return 0.0
    whale_sell_qty = float(whale_sell_qty)
    if whale_sell_qty <= 0:
        return 0.0

    if whale_holdings_before > 1e-12:
        frac = min(1.0, whale_sell_qty / whale_holdings_before)
        return our_qty * frac
    # Unknown holdings: if sell size looks like full exit relative to our book, close all
    return our_qty
