"""Why did this payment happen?

That is the only question an audit trail exists to answer, and until now the
honest answer was "join two databases and hope". The ledger opened its own
connection -- and on `:memory:`, the default that every test, the whole
evaluation harness and the deployed instance ran on, its own DATABASE. A
journey that ended in DENY wrote its `decisions` row to one store and the
events explaining it to another, with no shared transaction and no ordering
between them.

These tests answer the question from the record alone, with no access to the
objects that produced it: open the database, read the rows, reconstruct the
decision.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from remit.assembly import build
from remit.domain.risk import Exposure
from remit.exec.razorpay import FakeGateway
from remit.ledger.events import (KINDS, MANDATORY_FOR_A_DECISION,
                                 missing_fields, unknown)

NOW = datetime(2026, 8, 22, 13, 0, tzinfo=timezone.utc)


@pytest.fixture
def app():
    return build(now=NOW, gateway=FakeGateway())


def run(app, utterance, user="usr_audit", **kw):
    return app.journey.run(utterance=utterance, user_id=user, now=NOW,
                           exposure=Exposure(), **kw)


def events(app, cid):
    return [dict(r) | {"payload": json.loads(r["payload"])}
            for r in app.db.execute(
                "SELECT * FROM events WHERE trace_id=? ORDER BY seq", (cid,))]


# ────────────────────────────────────────────────────────────── one store

def test_the_ledger_and_the_decisions_share_one_database(app):
    assert app.ledger.db is app.db, (
        "the audit chain is in a different database from the decisions it "
        "explains")


@pytest.mark.parametrize("utterance,kw", [
    ("buy running shoes under 5000", {"human_confirms": True}),   # bought
    ("buy whisky under 2000", {}),                                # asked
    ("buy headphones under $5000", {}),                           # refused
])
def test_a_journey_and_its_evidence_are_readable_together(app, utterance, kw):
    """The case that was broken: a refusal. Its decision row and its
    PAYMENT_BLOCKED event used to live in separate stores."""
    r = run(app, utterance, **kw)
    cid = r.correlation_id
    decision = app.db.execute(
        "SELECT * FROM decisions WHERE correlation_id=?", (cid,)).fetchone()
    trail = events(app, cid)
    assert decision is not None, "no decision row"
    assert trail, "no events"
    assert {e["kind"] for e in trail} >= set(MANDATORY_FOR_A_DECISION), \
        sorted({e["kind"] for e in trail})


def test_the_whole_story_reconstructs_from_the_database_alone(app):
    """No objects, no journey, no model. Rows.

    A reviewer asking "why did this payment happen" gets: the sentence, the
    envelope it compiled to, what was searched, what was picked and why, what
    the cart cost, how far it drifted, what it risked, which clauses passed,
    what the verdict was, and the order id.
    """
    r = run(app, "buy running shoes under 5000", human_confirms=True)
    db, cid = app.db, r.correlation_id

    trail = {e["kind"]: e["payload"] for e in events(app, cid)}
    decision = dict(db.execute(
        "SELECT * FROM decisions WHERE correlation_id=?", (cid,)).fetchone())

    # what the human asked
    assert trail["UTTERANCE"]["utterance"] == "buy running shoes under 5000"
    # what it compiled to -- and the envelope is stored in full, versioned
    env_row = db.execute(
        "SELECT envelope, version, reason FROM intent_versions"
        " WHERE intent_id=? ORDER BY version", (decision["intent_id"],)
    ).fetchall()
    assert env_row, "no envelope version was persisted"
    envelope = json.loads(env_row[0]["envelope"])
    assert envelope["utterance"] == "buy running shoes under 5000"
    assert envelope["max_price_paise"] or envelope["max_total_paise"]
    # what was chosen and why
    assert trail["PRODUCT_SELECTED"]["name"]
    assert trail["PRODUCT_SELECTED"]["why"]
    # what it cost
    assert trail["CART_PRICED"]["total_paise"] > 0
    # how it was judged -- every clause, by id, with its detail
    policy = json.loads(decision["policy"])
    assert policy["verdict"] in ("AUTO", "STEP_UP", "DENY")
    assert len(policy["clauses"]) >= 15
    assert all("clause_id" in c and "passed" in c for c in policy["clauses"])
    # the versions it was judged under
    assert decision["policy_version"] and decision["catalog_version"]
    # and what actually happened
    assert trail["PAYMENT_CREATED"]["order_id"] == r.order_id
    pay = db.execute("SELECT * FROM payments WHERE correlation_id=?",
                     (cid,)).fetchone()
    assert pay["state"] in ("CREATED", "AUTHORIZED", "SUCCESS")


def test_a_refusal_records_which_clause_and_what_it_saw(app):
    r = run(app, "buy headphones under $5000")
    decision = app.db.execute(
        "SELECT policy FROM decisions WHERE correlation_id=?",
        (r.correlation_id,)).fetchone()
    clauses = json.loads(decision["policy"])["clauses"]
    cur = next(c for c in clauses if c["clause_id"] == "CUR-001")
    assert cur["passed"] is False
    assert "USD" in cur["detail"], cur["detail"]


def test_the_authority_lifecycle_is_in_the_record(app):
    r = run(app, "buy whisky under 2000", human_confirms=True)
    hist = app.db.execute(
        "SELECT from_state, to_state, cause FROM authority_transitions"
        " WHERE intent_id=? ORDER BY seq", (r.intent.intent_id,)).fetchall()
    assert [h["to_state"] for h in hist] == [
        "DRAFT", "INTERPRETED", "GROUNDED", "PENDING_STEP_UP", "APPROVED",
        "EXECUTING", "EXECUTED"]
    assert all(h["cause"] for h in hist)


# ──────────────────────────────────────────────── the vocabulary is honest

def test_every_kind_the_code_emits_is_declared(app):
    """The vocabulary is enforced here rather than at write time. An audit log
    that can refuse a write is an audit log that can be made to forget, and the
    failure mode of a strict schema on that path is a missing record of exactly
    the thing somebody wanted to look at."""
    for u, kw in (("buy running shoes under 5000", {"human_confirms": True}),
                  ("buy whisky under 2000", {}),
                  ("buy headphones under $5000", {}),
                  ("buy a helicopter", {})):
        run(app, u, **kw)
    app.revocations.revoke(user_id="usr_audit", now=NOW, reason="done")
    app.ledger.append("AUTHORIZATION_REVOKED", "cor_x",
                      app.revocations.check(user_id="usr_audit").dict(), NOW)
    emitted = {r["kind"] for r in app.db.execute("SELECT DISTINCT kind FROM events")}
    assert unknown(emitted) == [], (
        f"undeclared event kinds: {unknown(emitted)} -- add them to "
        f"remit/ledger/events.py or stop emitting them")


def test_the_events_that_matter_carry_the_fields_that_matter(app):
    run(app, "buy running shoes under 5000", human_confirms=True)
    run(app, "buy headphones under $5000")
    bad = []
    for row in app.db.execute("SELECT kind, payload FROM events"):
        gaps = missing_fields(row["kind"], json.loads(row["payload"]))
        if gaps:
            bad.append((row["kind"], gaps))
    assert bad == [], bad


def test_the_chain_still_verifies_across_a_shared_connection(app):
    for i in range(6):
        run(app, f"buy running shoes under {5000 + i}", human_confirms=True)
    ok, first_bad = app.ledger.verify_chain()
    assert ok and first_bad is None


def test_tampering_is_still_detected(app):
    """The chain's actual guarantee, restated after the connection change:
    tamper evidence with a single writer. Not non-repudiation -- an operator
    who controls the whole chain can rewrite it from any point and re-link,
    which is why chain.py says so in its first paragraph rather than claiming
    otherwise."""
    run(app, "buy running shoes under 5000", human_confirms=True)
    seq = app.db.execute(
        "SELECT seq FROM events ORDER BY seq LIMIT 1 OFFSET 2").fetchone()["seq"]
    app.db.execute("UPDATE events SET payload=? WHERE seq=?",
                   ('{"tampered": true}', seq))
    ok, first_bad = app.ledger.verify_chain()
    assert ok is False
    assert first_bad == seq
