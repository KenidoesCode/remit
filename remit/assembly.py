"""Composition root. The only place that knows how the pieces fit together.

Everything else takes its collaborators as arguments, which is why the whole
system is testable offline with no API key and no network.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from .buyer.journey import Journey
from .db import connect
from .domain.catalog import Catalog
from .domain.revenue import RevenueEngine
from .exec.payments import PaymentStore
from .exec.razorpay import FakeGateway, PaymentGateway, RazorpayTestClient
from .exec.recon import Reconciler
from .exec.webhooks import WebhookProcessor
from .intent.grounding import Lexicon
from .intent.shopping import LLMCompiler, RuleCompiler
from .ledger.chain import Ledger
from .paths import POLICY as PATHS_POLICY
from .policy.authorize import Policy
from .seed.catalog_seed import seed
from .tools.broker import Tool, ToolBroker

WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "remit_test_webhook_secret")


def load_calibrator(path: str | None = None):
    """Load a temperature fitted on the TRAIN split, if one exists.

    If it does not, the system runs UNCALIBRATED and says so rather than
    pretending. `eval/calibrate.py` produces the file.
    """
    import json as _json
    from .paths import CALIBRATION
    from .risk.calibration import IsotonicCalibrator, TemperatureCalibrator
    try:
        with open(path or CALIBRATION) as fh:
            d = _json.load(fh)
        kind = d.get("chosen", "uncalibrated")
        if kind == "isotonic":
            return IsotonicCalibrator.from_dict(d["isotonic"])
        if kind == "temperature":
            return TemperatureCalibrator(d["temperature"])
        return TemperatureCalibrator(1.0)
    except Exception:
        # No fitted calibrator on disk: run UNCALIBRATED and say so, rather
        # than pretending a raw parser score is a probability.
        return TemperatureCalibrator(1.0)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class App:
    db: object
    catalog: Catalog
    policy: Policy
    ledger: Ledger
    payments: PaymentStore
    webhooks: WebhookProcessor
    recon: Reconciler
    broker: ToolBroker
    gateway: PaymentGateway
    journey: Journey
    seed_info: dict

    def rebuild_journey(self, policy: Policy | None = None,
                        aggressiveness: float | None = None) -> Journey:
        """Frontier sweeps need a journey with different knobs and the same
        world. Policy is immutable; we build a new Journey, never mutate."""
        p = policy or self.policy
        rev = RevenueEngine(
            self.catalog,
            aggressiveness=(aggressiveness if aggressiveness is not None
                            else p.revenue.get("aggressiveness", 1.0)),
            max_offers=p.revenue.get("max_offers", 3),
            min_relevance=p.revenue.get("min_relevance", 0.35))
        return Journey(db=self.db, catalog=self.catalog,
                       compiler=self.journey.compiler, revenue=rev, policy=p,
                       ledger=self.ledger, payments=self.payments,
                       broker=self.broker, gateway=self.gateway,
                       calibrator=self.journey.calibrator)


def build(*, db_path: str = ":memory:", policy_path: str | None = None,
          live: bool = False, now: datetime | None = None,
          gateway: PaymentGateway | None = None, use_llm: bool = False) -> App:
    now = now or utcnow()
    db = connect(db_path)
    info = seed(db, now)
    catalog = Catalog(db)
    policy = Policy.load(policy_path or str(PATHS_POLICY))
    ledger = Ledger(db_path if db_path != ":memory:" else ":memory:")
    payments = PaymentStore(db)

    if gateway is None:
        gateway = RazorpayTestClient() if live else FakeGateway()

    compiler = RuleCompiler(lexicon=Lexicon.from_db(db, catalog.version()))
    if use_llm:
        try:
            import anthropic  # noqa
            key = os.environ.get("ANTHROPIC_API_KEY")
            if key:
                compiler = LLMCompiler(client=anthropic.Anthropic(api_key=key),
                                       fallback=RuleCompiler(
                                           lexicon=Lexicon.from_db(
                                               db, catalog.version())))
        except Exception:
            pass   # degradation always moves toward MORE friction

    broker = ToolBroker()
    _register_tools(broker, catalog, gateway)
    revenue = RevenueEngine(catalog, **{
        "aggressiveness": policy.revenue.get("aggressiveness", 1.0),
        "max_offers": policy.revenue.get("max_offers", 3),
        "min_relevance": policy.revenue.get("min_relevance", 0.35)})
    journey = Journey(db=db, catalog=catalog, compiler=compiler, revenue=revenue,
                      policy=policy, ledger=ledger, payments=payments,
                      broker=broker, gateway=gateway,
                      calibrator=load_calibrator())
    return App(db=db, catalog=catalog, policy=policy, ledger=ledger,
               payments=payments,
               webhooks=WebhookProcessor(db, payments, WEBHOOK_SECRET),
               recon=Reconciler(db, payments, gateway), broker=broker,
               gateway=gateway, journey=journey, seed_info=info)


def _register_tools(broker: ToolBroker, catalog: Catalog, gw: PaymentGateway) -> None:
    broker.register(Tool(
        name="search_products",
        description="Search the merchant catalog. Read-only.",
        input_schema={"type": "object", "required": [],
                      "properties": {"category": {"type": ["string", "null"]},
                                     "terms": {"type": "array"},
                                     "max_price_paise": {"type": ["integer", "null"]},
                                     "required": {"type": "array"},
                                     "excluded": {"type": "array"},
                                     "merchants": {"type": ["array", "null"]},
                                     "match_all_terms": {"type": "boolean"}}},
        output_schema={"type": "array"}, financial=False, risk="none",
        requires_authority=False, version="1.0.0",
        fn=lambda category=None, max_price_paise=None, required=None,
                  excluded=None, merchants=None, terms=None,
                  match_all_terms=False: catalog.search(
                      category=category, max_price_paise=max_price_paise,
                      required=required, excluded=excluded, merchants=merchants,
                      match_all_terms=match_all_terms,
                      terms=terms)))

    broker.register(Tool(
        name="get_product",
        description="Fetch one product by id. Read-only.",
        input_schema={"type": "object", "required": ["product_id"],
                      "properties": {"product_id": {"type": "string"}}},
        output_schema={"type": "object"}, financial=False, risk="none",
        requires_authority=False, version="1.0.0",
        fn=lambda product_id: catalog.get(product_id)))

    broker.register(Tool(
        name="create_order",
        description=("Create a Razorpay order. FINANCIAL. Reachable only by the "
                     "orchestrator holding an AUTO or CONFIRMED authorization."),
        input_schema={"type": "object",
                      "required": ["amount_paise", "receipt", "notes"],
                      "properties": {"amount_paise": {"type": "integer"},
                                     "receipt": {"type": "string", "maxLength": 40},
                                     "notes": {"type": "object"}}},
        output_schema={"type": "object"}, financial=True, risk="high",
        requires_authority=True, version="1.0.0",
        fn=lambda amount_paise, receipt, notes: gw.create_order(
            amount_paise=amount_paise, receipt=receipt, notes=notes)))
