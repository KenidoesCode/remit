"""Separate processes. Not threads, not a loop, not one interpreter.

Everything REMIT claimed about concurrency was true of one process. The lock in
`api.py` is a `threading.RLock`, and a `threading.RLock` in process A means
nothing whatsoever to process B. So the honest description of the guarantee was
"correct as long as there is exactly one worker" -- which is a deployment
constraint disguised as a property.

These tests fork real OS processes with `multiprocessing`, point them at one
SQLite file, and let them fight. Nothing is shared: not the lock, not the
connection, not the catalog object, not the interpreter. The only thing they
have in common is the database, which is exactly the situation a second gunicorn
worker creates.

WHAT MAKES IT WORK, AND WHAT DOES NOT
-------------------------------------
Not the lock. The lock is irrelevant here and these tests would pass if it were
deleted. What works is what was always doing the work:

    UNIQUE(payments.idem_key)                 one payment per meaning
    UPDATE approvals ... WHERE used_at IS NULL one redemption per token
    UPDATE authority_state ... WHERE state=?   one transition per edge
    UNIQUE(revocations.scope, target, user_id) one kill switch per authority
    PRIMARY KEY(webhook_events.event_id)       one effect per gateway event

plus `busy_timeout` so a contended process waits instead of failing, and
`BEGIN IMMEDIATE` so a read-then-write cannot be interleaved.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import sqlite3
from datetime import datetime, timezone

import pytest

NOW_ISO = "2026-08-22T18:00:00+00:00"
BUYS = "buy running shoes under 5000"
ASKS = "buy whisky under 2000"

# fork is the default on Linux and is what a preforking web server does.
CTX = mp.get_context("fork")


# ── worker bodies. Module level so they are picklable. ──────────────────────

def _boot(path):
    """A completely fresh App in this process. New connection, new catalog,
    new everything -- the way a second worker starts."""
    from remit.assembly import build
    from remit.exec.razorpay import FakeGateway
    now = datetime.fromisoformat(NOW_ISO)
    return build(db_path=path, now=now, gateway=FakeGateway()), now


def w_buy(args):
    path, user, utterance = args
    app, now = _boot(path)
    from remit.domain.risk import Exposure
    try:
        r = app.journey.run(utterance=utterance, user_id=user, now=now,
                            exposure=Exposure(), human_confirms=True)
        return {"pid": os.getpid(), "payment_id": r.payment_id,
                "state": r.payment_state, "replayed": bool(r.replayed),
                "order": r.order_id}
    except Exception as e:                       # a crash is a result too
        return {"pid": os.getpid(), "error": f"{type(e).__name__}: {e}"}


def w_redeem(args):
    path, user, token = args
    app, now = _boot(path)
    from remit.domain.risk import Exposure
    try:
        r = app.journey.run(utterance=ASKS, user_id=user, now=now,
                            exposure=Exposure(), human_confirms=True,
                            approval_token=token)
        return {"state": r.payment_state, "note": (r.note or "")[:60]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def w_revoke(args):
    path, user = args
    app, now = _boot(path)
    try:
        rv = app.revocations.revoke(user_id=user, now=now, reason="mp")
        return {"revocation_id": rv.revocation_id, "already": rv.already}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def w_step_up(args):
    path, user = args
    app, now = _boot(path)
    from remit.domain.risk import Exposure
    r = app.journey.run(utterance=ASKS, user_id=user, now=now,
                        exposure=Exposure())
    return {"token": (r.approval or {}).get("token"), "state": r.payment_state}


# ── helpers ────────────────────────────────────────────────────────────────

@pytest.fixture
def path(tmp_path):
    p = str(tmp_path / "mp.sqlite")
    # Seed once in the parent so every child finds the same catalog version --
    # which is itself the property FAILURES #45 was about.
    app, _ = _boot(p)
    del app
    return p


def fan(fn, args, n):
    with CTX.Pool(processes=min(n, 8)) as pool:
        return pool.map(fn, [args] * n if not isinstance(args, list) else args)


def rows(path, sql, args=()):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute(sql, args)]
    finally:
        db.close()


# ── one authorization, one financial effect ────────────────────────────────

@pytest.mark.parametrize("n", [3, 12])
def test_n_processes_one_request_one_payment(path, n):
    """The brief's invariant, across real processes.

    Every child computes the idempotency key independently, from its own
    catalog, in its own interpreter. If any input to that key were
    process-local -- as `catalog_version` was until FAILURES #45 -- this test
    produces n payments and says so.
    """
    out = fan(w_buy, (path, "usr_mp", BUYS), n)
    errs = [o for o in out if "error" in o]
    assert not errs, errs[:3]

    paid = rows(path, "SELECT payment_id, idem_key, amount_paise FROM payments")
    assert len(paid) == 1, (
        f"{len(paid)} payment rows from {n} processes running one request")
    assert len({o["payment_id"] for o in out}) == 1
    assert sum(1 for o in out if o["replayed"]) == n - 1


def test_processes_that_start_at_different_times_agree(path):
    """A worker that boots later must reach the same key. This is the restart
    bug (FAILURES #45) in its multi-process form: it is not about time, it is
    about whether anything in the key is derived from process state."""
    first = w_buy((path, "usr_stagger", BUYS))
    later = fan(w_buy, (path, "usr_stagger", BUYS), 4)
    assert all(o.get("payment_id") == first["payment_id"] for o in later), later
    assert len(rows(path, "SELECT 1 FROM payments")) == 1


def test_three_processes_racing_one_approval_redeem_once(path):
    """32 threads already proved the predicated UPDATE holds inside one
    interpreter. Three processes prove it holds without one."""
    step = w_step_up((path, "usr_apr"))
    assert step["state"] == "AWAITING_HUMAN", step
    out = fan(w_redeem, (path, "usr_apr", step["token"]), 3)

    created = [o for o in out if o.get("state") == "CREATED"]
    rejected = [o for o in out if o.get("state") == "APPROVAL_REJECTED"]
    assert len(created) == 1, out
    assert len(rejected) == 2, out
    assert all("already_used" in o["note"] for o in rejected)
    used = rows(path, "SELECT used_at FROM approvals WHERE used_at IS NOT NULL")
    assert len(used) == 1


def test_processes_racing_the_kill_switch_produce_one_revocation(path):
    out = fan(w_revoke, (path, "usr_kill"), 6)
    errs = [o for o in out if "error" in o]
    assert not errs, errs[:2]
    ids = {o["revocation_id"] for o in out}
    assert len(ids) == 1, ids
    assert sum(1 for o in out if not o["already"]) == 1
    assert len(rows(path, "SELECT 1 FROM revocations")) == 1


def test_a_revoked_authority_is_revoked_in_every_process(path):
    """The kill switch has to reach processes that were already running. It
    does, because the check reads the database rather than a cached flag."""
    w_buy((path, "usr_reach", BUYS))
    w_revoke((path, "usr_reach"))
    out = fan(w_buy, (path, "usr_reach", "buy chips under 200"), 4)
    assert all(o.get("state") == "BLOCKED" for o in out), out
    paid = rows(path, "SELECT user_id FROM payments WHERE user_id='usr_reach'")
    assert len(paid) == 1, "a process spent after the authority was revoked"


def test_different_people_in_different_processes_do_not_collide(path):
    """Serialisation must not become shared fate. Six processes, six people,
    six purchases, six idempotency namespaces."""
    args = [(path, f"usr_mp_{i}", BUYS) for i in range(6)]
    with CTX.Pool(processes=6) as pool:
        out = pool.map(w_buy, args)
    assert all(o.get("state") == "CREATED" for o in out), out
    paid = rows(path, "SELECT user_id, idem_key FROM payments")
    assert len({r["user_id"] for r in paid}) == 6
    assert len({r["idem_key"] for r in paid}) == 6


def test_the_audit_chain_survives_concurrent_writers(path):
    """The chain is a linked list built by reading the head and appending. Two
    processes appending at once is the classic way to fork one."""
    args = [(path, f"usr_chain_{i}", BUYS) for i in range(6)]
    with CTX.Pool(processes=6) as pool:
        pool.map(w_buy, args)

    from remit.ledger.chain import Ledger
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        ledger = Ledger(conn=db)
        ok, bad = ledger.verify_chain()
    finally:
        db.close()
    assert ok, f"the hash chain forked at seq {bad} under concurrent writers"


# ── what the guarantee actually rests on ───────────────────────────────────

def test_the_database_is_configured_for_more_than_one_process(path):
    """`busy_timeout` is the difference between "a second worker waits" and "a
    second worker gets `database is locked` and the request fails". It was
    absent, and nothing had noticed because nothing was running two workers."""
    db = sqlite3.connect(path)
    try:
        from remit.db import connect
        conn = connect(path)
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        # FULL, not NORMAL: a payment row is written before the gateway is
        # called, so losing the last commit means losing the record of a
        # payment that may exist.
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        conn.close()
    finally:
        db.close()


def test_the_guarantee_does_not_depend_on_the_process_lock(path):
    """Stated as a test because it is the thing most likely to be assumed.

    These processes never share `remit.api.LOCK` -- each has its own. The
    constraints below are what decide every race in this file, and they are in
    the schema rather than in Python.
    """
    schema = "\n".join(
        r["sql"] or "" for r in rows(
            path, "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
    assert "idem_key TEXT NOT NULL UNIQUE" in schema
    assert "event_id TEXT PRIMARY KEY" in schema
    assert "token TEXT PRIMARY KEY" in schema
    assert "idx_revocation_target" in schema

    import inspect

    import remit.exec.payments as pay
    import remit.grants.approval as apr
    import remit.domain.authority as auth
    assert "WHERE token=? AND used_at IS NULL" in inspect.getsource(apr)
    assert "WHERE intent_id=? AND state=?" in inspect.getsource(auth)
    assert "with writing(self.db)" in inspect.getsource(pay)
