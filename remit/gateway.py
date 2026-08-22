"""The one place the whole path is assembled.

utterance -> intent -> calibrated p -> policy -> claim -> execute -> ledger

Read this file top to bottom and the architecture diagram should be
unnecessary. If it stops reading that way, the design has drifted.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from .buyer.journey import AMBIGUOUS as _AMBIGUOUS
from .exec.idempotency import idempotency_key, receipt_for
from .exec.razorpay import PaymentGateway
from .intent.compiler import Catalog, IntentCompiler
from .ledger.chain import Ledger
from .models import Decision, Intent, Remit, SpendState, Verdict
from .policy.engine import Policy, evaluate
from .risk.calibration import TemperatureCalibrator


@dataclass
class Outcome:
    trace_id: str
    decision: Decision | None
    intent: Intent | None
    order: dict | None
    idem_key: str | None
    replayed: bool = False
    note: str = ""


class Gateway:
    def __init__(self, *, ledger: Ledger, policy: Policy, catalog: Catalog,
                 compiler: IntentCompiler, gw: PaymentGateway,
                 calibrator: TemperatureCalibrator | None = None):
        self.ledger = ledger
        self.policy = policy
        self.catalog = catalog
        self.compiler = compiler
        self.gw = gw
        self.calibrate = calibrator or TemperatureCalibrator(1.0)

    def handle(self, *, utterance: str, merchant_id: str, remit: Remit | None,
               spend: SpendState, now: datetime,
               step_up_ok: bool = False) -> Outcome:
        trace_id = "trc_" + uuid.uuid4().hex[:16]
        self.ledger.append("UTTERANCE", trace_id,
                           {"merchant_id": merchant_id, "len": len(utterance)}, now)

        intent = self.compiler.compile(utterance, merchant_id, self.catalog)
        if intent is None:
            self.ledger.append("EXCEPTION", trace_id,
                               {"why": "compiler abstained"}, now)
            return Outcome(trace_id, None, None, None, None,
                           note="abstained: could not ground the utterance")

        self.ledger.append("INTENT", trace_id, intent.model_dump(mode="json"), now)
        p = self.calibrate(intent.raw_confidence)
        self.ledger.append("CALIBRATION", trace_id,
                           {"raw": intent.raw_confidence, "calibrated": p}, now)

        decision = evaluate(intent=intent, remit=remit, spend=spend,
                            p_correct=p, policy=self.policy, now=now)
        self.ledger.append("POLICY_DECISION", trace_id,
                           decision.model_dump(mode="json"), now)

        if decision.verdict is Verdict.DENY:
            return Outcome(trace_id, decision, intent, None, None,
                           note=decision.reason)

        if decision.verdict is Verdict.STEP_UP and not step_up_ok:
            self.ledger.append("CHALLENGE", trace_id,
                               {"amount_paise": intent.computed_amount_paise}, now)
            return Outcome(trace_id, decision, intent, None, None,
                           note="awaiting human confirmation")

        assert remit is not None
        key = idempotency_key(
            remit_id=remit.remit_id, intent_hash=intent.intent_hash,
            envelope_epoch=remit.envelope_epoch,
            revocation_epoch=remit.revocation_epoch)

        if not self.ledger.claim(key, trace_id, now):
            prior = self.ledger.result_for(key)
            self.ledger.append("GATEWAY_RESPONSE", trace_id,
                               {"replayed": True, "idem_key": key}, now)
            return Outcome(trace_id, decision, intent, None, key, replayed=True,
                           note=f"already executed; returning prior result {prior}")

        self.ledger.append("TOOL_CALL", trace_id,
                           {"tool": "create_order", "idem_key": key,
                            "amount_paise": intent.computed_amount_paise}, now)
        try:
            order = self.gw.create_order(
                amount_paise=intent.computed_amount_paise,
                receipt=receipt_for(key),
                notes={"remit_id": remit.remit_id, "trace_id": trace_id})
        except _AMBIGUOUS as e:
            # The order may or may not exist. Never retry blind, never
            # auto-refund. RBI allows T+5 for exactly this state.
            self.ledger.append("EXCEPTION", trace_id,
                               {"state": "AMBIGUOUS", "why": str(e)}, now)
            return Outcome(trace_id, decision, intent, None, key,
                           note="AMBIGUOUS: order may exist; reconciler owns it")
        except Exception as e:
            self.ledger.append("EXCEPTION", trace_id,
                               {"state": "FAILED", "why": str(e)}, now)
            return Outcome(trace_id, decision, intent, None, key,
                           note=f"failed: {e}")

        self.ledger.record_result(key, order["id"])
        self.ledger.append("GATEWAY_RESPONSE", trace_id,
                           {"order_id": order["id"]}, now)
        return Outcome(trace_id, decision, intent, order, key)
