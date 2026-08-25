"""The Authorization Receipt: one view of a decision, assembled from records
that already exist.

This module writes nothing and decides nothing. It is a projection. Every field
it returns is read from a table another part of REMIT already populated -- the
`decisions` row the policy engine wrote, the `payments` row the executor wrote,
the intent envelope the compiler wrote, the `events` chain the ledger wrote, the
authority state machine's own history. There is no second source of truth here,
and there is deliberately no way for this file to make a receipt say something
the underlying records do not.

Why it exists: the evidence was always present, but spread across five reads.
A reviewer asking "what was authorised, what happened, and can I check it"
should not have to join five endpoints in their head. This does the join, and
names where each field came from so the join can be audited too.

Verification is NOT done here. The receipt reports the chain's own
`chain_intact` result, but the honest check -- recomputing every hash from the
raw bytes -- is the client's job, done by `receipts.verify()` in the SDK and by
`remit receipt verify` in the CLI. A receipt that graded its own homework would
be exactly the anti-pattern the rest of this project argues against.
"""
from __future__ import annotations

import json
from typing import Any

from .domain.intent import IntentEnvelope


def _money(paise: int | None, currency: str = "INR") -> dict | None:
    if paise is None:
        return None
    sign = "-" if paise < 0 else ""
    whole = abs(paise) // 100
    frac = abs(paise) % 100
    symbol = "₹" if currency == "INR" else ""
    return {"paise": paise, "display": f"{sign}{symbol}{whole:,}.{frac:02d}"}


def build_receipt(app: Any, correlation_id: str, principal: str) -> dict | None:
    """Assemble the receipt for one correlation id, scoped to `principal`.

    Returns None when there is no such decision for this principal -- the caller
    turns that into a 404. Scoping is not decoration: an audit trail carries the
    sentence somebody typed, and that sentence belongs to whoever typed it.
    """
    db = app.db

    decision_row = db.execute(
        "SELECT d.*, i.user_id, i.revoked_at AS intent_revoked_at"
        " FROM decisions d JOIN intents i ON i.intent_id = d.intent_id"
        " WHERE d.correlation_id = ?", (correlation_id,)).fetchone()
    if decision_row is None or decision_row["user_id"] != principal:
        return None

    intent_id = decision_row["intent_id"]
    policy = json.loads(decision_row["policy"])

    # The authority envelope, as it was at the decisive version.
    env = None
    ev_row = db.execute(
        "SELECT iv.envelope FROM intents i"
        " JOIN intent_versions iv ON iv.intent_id = i.intent_id"
        "   AND iv.version = i.current_version"
        " WHERE i.intent_id = ?", (intent_id,)).fetchone()
    if ev_row is not None:
        try:
            env = IntentEnvelope(**json.loads(ev_row["envelope"]))
        except Exception:
            env = None

    # What actually happened at the rail. A DENY or STEP_UP has no payment row,
    # and that absence is itself part of the receipt: nothing moved.
    payment = db.execute(
        "SELECT * FROM payments WHERE correlation_id = ?"
        " ORDER BY created_at DESC LIMIT 1", (correlation_id,)).fetchone()

    # The hash-linked events for this trace, and the chain's own view of itself.
    events = [
        {"seq": r["seq"], "ts": r["ts"], "kind": r["kind"],
         "trace_id": r["trace_id"], "payload": json.loads(r["payload"]),
         "prev_hash": r["prev_hash"], "hash": r["hash"]}
        for r in db.execute(
            "SELECT * FROM events WHERE trace_id = ? ORDER BY seq",
            (correlation_id,))]
    chain_ok, first_bad = app.ledger.verify_chain()

    # Authority state and revocation, from the machine that owns them.
    authority_state = (app.authority.state(intent_id)
                       if getattr(app, "authority", None) else None)
    authority_history = (app.authority.history(intent_id)
                         if getattr(app, "authority", None) else [])
    revocation = None
    if getattr(app, "revocations", None):
        rv = app.revocations.check(user_id=principal, intent_id=intent_id)
        if rv is not None:
            revocation = {"revoked": True, "revoked_at": rv.revoked_at,
                          "scope": getattr(rv, "scope", None)}
    if revocation is None:
        revocation = {"revoked": bool(decision_row["intent_revoked_at"]),
                      "revoked_at": decision_row["intent_revoked_at"],
                      "scope": None}

    verdict = policy.get("verdict") or decision_row["verdict"]
    executed = bool(payment and payment["order_id"])

    # A stable id for the receipt itself. It is derived, not stored: the same
    # decision always yields the same receipt id, and it is not a new key that
    # anything else in the system depends on. "rcpt_" + the correlation id it
    # projects, so the id says what it is a receipt OF.
    receipt_id = f"rcpt_{correlation_id}"

    return {
        "receipt_id": receipt_id,
        "correlation_id": correlation_id,
        "intent_id": intent_id,
        "principal": principal,
        "tenant_id": (decision_row["tenant_id"]
                      if "tenant_id" in decision_row.keys() else None),

        "intent": {
            "text": env.utterance if env else None,
            "category": env.category if env else None,
        },
        "authority": {
            "ceiling": _money(env.ceiling_paise(), env.currency) if env else None,
            "currency": env.currency if env else "INR",
            "category": env.category if env else None,
            "expires_at": env.expires_at.isoformat() if env else None,
            "state": authority_state,
            "history": authority_history,
        },
        "decision": {
            "verdict": verdict,
            "reason": policy.get("reason"),
            "failed_clauses": policy.get("failed", []),
            "clauses": policy.get("clauses", []),
            "policy_version": policy.get("policy_version")
                              or decision_row["policy_version"],
            "catalog_version": decision_row["catalog_version"],
            "requires_human": verdict == "STEP_UP",
            "blocked_value": _money(policy.get("blocked_value_paise") or 0,
                                    env.currency if env else "INR"),
            "at": decision_row["ts"],
        },
        "execution": {
            # The honest three-way state. AUTO that ran has an order; STEP_UP
            # and DENY have no payment row at all, and "money moved: no" is the
            # field a reviewer of a failure case is actually looking for.
            "money_moved": executed,
            "state": payment["state"] if payment else "NOT_EXECUTED",
            "order_id": payment["order_id"] if payment else None,
            "amount": (_money(payment["amount_paise"],
                              env.currency if env else "INR")
                       if payment else None),
            "mode": "razorpay_test" if executed else None,
            "at": payment["updated_at"] if payment else None,
        },
        "revocation": revocation,
        "audit": {
            "event_count": len(events),
            "chain_intact": chain_ok,
            "first_bad_seq": first_bad,
            "events": events,
            # How to check this receipt without trusting it. Named here so the
            # instruction travels with the object.
            "verify": {
                "cli": f"remit receipt verify {correlation_id}",
                "how": ("recompute sha256(prev_hash + canonical({kind, "
                        "trace_id, ts, payload})) for each event and compare"),
            },
        },
        # The receipt reports what the chain says about itself. It does not
        # claim the receipt is verified -- that word is reserved for a client
        # that recomputed the hashes.
        "self_reported_chain": "intact" if chain_ok else "BROKEN",
    }


def render_text(receipt: dict) -> str:
    """A concise, human-readable receipt. Same data as the dict, nothing added."""
    d = receipt["decision"]
    ex = receipt["execution"]
    au = receipt["authority"]
    lines = []
    add = lines.append

    add("-" * 40)
    add("REMIT AUTHORIZATION RECEIPT")
    add("-" * 40)
    add("")
    add("Intent:")
    add(f'  "{receipt["intent"]["text"] or "—"}"')
    add("")
    add("Authority:")
    if au["ceiling"]:
        add(f"  {au['ceiling']['display']} maximum")
    if au["category"]:
        add(f"  {au['category']} category")
    add(f"  Principal: {receipt['principal']}")
    add(f"  State: {au['state'] or '—'}")
    add("")
    add(f"Decision:  {d['verdict']}")
    if d["reason"]:
        add(f"  {d['reason']}")
    if d["failed_clauses"]:
        add(f"  Failed clauses: {', '.join(d['failed_clauses'])}")
    if d["requires_human"]:
        add("  A human must decide — an agent may not approve this.")
    add("")
    add("Execution:")
    if ex["money_moved"]:
        add(f"  Razorpay test-mode order {ex['order_id']}")
        if ex["amount"]:
            add(f"  {ex['amount']['display']}")
    else:
        add(f"  No payment executed ({ex['state']}). No money moved.")
    add("")
    add(f"Authority:  {au['state'] or '—'}"
        f"{'  (REVOKED)' if receipt['revocation']['revoked'] else ''}")
    add(f"Audit:      chain {receipt['self_reported_chain']}"
        f"  ({receipt['audit']['event_count']} events)")
    add(f"Verify:     {receipt['audit']['verify']['cli']}")
    add("-" * 40)
    return "\n".join(lines)
