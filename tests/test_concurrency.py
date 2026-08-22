"""Two agents at once.

`remit/grants/approval.py` says single use is enforced by a predicated UPDATE
"so that two browser tabs racing the same token cannot both win". The idempotency
key says the UNIQUE constraint is the serialisation point. `remit/lab/attacks.py`
advertises a retry storm as "six identical journeys at once".

None of that was ever executed concurrently. The retry storm is a `for` loop.
The approval race is two sequential calls. Every claim about what happens under
contention was a claim about what the code looks like.

So this file actually issues simultaneous requests, from real threads, against
one instance, and counts what came out the other side. It is slower than the
rest of the suite and it is worth it: a serialisation point that has never been
contended is a serialisation point nobody has tested.

The threading model under test is the deployed one -- a single process, one
RLock, SQLite in WAL. That is honestly what REMIT is today, and
docs/FINAL_AUDIT.md section F says so. A second process would not share the
lock; the UNIQUE constraints would still hold and the exposure read would race.
That gap is named there rather than papered over here.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import remit.api as api_mod
from remit.api import api

BUYS = "buy a yoga mat under 2500"
ASKS = "buy whisky under 2000"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REMIT_DB", str(tmp_path / "race.sqlite"))
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_forthetests")
    api_mod.STATE.pop("app", None)
    with TestClient(api) as boot:
        boot.get("/health")
        yield
    api_mod.STATE.pop("app", None)


def _storm(fn, n):
    with ThreadPoolExecutor(max_workers=n) as pool:
        return [f.result() for f in [pool.submit(fn, i) for i in range(n)]]


def _paid(user=None):
    q = "SELECT payment_id, idem_key, amount_paise, user_id FROM payments"
    rows = api_mod.get_app().db.execute(q).fetchall()
    return [r for r in rows if user is None or r["user_id"] == user]


# ───────────────────────────────────────────────────────── one effect

@pytest.mark.parametrize("n", [10, 40])
def test_identical_requests_in_parallel_produce_one_payment(env, n):
    """The retry storm, actually stormed.

    One browser, one sentence, N simultaneous sends -- a double-tapped button,
    a chat UI that resends, an agent with a retry policy and no jitter. The
    idempotency key is H(user | semantic hash | cart | total | catalog version)
    and the UNIQUE constraint on it is where the race is decided, so N-1 of
    these must come back as replays of the same payment.
    """
    with TestClient(api) as c:
        c.post("/api/shop", json={"utterance": "hello"})       # settle a cookie
        out = _storm(lambda _: c.post("/api/shop", json={"utterance": BUYS}).json(), n)

    created = [d for d in out if d.get("payment_state") == "CREATED"]
    assert len(created) == n, [d.get("payment_state") for d in out][:5]
    ids = {d["payment_id"] for d in created}
    assert len(ids) == 1, f"{len(ids)} distinct payments from one sentence"
    assert sum(1 for d in created if d.get("replayed")) == n - 1
    rows = _paid()
    assert len(rows) == 1, f"{len(rows)} payment rows"
    assert len({r["idem_key"] for r in rows}) == 1


@pytest.mark.parametrize("n", [8, 32])
def test_one_approval_redeemed_in_parallel_is_spent_once(env, n):
    """N tabs, one token, one basket. The predicated UPDATE decides it.

    This is the property the whole approval design rests on, and until now the
    only evidence for it was a comment.
    """
    with TestClient(api) as c:
        step = c.post("/api/shop", json={"utterance": ASKS}).json()
        assert step["payment_state"] == "AWAITING_HUMAN"
        token = step["approval"]["token"]
        out = _storm(lambda _: c.post("/api/shop", json={
            "utterance": ASKS, "human_confirms": True,
            "approval_token": token}).json(), n)

    ok = [d for d in out if d["payment_state"] == "CREATED"]
    rejected = [d for d in out if d["payment_state"] == "APPROVAL_REJECTED"]
    assert len(ok) == 1, f"{len(ok)} redemptions of a single-use approval"
    assert len(rejected) == n - 1
    assert all("already_used" in d["note"] for d in rejected)
    assert len(_paid()) == 1


def test_two_agents_racing_one_mandate_cannot_both_spend(env):
    """The brief's example: authority 5,000, two agents, 4,000 each.

    Both journeys are legitimate on their own. Only one may complete, and the
    total that moves may not exceed what the human authorised.

    REMIT's answer here is the aggregate exposure clause, not the per-cart
    ceiling: MANDATE-001 sums what this principal has already spent under this
    mandate and adds the cart in hand. Whichever request loses the race reads a
    total that already includes the winner's payment.
    """
    with TestClient(api) as c:
        c.post("/api/shop", json={"utterance": "hello"})
        u = "buy a yoga mat under 5000"
        out = _storm(lambda _: c.post("/api/shop", json={
            "utterance": u, "human_confirms": True}).json(), 2)

    rows = _paid()
    moved = sum(r["amount_paise"] for r in rows)
    distinct = {r["payment_id"] for r in rows}
    assert len(distinct) <= 1, (
        f"two agents under one mandate created {len(distinct)} payments "
        f"totalling {moved} paise")
    assert moved <= 500000, f"{moved} paise moved against a 5,000 rupee mandate"
    assert all(d["payment_state"] in ("CREATED", "BLOCKED", "AWAITING_HUMAN")
               for d in out), [d["payment_state"] for d in out]


def test_parallel_journeys_by_different_people_do_not_collide(env):
    """The other half. Serialisation must not become a shared fate: two
    unrelated visitors buying at the same moment are two purchases, and one
    person's idempotency namespace must not swallow the other's."""
    clients = [TestClient(api) for _ in range(6)]
    for c in clients:
        c.__enter__()
        c.post("/api/shop", json={"utterance": "hello"})
    try:
        out = _storm(lambda i: clients[i].post(
            "/api/shop", json={"utterance": BUYS}).json(), len(clients))
    finally:
        for c in clients:
            c.__exit__(None, None, None)

    assert all(d["payment_state"] == "CREATED" for d in out), \
        [d["payment_state"] for d in out]
    assert len({d["payment_id"] for d in out}) == len(clients), \
        "two people shared one payment"
    rows = _paid()
    assert len({r["user_id"] for r in rows}) == len(clients)
    assert len({r["idem_key"] for r in rows}) == len(clients)


def test_duplicate_webhooks_in_parallel_apply_once(env):
    """Razorpay retries a webhook it did not hear an answer to, and the retry
    can overtake the original. The dedupe is a PRIMARY KEY on event_id, which
    is the right kind of defence precisely because it does not depend on the
    order they arrive in -- and this endpoint used to be the one writer that
    ran outside the lock."""
    import hashlib
    import hmac
    import json as _json

    with TestClient(api) as c:
        d = c.post("/api/shop", json={"utterance": BUYS, "human_confirms": True}).json()
        assert d["payment_state"] == "CREATED", d
        a = api_mod.get_app()
        secret = a.webhooks.secret
        body = _json.dumps({
            "event": "payment.captured",
            "payload": {"payment": {"entity": {
                "id": "pay_race_1", "order_id": d["order_id"],
                "amount": d["totals"]["total_paise"], "status": "captured"}}},
        }).encode()
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        out = _storm(lambda _: c.post(
            "/api/webhook", content=body,
            headers={"x-razorpay-signature": sig,
                     "content-type": "application/json"}).json(), 12)

    applied = [o for o in out if o.get("applied")]
    assert len(applied) <= 1, f"{len(applied)} webhooks applied the same event"
    rows = api_mod.get_app().db.execute(
        "SELECT COUNT(*) c FROM webhook_events").fetchone()["c"]
    assert rows == 1, f"{rows} webhook_events rows for one event id"
    assert len(_paid()) == 1


def test_many_threads_sharing_one_connection_never_collide_on_begin():
    """FAILURES #50 -- the regression for a race inside the race-fixer.

    `writing()` used to be `if db.in_transaction: ... else: BEGIN IMMEDIATE`,
    which is check-then-act across two calls on a connection several threads
    share. Two threads read False, both BEGIN, and the loser raises
    OperationalError: cannot start a transaction within a transaction.

    It surfaced as an attack that passed twice and failed once -- found by
    eval/attacks.py, not by this file, because the existing thread tests went
    through the API's global lock and the process tests used one connection
    each. Neither had two threads inside writing() on the SAME connection.
    """
    import sqlite3
    import threading

    from remit.db import writing

    db = sqlite3.connect(":memory:", isolation_level=None,
                         check_same_thread=False)
    db.execute("CREATE TABLE t (k INTEGER PRIMARY KEY, n INTEGER)")
    db.execute("INSERT INTO t (k, n) VALUES (1, 0)")

    N = 32
    ready = threading.Barrier(N)
    errors: list[BaseException] = []

    def bump():
        try:
            ready.wait(timeout=10)
            for _ in range(20):
                # read-then-write: the whole reason writing() exists
                with writing(db):
                    cur = db.execute("SELECT n FROM t WHERE k=1").fetchone()[0]
                    db.execute("UPDATE t SET n=? WHERE k=1", (cur + 1,))
        except BaseException as e:            # noqa: BLE001 - recorded, then asserted
            errors.append(e)

    threads = [threading.Thread(target=bump) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"threads raised: {errors[:3]}"
    # and the lost-update check, which is the point of holding the lock for the
    # whole transaction rather than only across BEGIN
    assert db.execute("SELECT n FROM t WHERE k=1").fetchone()[0] == N * 20
