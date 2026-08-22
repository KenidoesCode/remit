"""/v1 -- the surface an agent that is not this website talks to.

Every route here is a projection over the same journey, the same policy engine
and the same payment path that `/api/shop` uses. That is the point and it is
the only thing that makes the protocol worth anything: if `/v1` had its own
code path, the guarantee a reviewer verified on the website would not be the
guarantee an integrator gets.

So there is no second engine, no second authorization check and no second set
of clauses. `/v1/execute` calls `journey.run`. The nine tests in
`tests/test_no_bypass.py` that walk the public surface and then ask the
database whether every payment has a decision behind it cover these routes for
free, because these routes ARE that surface.

IDENTITY
--------
Same session principal as everything else -- an httpOnly signed cookie, or the
`Authorization: Bearer <session>` header for a client that has no cookie jar.
There is deliberately no API key: a key is a bearer credential that would let a
caller choose whose limits to spend, which is the exact bug FAILURES #32 was
about. A production deployment binds this to the merchant's own identity
provider; that seam is `principal_from_upstream()` and it is named in the
audit rather than faked here.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .protocol import (PROTOCOL_VERSION, Action, Authority, Clause, Decision,
                       Evidence, Execution, Intent, Money)

v1 = APIRouter(prefix="/v1", tags=["protocol"])


class CreateIntent(BaseModel):
    utterance: str = Field(max_length=2000)


class Evaluate(BaseModel):
    """Evaluate without executing. The property line, as a contract.

    An agent that wants to know whether it MAY do something before doing it is
    the well-behaved case, and it should not have to risk a payment to find
    out.
    """
    intent_id: str | None = Field(default=None, max_length=64)
    utterance: str | None = Field(default=None, max_length=2000)
    ceiling_paise: int | None = None


class Execute(BaseModel):
    intent_id: str | None = Field(default=None, max_length=64)
    utterance: str | None = Field(default=None, max_length=2000)
    approval_token: str | None = Field(default=None, max_length=128)
    accept_offers: str = "in_envelope"


class Approve(BaseModel):
    intent_id: str | None = Field(default=None, max_length=64)
    utterance: str = Field(max_length=2000)
    approval_token: str = Field(max_length=128)


class Revoke(BaseModel):
    scope: str = "principal"
    intent_id: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=280)


def _money(paise, currency="INR"):
    return None if paise is None else Money(amount_paise=int(paise),
                                            currency=currency)


def _intent_of(env, tel, policy_version, catalog_version) -> Intent:
    return Intent(
        intent_id=env.intent_id, actor_id=env.user_id, utterance=env.utterance,
        semantic_hash=env.semantic_hash, category=env.category,
        requested=list(env.product_terms), excluded=list(env.excluded_attributes),
        quantity=env.quantity, ceiling=_money(env.ceiling_paise(), env.currency),
        objective=env.objective, merchants=list(env.merchant_constraints),
        created_at=env.created_at.isoformat(),
        expires_at=env.expires_at.isoformat(),
        policy_version=policy_version, catalog_version=catalog_version,
        interpreter=(tel or {}).get("compiler", "rule"),
        confidence=env.parse_confidence)


def _decision_of(r) -> Decision:
    a = r.authorization
    return Decision(
        verdict=a.verdict.value if a else "DENY",
        reason=r.note or (a.reason if a else "no decision"),
        clauses=[Clause(clause_id=c.clause_id, passed=c.passed, detail=c.detail)
                 for c in (a.clauses if a else [])],
        failed=list(a.failed) if a else [],
        drift=r.drift.score if r.drift else None,
        total=_money(r.totals.total_paise if r.totals else None),
        correlation_id=r.correlation_id,
        latency_ms=round(r.latency_ms, 3) if r.latency_ms else None)


def _execution_of(r, key_id: str | None) -> Execution:
    return Execution(
        correlation_id=r.correlation_id, payment_id=r.payment_id,
        order_id=r.order_id, state=r.payment_state,
        total=_money(r.totals.total_paise if r.totals else None),
        replayed=bool(r.replayed),
        checkout_key_id=key_id if r.order_id else None)


def _abstained(r):
    return JSONResponse(
        {"error": "not_grounded",
         "detail": r.note or "this catalog cannot answer that request",
         "correlation_id": r.correlation_id,
         "protocol_version": PROTOCOL_VERSION}, status_code=422)


def _no_decision(r):
    """No cart, therefore no decision -- and the protocol must not invent one.

    `_decision_of` defaulted a missing authorization to DENY, which reads as
    "the policy engine refused this" when what actually happened is that the
    policy engine was never asked: nothing in the catalog answered the request
    at the price the human named. Those are different sentences and an
    integrator building a retry policy on top needs the right one. Caught by
    the test that asserts /v1 and the website agree on every verdict, on
    "buy chips under 20" -- a request this shop can answer and not that
    cheaply.
    """
    return JSONResponse(
        {"error": "no_decision",
         "detail": r.note or "nothing in this catalog satisfied the request",
         "reason": ("the policy engine was never reached: there was no cart "
                    "to decide about"),
         "correlation_id": r.correlation_id,
         "protocol_version": PROTOCOL_VERSION}, status_code=422)


def _revoked_decision(r):
    """A revocation IS a decision, and a refusal, and it must not be reported
    as "no decision was reached".

    It is also not a clause: the journey stops before the policy engine is
    asked, so claiming AUTH-003 fired would be inventing evidence. Verdict
    DENY, reason in the human's words, `clauses` empty, and the revocation
    record attached so a client can show who cancelled it and when.
    """
    rv = (r.telemetry or {}).get("revocation")
    if rv is None:
        return None
    return {
        "decision": Decision(
            verdict="DENY", reason=r.note or "authority revoked",
            clauses=[], failed=["REVOKED"], correlation_id=r.correlation_id,
            latency_ms=round(r.latency_ms, 3) if r.latency_ms else None
        ).model_dump(),
        "execution": Execution(correlation_id=r.correlation_id,
                               state="BLOCKED").model_dump(),
        "revocation": rv,
        "authority_state": "REVOKED",
        "protocol_version": PROTOCOL_VERSION,
    }


def _undecided(r):
    return r.authorization is None or r.cart is None


def install(api, *, get_app, principal, LOCK, utcnow, exposure_for, key_id):
    """Wired from remit/api.py so there is exactly one app, one lock and one
    set of helpers. Passing them in rather than importing avoids a cycle and,
    more usefully, makes it impossible for this module to acquire its own."""

    # One line per route. A route with no entry here is a route this index
    # cannot describe, and test_protocol.py fails rather than quietly omitting
    # it -- which is exactly how /v1/step-up went unadvertised.
    DESCRIBED = {
        "GET /v1/": "this description",
        "POST /v1/intents": "compile an utterance into a bounded authority",
        "POST /v1/evaluate": "would this be allowed? no money moves",
        "POST /v1/execute": "do it, if the policy allows",
        "POST /v1/step-up": "ask the human, returning a token bound to one basket",
        "POST /v1/approve": "redeem a step-up token bound to one basket",
        "POST /v1/deny": "decline a step-up",
        "POST /v1/revoke": "cancel an authority, forward only",
        "GET /v1/authorization/{intent_id}": "current authority state",
        "GET /v1/audit/{correlation_id}": "why this happened",
    }

    def _served() -> list[str]:
        """Every route this router actually serves, as "METHOD /path"."""
        out = []
        for r in v1.routes:
            for m in sorted(getattr(r, "methods", set()) - {"HEAD", "OPTIONS"}):
                out.append(f"{m} {r.path}")
        return sorted(out)

    def _routes() -> dict[str, str]:
        return {k: DESCRIBED.get(k, "(undescribed -- see test_protocol.py)")
                for k in _served()}

    @v1.get("/")
    def describe():
        """What this is, in one response, so an integrator can start without
        reading a document."""
        return {
            "protocol": "remit", "version": PROTOCOL_VERSION,
            "thesis": ("an agent may interpret; a deterministic policy engine "
                       "authorises; the payment rail executes"),
            "nouns": ["intent", "authority", "action", "decision", "evidence",
                      "execution"],
            # Derived from the router, not listed by hand. The hand-written
            # list had gone stale: it advertised eight routes while ten were
            # served, and POST /v1/step-up -- the one an integrator most needs
            # to know about, because it is how a DENY becomes a purchase --
            # was the one missing. An agent reading this index could not have
            # discovered it. Same lesson as FAILURES #49: derive it.
            "routes": _routes(),
            "identity": ("session cookie, or Authorization: Bearer <session>. "
                         "No API key: a bearer key would let a caller choose "
                         "whose limits to spend"),
            "notes": ["Razorpay test mode", "synthetic catalog",
                      "SQLite on one host; multi-process correctness is "
                      "tested, multi-host is not"],
        }

    @v1.post("/intents")
    def create_intent(req: CreateIntent, request: Request):
        with LOCK:
            a = get_app()
            who = principal(request)
            now = utcnow()
            env, tel = a.journey.compiler.compile(req.utterance, who, now)
            if env is None:
                return JSONResponse(
                    {"error": "not_grounded",
                     "detail": (tel or {}).get("reason", "could not ground"),
                     "stocked_hint": (tel or {}).get("unstocked", []),
                     "protocol_version": PROTOCOL_VERSION}, status_code=422)
            a.journey._persist_intent(env, now, "created via /v1/intents") \
                if hasattr(a.journey, "_persist_intent") else None
            if a.authority is not None:
                a.authority.open(intent_id=env.intent_id, user_id=who, now=now)
            return {
                "intent": _intent_of(env, tel, a.policy.version,
                                     a.catalog.version()).model_dump(),
                "authority": Authority(
                    intent_id=env.intent_id, actor_id=who,
                    state=(a.authority.state(env.intent_id)
                           if a.authority else "INTERPRETED"),
                    ceiling=_money(env.ceiling_paise(), env.currency),
                    expires_at=env.expires_at.isoformat(),
                    revoked=a.revocations.is_revoked(user_id=who)
                    if a.revocations else False).model_dump(),
                "protocol_version": PROTOCOL_VERSION,
            }

    @v1.post("/evaluate")
    def evaluate(req: Evaluate, request: Request):
        """No money moves. Runs on a throwaway instance for exactly the reason
        /api/replay was fixed: an endpoint that says "this is only a question"
        must not be able to answer it by doing the thing."""
        if not req.utterance:
            return JSONResponse({"error": "utterance is required to evaluate"},
                                status_code=400)
        with LOCK:
            from .assembly import build
            from .exec.razorpay import FakeGateway
            now = utcnow()
            sub = build(now=now, gateway=FakeGateway())
            who = principal(request)
            r = sub.journey.run(utterance=req.utterance, user_id=who, now=now,
                                exposure=exposure_for(get_app(), who),
                                accept_offers="in_envelope")
            if r.intent is None:
                return _abstained(r)
            revoked = _revoked_decision(r)
            if revoked is not None:
                return revoked
            if _undecided(r):
                return _no_decision(r)
            d = _decision_of(r).model_dump()
            d["would_execute"] = r.authorization is not None and \
                r.authorization.verdict.value == "AUTO"
            d["sandboxed"] = True
            d["intent"] = _intent_of(r.intent, r.telemetry, sub.policy.version,
                                     sub.catalog.version()).model_dump()
            return d

    @v1.post("/execute")
    def execute(req: Execute, request: Request):
        if not req.utterance:
            return JSONResponse(
                {"error": "utterance is required",
                 "detail": "an authority is bound to the words that created "
                           "it; executing against an id alone would let a "
                           "caller reuse somebody's mandate for a different "
                           "request"}, status_code=400)
        with LOCK:
            a = get_app()
            who = principal(request)
            now = utcnow()
            r = a.journey.run(utterance=req.utterance, user_id=who, now=now,
                              exposure=exposure_for(a, who),
                              accept_offers=req.accept_offers,
                              human_confirms=True if req.approval_token else None,
                              approval_token=req.approval_token)
            if r.intent is None:
                return _abstained(r)
            revoked = _revoked_decision(r)
            if revoked is not None:
                return revoked
            if _undecided(r):
                return _no_decision(r)
            return {
                "decision": _decision_of(r).model_dump(),
                "execution": _execution_of(r, key_id()).model_dump(),
                "approval": r.approval,
                "authority_state": (a.authority.state(r.intent.intent_id)
                                    if a.authority else None),
                "protocol_version": PROTOCOL_VERSION,
            }

    @v1.post("/step-up")
    def step_up(req: Evaluate, request: Request):
        """What is being asked of the human, in the shape a client can render
        without knowing anything about baskets."""
        if not req.utterance:
            return JSONResponse({"error": "utterance is required"},
                                status_code=400)
        with LOCK:
            a = get_app()
            who = principal(request)
            now = utcnow()
            r = a.journey.run(utterance=req.utterance, user_id=who, now=now,
                              exposure=exposure_for(a, who))
            if r.intent is None:
                return _abstained(r)
            revoked = _revoked_decision(r)
            if revoked is not None:
                return revoked
            if _undecided(r):
                return _no_decision(r)
            if r.payment_state != "AWAITING_HUMAN":
                return {"required": False,
                        "decision": _decision_of(r).model_dump(),
                        "protocol_version": PROTOCOL_VERSION}
            failed = [c for c in r.authorization.clauses if not c.passed]
            return {
                "required": True,
                "asking": {
                    "why": (failed[0].detail if failed
                            else r.authorization.reason),
                    "clause": failed[0].clause_id if failed else None,
                    "amount": _money(r.totals.total_paise).model_dump(),
                    "items": [{"name": l.name, "qty": l.qty,
                               "unit_price_paise": l.unit_price_paise}
                              for l in r.cart.lines],
                },
                "approval": r.approval,
                "decision": _decision_of(r).model_dump(),
                "protocol_version": PROTOCOL_VERSION,
            }

    @v1.post("/approve")
    def approve(req: Approve, request: Request):
        with LOCK:
            a = get_app()
            who = principal(request)
            now = utcnow()
            r = a.journey.run(utterance=req.utterance, user_id=who, now=now,
                              exposure=exposure_for(a, who), human_confirms=True,
                              approval_token=req.approval_token)
            if r.intent is None:
                return _abstained(r)
            revoked = _revoked_decision(r)
            if revoked is not None:
                return revoked
            if _undecided(r):
                return _no_decision(r)
            return {"decision": _decision_of(r).model_dump(),
                    "execution": _execution_of(r, key_id()).model_dump(),
                    "protocol_version": PROTOCOL_VERSION}

    @v1.post("/deny")
    def deny(req: Approve, request: Request):
        with LOCK:
            a = get_app()
            who = principal(request)
            r = a.journey.run(utterance=req.utterance, user_id=who,
                              now=utcnow(), exposure=exposure_for(a, who),
                              human_confirms=False)
            if r.intent is None:
                return _abstained(r)
            revoked = _revoked_decision(r)
            if revoked is not None:
                return revoked
            if _undecided(r):
                return _no_decision(r)
            return {"decision": _decision_of(r).model_dump(),
                    "authority_state": (a.authority.state(r.intent.intent_id)
                                        if a.authority else None),
                    "protocol_version": PROTOCOL_VERSION}

    @v1.post("/revoke")
    def revoke_v1(req: Revoke, request: Request):
        from .grants.revocation import NoSuchIntent, NotYours
        with LOCK:
            a = get_app()
            who = principal(request)
            try:
                rv = a.revocations.revoke(
                    user_id=who, now=utcnow(), scope=req.scope,
                    target=req.intent_id if req.scope == "intent" else who,
                    revoked_by=who, reason=req.reason)
            except (NoSuchIntent, NotYours):
                return JSONResponse({"error": "no such authorization"},
                                    status_code=404)
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            a.ledger.append("AUTHORIZATION_REVOKED", who, rv.dict(), utcnow())
            return rv.dict() | {"protocol_version": PROTOCOL_VERSION}

    @v1.get("/authorization/{intent_id}")
    def authorization(intent_id: str, request: Request):
        with LOCK:
            a = get_app()
            who = principal(request)
            row = a.db.execute(
                "SELECT i.user_id, i.revoked_at, iv.envelope FROM intents i"
                " JOIN intent_versions iv ON iv.intent_id=i.intent_id"
                "   AND iv.version=i.current_version"
                " WHERE i.intent_id=?", (intent_id,)).fetchone()
            # 404 rather than 403 for somebody else's: whether an id exists is
            # not a thing this endpoint should confirm.
            if row is None or row["user_id"] != who:
                return JSONResponse({"error": "no such authorization"},
                                    status_code=404)
            import json as _json
            from .domain.intent import IntentEnvelope
            env = IntentEnvelope(**_json.loads(row["envelope"]))
            rv = a.revocations.check(user_id=who, intent_id=intent_id) \
                if a.revocations else None
            return {
                "authority": Authority(
                    intent_id=intent_id, actor_id=who,
                    state=(a.authority.state(intent_id) if a.authority
                           else "UNKNOWN"),
                    ceiling=_money(env.ceiling_paise(), env.currency),
                    expires_at=env.expires_at.isoformat(),
                    revoked=rv is not None,
                    revoked_at=rv.revoked_at if rv else None,
                    version=env.version).model_dump(),
                "history": (a.authority.history(intent_id) if a.authority
                            else []),
                "protocol_version": PROTOCOL_VERSION,
            }

    @v1.get("/audit/{correlation_id}")
    def audit(correlation_id: str, request: Request):
        """Why this happened, from the record. Scoped to the caller, because an
        audit trail carries the sentence somebody typed."""
        import json as _json
        with LOCK:
            a = get_app()
            who = principal(request)
            decision = a.db.execute(
                "SELECT d.*, i.user_id FROM decisions d"
                " JOIN intents i ON i.intent_id = d.intent_id"
                " WHERE d.correlation_id=?", (correlation_id,)).fetchone()
            if decision is None or decision["user_id"] != who:
                return JSONResponse({"error": "no such correlation id"},
                                    status_code=404)
            events = [{"seq": r["seq"], "ts": r["ts"], "kind": r["kind"],
                       "payload": _json.loads(r["payload"]), "hash": r["hash"]}
                      for r in a.db.execute(
                          "SELECT * FROM events WHERE trace_id=? ORDER BY seq",
                          (correlation_id,))]
            ok, bad = a.ledger.verify_chain()
            return Evidence(
                correlation_id=correlation_id, intent_id=decision["intent_id"],
                events=events, decision=_json.loads(decision["policy"]),
                authority_history=(a.authority.history(decision["intent_id"])
                                   if a.authority else []),
                chain_intact=ok, first_bad_seq=bad).model_dump() | {
                "protocol_version": PROTOCOL_VERSION}

    api.include_router(v1)
    return v1
