"""SQLite schema. One file, inspectable, no ops cost.

Tables exist because a query needs them, not because a diagram had a box.
Money is always paise (INTEGER). Timestamps are ISO-8601 UTC strings.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

-- ---------- catalog ----------
CREATE TABLE IF NOT EXISTS merchants (
  merchant_id TEXT PRIMARY KEY, name TEXT NOT NULL, rating REAL,
  free_ship_over_paise INTEGER NOT NULL DEFAULT 0,
  base_ship_paise INTEGER NOT NULL DEFAULT 0, risk_tier TEXT NOT NULL DEFAULT 'low');

CREATE TABLE IF NOT EXISTS catalog_versions (
  version INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, note TEXT);

CREATE TABLE IF NOT EXISTS products (
  product_id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL REFERENCES merchants,
  name TEXT NOT NULL, category TEXT NOT NULL, subcategory TEXT,
  price_paise INTEGER NOT NULL, mrp_paise INTEGER NOT NULL,
  margin_bps INTEGER NOT NULL,        -- merchant gross margin, basis points
  rating REAL NOT NULL, reviews INTEGER NOT NULL,
  inventory INTEGER NOT NULL, attributes TEXT NOT NULL,  -- json list
  premium INTEGER NOT NULL DEFAULT 0, ship_days INTEGER NOT NULL DEFAULT 3,
  -- NULL for ordinary goods. 'age' and 'pharmacy' mark things an autonomous
  -- agent must never buy on its own, whatever the amount. See RESTRICT-001.
  restricted TEXT,
  catalog_version INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS idx_products_cat ON products(category, active);

-- directed: buying `product_id` makes `related_id` relevant
CREATE TABLE IF NOT EXISTS relations (
  product_id TEXT NOT NULL REFERENCES products, related_id TEXT NOT NULL REFERENCES products,
  kind TEXT NOT NULL,                 -- 'upsell' | 'cross_sell'
  reason TEXT NOT NULL, strength REAL NOT NULL,
  PRIMARY KEY (product_id, related_id, kind));

-- ---------- intent ----------
CREATE TABLE IF NOT EXISTS intents (
  intent_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL,
  current_version INTEGER NOT NULL, revoked_at TEXT);

CREATE TABLE IF NOT EXISTS intent_versions (
  intent_id TEXT NOT NULL REFERENCES intents, version INTEGER NOT NULL,
  envelope TEXT NOT NULL,             -- json IntentEnvelope
  created_at TEXT NOT NULL, reason TEXT NOT NULL, envelope_hash TEXT NOT NULL,
  PRIMARY KEY (intent_id, version));

CREATE TABLE IF NOT EXISTS intent_graph_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, intent_id TEXT NOT NULL,
  correlation_id TEXT NOT NULL, node TEXT NOT NULL, parent_node TEXT,
  ts TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_graph_intent ON intent_graph_events(intent_id);

-- ---------- cart / payment ----------
CREATE TABLE IF NOT EXISTS carts (
  cart_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL, intent_version INTEGER NOT NULL,
  catalog_version INTEGER NOT NULL, created_at TEXT NOT NULL, state TEXT NOT NULL,
  items TEXT NOT NULL, totals TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS payments (
  payment_id TEXT PRIMARY KEY, cart_id TEXT NOT NULL, intent_id TEXT NOT NULL,
  idem_key TEXT NOT NULL UNIQUE, amount_paise INTEGER NOT NULL,
  state TEXT NOT NULL, order_id TEXT, correlation_id TEXT,
  user_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  unknown_since TEXT);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_payments_corr ON payments(correlation_id);

CREATE TABLE IF NOT EXISTS payment_transitions (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, payment_id TEXT NOT NULL REFERENCES payments,
  from_state TEXT NOT NULL, to_state TEXT NOT NULL, ts TEXT NOT NULL, cause TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS webhook_events (
  event_id TEXT PRIMARY KEY, payment_id TEXT, kind TEXT NOT NULL, ts TEXT NOT NULL,
  received_at TEXT NOT NULL, body TEXT NOT NULL, signature_ok INTEGER NOT NULL,
  applied INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '');

-- ---------- decisions ----------
CREATE TABLE IF NOT EXISTS decisions (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, correlation_id TEXT NOT NULL,
  intent_id TEXT NOT NULL, cart_id TEXT, ts TEXT NOT NULL,
  drift TEXT NOT NULL, risk TEXT NOT NULL, policy TEXT NOT NULL,
  verdict TEXT NOT NULL, policy_version TEXT NOT NULL, catalog_version INTEGER NOT NULL);

-- ---------- experiments / eval ----------
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, kind TEXT NOT NULL, arm TEXT NOT NULL,
  started_at TEXT NOT NULL, config TEXT NOT NULL, summary TEXT);

CREATE TABLE IF NOT EXISTS run_cases (
  run_id TEXT NOT NULL REFERENCES runs, case_id TEXT NOT NULL, split TEXT NOT NULL,
  bucket TEXT NOT NULL, expected TEXT NOT NULL, actual TEXT NOT NULL,
  passed INTEGER NOT NULL, revenue_paise INTEGER NOT NULL DEFAULT 0,
  margin_paise INTEGER NOT NULL DEFAULT 0, verdict TEXT NOT NULL DEFAULT '',
  latency_ms REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, case_id));
"""


def connect(path: str | Path = "remit.sqlite") -> sqlite3.Connection:
    """A connection that is correct with other PROCESSES, not just other threads.

    This used to say: "safe here only because every write goes through the
    single lock in remit/api.py -- SQLite itself is not the serialisation
    point." That was an accurate description of a process-local guarantee, and
    a process-local guarantee is not a guarantee. A second worker, a second
    container, or a cron job running the reconciler would each hold their own
    lock and none of them would hold each other's.

    Three settings turn that around, and they are the whole change:

    · `journal_mode=WAL` -- readers do not block the writer and the writer does
      not block readers. Already here.

    · `busy_timeout` -- without it, a second process that finds the database
      locked gets `database is locked` IMMEDIATELY and the request fails. With
      it, that process waits. This was absent, which is why nothing had ever
      been observed to contend: the failure mode was an exception, not a
      corruption, and nothing was running two processes to see it.

    · `synchronous=FULL` -- the default `NORMAL` in WAL mode can lose the last
      committed transactions on power loss. For a payment row that is written
      BEFORE the gateway is called, losing the last commit means losing the
      record of a payment that may exist. The cost is fsync latency on write,
      which at this volume is a decision worth making in the safe direction.

    The lock in api.py stays. It is now belt over braces rather than the only
    thing holding the trousers up: the real serialisation points are the UNIQUE
    index on `payments.idem_key`, the predicated UPDATE on approvals, the
    predicated UPDATE on the authority machine, and `BEGIN IMMEDIATE` for the
    read-then-write sequences that could not be expressed as one statement.
    """
    # check_same_thread=False because FastAPI runs sync endpoints in a
    # threadpool.
    db = sqlite3.connect(str(path), isolation_level=None,
                         check_same_thread=False, timeout=30.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    # 30 seconds. Long enough that a contended writer waits rather than failing,
    # short enough that a genuinely stuck writer is still an incident.
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(SCHEMA)
    _migrate(db)
    return db


@contextmanager
def writing(db: sqlite3.Connection):
    """A write transaction that takes the lock at BEGIN, not at first write.

    `BEGIN` in SQLite is deferred: the transaction becomes a writer only when
    it first writes, and if another process wrote in between, the deferred
    transaction fails with SQLITE_BUSY *and cannot be upgraded*. That turns a
    read-then-write sequence into a race that fails at the worst moment --
    after the read, having already decided.

    `BEGIN IMMEDIATE` takes the write lock up front, so a second process waits
    at the door rather than getting halfway in. Everything that reads a value
    and then writes based on it belongs in here.

    Reentrant: nested use joins the outer transaction rather than starting a
    second one, because the alternative is that a helper called from inside a
    transaction silently commits half of it.
    """
    if db.in_transaction:
        yield db
        return
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except BaseException:
        db.execute("ROLLBACK")
        raise
    else:
        db.execute("COMMIT")


TENANT_COLUMNS = {
    # table -> the column that carries the tenant. Every one of these is on the
    # money path or the evidence path; nothing that belongs to somebody is
    # allowed to be tenant-less.
    "intents": "tenant_id",
    "payments": "tenant_id",
    "decisions": "tenant_id",
    "carts": "tenant_id",
    "events": "tenant_id",
    "approvals": "tenant_id",
    "revocations": "tenant_id",
    "authority_state": "tenant_id",
}


def _migrate(db: sqlite3.Connection) -> None:
    """Add columns that a database created by an older build will not have.

    Render keeps the SQLite file across deploys, so a schema change that only
    exists in CREATE TABLE reaches a fresh container and never reaches the
    running one. Cheap, idempotent, and it runs on every connect.

    IDEMPOTENT ACROSS PROCESSES, not just across calls. This is a
    read-then-write -- check `PRAGMA table_info`, then `ALTER TABLE` -- and
    three workers booting at the same moment all read "column absent" and all
    three ALTER. Two of them get `duplicate column name` and the process dies
    during startup.

    Exactly the same shape as the payment race, the chain race and the
    authority race, in the least interesting place in the system, and it only
    appeared once real processes started at the same instant. The transaction
    is the fix; the tolerated exception below is the belt, for a migration that
    lands between another process's BEGIN and this one's.
    """
    with writing(db):
        _migrate_locked(db)


def _add_column(db, table: str, column: str, decl: str) -> None:
    have = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
    if not have or column in have:
        return
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    except sqlite3.OperationalError as e:
        # Another process won the race between the transaction and here. The
        # column exists, which is the outcome we wanted; anything else is real.
        if "duplicate column name" not in str(e):
            raise


def _migrate_locked(db: sqlite3.Connection) -> None:
    for table, column, decl in (
            ("payments", "user_id", "TEXT NOT NULL DEFAULT ''"),
            ("payments", "correlation_id", "TEXT"),
            ("products", "restricted", "TEXT"),
    ):
        _add_column(db, table, column, decl)

    # Tenancy, added the same way and for the same reason: these tables exist
    # in databases created before tenants did. The default is the single-tenant
    # value rather than NULL -- a NULL tenant is a row that belongs to nobody,
    # and a row that belongs to nobody is a row every query has to special-case
    # forever. Some of these tables are created lazily by their own stores, so
    # a missing table here is expected, not an error.
    for table, column in TENANT_COLUMNS.items():
        try:
            have = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
        except sqlite3.DatabaseError:
            continue
        if not have:                      # table not created yet
            continue
        if column not in have:
            _add_column(db, table, column,
                        "TEXT NOT NULL DEFAULT 'tnt_default'")
            db.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_tenant"
                       f" ON {table}({column})")
