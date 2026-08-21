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
from ..domain.intent import IntentEnvelope
from ..domain.revenue import Offer, RevenueEngine
from ..domain.risk import Exposure, RiskDecision, assess
from ..exec.idempotency import idempotency_key, receipt_for
from ..exec.payments import PaymentStore
from ..ledger.chain import Ledger
from ..models import canonical as _canonical, sha as _sha
from ..money import Paise, rupees
from ..policy.authorize import Authorization, Policy, Verdict, authorize
from ..tools.broker import ToolBroker


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
            "shown_total_paise": self.shown_total_paise,
            "note": self.note, "latency_ms": round(self.latency_ms, 2),
        }


class Journey:
    def __init__(self, *, db, catalog: Catalog, compiler, revenue: RevenueEngine,
                 policy: Policy, ledger: Ledger, payments: PaymentStore,
                 broker: ToolBroker, gateway, calibrator=None):
        self.db = db
        self.catalog = catalog
        self.compiler = compiler
        self.revenue = revenue
        self.policy = policy
        self.ledger = ledger
        self.payments = payments
        self.broker = broker
        self.gw = gateway
        # Identity until a temperature has been fitted on labelled data.
        # Deliberately explicit: an uncalibrated system says so.
        self.calibrator = calibrator or (lambda x: x)

    # ---------- audit helpers ----------
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

    # ---------- the journey ----------
    def run(self, *, utterance: str, user_id: str, now: datetime,
            exposure: Exposure | None = None,
            accept_offers: str = "in_envelope",   # 'none'|'in_envelope'|'all'
            human_confirms: bool | None = None,
            inject: dict | None = None) -> JourneyResult:
        t0 = time.perf_counter()
        inject = inject or {}
        exposure = exposure or Exposure()
        cid = "cor_" + format(abs(hash((utterance, user_id, now.isoformat()))), "x")[:16]
        r = JourneyResult(correlation_id=cid)

        # 1. INTENT ------------------------------------------------------
        self._event("UTTERANCE", cid, {"len": len(utterance), "user": user_id}, now)
        env, tel = self.compiler.compile(utterance, user_id, now)
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
        self._event("INTENT_CREATED", cid, json.loads(env.model_dump_json()), now)
        self._graph(env.intent_id, cid, "intent", None,
                    {"category": env.category, "ceiling": env.ceiling_paise()}, now)

        # 2. SEARCH + RANK ----------------------------------------------
        products = self.broker.call(
            "search_products", {
                "category": env.category,
                "terms": env.product_terms,
                "max_price_paise": env.max_price_paise,
                "required": env.required_attributes,
                "excluded": env.excluded_attributes,
                "merchants": env.merchant_constraints or None},
            actor="model")
        term_fallback = False
        if not products and env.product_terms:
            # The human named something this catalog does not stock. We do NOT
            # silently substitute: we widen to the category, mark the fallback,
            # and let the product_match drift dimension flag that the cart does
            # not contain the thing that was asked for.
            products = self.broker.call(
                "search_products", {
                    "category": env.category, "terms": None,
                    "max_price_paise": env.max_price_paise,
                    "required": env.required_attributes,
                    "excluded": env.excluded_attributes,
                    "merchants": env.merchant_constraints or None},
                actor="model")
            term_fallback = bool(products)
            r.telemetry = dict(r.telemetry) | {
                "term_fallback": term_fallback,
                "term_fallback_note": (
                    f"no product named {env.product_terms[0]!r}; widened to the "
                    f"'{env.category}' category")}
        self._event("PRODUCT_SEARCH", cid,
                    {"results": len(products), "term_fallback": term_fallback}, now)
        self._graph(env.intent_id, cid, "search", "intent", {"results": len(products)}, now)
        ranked = rank(products, env.objective, env.max_price_paise)
        r.candidates = [{"product_id": p.product_id, "name": p.name,
                         "price_paise": p.price_paise, "score": s, "why": w}
                        for p, s, w in ranked[:8]]
        if not ranked:
            self._event("EXCEPTION", cid, {"why": "no product matched the intent"}, now)
            r.note = "no product matched the intent"
            r.latency_ms = (time.perf_counter() - t0) * 1000
            return r

        # 3. SELECT ------------------------------------------------------
        sel, score, why = ranked[0]
        r.selected, r.why_selected = sel, why
        self._event("PRODUCT_SELECTED", cid,
                    {"product_id": sel.product_id, "score": score}, now)
        self._graph(env.intent_id, cid, "selection", "search",
                    {"product_id": sel.product_id, "price": sel.price_paise}, now)

        cart = new_cart(env.intent_id, env.version, self.catalog.version(), now)
        cart.add(line_from(sel, env.quantity, "primary", "intent", why))

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
        oos = [l.product_id for l in cart.lines
               if (self.catalog.get(l.product_id) or None) is None
               or (self.catalog.get(l.product_id).inventory < l.qty)]
        auth = authorize(env=env, cart=cart, totals=totals, drift=drift, risk=risk,
                         exposure=exposure, policy=self.policy, now=now,
                         catalog_version=catalog_now, out_of_stock=oos,
                         intent_revoked=bool(inject.get("revoked")),
                         stale_pricing=stale)
        r.authorization = auth
        self._event("POLICY_DECIDED", cid, auth.dict(), now)
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
            if human_confirms is None:
                r.note = "awaiting human confirmation"
                r.payment_state = "AWAITING_HUMAN"
                r.latency_ms = (time.perf_counter() - t0) * 1000
                return r
            if not human_confirms:
                r.note = "human declined at the step-up"
                r.payment_state = "DECLINED_BY_HUMAN"
                self._event("PAYMENT_BLOCKED", cid,
                            {"amount_paise": totals.total_paise,
                             "why": "human declined"}, now)
                r.latency_ms = (time.perf_counter() - t0) * 1000
                return r
            self._event("USER_CONFIRMED", cid,
                        {"amount_paise": totals.total_paise}, now)
            authorization_state = "CONFIRMED"

        # 8. PAY ---------------------------------------------------------
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
        pid, created = self.payments.create(
            cart_id=cart.cart_id, intent_id=env.intent_id, idem_key=idem,
            amount_paise=totals.total_paise, now=now, correlation_id=cid)
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
        except TimeoutError as e:
            self.payments.transition(pid, "UNKNOWN", now, f"timeout: {e}")
            r.payment_state = "UNKNOWN"
            r.note = ("AMBIGUOUS: the order may exist. Reconciler owns this; "
                      "RBI allows T+5 for exactly this state.")
            self._event("EXCEPTION", cid, {"state": "UNKNOWN", "why": str(e)}, now)
            r.latency_ms = (time.perf_counter() - t0) * 1000
            return r
        except Exception as e:
            self.payments.transition(pid, "FAILED", now, f"gateway error: {e}")
            r.payment_state = "FAILED"
            r.note = f"payment failed: {e}"
            self._event("PAYMENT_FAILED", cid, {"why": str(e)}, now)
            r.latency_ms = (time.perf_counter() - t0) * 1000
            return r

        self.payments.attach_order(pid, order["id"])
        r.order_id = order["id"]
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
