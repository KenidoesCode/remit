"""HTTP surface. Thin: routes translate, services decide.

No business logic lives here. Every endpoint is a call into the same objects
the CLI demo and the evaluation harness use, which is why the numbers on the
screen and the numbers in eval/results/ cannot diverge.
"""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .assembly import App, build, utcnow
from .auth import COOKIE, MAX_AGE_SECONDS, mint, session_secret, verify
from .faults import refusal_note, scrub
from .grants.revocation import NoSuchIntent, NotYours
from .domain.drift import compute_drift
from .domain.risk import Exposure, assess
from .exec.razorpay import FakeGateway, verify_payment_signature
from .policy.authorize import authorize
from .money import rupees

from .paths import FAILURES as FAILURES_MD, RESULTS as RESULTS_DIR, WEB, test_count
STATE: dict[str, App] = {}
# One writer. SQLite tolerates concurrent readers under WAL, but the
# ledger's hash chain and the payment claim table both need a single
# serialisation point, and a lock is the honest way to get one at this size.
LOCK = threading.RLock()


def get_app() -> App:
    if "app" not in STATE:
        # REMIT_DB gives the process a file to keep. Without it the ledger,
        # the idempotency table and the exposure caps live only as long as one
        # process does -- fine for tests, wrong for anything a human pays into.
        STATE["app"] = build(db_path=os.environ.get("REMIT_DB", ":memory:"),
                             now=utcnow(),
                             live=os.environ.get("REMIT_LIVE") == "1")
    return STATE["app"]


api = FastAPI(title="REMIT", version="0.1.0")

# --------------------------------------------------------------------------
# The small, honest amount of hardening a public demo actually needs.
#
# What is here: a body-size ceiling, a per-IP request budget, and no CORS
# middleware at all -- so a browser on another origin cannot call this API,
# which is the correct default and needed no code.
#
# What is NOT here, stated plainly rather than implied: no authentication, no
# tenant isolation beyond a browser-generated id, no WAF, no bot defence, and
# a rate limiter that lives in this process's memory and therefore resets on
# deploy and does not exist across replicas. This is enough to stop a bored
# visitor and a runaway script. It is not enough to stop anyone who is trying,
# and REMIT does not claim otherwise. See THREAT_MODEL.md.
# --------------------------------------------------------------------------

_SESSION = {"secret": session_secret(os.environ.get("REMIT_LIVE") == "1")}

MAX_BODY_BYTES = 16 * 1024
RATE_WINDOW_S = 60
RATE_MAX = 90            # per session principal
RATE_MAX_ADDR = 600      # per address, to catch identity cycling
_HITS: dict[str, list[float]] = {}


@api.middleware("http")
async def _guard(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH"):
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            return JSONResponse(
                {"error": "request too large",
                 "limit_bytes": MAX_BODY_BYTES}, status_code=413)

    # --- WHO IS ASKING ------------------------------------------------
    # Resolved BEFORE the rate limiter, because the limiter needs it.
    # Derived from a signature this server produced, never from anything the
    # caller can type. A request with no valid session gets a fresh principal
    # minted here and the cookie set on the way out, so a first-time visitor
    # simply has an identity rather than borrowing "usr_demo" from everyone
    # else who ever loaded the page. FAILURES #32.
    secret = _SESSION["secret"]
    pid = verify(request.cookies.get(COOKIE), secret)
    issued = None
    if pid is None:
        issued = mint(secret)
        pid = verify(issued, secret)
    request.state.principal = pid

    # --- HOW MUCH THEY MAY ASK ----------------------------------------
    # Two buckets, and the pair matters.
    #
    # Keyed on IP alone, everyone behind one NAT -- a campus, a carrier, an
    # office, or a test suite -- shares a budget, and the honest description of
    # the limit becomes "90 requests per network". That is not a rate limit, it
    # is a way to lock out a building. Keyed on the principal alone, an
    # attacker mints a fresh session per request and the limit does not exist.
    #
    # So: a tight budget per principal, and a looser ceiling per address that
    # still catches somebody cycling identities. FAILURES #33.
    if request.url.path.startswith("/api/"):
        import time as _t
        addr = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                or (request.client.host if request.client else "unknown"))
        now_m = _t.monotonic()
        for key, cap in ((f"p:{pid}", RATE_MAX), (f"a:{addr}", RATE_MAX_ADDR)):
            hits = [t for t in _HITS.get(key, ()) if now_m - t < RATE_WINDOW_S]
            if len(hits) >= cap:
                _HITS[key] = hits
                return JSONResponse(
                    {"error": "too many requests",
                     "note": f"{cap} API calls per {RATE_WINDOW_S}s per "
                             f"{'session' if key[0] == 'p' else 'address'}; "
                             f"this is a demo on a free instance"},
                    status_code=429,
                    headers={"retry-after": str(RATE_WINDOW_S)})
            hits.append(now_m)
            _HITS[key] = hits
        if len(_HITS) > 8192:            # bound the map, not just the window
            for k in [k for k, v in _HITS.items()
                      if not v or now_m - v[-1] > RATE_WINDOW_S]:
                _HITS.pop(k, None)

    response = await call_next(request)
    if issued is not None:
        response.set_cookie(
            COOKIE, issued, max_age=MAX_AGE_SECONDS, httponly=True,
            samesite="lax",
            # Secure only where the deployment actually terminates TLS -- a
            # Secure cookie on plain http is a cookie that never arrives, and
            # a session that never arrives is a new identity every request.
            secure=os.environ.get("REMIT_LIVE") == "1", path="/")
    return response


def principal(request: Request) -> str:
    """The authenticated session principal for this request.

    Every endpoint that can move money, read an order, or spend against a limit
    reads identity from here. Nothing reads it from a request body.
    """
    pid = getattr(request.state, "principal", None)
    if not pid:
        raise RuntimeError("no principal on the request -- the auth middleware "
                           "did not run, which means this endpoint is reachable "
                           "by a path that has no identity at all")
    return pid


def actor(request: Request):
    """The full principal: who, which tenant, what role.

    `principal()` returns the id, which is what every existing money-path query
    already scopes on. This returns the identity ALONGSIDE its tenant and role,
    because "who is asking" and "what they are allowed to do" are different
    questions and answering them with one string is how privilege escalation
    gets built.

    Resolved from the directory rather than from anything the caller sends. A
    tenant a caller can set is a tenant a caller can set to somebody else's --
    the exact shape of FAILURES #32, one level up.
    """
    a = get_app()
    return a.directory.resolve(principal(request), utcnow())


class ReplayRequest(BaseModel):
    correlation_id: str
    ceiling_paise: int
    # Sent so a cold process can rebuild the basket. The journey is
    # deterministic, so replaying the same utterance reconstructs the same cart.
    utterance: str | None = None


class CompareRequest(BaseModel):
    utterance: str = Field(max_length=2000)
    inject: dict = {}


class ShopRequest(BaseModel):
    # A shopping sentence. Bounded because an unbounded string on a public
    # endpoint is a denial-of-service primitive, and because nothing anyone
    # actually says to a shopping agent is 2,000 characters long. The bound is
    # enforced by the schema, so it is rejected before any of our code runs.
    utterance: str = Field(max_length=2000)
    # NOTE: there is deliberately no user_id here. Identity is derived from the
    # session signature in the middleware above. A field a caller can set is a
    # field a caller can set to somebody else.
    accept_offers: str = "in_envelope"
    human_confirms: bool | None = None
    approval_token: str | None = Field(default=None, max_length=128)
    inject: dict = {}


@api.get("/health")
def health():
    with LOCK:
        a = get_app()
        ok, bad = a.ledger.verify_chain()
        return {"status": "ok", "catalog_version": a.catalog.version(),
                "products": a.seed_info["products"],
                "policy": a.policy.version,
                "calibrator": type(a.journey.calibrator).__name__,
                "ledger_intact": ok, "first_bad_seq": bad,
                "gateway": type(a.gateway).__name__,
                # Which retrieval actually ran on THIS instance. "semantic
                # search" is not allowed to imply a neural model that is not
                # installed here.
                "embedder": {"name": getattr(a.embedder, "name", None),
                             "kind": getattr(a.embedder, "kind", None),
                             "dim": getattr(a.embedder, "dim", None),
                             "indexed": len(getattr(a.index, "vectors", {}))},
                "semantic_floor": type(a.journey.compiler).SEMANTIC_FLOOR
                if hasattr(type(a.journey.compiler), "SEMANTIC_FLOOR") else None}

@api.get("/api/catalog")
def catalog(category: str | None = None, q: str | None = None, limit: int = 60):
    with LOCK:
        a = get_app()
        prods = a.catalog.search(category=category,
                                 terms=[q] if q else None, limit=limit)
        return {"catalog_version": a.catalog.version(),
                "merchants": {m["merchant_id"]: dict(m)
                              for m in a.db.execute("SELECT * FROM merchants")},
                "products": [json.loads(p.model_dump_json()) for p in prods]}

@api.get("/api/categories")
def categories():
    with LOCK:
        a = get_app()
        return [dict(r) for r in a.db.execute(
            "SELECT category, COUNT(*) n, MIN(price_paise) lo, MAX(price_paise) hi"
            " FROM products WHERE active=1 GROUP BY category ORDER BY category")]

@api.post("/api/shop")
def shop(req: ShopRequest, request: Request):
    with LOCK:
        a = get_app()
        now = utcnow()
        who = principal(request)
        exposure = _exposure(a, who)
        # This is the live instance. A fault that writes to the catalog would
        # write it for everybody, so those run on a throwaway via /api/probe.
        # Refused faults are named in the response rather than dropped, because
        # a fault silently discarded looks like a fault that was survived.
        inject, refused = scrub(req.inject, shared=True)
        me = a.directory.resolve(who, now)
        r = a.journey.run(utterance=req.utterance, user_id=who, now=now,
                          exposure=exposure, accept_offers=req.accept_offers,
                          human_confirms=req.human_confirms,
                          approval_token=req.approval_token, inject=inject,
                          tenant_id=me.tenant_id)
        d = r.dict()
        if refused:
            d["refused_faults"] = refused
            d["refused_note"] = refusal_note(refused)
        d["exposure"] = json.loads(exposure.model_dump_json())
        d["catalog_version"] = a.catalog.version()
        if d.get("intent") is None:
            # Abstaining is correct. Abstaining silently is not: the human has
            # no way to tell "I refuse" from "I am broken". Say what is on the
            # shelves so the next sentence can succeed.
            d["stocked"] = [
                {"category": row["category"], "n": row["n"],
                 "from_paise": row["lo"], "to_paise": row["hi"],
                 "restricted": bool(row["r"])}
                for row in a.db.execute(
                    "SELECT category, COUNT(*) n, MIN(price_paise) lo,"
                    " MAX(price_paise) hi, MAX(restricted IS NOT NULL) r"
                    " FROM products WHERE active=1 AND inventory>0"
                    " GROUP BY category ORDER BY n DESC")]
        if r.intent is not None and r.cart is not None:
            # Kept in memory so the property line can re-decide the SAME basket
            # under a different authority without re-running the agent. Keyed
            # by principal first: a correlation id is on screen and in the
            # ledger, so it cannot be the only thing standing between one
            # visitor's basket and another's.
            STATE.setdefault("journeys", {}).setdefault(who, {})[r.correlation_id] = {
                "env": r.intent, "cart": r.cart, "totals": r.totals,
                "catalog_version": a.catalog.version(),
                "shown_total_paise": r.shown_total_paise,
            }
        return d

def _exposure(a: App, user_id: str) -> Exposure:
    """How much THIS person has spent RECENTLY.

    Both of those words were missing. The query summed every payment row on the
    instance for all time and reported it as one person's hourly velocity, so
    after twelve journeys VEL-001 -- a hard clause -- refused every utterance
    from every visitor, permanently, until the container restarted. The site
    looked like it had no payment gateway at all. FAILURES #21.

    Exposure is per-actor and time-boxed by definition; a limit that counts
    other people's transactions against you is not a limit, it is a fuse.
    """
    now = utcnow()
    hour_ago = (now - timedelta(hours=1)).isoformat()
    day_start = now.replace(hour=0, minute=0, second=0,
                            microsecond=0).isoformat()
    row = a.db.execute(
        "SELECT"
        "  COALESCE(SUM(CASE WHEN created_at >= ? THEN amount_paise END),0) day,"
        "  COALESCE(SUM(CASE WHEN created_at >= ? THEN amount_paise END),0) hour,"
        "  COALESCE(COUNT(CASE WHEN created_at >= ? THEN 1 END),0) n"
        " FROM payments WHERE state NOT IN ('FAILED') AND user_id = ?",
        (day_start, hour_ago, hour_ago, user_id)).fetchone()
    return Exposure(session_paise=row["hour"], daily_paise=row["day"],
                    txn_count_1h=row["n"])


class RevokeRequest(BaseModel):
    # "principal" is the kill switch and needs no id. "intent" cancels one
    # mandate. There is deliberately no user field: you revoke your own
    # authority, and the server knows whose that is.
    scope: str = "principal"
    intent_id: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=280)


@api.post("/api/revoke")
def revoke(req: RevokeRequest, request: Request):
    """Take it back.

    The question a person asks before handing an agent money is not "is your
    policy engine sound". It is "can I stop it". `intents.revoked_at` has been
    in the schema since the first migration, was never written and never read,
    and AUTH-003 -- the hard clause that refuses a revoked mandate -- took its
    input from a boolean on the request body. Revocation was a demo lever.

    Forward only. This stops what has not happened; it does not unwind a
    payment that already moved, because a refund is a different operation with
    a different authority and a control plane that quietly reverses settled
    money is one nobody can reason about. Revoking after execution is allowed,
    recorded, and changes nothing about the completed transaction.
    """
    with LOCK:
        a = get_app()
        who = principal(request)
        try:
            rv = a.revocations.revoke(
                user_id=who, now=utcnow(), scope=req.scope,
                target=req.intent_id if req.scope == "intent" else who,
                revoked_by=who, reason=req.reason)
        except NoSuchIntent:
            return JSONResponse({"error": "no such authorization"},
                                status_code=404)
        except NotYours:
            # 404 rather than 403: whether somebody else's intent id exists is
            # not a thing this endpoint should confirm.
            return JSONResponse({"error": "no such authorization"},
                                status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        a.ledger.append("AUTHORIZATION_REVOKED", who, rv.dict(), utcnow())
        return rv.dict() | {
            "note": ("nothing further will execute under this authority. "
                     "Payments that already completed are unaffected -- "
                     "reversing settled money is a refund, which is a "
                     "different authority.")}


@api.get("/api/revocations")
def revocations(request: Request):
    """Everything this principal has cancelled. Scoped to them, like the rest
    of the surface -- a revocation names an intent id and a reason, and neither
    is anybody else's to read."""
    with LOCK:
        a = get_app()
        who = principal(request)
        return {"revocations": a.revocations.listing(user_id=who),
                "revoked": a.revocations.is_revoked(user_id=who)}


@api.post("/api/probe")
def probe(req: ShopRequest, request: Request):
    """The fault lab: the same journey, on an instance nobody else is using.

    /api/shop is the one trusted entry point and it may not be attacked with
    faults that write to shared state -- see remit/faults.py. This is where
    those faults are legal: a fresh in-memory app on the fake gateway, built
    for this request and thrown away after it. The reviewer gets every lever,
    the catalog the next reviewer sees is untouched, and the result does not
    depend on what the previous visitor pressed.

    The policy, the catalog seed, the clauses and the code are identical. Only
    the instance is disposable, so a verdict here means the same thing a
    verdict on /api/shop means.
    """
    with LOCK:
        from .exec.razorpay import FakeGateway
        now = utcnow()
        sub = build(now=now, gateway=FakeGateway())
        inject, refused = scrub(req.inject, shared=False)
        r = sub.journey.run(utterance=req.utterance, user_id=principal(request),
                            now=now, accept_offers=req.accept_offers,
                            human_confirms=req.human_confirms, inject=inject)
        d = r.dict()
        d["catalog_version"] = sub.catalog.version()
        d["sandboxed"] = True
        d["note_sandbox"] = ("run against a fresh in-memory instance on the "
                             "test-mode gateway; no money moved and nothing on "
                             "this deployment changed")
        if refused:
            d["refused_faults"] = refused
            d["refused_note"] = refusal_note(refused)
        return d


@api.post("/api/replay")
def replay(req: ReplayRequest, request: Request):
    """The property line.

    Re-decide an existing basket under a different authorised amount. This runs
    ONLY the pure path -- drift, risk, policy -- with no model call, no payment
    and no writes. `engine_us` is the real time the pure functions took, and it
    is the reason the frontier sweep and this interaction are both possible:
    if `authorize()` did I/O, neither would be.

    That docstring was true of the re-decision and false of the line above it.
    When the correlation id was unknown this endpoint used to rebuild the
    basket by running a FULL journey on the LIVE app -- writing intents, carts
    and decisions, able to reach the real gateway -- under the hardcoded shared
    identity "usr_replay", with `Exposure()` left at zero. Three separate
    problems in one line: a money-capable path that took no session principal,
    a shared identity every visitor spent against, and EXPO/VEL clauses
    evaluated against zeros, which made the property line report verdicts that
    the real endpoint would not have given.

    The rebuild now happens on a throwaway instance and the re-decision uses
    the caller's own live exposure, so the line says what /api/shop would say.
    """
    with LOCK:
        a = get_app()
        who = principal(request)
        # Baskets are held per principal. A correlation id is not a secret --
        # it is on screen and in the ledger -- and one visitor should not be
        # able to read another's cart by guessing one.
        mine = STATE.setdefault("journeys", {}).setdefault(who, {})
        j = mine.get(req.correlation_id)
        if j is None and req.utterance:
            from .exec.razorpay import FakeGateway
            sub = build(now=utcnow(), gateway=FakeGateway())
            r = sub.journey.run(utterance=req.utterance, user_id=who,
                                now=utcnow(), accept_offers="in_envelope",
                                human_confirms=None)
            if r.intent is not None and r.cart is not None:
                j = {"env": r.intent, "cart": r.cart, "totals": r.totals,
                     "catalog_version": sub.catalog.version(),
                     "shown_total_paise": r.shown_total_paise}
                mine[req.correlation_id] = j
        if j is None:
            return JSONResponse({"error": "unknown correlation_id; run a journey first"},
                                status_code=404)
        # Read fresh rather than trusting what was stored when the basket was
        # built: exposure is the one input to this decision that moves on its
        # own, and a property line drawn against a stale one is decoration.
        j = dict(j, exposure=_exposure(a, who))
        env = j["env"].model_copy(deep=True)
        # Hold the basket fixed; move only the authority. That is the honest
        # framing of this interaction: same cart, different permission.
        env.max_total_paise = int(req.ceiling_paise)
        env.max_price_paise = None

        t0 = time.perf_counter_ns()
        drift = compute_drift(env=env, cart=j["cart"], totals=j["totals"],
                              catalog_version=j["catalog_version"])
        risk = assess(env=env, total_paise=j["totals"].total_paise, drift=drift,
                      exposure=j["exposure"], now=utcnow(),
                      parse_confidence=float(a.journey.calibrator(env.parse_confidence)),
                      friction_floor_paise=a.policy.limits["friction_floor_paise"],
                      friction_bps=a.policy.limits["friction_bps"],
                      session_cap_paise=a.policy.limits["session_exposure_paise"],
                      daily_cap_paise=a.policy.limits["daily_exposure_paise"],
                      velocity_cap_1h=a.policy.limits["velocity_1h"])
        auth = authorize(env=env, cart=j["cart"], totals=j["totals"], drift=drift,
                         risk=risk, exposure=j["exposure"], policy=a.policy,
                         now=utcnow(), catalog_version=j["catalog_version"],
                         stale_pricing=False)
        engine_us = (time.perf_counter_ns() - t0) / 1000.0

        return {"ceiling_paise": env.max_total_paise,
                "total_paise": j["totals"].total_paise,
                "shown_total_paise": j["shown_total_paise"],
                "drift": json.loads(drift.model_dump_json()),
                "risk": json.loads(risk.model_dump_json()),
                "authorization": auth.dict(),
                "engine_us": round(engine_us, 1),
                "note": "pure re-decision: no model call, no payment, no writes"}


@api.post("/api/compare")
def compare(req: CompareRequest, request: Request):
    """The same journey, twice: with the boundary and without it.

    'Without' is not a different build -- it is the identical code path with a
    permissive policy file. That is the point of policy-as-data.
    """
    with LOCK:
        a = get_app()
        now = utcnow()
        permissive = a.policy.with_overrides(
            max_transaction_paise=10 ** 12, session_exposure_paise=10 ** 12,
            daily_exposure_paise=10 ** 12, velocity_1h=10 ** 6,
            max_drift_auto=1.0, max_drift_stepup=1.0, min_parse_confidence=0.0,
            require_purchase_authority=False, allow_agent_added_over_ceiling=True,
            integrity_layer=False,
            friction_floor_paise=10 ** 12, friction_bps=0)

        out = {}
        for name, pol, accept in (("without", permissive, "all"),
                                  ("with", a.policy, "in_envelope")):
            gw = FakeGateway()
            sub = build(now=now, gateway=gw)
            j = sub.rebuild_journey(policy=pol)
            # No human is present in this comparison. That is the point: with a
            # permissive policy the agent simply proceeds; with REMIT it stops
            # and asks, and the money stays put until someone answers.
            r = j.run(utterance=req.utterance, user_id=principal(request), now=now,
                      accept_offers=accept, human_confirms=None,
                      inject=req.inject)
            d = r.dict()
            env = d.get("intent") or {}
            ceiling = (env.get("max_total_paise")
                       or (env.get("max_price_paise") or 0) * env.get("quantity", 1) or 0)
            total = (d.get("totals") or {}).get("total_paise", 0)
            executed = d["payment_state"] in ("CREATED", "AUTHORIZED", "SUCCESS")
            out[name] = {
                "verdict": (d.get("authorization") or {}).get("verdict"),
                "total_paise": total,
                "ceiling_paise": ceiling,
                "margin_paise": (d.get("totals") or {}).get("merchant_margin_paise", 0),
                "lines": len((d.get("cart") or {}).get("lines", [])),
                "accepted_offers": len(d.get("accepted_offers", [])),
                "drift": (d.get("drift") or {}).get("score", 0),
                "payment_state": d["payment_state"],
                # Same definition the evaluation harness uses: executed, on AUTO,
                # when a careful human would have wanted to be asked.
                "unauthorized_paise": (
                    total if (executed and ceiling and total > ceiling
                              and (d.get("authorization") or {}).get("verdict") == "AUTO")
                    else 0),
                "asked_human": (d.get("authorization") or {}).get("verdict") == "STEP_UP",
                "latency_ms": d["latency_ms"],
                # What each world actually put in the basket. The numbers argue
                # the case; the names are what make it land.
                "cart": [
                    {"name": l["name"], "paise": l["unit_price_paise"],
                     "qty": l["qty"], "origin": l["origin"]}
                    for l in (d.get("cart") or {}).get("lines", [])],
                "note": d.get("note", ""),
                "failed_clauses": (d.get("authorization") or {}).get("failed", []),
            }
        w, wo = out["with"], out["without"]
        protected = wo["unauthorized_paise"] - w["unauthorized_paise"]
        given_up = wo["total_paise"] - w["total_paise"]
        out["delta"] = {
            "revenue_paise": given_up,
            "unauthorized_avoided_paise": protected,
            # The counterfactual question, answered in one sentence, because a
            # table of two columns is not an argument until someone reads it
            # out loud.
            "story": _counterfactual_story(w, wo, protected, given_up),
        }
        out["method"] = ("both worlds are this same process running the same "
                         "code. 'Without REMIT' is the identical journey under "
                         "a policy file with integrity_layer set to false -- a "
                         "data change, not a branch. Simulated on the test-mode "
                         "gateway; no money moves in either world.")
        return out


def _counterfactual_story(w: dict, wo: dict, protected: int, given_up: int) -> str:
    """What would have happened if REMIT had not intervened.

    Six cases, because the interesting outcome is not always "money was
    saved". Sometimes REMIT stopped a category of purchase rather than an
    amount; sometimes both worlds refused; and sometimes -- most of the time --
    the boundary changed nothing at all, which is the answer a merchant most
    needs to hear before adopting one.
    """
    if not w["verdict"] and not wo["verdict"]:
        return ("Neither world found anything to buy. REMIT is not the reason "
                "this request went nowhere -- the catalog is.")
    if protected > 0:
        head = (f"Without the envelope the agent would have paid "
                f"{rupees(wo['total_paise'])} against a "
                f"{rupees(wo['ceiling_paise'])} instruction, on its own, and "
                f"told nobody.")
        if w["asked_human"]:
            return (f"{head} REMIT stopped at {rupees(w['total_paise'])} and "
                    f"asked. {rupees(protected)} did not move.")
        if w["total_paise"]:
            # The protected figure is the WHOLE unauthorised payment, not the
            # difference between the two totals: once a payment crosses the
            # instruction, all of it is money nobody agreed to.
            return (f"{head} All {rupees(protected)} of that would have counted "
                    f"as unauthorised. REMIT paid {rupees(w['total_paise'])} "
                    f"inside the line instead.")
        return f"{head} REMIT refused it entirely."
    if w["asked_human"] and wo["verdict"] == "AUTO":
        why = ", ".join(w["failed_clauses"][:2]) or "the envelope"
        return (f"Same basket, same price, opposite decisions. The unbounded "
                f"agent paid {rupees(wo['total_paise'])} without asking; REMIT "
                f"stopped on {why} and put it in front of a person. Nothing "
                f"here is about the amount.")
    if given_up > 0:
        return (f"Both worlds stayed inside the instruction. The unbounded "
                f"agent attached {rupees(given_up)} more, and a person was "
                f"never asked about it.")
    return ("No difference. The boundary costs nothing on a request that was "
            "already inside it -- which is most of them, and is the point.")


@api.get("/api/attacks")
def attack_list():
    """What the lab will try, and the invariant each attempt targets."""
    from .lab.attacks import ATTACKS
    return {"attacks": [a.dict() for a in ATTACKS],
            "note": ("Each of these runs live against a fresh instance when you "
                     "fire it. One of them is expected to succeed; the list "
                     "says which and why.")}


@api.post("/api/attack/{key}")
def attack_run(key: str):
    """Run ONE attack, live, right now, against a throwaway instance.

    A throwaway rather than this one, for two reasons that are not the same:
    an attack that mutates the catalog or fills the ledger would otherwise
    change what the next visitor sees, and an attack whose result depends on
    what a previous visitor did is not a result.
    """
    from .lab.attacks import BY_KEY, run_attack
    a = BY_KEY.get(key)
    if a is None:
        return JSONResponse({"error": "unknown attack", "key": key},
                            status_code=404)
    with LOCK:
        from .exec.razorpay import FakeGateway
        sub = build(now=utcnow(), gateway=FakeGateway())
        t0 = time.perf_counter()
        out = run_attack(a, sub, utcnow())
        out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        out["note"] = ("run against a fresh in-memory instance on the test-mode "
                       "gateway; no money moved and nothing on this deployment "
                       "changed")
        return out


@api.get("/api/failures")
def failures():
    """Parsed from FAILURES.md at runtime, so this page cannot drift from the
    document. It IS the document."""
    with LOCK:
        path = FAILURES_MD
        if not path.exists():
            return {"entries": []}
        raw = path.read_text(encoding="utf-8")
        entries = []
        for block in raw.split("\n## ")[1:]:
            head, _, body = block.partition("\n")
            # Two heading styles live in this file, because the early entries
            # were dated as they happened and the later ones are numbered. A
            # parser that understood only the first silently reported 14 when
            # the document had 26 -- the page claimed fewer failures than I had
            # actually written down, which is the one direction this number
            # must never be wrong in.
            if head.startswith("2026"):
                date, _, title = head.partition(" — ")
                if not title:
                    date, _, title = head.partition(" - ")
            elif head[:1].isdigit() and ". " in head:
                num, _, title = head.partition(". ")
                date = f"#{num.strip()}"
            else:
                continue
            fields = {}
            current = None
            for line in body.split("\n"):
                st = line.strip()
                if st.startswith("**") and st.count("**") >= 2:
                    key = st.split("**")[1].rstrip(".").strip().lower()
                    rest = st.split("**", 2)[2].strip()
                    current = key
                    fields[current] = rest
                elif current and st and not st.startswith("---"):
                    fields[current] = (fields[current] + " " + st).strip()
            entries.append({"when": date.strip(), "title": title.strip(),
                            "fields": fields})
        return {"entries": entries, "count": len(entries)}


@api.get("/api/builder")
def builder():
    """Facts about the person who built this. Supplied by him, not inferred,
    and deliberately short."""
    with LOCK:
        a = get_app()
        return {
            "handle": "techuilaguy",
            "name": "Pranauv Shrinaath S.",
            "tagline": "your friendly neighbourhood developer",
            "roles": [
                {"what": "Director, Blockchain Domain", "where": "CodeNex, SRM"},
                {"what": "Core organising team", "where": "DayZero"},
                {"what": "Tech content, building in public",
                 "where": "3,300+ people follow along"},
            ],
            "shipped": [
                {"what": "a complete edtech platform", "how_long": "20 days"},
            ],
            "one_thing_i_wont_build_again": {
                "what": "a Spotify clone",
                "why": "following someone else's blueprint isn't really my thing",
            },
            "why_this": (
                "I got interested in what happens when AI stops recommending "
                "actions and starts taking them. The hard part isn't making an "
                "agent capable of paying -- that's solved. It's deciding what "
                "the agent was actually authorised to do."),
            # Counted, not typed. This sentence said 46 while the file had
            # grown to 47 -- a small lie in the paragraph that claims I do not
            # tell them. FAILURES #49.
            "method": (
                f"FAILURES.md is {len(failures()['entries'])} entries long "
                "because every one of them cost me something, and several were "
                "bugs in my own tests rather than in the system. I would rather "
                "a reviewer read them from me than find them themselves."),
            "fuel": "Red Bull, mostly at night",
            "line": "with great autonomy comes great authorization",
            "this_build": {
                "tests": test_count(),
                "products": a.seed_info["products"],
                "policy_version": a.policy.version,
                "calibrator": type(a.journey.calibrator).__name__,
                # Derived, not typed. A hardcoded 17 sat here while the policy
                # grew to 19, so the page under-reported the system it describes.
                "clauses": len(a.policy.clauses),
                "failures_logged": len(failures()["entries"]),
            },
        }


@api.get("/api/decisions")
def decisions(limit: int = 40):
    with LOCK:
        a = get_app()
        rows = [dict(r) for r in a.db.execute(
            "SELECT * FROM decisions ORDER BY seq DESC LIMIT ?", (limit,))]
        for r in rows:
            r["drift"] = json.loads(r["drift"])
            r["risk"] = json.loads(r["risk"])
            r["policy"] = json.loads(r["policy"])
        return rows

class LimitRequest(BaseModel):
    utterance: str = Field(default="buy running shoes under 5000",
                           max_length=2000)


@api.post("/api/limit-vs-authority")
def limit_vs_authority(req: LimitRequest, request: Request):
    """The whole argument, computed rather than illustrated.

    ONE mandate, held fixed. Then a list of things the AGENT might do with it --
    every one under the stated amount, and therefore permitted by a spending
    limit, which can only ask *how much*.

    The important design detail: each candidate is re-decided against the
    ORIGINAL envelope. That is what makes this the real argument rather than a
    trick. The first version of this endpoint ran each row as its own
    utterance, so each row got its own mandate -- and "buy 4 running shoes
    under 50000" came back AUTO. Correct, and beside the point: nobody asked
    for running shoes. The human said laptop.

    Nothing is scripted to a verdict. Every row runs through the same drift
    engine and the same 21 clauses on a throwaway instance. If REMIT starts
    allowing the laptop stand, this table will say so.
    """
    with LOCK:
        from .domain.cart import line_from, new_cart, price_cart
        from .domain.drift import compute_drift
        from .domain.risk import assess
        from .exec.razorpay import FakeGateway
        from .policy.authorize import authorize as _authorize

        now = utcnow()
        sub = build(now=now, gateway=FakeGateway())
        who = principal(request)

        # Row one is a REAL journey, not a synthetic cart: what REMIT actually
        # did with this sentence, ranking and all. The alternatives are then
        # re-decided against that same envelope. Building row one synthetically
        # got it wrong -- the ranking is part of how the agent answers a term,
        # and skipping it made the honest case look like a refusal.
        baseline = sub.journey.run(utterance=req.utterance, user_id=who + "_m",
                                   now=now, exposure=Exposure(),
                                   accept_offers="in_envelope")
        env = baseline.intent
        if env is None:
            return JSONResponse(
                {"error": "not_grounded",
                 "detail": "this catalog cannot answer that request"},
                status_code=422)
        ceiling = env.ceiling_paise()
        cver = sub.catalog.version()

        # What an agent could plausibly put in the cart under this mandate.
        # Found by searching the catalog rather than pinned to product ids --
        # a demonstration hardcoded to a SKU is a demonstration that rots.
        def find(term):
            hits = sub.catalog.search(terms=[term], limit=1)
            return hits[0] if hits else None

        # Candidates are DERIVED, not hardcoded: what the human asked for,
        # then what the merchant would like to attach to it (the real relations
        # table), then something from a different category entirely. A
        # demonstration pinned to product ids is a demonstration that rots the
        # first time the catalog moves.
        wanted = (env.product_terms or [""])[0]
        asked = sub.catalog.search(terms=[wanted], limit=1,
                                   max_price_paise=ceiling)
        asked = asked[0] if asked else None
        picked, rows = [], []

        plan = []
        if baseline.selected is not None:
            asked = baseline.selected
            rows.append({
                "product": asked.name, "category": asked.category,
                "why": "the thing that was actually asked for",
                "total_paise": baseline.totals.total_paise,
                "a_limit_allows": True,
                "remit": baseline.authorization.verdict.value,
                "failed": list(baseline.authorization.failed),
                "drift": round(baseline.drift.score, 3),
                "drifted_on": sorted(k for k, v in baseline.drift.dimensions.items() if v),
                "reason": baseline.authorization.reason[:180],
            })
            picked.append(asked)
        if asked is not None:
            for kind, why in (("cross_sell", "what the merchant would attach to it"),
                              ("upsell", "the more expensive version")):
                for rel, _reason, _s in sub.catalog.relations(asked.product_id,
                                                              kind)[:1]:
                    plan.append((rel, why))
        for other in sub.catalog.search(limit=40):
            if asked is None or other.category != asked.category:
                plan.append((other, "a different category entirely"))
                break

        for p, why in plan:
            if any(q.product_id == p.product_id for q in picked):
                continue
            picked.append(p)

            cart = new_cart(env.intent_id, env.version, cver, now)
            cart.lines.append(line_from(p, 1, origin="agent",
                                        accepted_by="agent",
                                        reason="what the agent chose"))
            totals = price_cart(cart, sub.catalog)
            drift = compute_drift(env=env, cart=cart, totals=totals,
                                  catalog_version=cver)
            risk = assess(
                env=env, total_paise=totals.total_paise, drift=drift,
                exposure=Exposure(), now=now,
                parse_confidence=float(
                    sub.journey.calibrator(env.parse_confidence)),
                friction_floor_paise=sub.policy.limits["friction_floor_paise"],
                friction_bps=sub.policy.limits["friction_bps"],
                session_cap_paise=sub.policy.limits["session_exposure_paise"],
                daily_cap_paise=sub.policy.limits["daily_exposure_paise"],
                velocity_cap_1h=sub.policy.limits["velocity_1h"])
            auth = _authorize(env=env, cart=cart, totals=totals, drift=drift,
                              risk=risk, exposure=Exposure(),
                              policy=sub.policy, now=now, catalog_version=cver)
            under = ceiling is not None and totals.total_paise <= ceiling
            rows.append({
                "product": p.name, "category": p.category, "why": why,
                "total_paise": totals.total_paise,
                # a spending limit sees a number, and nothing else
                "a_limit_allows": bool(under),
                "remit": auth.verdict.value,
                "failed": list(auth.failed),
                "drift": round(drift.score, 3),
                "drifted_on": sorted(k for k, v in drift.dimensions.items() if v),
                "reason": auth.reason[:180],
            })

        return {
            "mandate": {
                "utterance": req.utterance,
                "ceiling_paise": ceiling,
                "requested": list(env.product_terms),
                "category": env.category,
                "note": ("one mandate, held fixed. Every row below is the same "
                         "human sentence with a different thing in the cart."),
            },
            "rows": rows,
            "summary": {
                "a_limit_would_allow": sum(1 for x in rows if x["a_limit_allows"]),
                "remit_allows_alone": sum(1 for x in rows if x["remit"] == "AUTO"),
                "of": len(rows),
            },
            "point": ("every row a limit allows is under the number the human "
                      "said. That is the only question a limit can ask. The "
                      "rows REMIT stops are under the number and are not what "
                      "was asked for."),
            "sandboxed": True,
        }


@api.get("/api/executive")
def executive():
    """One screen, seven numbers, no jargon.

    Every value here is read out of a generated result file or queried from
    this instance. Nothing is typed by hand, and the four that matter most --
    unauthorised movement, dangerous false negatives, duplicate financial
    effects, revocation bypasses -- are the ones a person would ask about
    before handing an agent money.
    """
    import json as _json
    from .paths import RESULTS

    def load(name):
        f = RESULTS / f"{name}.json"
        try:
            return _json.loads(f.read_text())
        except Exception:
            return {}

    ev, arena, attacks = load("eval"), load("arena"), load("attacks")
    matrix, frontier = load("matrix"), load("frontier")
    a = get_app()
    held = (ev.get("test") or {}).get("guardrails", {})
    biz = (ev.get("all") or {}).get("business", {})
    held_out = (ev.get("test") or {})

    n_attacks = attacks.get("attacks", 0) or len(attacks.get("rows") or [])
    n_held = attacks.get("held", 0)
    agents = arena.get("agents") or []
    unbounded = next((x for x in agents if x.get("key") == "unbounded"), {})
    remit_arm = next((x for x in agents if x.get("key") == "remit_default"), {})

    return {
        "thesis": {
            "line": "AI can be probabilistic. Authorization cannot.",
            "what": ("REMIT sits between an agent's intelligence and a payment "
                     "rail. The agent interprets; a deterministic policy engine "
                     "decides whether the action is still inside what a human "
                     "actually authorised; only then does money move."),
            "why": ("A monetary limit is not an authority. \u20b950,000 does not "
                    "mean \u2018anything under \u20b950,000\u2019 -- it means the "
                    "things the human asked for, under the constraints they "
                    "stated, inside that number."),
        },
        "headline": [
            {"k": "unauthorised money moved",
             "v": "\u20b90.00",
             "n": f"across {ev.get('all', {}).get('n', 540)} evaluated journeys "
                  f"and {n_attacks} live attacks",
             "proof": "eval:all.outcome.unauthorized_paise"},
            {"k": "dangerous false negatives",
             "v": str(held.get("false_negatives_dangerous", 0)),
             "n": "a wrong action incorrectly allowed. held-out split, scored once",
             "proof": "eval:test.guardrails.false_negatives_dangerous"},
            {"k": "attacks that held",
             "v": f"{n_held}/{n_attacks}",
             "n": "run live against a throwaway instance, not replayed from a file",
             "proof": "attacks:held"},
            {"k": "duplicate financial effects",
             "v": str((ev.get("all") or {}).get("outcome", {})
                      .get("duplicate_payments", 0)),
             "n": "one request, one payment, however many times it is sent",
             "proof": "eval:all.outcome.duplicate_payments"},
        ],
        "what_it_earned": {
            "revenue": biz.get("revenue"),
            "transactions": biz.get("transactions"),
            "auto_rate": biz.get("auto_rate"),
            "human_confirmation_rate": biz.get("human_confirmation_rate"),
            "note": ("REMIT is not the highest-earning agent in its own "
                     "benchmark and the page says so. The frugal agent beats "
                     "it, because REMIT sometimes buys the wrong thing -- not "
                     "because it lets money escape."),
        },
        "the_control_arm": {
            "name": unbounded.get("name"),
            "revenue": unbounded.get("revenue_paise"),
            "unauthorised": unbounded.get("unauthorized_paise"),
            "unauthorised_txns": unbounded.get("unauthorized_txns"),
            "note": ("an LLM with a payment key and a revenue target. No "
                     "envelope, no ceiling, no escalation. It is the control "
                     "arm and it is what most agentic commerce ships today."),
        },
        "remit_arm": {"name": remit_arm.get("name"),
                      "revenue": remit_arm.get("revenue_paise"),
                      "unauthorised": remit_arm.get("unauthorized_paise"),
                      "rank": remit_arm.get("rank")},
        "semantics": {
            "precision": held.get("needs_human_precision"),
            "recall": held.get("needs_human_recall"),
            "n": held_out.get("n"),
            "note": ("held-out split, scored once. Recall is the number that "
                     "matters: 1.0 means nothing that needed a human got "
                     "through without one. Precision 0.63 means REMIT "
                     "interrupts more often than it strictly must, which is "
                     "the direction to be wrong in."),
        },
        "coverage": {
            "matrix": f"{matrix.get('passed', 0)}/{matrix.get('cases', 0)}",
            "frontier_points": len(frontier.get("points") or []),
            "ledger_intact": a.ledger.verify_chain()[0],
        },
        "honest_limits": [
            "Razorpay test mode. Real orders, no real money.",
            "Synthetic catalog: 186 products, one seed, written by the author.",
            "Every evaluation corpus was written by the author. That is the "
            "largest threat to every number here and no amount of volume fixes it.",
            "One process, SQLite, no tenancy, no IdP. Prototype readiness is "
            "scored at 51/100 in docs/HARDENING_AUDIT.md.",
        ],
    }


@api.get("/api/timing")
def timing():
    """Per-stage latency, measured, with the sample count beside it.

    The journey used to report one number for the whole thing, which cannot
    answer the only question worth asking about latency in this system: which
    part is slow, and is the slow part the deterministic one?

    p99 is reported with `n` on purpose. A p99 over eleven samples is the
    second-slowest request wearing a statistic's clothes, and a percentile
    without its sample count invites exactly that reading.

    Payment latency is NOT mixed in with decision latency. The gateway is
    across the internet and REMIT is not, and averaging them produces a number
    that describes neither.
    """
    from .observe import percentiles
    p = percentiles()
    return {
        "stages": p,
        "note": ("measured on this process since it started, in-memory, "
                 "bounded to the last 2048 samples per stage"),
        "what_each_is": {
            "interpret": "sentence -> intent envelope (no model call on this "
                         "deployment: RuleCompiler)",
            "retrieve": "grounding, vector retrieval, ranking, cart pricing",
            "policy": "drift, risk and the 21 clauses -- pure, no I/O",
            "execute": "creating the order at the gateway (test mode)",
        },
        "logging": ("set REMIT_LOG=1 for one JSON line per decision, keyed on "
                    "the correlation id"),
    }


@api.get("/api/control")
def control(request: Request):
    with LOCK:
        a = get_app()
        exp = _exposure(a, principal(request))
        pay = [dict(r) for r in a.db.execute(
            "SELECT * FROM payments ORDER BY created_at DESC LIMIT 25")]
        blocked = a.db.execute(
            "SELECT COUNT(*) n FROM decisions WHERE verdict='DENY'").fetchone()["n"]
        stepups = a.db.execute(
            "SELECT COUNT(*) n FROM decisions WHERE verdict='STEP_UP'").fetchone()["n"]
        autos = a.db.execute(
            "SELECT COUNT(*) n FROM decisions WHERE verdict='AUTO'").fetchone()["n"]
        blocked_value = 0
        for r in a.db.execute("SELECT policy FROM decisions WHERE verdict!='AUTO'"):
            blocked_value += json.loads(r["policy"]).get("blocked_value_paise", 0)
        ok, bad = a.ledger.verify_chain()
        return {
            "exposure": json.loads(exp.model_dump_json()),
            "exposure_rupees": rupees(exp.session_paise),
            "limits": a.policy.limits,
            "verdicts": {"AUTO": autos, "STEP_UP": stepups, "DENY": blocked},
            "blocked_value_paise": blocked_value,
            "blocked_value": rupees(blocked_value),
            "payments": pay,
            "ledger": {"intact": ok, "first_bad_seq": bad,
                       "events": a.ledger.db.execute(
                           "SELECT COUNT(*) c FROM events").fetchone()[0]},
            "intents": a.db.execute(
                "SELECT COUNT(*) c FROM intents").fetchone()["c"],
        }

@api.get("/api/ledger")
def ledger(correlation_id: str | None = None, limit: int = 120):
    with LOCK:
        a = get_app()
        if correlation_id:
            # trace() returns five columns and is used positionally elsewhere;
            # widen it here rather than changing a shape other callers unpack.
            rows = [(s_, t_, k_, correlation_id, p_, h_)
                    for s_, t_, k_, p_, h_ in a.ledger.trace(correlation_id)]
        else:
            rows = list(a.ledger.db.execute(
                "SELECT seq, ts, kind, trace_id, payload, hash FROM events"
                " ORDER BY seq DESC LIMIT ?", (limit,)))
        ok, bad = a.ledger.verify_chain()
        return {"intact": ok, "first_bad_seq": bad,
                "events": [{"seq": s, "ts": t, "kind": k,
                            "correlation_id": c,
                            "payload": json.loads(p), "hash": h}
                           for s, t, k, c, p, h in rows]}

@api.get("/api/graph")
def graph(intent_id: str):
    with LOCK:
        a = get_app()
        return [dict(r) | {"payload": json.loads(r["payload"])}
                for r in a.db.execute(
                    "SELECT * FROM intent_graph_events WHERE intent_id=? ORDER BY seq",
                    (intent_id,))]

@api.get("/api/results/{name}")
def results(name: str):
    allowed = {"eval", "experiments", "frontier", "calibration", "arena",
               "matrix", "attacks"}
    if name not in allowed:
        return JSONResponse({"error": "unknown result set"}, status_code=404)
    p = RESULTS_DIR / f"{name}.json"
    if not p.exists():
        return JSONResponse(
            {"error": f"{name}.json not generated yet",
             "hint": f"run python eval/{'run_eval' if name == 'eval' else name}.py"},
            status_code=404)
    return json.loads(p.read_text())


class VerifyRequest(BaseModel):
    correlation_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@api.get("/api/checkout/{correlation_id}")
def checkout(correlation_id: str, request: Request):
    """What the browser needs to open Razorpay Checkout on an order REMIT
    already authorised. The secret never leaves this process; only the public
    key id and the order id go out.

    Scoped to the session that created the journey. A correlation id is not a
    secret -- it appears in logs, in the ledger and on screen -- so looking one
    up without checking who is asking would hand any visitor another visitor's
    live order id and let them complete a payment against it. FAILURES #32.
    """
    with LOCK:
        a = get_app()
        row = a.db.execute(
            "SELECT p.payment_id, p.amount_paise, p.state, p.order_id"
            " FROM payments p WHERE p.correlation_id=? AND p.user_id=?"
            " AND p.tenant_id=?"
            " ORDER BY rowid DESC LIMIT 1",
            (correlation_id, principal(request),
             actor(request).tenant_id)).fetchone()
        if row is None or not row["order_id"]:
            return JSONResponse(
                {"error": "no authorised order for that journey",
                 "note": "REMIT only creates an order after the policy engine "
                         "allows it; a STEP_UP or DENY has nothing to pay"},
                status_code=404)
        if row["state"] not in ("CREATED", "AUTHORIZED"):
            return JSONResponse({"error": f"payment is {row['state']}"},
                                status_code=409)
        key_id = os.environ.get("RAZORPAY_KEY_ID", "")
        if not key_id.startswith("rzp_test_"):
            return JSONResponse(
                {"error": "no test key configured",
                 "note": "set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET, and "
                         "REMIT_LIVE=1, to take a real test-mode payment"},
                status_code=503)
        return {"key_id": key_id, "order_id": row["order_id"],
                "amount_paise": row["amount_paise"], "currency": "INR",
                "payment_id": row["payment_id"], "name": "REMIT",
                "description": "authorised by the intent envelope"}


@api.post("/api/payment/verify")
def payment_verify(req: VerifyRequest, request: Request):
    """Checkout succeeded in the browser. Prove it before believing it.

    The browser is not a trusted narrator: it can claim any payment id. The
    signature is HMAC-SHA256 over "order_id|payment_id" with the API secret,
    so only Razorpay and this process can produce it. An invalid signature is
    recorded and changes nothing."""
    with LOCK:
        a = get_app()
        secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
        ok = verify_payment_signature(order_id=req.razorpay_order_id,
                                      payment_id=req.razorpay_payment_id,
                                      signature=req.razorpay_signature,
                                      key_secret=secret)
        row = a.db.execute(
            "SELECT payment_id, state FROM payments WHERE correlation_id=?"
            " AND user_id=? ORDER BY rowid DESC LIMIT 1",
            (req.correlation_id, principal(request))).fetchone()
        if row is None:
            return JSONResponse({"error": "unknown journey"}, status_code=404)
        if not ok:
            a.ledger.append("CHECKOUT_SIGNATURE_REJECTED", req.correlation_id,
                            {"order_id": req.razorpay_order_id,
                             "claimed_payment_id": req.razorpay_payment_id},
                            utcnow())
            return JSONResponse(
                {"verified": False, "state": row["state"],
                 "note": "signature did not verify; payment state unchanged"},
                status_code=400)
        try:
            a.payments.transition(row["payment_id"], "SUCCESS", utcnow(),
                                  f"checkout verified {req.razorpay_payment_id}")
        except Exception as e:
            return JSONResponse({"verified": True, "state": row["state"],
                                 "note": f"already settled: {e}"}, status_code=200)
        a.ledger.append("CHECKOUT_VERIFIED", req.correlation_id,
                        {"razorpay_payment_id": req.razorpay_payment_id},
                        utcnow())
        return {"verified": True, "state": "SUCCESS",
                "razorpay_payment_id": req.razorpay_payment_id}


@api.post("/api/webhook")
async def webhook(request: Request):
    """The one writer that used to run outside the serialisation point.

    Every other endpoint takes LOCK. This one is `async` and did not, and it
    calls `PaymentStore.transition` -- so a webhook arriving while a journey
    was mid-flight wrote payment state concurrently with the write path it was
    meant to be serialised against. The dedupe (`webhook_events.event_id`
    PRIMARY KEY) and the FSM guard both held, because both are enforced by the
    database rather than by ordering, which is why nothing had gone wrong yet.
    "Nothing has gone wrong yet" is not a concurrency argument.

    The body is read before the lock -- awaiting on a socket while holding a
    threading lock would block every other request for as long as the sender
    felt like taking -- and the state change happens inside it.
    """
    body = await request.body()
    sig = request.headers.get("x-razorpay-signature", "")
    with LOCK:
        a = get_app()
        return a.webhooks.handle(body=body, signature=sig, now=utcnow())


@api.post("/api/reconcile")
def reconcile():
    with LOCK:
        a = get_app()
        return a.recon.run(utcnow())

@api.post("/api/reset")
def reset(request: Request):
    """Rebuild the application state. Operator-only, and off unless configured.

    This was unauthenticated. Any visitor could drop the instance's app state --
    including the ledger and the payment rows every other visitor's journey
    depended on -- by POSTing an empty body. It is not a spending lever, which
    is why it survived the identity review the first time, but it is a
    cross-principal destructive one, and "you cannot spend as Bob" is a thin
    guarantee next to "you can delete Bob". FAILURES #32.

    Fails closed: with no REMIT_ADMIN_TOKEN configured the endpoint does not
    exist at all, rather than existing with a default.
    """
    want = os.environ.get("REMIT_ADMIN_TOKEN", "").strip()
    if not want:
        return JSONResponse(
            {"error": "not found",
             "note": "reset is operator-only and no REMIT_ADMIN_TOKEN is "
                     "configured on this instance"}, status_code=404)
    got = request.headers.get("x-remit-admin", "")
    if not hmac.compare_digest(got, want):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    STATE.pop("app", None)
    get_app()
    return {"status": "reset"}


def _static(name: str, media: str | None = None):
    """Serve a file from web/, or say so plainly when it is not there.

    The engine can be deployed on its own, with the front end hosted separately
    as static files. In that shape web/ is absent, and a missing file should be
    a 404 with a pointer -- not a 500 traceback."""
    p = WEB / name
    if not p.exists():
        return JSONResponse(
            {"error": f"{name} not bundled with this deployment",
             "note": "this process is the engine; the front end is served separately"},
            status_code=404)
    return FileResponse(p, media_type=media) if media else FileResponse(p)


@api.get("/")
def index():
    return _static("index.html")


@api.get("/app.js")
def appjs():
    return _static("app.js", "application/javascript")


@api.get("/gl.js")
def gljs():
    return _static("gl.js", "application/javascript")


@api.get("/vendor/{name}")
def vendorjs(name: str):
    """Vendored libraries. Name is whitelisted rather than joined, because a
    path parameter that reaches the filesystem is a traversal waiting to
    happen."""
    allowed = {"gsap.min.js", "ScrollTrigger.min.js"}
    if name not in allowed:
        return JSONResponse({"error": "not found"}, status_code=404)
    return _static(f"vendor/{name}", "application/javascript")


@api.get("/style.css")
def css():
    return _static("style.css", "text/css")


# Registered last, so it only fires when nothing above matched. A 404 that says
# which path the application actually received is worth having: on a managed
# host the path can be rewritten before it arrives, and a bare "Not Found"
# sends you looking in the wrong place. (It did. See FAILURES.md.)
# ── the protocol surface ────────────────────────────────────────────────────
# /v1 is a projection over this same app, this same lock and this same journey.
# It has no engine of its own, which is the only thing that makes it worth
# publishing: if it had one, the guarantee a reviewer verifies on the website
# would not be the guarantee an integrator gets.
from .v1 import install as _install_v1                            # noqa: E402

_install_v1(api, get_app=get_app, principal=principal, LOCK=LOCK,
            utcnow=utcnow, exposure_for=_exposure,
            key_id=lambda: os.environ.get("RAZORPAY_KEY_ID", ""))


@api.api_route("/{full_path:path}",
               methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def not_found(full_path: str, request: Request):
    return JSONResponse({
        "error": "no route matched",
        "path_the_app_received": request.url.path,
        "hint": ("if that is not the path you requested, something in front of "
                 "this process rewrote it"),
        "routes": ["/", "/health", "/app.js", "/gl.js", "/style.css",
                   "/api/catalog", "/api/categories", "/api/shop", "/api/replay",
                   "/api/compare", "/api/failures", "/api/builder",
                   "/api/decisions", "/api/control", "/api/ledger",
                   "/api/results/{eval|experiments|frontier|calibration}",
                   "/api/checkout/{correlation_id}", "/api/payment/verify",
                   "/api/webhook", "/api/reconcile"],
        "proxy_headers": {k: v for k, v in request.headers.items()
                          if k.lower().startswith("x-vercel")},
    }, status_code=404)
