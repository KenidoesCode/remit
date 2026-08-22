"""Append-only, hash-chained event log in SQLite.

A hash chain gives tamper-EVIDENCE with a single writer. It does not give
non-repudiation -- an operator who controls the whole chain can rewrite it
from any point and re-link. Fixing that needs an external witness, which is
listed as a known limitation rather than pretended away.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from ..models import canonical, sha

GENESIS = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT NOT NULL,
  kind       TEXT NOT NULL,
  trace_id   TEXT NOT NULL,
  payload    TEXT NOT NULL,
  prev_hash  TEXT NOT NULL,
  hash       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id);

-- The claim table IS the idempotency mechanism. The UNIQUE constraint is
-- the serialisation point; check-then-act in application code is a race.
CREATE TABLE IF NOT EXISTS claims (
  idem_key   TEXT PRIMARY KEY,
  trace_id   TEXT NOT NULL,
  claimed_at TEXT NOT NULL,
  result     TEXT
);
"""


class Ledger:
    """The audit chain. Shares the application's connection by default.

    It used to open its own. Against a real file that meant two connections
    with two independent transactions; against ``:memory:`` -- the default, and
    therefore what every test, the whole evaluation harness and the deployed
    instance ran on -- it meant a **completely separate database**.

    The consequence was not theoretical. A journey that ended in DENY wrote its
    `decisions` row to one store and its `PAYMENT_BLOCKED`, `UTTERANCE`,
    `PRODUCT_SEARCH` and `STEP_UP_REQUIRED` events to another. "Reconstruct why
    this was refused" required joining across two databases that shared no
    transaction, no ordering guarantee and, in the default configuration, no
    file. A tamper-evident chain of events that cannot be tied atomically to
    the decision it explains is a chain of events.

    Passing the connection in fixes it by deletion rather than by machinery:
    one database, one connection, one lock, and an event and the decision it
    describes are written inside the same serialised section. The alternative
    was a two-phase commit between two SQLite files, which is distributed
    systems complexity bought to solve a problem created by a default argument.

    `path` is kept for the standalone case -- a script that wants a chain and
    nothing else -- and it is no longer how the application builds one.
    """

    def __init__(self, path: str | Path = ":memory:", conn=None):
        if conn is not None:
            self.db = conn
            self.owns_connection = False
        else:
            self.db = sqlite3.connect(str(path), isolation_level=None,
                                      check_same_thread=False)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.owns_connection = True
        self.db.executescript(SCHEMA)

    def head(self) -> str:
        row = self.db.execute(
            "SELECT hash FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS

    def append(self, kind: str, trace_id: str, payload: dict, ts: datetime) -> str:
        prev = self.head()
        body = canonical({"kind": kind, "trace_id": trace_id,
                          "ts": ts.isoformat(), "payload": payload})
        h = sha(prev + body)
        self.db.execute(
            "INSERT INTO events (ts, kind, trace_id, payload, prev_hash, hash)"
            " VALUES (?,?,?,?,?,?)",
            (ts.isoformat(), kind, trace_id, canonical(payload), prev, h),
        )
        return h

    def verify_chain(self) -> tuple[bool, int | None]:
        """Returns (ok, first_bad_seq)."""
        prev = GENESIS
        for seq, ts, kind, trace_id, payload, prev_hash, h in self.db.execute(
            "SELECT seq, ts, kind, trace_id, payload, prev_hash, hash"
            " FROM events ORDER BY seq"
        ):
            if prev_hash != prev:
                return False, seq
            import json
            body = canonical({"kind": kind, "trace_id": trace_id, "ts": ts,
                              "payload": json.loads(payload)})
            if sha(prev + body) != h:
                return False, seq
            prev = h
        return True, None

    def trace(self, trace_id: str) -> list[tuple]:
        return list(self.db.execute(
            "SELECT seq, ts, kind, payload, hash FROM events"
            " WHERE trace_id=? ORDER BY seq", (trace_id,)))

    # --- idempotency -------------------------------------------------
    def claim(self, idem_key: str, trace_id: str, ts: datetime) -> bool:
        """True if this caller won the claim. False means someone already
        executed this exact intent -- read their result, do not re-execute."""
        try:
            self.db.execute(
                "INSERT INTO claims (idem_key, trace_id, claimed_at)"
                " VALUES (?,?,?)", (idem_key, trace_id, ts.isoformat()))
            return True
        except sqlite3.IntegrityError:
            return False

    def record_result(self, idem_key: str, result: str) -> None:
        self.db.execute("UPDATE claims SET result=? WHERE idem_key=?",
                        (result, idem_key))

    def result_for(self, idem_key: str) -> str | None:
        row = self.db.execute(
            "SELECT result FROM claims WHERE idem_key=?", (idem_key,)).fetchone()
        return row[0] if row else None
