"""The orchestrator: one human utterance to one settled (or refused) payment.

This file is the product. Read it top to bottom and the architecture diagram
is redundant; if it stops reading that way, the design has drifted.

    utterance
      -> intent envelope (versioned, immutable)
      -> product search + deterministic ranking
      -> selection
      -> revenue engine proposes (never adds silently)
      -> cart priced deterministically
      -> DRIFT measured against the envelope
      -> RISK sized in rupees
      -> POLICY decides AUTO | STEP_UP | DENY
      -> [step-up] human confirms or declines
      -> idempotent payment through the Razorpay adapter
      -> webhooks -> reconciliation
      -> every step hash-chained into the ledger and the intent graph

The model appears exactly twice: it compiles the utterance, and it phrases
an explanation AFTER the decision is made. It is never asked whether to pay.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..domain.cart import Cart, Totals, line_from, new_cart, price_cart
from ..domain.catalog import Catalog, Product, rank
from ..domain.drift import DriftResult, compute_drift
from ..domain.intent import IntentEnvelope, amend
from ..domain.revenue import Offer, RevenueEngine
from ..domain.risk import Exposure, RiskDecision, assess
from ..exec.idempotency import idempotency_key, receipt_for
from ..exec.payments import PaymentStore
from ..ledger.chain import Ledger
from ..models import canonical as _canonical, sha as _sha
from ..money import Paise, rupees
from ..policy.authorize import Authorization, Policy, Verdict, authorize
from ..retrieval.index import hard_filter
from ..observe import log as _olog, record as _orecord
from ..tools.broker import ToolBroker

# How far back SPLIT-001 looks. Long enough that an agent decomposing a
# purchase across a few minutes is still one episode; short enough that
# buying the same thing again tomorrow is a new decision, not a suspicion.
WINDOW_HOURS = 1


# A network timeout talking to a payment gateway is the one error that must
# not be treated as a failure: the order may exist. httpx raises its own
# TimeoutException, which is NOT a subclass of the built-in TimeoutError, so
# the only branch that ever reached this handler was the fake gateway's
# injected fault -- a real Razorpay read-timeout was classified FAILED,
# terminal, and the reconciler (which only revisits UNKNOWN) never looked at
# it again. FAILURES #22.
AMBIGUOUS: tuple[type[BaseException], ...] = (TimeoutError,)
try:                                    # pragma: no cover - import shape only
    import httpx as _httpx
    AMBIGUOUS = (TimeoutError, _httpx.TimeoutException, _httpx.NetworkError)
except Exception:                       # httpx absent in a pure-offline install
    pass


@dataclass
class JourneyResult:
    correlation_id: str
    intent: IntentEnvelope | None = None
    telemetry: dict = field(default_factory=dict)
    candidates: list = field(default_factory=list)
    selected: Product | None = None
    why_selected: str = ""
    offers: list[Offer] = field(default_factory=list)
    accepted_offers: list[str] = field(default_factory=list)
    cart: Cart | None = None
    totals: Totals | None = None
    drift: DriftResult | None = None
    risk: RiskDecision | None = None
    authorization: Authorization | None = None
    payment_id: str | None = None
    order_id: str | None = None
    payment_state: str = "NONE"
    shown_total_paise: int = 0
    replayed: bool = False
    approval: dict | None = None    # the token a human must redeem to proceed
    note: str = ""
    latency_ms: float = 0.0

    @property
    def revenue_paise(self) -> Paise:
        if self.payment_state in ("SUCCESS", "CREATED", "AUTHORIZED") and self.totals:
            return self.totals.total_paise
        return 0

    @property
    def margin_paise(self) -> Paise:
        if self.payment_state in ("SUCCESS", "CREATED", "AUTHORIZED") and self.totals:
            return self.totals.merchant_margin_paise
        return 0

    def dict(self) -> dict:
        return {
            "correlation_id": self.correlation_id,
            "intent": json.loads(self.intent.model_dump_json()) if self.intent else None,
            "telemetry": self.telemetry,
            "selected": json.loads(self.selected.model_dump_json()) if self.selected else None,
            "why_selected": self.why_selected,
            "offers": [json.loads(o.model_dump_json()) for o in self.offers],
            "accepted_offers": self.accepted_offers,
            "cart": json.loads(self.cart.model_dump_json()) if self.cart else None,
            "totals": json.loads(self.totals.model_dump_json()) if self.totals else None,
            "drift": json.loads(self.drift.model_dump_json()) if self.drift else None,
            "risk": json.loads(self.risk.model_dump_json()) if self.risk else None,
            "authorization": self.authorization.dict() if self.authorization else None,
            "payment_id": self.payment_id, "order_id": self.order_id,
            "payment_state": self.payment_state, "replayed": self.replayed,
            "approval": self.approval,
            "shown_total_paise": self.shown_total_paise,
            "note": self.note, "latency_ms": round(self.latency_ms, 2),
        }


class Journey:
    def __init__(self, *, db, catalog: Catalog, compiler, revenue: RevenueEngine,
                 policy: Policy, ledger: Ledger, payments: PaymentStore,
                 broker: ToolBroker, gateway, calibrator=None, index=None,
                 approvals=None, revocations=None, authority=None):
        self.db = db
        self.catalog = catalog
        self.compiler = compiler
        self.revenue = revenue
        self.policy = policy
        self.ledger = ledger
        self.payments = payments
        self.broker = broker
        self.gw = gateway
        # Semantic retrieval. Optional so that every existing construction of a
        # Journey keeps working; when it is absent the agent simply cannot
        # answer a request it has no words for, which is the old behaviour.
        self.index = index
        # Issues and redeems the tokens that bind a human's yes to one basket.
        self.approvals = approvals
        # The authority's own lifecycle, as a machine with a transition table
        # rather than as eight strings assigned at the end of this function.
        # Optional for the same reason as the others: every existing
        # construction keeps working, and without it the journey simply does
        # not record a lifecycle -- it never skips a check.
        self.authority = authority
        # Persisted cancellation. Until this existed AUTH-003 read a boolean
        # off the request, which made revocation a demo lever rather than a
        # control. Optional so every existing construction keeps working; when
        # it is absent nothing is revoked, which is the old behaviour.
        self.revocations = revocations
        # Identity until a temperature has been fitted on labelled data.
        # Deliberately explicit: an uncalibrated system says so.
        self.calibrator = calibrator or (lambda x: x)

    # ---------- audit helpers ----------
    def _to(self, env, state: str, now, cid: str, cause: str) -> None:
        """Move the authority, and refuse the journey if the move is illegal.

        Swallowing an IllegalTransition here would make the machine
        decorative -- the thing it exists to prevent would simply happen with a
        log line next to it. It propagates.
        """
        if self.authority is None or env is None:
            return
        self.authority.advance(intent_id=env.intent_id, to=state, now=now,
                               cause=cause, correlation_id=cid)

    def _revoked(self, env):
        """Has this authority been cancelled? Cheap, and asked more than once.

        Returns the Revocation or None. Never raises: a control that throws
        when its store is unavailable turns a cancelled mandate into a stack
        trace, and the caller of this is on the path to a payment.
        """
        if self.revocations is None:
            return None
        try:
            return self.revocations.check(user_id=env.user_id,
                                          intent_id=env.intent_id)
        except Exception:
            return None

    def _mandate_exposure(self, env, exposure, now):
        """What has already been spent under a statement that reads like this one.

        A ceiling was only ever compared against the basket in front of it, so
        an agent that could not fit inside "under 2000" in one cart could use
        three. This is the only input SPLIT-001 needs, and it is computed here
        rather than inside authorize() because authorize() does no I/O and is
        not going to start.

        "Reads like this one" is deliberately narrow: same category, same
        stated ceiling, inside the window. Summing every purchase against the
        smallest ceiling anyone mentioned recently would refuse a person who
        bought socks and then a laptop, which is arithmetic rather than
        consent. Two different instructions are two authorities.

        Returns the exposure unchanged when the human named no ceiling -- there
        is nothing to aggregate against -- and when the caller has opted out by
        passing an exposure explicitly zeroed, as the evaluation harness does
        to keep its cases independent.
        """
        ceiling = env.ceiling_paise()
        if not ceiling:
            return exposure
        window = (now - timedelta(hours=WINDOW_HOURS)).isoformat()
        spent = txns = 0
        try:
            rows = self.db.execute(
                "SELECT p.amount_paise, iv.envelope FROM payments p"
                " JOIN intents i ON i.intent_id = p.intent_id"
                " JOIN intent_versions iv ON iv.intent_id = i.intent_id"
                "   AND iv.version = i.current_version"
                " WHERE p.user_id = ? AND p.created_at >= ?"
                "   AND p.state NOT IN ('FAILED')",
                (env.user_id, window)).fetchall()
        except Exception:
            # A control that cannot read its history must not therefore permit
            # more than it otherwise would. Nothing to add is not the same as
            # nothing spent, but the failure mode here is a missing STEP_UP,
            # not a missing DENY, and the clause is soft by design.
            return exposure
        for row in rows:
            try:
                prior = IntentEnvelope(**json.loads(row["envelope"]))
            except Exception:
                continue
            if prior.semantic_hash == env.semantic_hash:
                # The same request, sent again -- a double-tapped button, a
                # chat UI that resends, an agent retrying. That is one basket,
                # and idempotency already returns the one payment it made. A
                # split is DIFFERENT baskets under one instruction; counting a
                # resend as one would step up on the most ordinary event in the
                # system and cost a person their purchase.
                continue
            if prior.category != env.category:
                continue
            if prior.ceiling_paise() != ceiling:
                continue
            spent += int(row["amount_paise"])
            txns += 1
        if not txns:
            return exposure
        return exposure.model_copy(update={
            "mandate_paise": ceiling,
            "mandate_spent_paise": spent,
            "mandate_txns": txns,
        })

    def _event(self, kind: str, cid: str, payload: dict, now: datetime) -> None:
        self.ledger.append(kind, cid, payload, now)

    def _graph(self, intent_id: str, cid: str, node: str, parent: str | None,
               payload: dict, now: datetime) -> None:
        self.db.execute(
            "INSERT INTO intent_graph_events (intent_id, correlation_id, node,"
            " parent_node, ts, payload) VALUES (?,?,?,?,?,?)",
            (intent_id, cid, node, parent, now.isoformat(), json.dumps(payload)))

    def _persist_intent(self, env: IntentEnvelope, now: datetime, reason: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO intents (intent_id, user_id, created_at,"
            " current_version) VALUES (?,?,?,?)",
            (env.intent_id, env.user_id, now.isoformat(), env.version))
        self.db.execute("UPDATE intents SET current_version=? WHERE intent_id=?",
                        (env.version, env.intent_id))
        self.db.execute(
            "INSERT OR REPLACE INTO intent_versions (intent_id, version, envelope,"
            " created_at, reason, envelope_hash) VALUES (?,?,?,?,?,?)",
            (env.intent_id, env.version, env.model_dump_json(), now.isoformat(),
             reason, env.envelope_hash))

    def _candidates(self, env: IntentEnvelope, item: dict,
                    ignore_budget: bool = False,
                    strict: bool = False) -> list[Product]:
        """Everything that answers ONE requested item.

        The grammar said "waterproof trail shoes" is one thing described three
        ways, so the first attempt requires a product to answer all three. If
        nothing does, the grouping was the parser's guess and not the human's
        meaning -- "diapers baby wipes" with no comma between them is two
        things -- so we fall back to matching the terms independently.

        Grammar proposes; the catalog disposes. What we never do is widen to
        the category and buy something that answers none of the words.
        """
        terms = [t for t in item.get("terms") or [] if t]
        cat = item.get("category")
        # Retrieval already named the products. They still have to survive the
        # hard filter -- an embedding does not get to reintroduce a candidate
        # the human's budget excluded.
        if item.get("product_ids"):
            found = [p for p in (self.catalog.get(pid)
                                 for pid in item["product_ids"]) if p]
            return hard_filter(
                found,
                max_price_paise=None if ignore_budget else env.max_price_paise,
                required=env.required_attributes,
                excluded=env.excluded_attributes,
                merchants=env.merchant_constraints or None)
        base = {"max_price_paise": None if ignore_budget else env.max_price_paise,
                "required": env.required_attributes,
                "excluded": env.excluded_attributes,
                "merchants": env.merchant_constraints or None}
        found: list[Product] = []
        if terms:
            found = self.broker.call(
                "search_products",
                {"category": cat, "terms": terms, "match_all_terms": True} | base,
                actor="model")
            if not found and cat and not strict:
                # The words did not land, but the shelf did. Search the shelf
                # WITHOUT the category filter's help so the terms still have to
                # do the work; if that is also empty the answer is "we do not
                # stock it", which is a real answer.
                #
                # `strict` exists because this fallback is an OR, and asking
                # "does anything satisfy ALL of these words" is the question the
                # item-splitting pass needs answered. Letting it fall through to
                # an OR here made every group look satisfiable and the split
                # never fired -- so "diapers baby wipes" quietly delivered the
                # wipes. FAILURES #27.
                found = self.broker.call(
                    "search_products",
                    {"category": None, "terms": terms,
                     "match_all_terms": False} | base, actor="model")
        elif cat:
            found = self.broker.call(
                "search_products", {"category": cat, "terms": None} | base,
                actor="model")
        return found

    # ---------- the journey ----------
    def run(self, *, utterance: str, user_id: str, now: datetime,
            exposure: Exposure | None = None,
            accept_offers: str = "in_envelope",   # 'none'|'in_envelope'|'all'
            human_confirms: bool | None = None,
            approval_token: str | None = None,
            inject: dict | None = None) -> JourneyResult:
        t0 = time.perf_counter()
        inject = inject or {}
        exposure = exposure or Exposure()
        cid = "cor_" + format(abs(hash((utterance, user_id, now.isoformat()))), "x")[:16]
        r = JourneyResult(correlation_id=cid)

        # 1. INTENT ------------------------------------------------------
        # The sentence itself, not a length.
        #
        # This recorded {"len": ..., "user": ...} -- which answers "was there an
        # utterance" and not "what did the human ask", the first question any
        # audit of a payment asks. The text was recoverable from
        # intent_versions.envelope, but ONLY for a journey that compiled: an
        # abstention has no envelope, so the one class of journey where the
        # sentence is the entire evidence was the one that discarded it.
        #
        # A shopping sentence is not a credential. It is bounded at 2,000 chars
        # by the request schema, and the audit trail is worth more than the
        # bytes.
        self._event("UTTERANCE", cid,
                    {"utterance": utterance, "len": len(utterance),
                     "user": user_id}, now)
        _t_compile = time.perf_counter()
        env, tel = self.compiler.compile(utterance, user_id, now)
        _orecord("interpret", (time.perf_counter() - _t_compile) * 1000)
        r.telemetry = tel
        if env is not None and inject.get("expire"):
            # Move the clock past the envelope's TTL rather than faking a flag:
            # the same code path a real expiry takes.
            now = env.expires_at + timedelta(seconds=1)
        if env is None:
            self._event("EXCEPTION", cid, {"why": "compiler abstained"}, now)
            r.note = "abstained: could not ground the utterance"
            r.latency_ms = (time.perf_counter() - t0) * 1000
            return r
        r.intent = env
        self._persist_intent(env, now, "created from utterance")
        if self.authority is not None:
            self.authority.open(intent_id=env.intent_id, user_id=user_id,
                                now=now, correlation_id=cid)
            self._to(env, "INTERPRETED", now, cid, "compiled from the utterance")

        # Stop here if the authority is cancelled.
        #
        # AUTH-003 already refuses a revoked mandate, but the policy engine runs
        # at step 7 -- after search, ranking, offers and pricing. A revoked
        # principal asking for something the shop cannot afford therefore got
        # "the cheapest running shoes is Rs 4,299, above the Rs 100 you allowed":
        # true, useless, and not the reason. Somebody who pressed stop is owed
        # the sentence "you pressed stop", not a price comparison. Found by a
        # generated case, where a ceiling of 100 made the abstain path fire
        # before the clause that mattered.
        #
        # It is also simply less work: a cancelled authority should not be
        # searching a catalog.
        revoked_early = self._revoked(env)
        if revoked_early is not None:
            self._to(env, "REVOKED", now, cid,
                     f"revoked at {revoked_early.revoked_at} "
                     f"({revoked_early.scope})")
            self._event("AUTHORIZATION_REVOKED", cid, revoked_early.dict(), now)
            r.payment_state = "BLOCKED"
            r.note = (f"this authority was revoked at "
                      f"{revoked_early.revoked_at} ({revoked_early.scope} "
                      f"scope); nothing will execute under it")
            r.telemetry = dict(r.telemetry) | {
                "revocation": revoked_early.dict()}
            r.latency_ms = (time.perf_counter() - t0) * 1000
            return r
        self._event("INTENT_CREATED", cid, json.loads(env.model_dump_json()), now)
        self._graph(env.intent_id, cid, "intent", None,
                    {"category": env.category, "ceiling": env.ceiling_paise()}, now)

        # 2. SEARCH + SELECT, ONCE PER REQUESTED ITEM -------------------
        # The human said "rice and cooking oil". That is two things, and the
        # cart owes them two lines. The old code kept one noun and silently
        # dropped the rest -- not refused, dropped -- which is the quietest way
        # an agent can fail you: you get a bill that looks right and a delivery
        # that is short. FAILURES #16.
        requested = env.requested_items or [
            {"terms": env.product_terms, "category": env.category,
             "surface": (env.product_terms or ["it"])[0], "how": "exact"}]
        # Grammar proposes; the catalog disposes -- and it has to dispose at the
        # ITEM level, not the candidate level. The first version of this split
        # the search and then still selected one product from the union, so
        # "toothpaste toothbrush and soap" came back as toothpaste and soap and
        # the toothbrush vanished exactly the way the rice used to. A group
        # that nothing satisfies as a whole is not one thing described twice;
        # it is two things said without a comma. FAILURES #27.
        resolved: list[dict] = []
        for it in requested:
            terms = [t for t in (it.get("terms") or []) if t]
            if len(terms) > 1 and not self._candidates(env, it, strict=True):
                resolved.extend(dict(it, terms=[t], surface=t) for t in terms)
            else:
                resolved.append(it)
        requested = resolved

        picks: list[tuple[Product, dict, str, float]] = []
        unfulfilled: list[str] = []
        over_budget: list[dict] = []
        excluded_out: list[dict] = []
        searched = 0
        for it in requested:
            found = self._candidates(env, it)
            if not found:
                # Before saying "we do not stock that", check whether we stock
                # it and it is simply dearer than the human allowed. Those are
                # completely different sentences and the human deserves the
                # right one -- "the cheapest sunscreen is Rs 749, you said 500"
                # is useful; "we do not stock sunscreen" is false. FAILURES #19.
                any_price = self._candidates(env, it, ignore_budget=True)
                if any_price:
                    cheapest = min(any_price, key=lambda p: p.price_paise)
                    over_budget.append({
                        "surface": it.get("surface") or (it["terms"] or [""])[0],
                        "cheapest_paise": cheapest.price_paise,
                        "cheapest_name": cheapest.name,
                        "ceiling_paise": env.max_price_paise})
                elif env.excluded_attributes:
                    # And before saying "we do not stock that" for the second
                    # time: check whether we stock it and the human excluded
                    # all of it. "buy rice but not basmati", in a shop whose
                    # only rice is basmati, is not a shop with no rice. Telling
                    # them it is would be the same class of lie as FAILURES #19
                    # -- true-sounding, wrong, and it sends them away.
                    unfiltered = env.model_copy(update={"excluded_attributes": []})
                    would = self._candidates(unfiltered, it, ignore_budget=True)
                    if would:
                        excluded_out.append({
                            "surface": it.get("surface") or (it["terms"] or [""])[0],
                            "excluded": list(env.excluded_attributes),
                            "example": would[0].name,
                            "n": len(would)})
            searched += len(found)
            ranked_i = rank(found, env.objective, env.max_price_paise,
                            terms=it.get("terms"))
            if not ranked_i:
                unfulfilled.append(it.get("surface") or (it["terms"] or [""])[0])
                continue
            p, sc, why = ranked_i[0]
            picks.append((p, it, why, sc))
            if len(requested) == 1:
                r.candidates = [{"product_id": q.product_id, "name": q.name,
                                 "price_paise": q.price_paise, "score": s2,
                                 "why": w2} for q, s2, w2 in ranked_i[:8]]
        if len(requested) > 1:
            r.candidates = [{"product_id": p.product_id, "name": p.name,
                             "price_paise": p.price_paise, "score": sc,
                             "why": f"for {it.get('surface')!r}: {why}"}
                            for p, it, why, sc in picks]

        approx = [it for it in requested if it.get("approximate")]
        if approx and picks:
            by_surface = {it.get("surface"): p for p, it, _w, _s in picks}
            r.telemetry = dict(r.telemetry) | {
                "approximate_note": "; ".join(
                    f"you said {a.get('surface')!r}; the nearest thing this shop"
                    f" sells is {by_surface[a.get('surface')].name!r}"
                    for a in approx if a.get("surface") in by_surface)}
        if excluded_out:
            r.telemetry = dict(r.telemetry) | {
                "excluded_out": excluded_out,
                "excluded_note": "; ".join(
                    f"this shop has {o['n']} thing{'' if o['n'] == 1 else 's'} "
                    f"answering {o['surface']!r} ({o['example']}), and you "
                    f"excluded {', '.join(o['excluded'])}"
                    for o in excluded_out)}
        if over_budget:
            r.telemetry = dict(r.telemetry) | {
                "over_budget": over_budget,
                "over_budget_note": "; ".join(
                    f"the cheapest {o['surface']} is {rupees(o['cheapest_paise'])}"
                    f" ({o['cheapest_name']}),"
                    f" above the {rupees(o['ceiling_paise'] or 0)} you allowed"
                    for o in over_budget)}
        if unfulfilled:
            # We do NOT substitute. A catalog that cannot answer "helicopter"
            # says so; it does not hand over a yoga mat and hope. The shortfall
            # travels into telemetry, into drift, and into the sentence the
            # human reads. See ADR-031.
            r.telemetry = dict(r.telemetry) | {
                "unfulfilled": unfulfilled,
                "unfulfilled_note": (
                    "this catalog has nothing that answers "
                    + ", ".join(repr(x) for x in unfulfilled)
                    + "; REMIT does not substitute")}
        _orecord("retrieve", (time.perf_counter() - t0) * 1000)
        self._event("PRODUCT_SEARCH", cid,
                    {"results": searched, "requested_items": len(requested),
                     "fulfilled": len(picks), "unfulfilled": unfulfilled}, now)
        self._graph(env.intent_id, cid, "search", "intent",
                    {"results": searched, "unfulfilled": len(unfulfilled)}, now)
        if not picks:
            self._event("EXCEPTION", cid, {"why": "no product matched the intent"}, now)
            if excluded_out:
                r.note = r.telemetry["excluded_note"]
            elif over_budget:
                r.note = r.telemetry["over_budget_note"]
            elif unfulfilled:
                r.note = "this catalog does not stock " + ", ".join(unfulfilled)
            else:
                r.note = "no product matched the intent"
            r.latency_ms = (time.perf_counter() - t0) * 1000
            return r

        # 3. SELECT ------------------------------------------------------
        sel, _first_item, why, score = picks[0]
        r.selected, r.why_selected = sel, why
        # Name, price and reason alongside the id. A product id is a join key,
        # and an auditor reading this six months from now against a catalog
        # that has moved on needs the row to say what was actually bought and
        # why it beat the alternatives -- not to require a table that may no
        # longer contain it.
        self._event("PRODUCT_SELECTED", cid,
                    {"product_id": sel.product_id, "name": sel.name,
                     "price_paise": sel.price_paise,
                     "why": r.why_selected, "score": score,
                     "lines": len(picks)}, now)
        self._graph(env.intent_id, cid, "selection", "search",
                    {"product_id": sel.product_id, "price": sel.price_paise}, now)

        cart = new_cart(env.intent_id, env.version, self.catalog.version(), now)
        # One line per thing asked for. Quantity only multiplies when a single
        # thing was asked for -- "2x earbuds" is two earbuds, but "rice and oil"
        # is one of each, not two of each.
        for p, it, w, _s in picks:
            cart.add(line_from(p, env.quantity if len(picks) == 1 else 1,
                               "primary", "intent", w))

        # 4. REVENUE ENGINE ---------------------------------------------
        offers = self.revenue.propose(env, cart)
        r.offers = offers
        for o in offers:
            self._event("OFFER_PROPOSED", cid, json.loads(o.model_dump_json()), now)
            self._graph(env.intent_id, cid, f"offer:{o.product_id}", "selection",
                        {"kind": o.kind, "delta": o.net_delta_paise,
                         "needs_human": o.needs_human}, now)
        # Offers are accepted against a RUNNING total, re-checking headroom
        # after each one. Evaluating them independently let three offers that
        # each fit jointly break the envelope -- the agent kept its promise three
        # times and broke it once. FAILURES.md 2026-08-21 18:40.
        ceiling_now = env.ceiling_paise()
        for o in offers:
            p = self.catalog.get(o.product_id)
            if p is None:
                continue
            if accept_offers == "all":
                take = True
            elif accept_offers == "in_envelope":
                trial = cart.model_copy(deep=True)
                trial.add(line_from(p, 1, o.kind, "agent", o.reason))
                would_be = price_cart(trial, self.catalog).total_paise
                take = ceiling_now is None or would_be <= ceiling_now
            else:
                take = False
            if not take:
                continue
            cart.add(line_from(p, 1, o.kind, "agent", o.reason))
            r.accepted_offers.append(o.product_id)
            self._event("OFFER_ACCEPTED", cid,
                        {"product_id": o.product_id, "by": "agent",
                         "running_total_paise": price_cart(cart, self.catalog
                                                           ).total_paise}, now)

        # Snapshot what the human was shown, BEFORE the world is allowed to move.
        _shown = price_cart(cart, self.catalog)
        shipping_shown = _shown.shipping_paise
        r.shown_total_paise = _shown.total_paise

        # 5. MUTATE THE WORLD (chaos hooks) ------------------------------
        if "shipping" in inject:
            self.catalog.set_shipping(sel.merchant_id, inject["shipping"],
                                      10**12, now)
        if "price" in inject:
            self.catalog.set_price(sel.product_id, inject["price"], now)
            fresh = self.catalog.get(sel.product_id)
            if fresh:
                for l in cart.lines:
                    if l.product_id == fresh.product_id:
                        # shown_price_paise is NOT touched: the whole point is
                        # to remember what the human was shown.
                        l.unit_price_paise = fresh.price_paise
        if inject.get("delist"):
            self.catalog.deactivate(sel.product_id, now)
        if inject.get("qty"):
            # Something upstream inflated the quantity. The envelope still says
            # what the human asked for, so the quantity drift dimension sees it.
            for l in cart.lines:
                if l.origin == "primary":
                    l.qty = int(inject["qty"])

        # 6. PRICE + DRIFT + RISK ---------------------------------------
        totals = price_cart(cart, self.catalog)
        r.cart, r.totals = cart, totals
        self._to(env, "GROUNDED", now, cid,
                 f"{len(cart.lines)} line(s) bound to real products")
        self._event("CART_PRICED", cid, json.loads(totals.model_dump_json()), now)
        self._graph(env.intent_id, cid, "cart", "selection",
                    {"total": totals.total_paise, "lines": len(cart.lines)}, now)

        catalog_now = self.catalog.version()
        # Material staleness: does any line still cost what we showed, and does
        # the cart still price to the same total?
        stale = False
        for l in cart.lines:
            fresh = self.catalog.get(l.product_id)
            if fresh is None or fresh.price_paise != l.unit_price_paise:
                stale = True
                break
        shown_sub = sum((l.shown_price_paise or l.unit_price_paise) * l.qty
                        for l in cart.lines)
        shipping_at_selection = shipping_shown
        if totals.subtotal_paise != shown_sub or totals.shipping_paise != shipping_at_selection:
            stale = True
        drift = compute_drift(env=env, cart=cart, totals=totals,
                              catalog_version=catalog_now, stale_pricing=stale)
        r.drift = drift
        self._event("DRIFT_MEASURED", cid, json.loads(drift.model_dump_json()), now)
        self._graph(env.intent_id, cid, "drift", "cart",
                    {"score": drift.score, "worst": drift.worst[0]}, now)

        merchant = self.catalog.merchant(sel.merchant_id)
        p_cal = float(self.calibrator(env.parse_confidence))
        risk = assess(env=env, total_paise=totals.total_paise, drift=drift,
                      exposure=exposure, now=now, parse_confidence=p_cal,
                      merchant_risk=(merchant.risk_tier if merchant else "low"),
                      friction_floor_paise=self.policy.limits["friction_floor_paise"],
                      friction_bps=self.policy.limits["friction_bps"],
                      session_cap_paise=self.policy.limits["session_exposure_paise"],
                      daily_cap_paise=self.policy.limits["daily_exposure_paise"],
                      velocity_cap_1h=self.policy.limits["velocity_1h"])
        r.risk = risk
        self._event("RISK_EVALUATED", cid,
                    json.loads(risk.model_dump_json()) |
                    {"raw_confidence": env.parse_confidence,
                     "calibrated_confidence": round(p_cal, 4)}, now)

        # 7. POLICY ------------------------------------------------------
        exposure = self._mandate_exposure(env, exposure, now)
        revoked_now = self._revoked(env)
        if revoked_now is not None:
            r.telemetry = dict(r.telemetry) | {
                "revocation": revoked_now.dict()}
        oos = [l.product_id for l in cart.lines
               if (self.catalog.get(l.product_id) or None) is None
               or (self.catalog.get(l.product_id).inventory < l.qty)]
        _t_policy = time.perf_counter()
        auth = authorize(env=env, cart=cart, totals=totals, drift=drift, risk=risk,
                         exposure=exposure, policy=self.policy, now=now,
                         catalog_version=catalog_now, out_of_stock=oos,
                         intent_revoked=revoked_now is not None
                                        or bool(inject.get("revoked")),
                         stale_pricing=stale)
        _orecord("policy", (time.perf_counter() - _t_policy) * 1000)
        r.authorization = auth
        self._event("POLICY_DECIDED", cid, auth.dict(), now)
        _olog("decision", cid, verdict=auth.verdict.value, failed=auth.failed,
              total_paise=totals.total_paise, drift=drift.score,
              intent=env.intent_id, actor=user_id,
              policy_version=auth.policy_version,
              catalog_version=catalog_now)
        self._graph(env.intent_id, cid, "policy", "drift",
                    {"verdict": auth.verdict.value, "failed": auth.failed}, now)
        self.db.execute(
            "INSERT INTO decisions (correlation_id, intent_id, cart_id, ts, drift,"
            " risk, policy, verdict, policy_version, catalog_version)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, env.intent_id, cart.cart_id, now.isoformat(),
             drift.model_dump_json(), risk.model_dump_json(),
             json.dumps(auth.dict()), auth.verdict.value, auth.policy_version,
             catalog_now))

        if auth.verdict is Verdict.DENY:
            r.note = auth.reason
            r.payment_state = "BLOCKED"
            self._to(env, "REVOKED" if revoked_now is not None else "REJECTED",
                     now, cid, f"refused by {', '.join(auth.failed) or 'policy'}")
            self._event("PAYMENT_BLOCKED", cid,
                        {"amount_paise": totals.total_paise,
                         "failed": auth.failed}, now)
            r.latency_ms = (time.perf_counter() - t0) * 1000
            return r

        authorization_state = "AUTO"
        if auth.verdict is Verdict.STEP_UP:
            self._event("STEP_UP_REQUIRED", cid,
                        {"amount_paise": totals.total_paise,
                         "reason": auth.reason}, now)
            if approval_token:
                # A token, not a boolean. It was bound to this person, this
                # request, this basket and this exact total at the moment they
                # were shown it -- so a price that moved, a line that changed or
                # a second tab replaying the same click all fail here rather
                # than paying. See remit/grants/approval.py.
                bad = self.approvals.redeem(
                    token=approval_token, user_id=user_id, env=env, cart=cart,
                    totals=totals, now=now) if self.approvals else None
                if bad is not None:
                    r.note = f"approval rejected ({bad.reason}): {bad.detail}"
                    r.payment_state = "APPROVAL_REJECTED"
                    self._event("APPROVAL_REJECTED", cid,
                                {"reason": bad.reason, "detail": bad.detail,
                                 "amount_paise": totals.total_paise}, now)
                    r.latency_ms = (time.perf_counter() - t0) * 1000
                    return r
                human_confirms = True
            elif human_confirms is None:
                r.note = "awaiting human confirmation"
                self._to(env, "PENDING_STEP_UP", now, cid,
                         "policy stopped and asked a person")
                r.payment_state = "AWAITING_HUMAN"
                if self.approvals is not None:
                    grant = self.approvals.issue(
                        user_id=user_id, env=env, cart=cart, totals=totals,
                        now=now, correlation_id=cid)
                    r.approval = {
                        "token": grant.token,
                        "amount_paise": grant.amount_paise,
                        "expires_at": grant.expires_at,
                        "cart_hash": grant.cart_hash,
                        "intent_hash": grant.intent_hash,
                        "binds": ["user", "intent hash", "cart hash", "amount",
                                  "expiry"],
                    }
                    self._event("APPROVAL_REQUESTED", cid, r.approval, now)
                r.latency_ms = (time.perf_counter() - t0) * 1000
                return r
            if not human_confirms:
                r.note = "human declined at the step-up"
                self._to(env, "CANCELLED", now, cid, "the human said no")
                r.payment_state = "DECLINED_BY_HUMAN"
                self._event("PAYMENT_BLOCKED", cid,
                            {"amount_paise": totals.total_paise,
                             "why": "human declined"}, now)
                r.latency_ms = (time.perf_counter() - t0) * 1000
                return r

            # THE APPROVAL IS RECORDED IN THE ENVELOPE, NOT JUST IN A LOG.
            #
            # A person who approves a Rs 7,315 basket against a Rs 5,000
            # instruction has authorised Rs 7,315 -- and until this existed,
            # the envelope still said Rs 5,000 and the payment went out above
            # it. Every clause downstream reads the envelope, so an approval
            # that does not amend it leaves the system's own record of what was
            # authorised disagreeing with what was paid. Version n+1, with the
            # reason, and version n is still there. FAILURES #29.
            ceiling_before = env.ceiling_paise()
            if ceiling_before is None or totals.total_paise > ceiling_before:
                env, reason = amend(
                    env, now=now,
                    reason=(f"human approved {rupees(totals.total_paise)} at a "
                            f"step-up" + (f", raising the ceiling from "
                                          f"{rupees(ceiling_before)}"
                                          if ceiling_before else "")),
                    max_total_paise=totals.total_paise, max_price_paise=None)
                r.intent = env
                self._persist_intent(env, now, reason)
                self._event("INTENT_AMENDED", cid,
                            {"version": env.version, "reason": reason,
                             "ceiling_paise": env.ceiling_paise()}, now)
            self._event("USER_CONFIRMED", cid,
                        {"amount_paise": totals.total_paise,
                         "envelope_version": env.version}, now)
            authorization_state = "CONFIRMED"

        # 8. PAY ---------------------------------------------------------
        # Asked a second time, immediately before the money moves.
        #
        # The interesting revocation is not the one that arrives before the
        # decision -- AUTH-003 already refuses that one. It is the one that
        # lands in the gap BETWEEN the decision and the execution, which is
        # exactly the moment a person reaching for a kill switch is living in.
        # Today a single process-wide lock makes that interleaving impossible,
        # so this check can never fire in this deployment. It is here because a
        # control that is only correct because of a lock it does not own is not
        # a control, and the day this runs in two processes is not the day to
        # discover that. FAILURES #43.
        late = self._revoked(env)
        if late is not None:
            self._event("AUTHORIZATION_REVOKED", cid, late.dict(), now)
            r.payment_state = "BLOCKED"
            r.note = (f"authority revoked at {late.revoked_at} "
                      f"({late.scope}); nothing was charged")
            r.telemetry = dict(r.telemetry) | {"revocation": late.dict()}
            r.latency_ms = (time.perf_counter() - t0) * 1000
            return r

        # Keyed on WHAT was asked for and WHAT is in the cart -- never on the
        # intent id, which is fresh on every utterance. A repeated utterance is
        # one purchase, not two. FAILURES.md 2026-08-21 15:00.
        cart_sig = _sha(_canonical(sorted(
            (l.product_id, l.qty, l.unit_price_paise) for l in cart.lines)))
        idem = idempotency_key(
            remit_id=f"{env.user_id}:{env.semantic_hash[:24]}",
            intent_hash=cart_sig,
            envelope_epoch=totals.total_paise,
            revocation_epoch=cart.catalog_version)
        # The state depends on the VERDICT, not on whether a confirm flag
        # happened to be set. An AUTO decision that a human also confirmed was
        # never a step-up, and recording it as APPROVED would claim a human
        # made a decision they were never asked to make. Where policy did stop,
        # the authority genuinely passed through PENDING_STEP_UP on its way
        # here -- the answer simply arrived with the request -- so both moves
        # are recorded rather than one being skipped.
        if auth.verdict is Verdict.AUTO:
            self._to(env, "AUTHORIZED", now, cid,
                     "policy authorised the agent to proceed alone")
        else:
            self._to(env, "PENDING_STEP_UP", now, cid,
                     "policy required a person")
            self._to(env, "APPROVED", now, cid,
                     "a person redeemed a token bound to this basket"
                     if approval_token else "a person confirmed")
        self._to(env, "EXECUTING", now, cid, "creating the order")
        _t_pay = time.perf_counter()
        pid, created = self.payments.create(
            cart_id=cart.cart_id, intent_id=env.intent_id, idem_key=idem,
            amount_paise=totals.total_paise, now=now, correlation_id=cid,
            user_id=user_id)
        r.payment_id = pid
        if not created:
            row = self.payments.get(pid)
            r.replayed = True
            r.payment_state = row["state"]
            r.order_id = row["order_id"]
            r.note = "identical intent+cart already executed; returning prior result"
            self._event("PAYMENT_REPLAYED", cid, {"payment_id": pid}, now)
            r.latency_ms = (time.perf_counter() - t0) * 1000
            return r

        self._event("PAYMENT_REQUESTED", cid,
                    {"payment_id": pid, "amount_paise": totals.total_paise,
                     "idem": idem[:16]}, now)
        try:
            order = self.broker.call(
                "create_order",
                {"amount_paise": totals.total_paise, "receipt": receipt_for(idem),
                 "notes": {"intent_id": env.intent_id, "cart_id": cart.cart_id,
                           "correlation_id": cid}},
                actor="orchestrator", authorization=authorization_state)
        except AMBIGUOUS as e:
            self.payments.transition(pid, "UNKNOWN", now, f"timeout: {e}")
            r.payment_state = "UNKNOWN"
            r.note = ("AMBIGUOUS: the order may exist. Reconciler owns this; "
                      "RBI allows T+5 for exactly this state.")
            self._event("EXCEPTION", cid, {"state": "UNKNOWN", "why": str(e)}, now)
            r.latency_ms = (time.perf_counter() - t0) * 1000
            return r
        except Exception as e:
            self.payments.transition(pid, "FAILED", now, f"gateway error: {e}")
            self._to(env, "FAILED", now, cid, f"gateway error: {e}")
            r.payment_state = "FAILED"
            r.note = f"payment failed: {e}"
            self._event("PAYMENT_FAILED", cid, {"why": str(e)}, now)
            r.latency_ms = (time.perf_counter() - t0) * 1000
            return r

        _orecord("execute", (time.perf_counter() - _t_pay) * 1000)
        self.payments.attach_order(pid, order["id"])
        r.order_id = order["id"]
        self._to(env, "EXECUTED", now, cid, f"gateway order {order['id']}")
        r.payment_state = "CREATED"
        self._event("PAYMENT_CREATED", cid,
                    {"payment_id": pid, "order_id": order["id"]}, now)
        self._graph(env.intent_id, cid, "payment", "policy",
                    {"order_id": order["id"], "amount": totals.total_paise}, now)
        self.db.execute(
            "INSERT OR REPLACE INTO carts (cart_id, intent_id, intent_version,"
            " catalog_version, created_at, state, items, totals)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (cart.cart_id, env.intent_id, env.version, cart.catalog_version,
             now.isoformat(), "paid", cart.model_dump_json(),
             totals.model_dump_json()))
        r.note = auth.reason
        r.latency_ms = (time.perf_counter() - t0) * 1000
        return r
