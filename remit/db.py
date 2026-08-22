"""SQLite schema. One file, inspectable, no ops cost.

Tables exist because a query needs them, not because a diagram had a box.
Money is always paise (INTEGER). Timestamps are ISO-8601 UTC strings.
"""
from __future__ import annotations

import sqlite3
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
    # check_same_thread=False because FastAPI runs sync endpoints in a
    # threadpool. Safe here only because every write goes through the single
    # lock in remit/api.py -- SQLite itself is not the serialisation point.
    db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    _migrate(db)
    return db


def _migrate(db: sqlite3.Connection) -> None:
    """Add columns that a database created by an older build will not have.

    Render keeps the SQLite file across deploys, so a schema change that only
    exists in CREATE TABLE reaches a fresh container and never reaches the
    running one. Cheap, idempotent, and it runs on every connect."""
    for table, column, decl in (
            ("payments", "user_id", "TEXT NOT NULL DEFAULT ''"),
            ("payments", "correlation_id", "TEXT"),
            ("products", "restricted", "TEXT"),
    ):
        have = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
